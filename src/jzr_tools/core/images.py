"""Plain Python image conversion helpers.

Two backends convert files. ``pillow`` is built in (see ``pillow_engine``) and
is the default. ``magick`` drives ImageMagick from PATH, for the formats and
behaviours only it has. Nothing here touches Click, so the logic stays
testable on its own.

ImageMagick reads ``%`` in a path as the start of a numbered-sequence escape
(``%d``, ``%03d``). A ``%`` in a file or directory name is always meant
literally here, so it is driven in a way that never expands it.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
from typing import Sequence

SUPPORTED_EXTENSIONS = frozenset(
    {
        "avif",
        "bmp",
        "gif",
        "heic",
        "heif",
        "ico",
        "jpeg",
        "jpg",
        "png",
        "ppm",
        "tga",
        "tif",
        "tiff",
        "webp",
    }
)

LOSSY_EXTENSIONS = frozenset({"avif", "heic", "heif", "jpeg", "jpg", "webp"})

# Targets that can hold more than one frame; any other target gets the first.
ANIMATED_EXTENSIONS = frozenset({"avif", "gif", "webp"})

# Spellings of the same format, so `jzr image jpeg2png` also picks up `.jpg`.
_EXTENSION_ALIASES = (
    frozenset({"jpg", "jpeg"}),
    frozenset({"tif", "tiff"}),
    frozenset({"heic", "heif"}),
)

BACKENDS = ("pillow", "magick")

# What the first bytes of a file say it is, for the formats that say so. The
# value is the name used in messages. `tga` has no signature.
_SIGNATURE_FAMILIES = {
    "avif": "AVIF",
    "bmp": "BMP",
    "gif": "GIF",
    "heic": "HEIF",
    "heif": "HEIF",
    "ico": "ICO",
    "jpeg": "JPEG",
    "jpg": "JPEG",
    "png": "PNG",
    "ppm": "PPM",
    "tif": "TIFF",
    "tiff": "TIFF",
    "webp": "WEBP",
}
_HEIF_BRANDS = frozenset(
    {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs"}
)


class BackendUnavailableError(RuntimeError):
    """Raised when the requested backend is not on PATH."""


def normalize_extension(value: str) -> str:
    """Return a bare, lowercased extension such as ``jpg``."""
    return value.strip().lstrip(".").lower()


def equivalent_extensions(extension: str) -> frozenset[str]:
    """Return every spelling of ``extension``, including itself."""
    normalized = normalize_extension(extension)
    for alias_group in _EXTENSION_ALIASES:
        if normalized in alias_group:
            return alias_group
    return frozenset({normalized})


def detect_backends() -> dict[str, list[str]]:
    """Return the external backends on PATH, mapped to their argv prefix."""
    found: dict[str, list[str]] = {}

    magick = shutil.which("magick")
    if magick is None and os.name != "nt":
        # ImageMagick 6 only ships `convert`; on Windows that name collides
        # with the filesystem conversion tool, so it is only trusted elsewhere.
        magick = shutil.which("convert")
    if magick is not None:
        found["magick"] = [magick]

    return found


def resolve_backend(preference: str = "auto") -> tuple[str, list[str]]:
    """Return the backend to use and its argv prefix (empty for Pillow)."""
    if preference in {"auto", "pillow"}:
        return "pillow", []

    executable = detect_backends().get(preference)
    if executable is None:
        raise BackendUnavailableError(f"Backend not found on PATH: {preference}")
    return preference, executable


def quality_caveat(backend: str, target_extension: str) -> str | None:
    """Explain how ``--quality`` behaves for this target, if it is surprising.

    Returns ``None`` for lossy targets, where quality means what users expect.
    """
    extension = normalize_extension(target_extension)
    if extension in LOSSY_EXTENSIONS:
        return None

    if backend == "pillow":
        return f".{extension} is lossless; --quality is ignored"

    # ImageMagick reuses -quality for the zlib level of these lossless formats,
    # which is not the visual-quality knob it is for JPEG.
    if backend == "magick" and extension in {"png", "tif", "tiff"}:
        return (
            f".{extension} is lossless; --quality only tunes compression, "
            "not image fidelity"
        )

    return f"{backend} ignores --quality for .{extension} output"


def build_command(
    backend: str,
    executable: list[str],
    source: Path,
    target: Path,
    *,
    quality: int | None = None,
    overwrite: bool = True,
) -> list[str]:
    """Build the ImageMagick argv used to convert ``source`` into ``target``.

    ``overwrite`` changes nothing here: ImageMagick always replaces its output,
    so ``convert_file`` decides whether to run at all.
    """
    if backend != "magick":
        raise ValueError(f"Unknown backend: {backend}")

    target_extension = normalize_extension(target.suffix)
    single_frame = target_extension not in ANIMATED_EXTENSIONS

    source_argument = str(source)
    command = [*executable]
    # A multi-frame source (GIF, ICO, TIFF) must be cut down to one frame, or
    # it is written as `name-0.ext`, `name-1.ext`, ... instead of the target.
    if not single_frame:
        command.append(source_argument)
    elif "%" not in source_argument:
        command.append(source_argument + "[0]")
    else:
        # A frame selector makes ImageMagick expand `%d` escapes in the path,
        # and whether `%%` escapes them depends on its version. A path without
        # a selector is always read literally, so the other frames are dropped
        # after reading instead.
        command += [source_argument, "-delete", "1--1"]
    if quality is not None:
        command += ["-quality", str(quality)]
    # `%d` escapes in the output path are expanded and cannot be escaped
    # portably, so `convert_file` never passes a target containing `%`.
    command.append(str(target))
    return command


@dataclass(frozen=True)
class ConversionResult:
    """Outcome of a single file conversion."""

    source: Path
    target: Path
    # The external command, or empty when the built-in engine does the work.
    command: tuple[str, ...]
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "failed"


def target_for(source: Path, target_extension: str, *, outdir: Path | None = None) -> Path:
    """Return the output path for ``source`` rendered as ``target_extension``."""
    parent = outdir if outdir is not None else source.parent
    return parent / f"{source.stem}.{normalize_extension(target_extension)}"


def _path_key(path: Path) -> str:
    """Return a comparison key that treats paths differing only by case as equal."""
    return str(path.resolve()).casefold()


def plan_targets(
    sources: Sequence[Path],
    target_extension: str,
    *,
    outdir: Path | None = None,
    root: Path | None = None,
) -> list[Path]:
    """Return one distinct output path per source, in order.

    With both ``outdir`` and ``root``, each source's directory relative to
    ``root`` is recreated under ``outdir``. Sources that would still share an
    output (``x.png`` and ``x.webp``) get ``-2``, ``-3``, ... appended to the
    stem, so no file in a run is written twice.
    """
    claimed: set[str] = set()
    targets: list[Path] = []
    for source in sources:
        directory = outdir
        if outdir is not None and root is not None:
            try:
                directory = outdir / source.parent.relative_to(root)
            except ValueError:
                directory = outdir

        target = target_for(source, target_extension, outdir=directory)
        if _path_key(target) == _path_key(source):
            # Converting a file onto itself is skipped later; it must not push
            # another source onto a suffixed name.
            targets.append(target)
            continue

        candidate = target
        counter = 2
        while _path_key(candidate) in claimed:
            candidate = target.with_name(f"{target.stem}-{counter}{target.suffix}")
            counter += 1
        claimed.add(_path_key(candidate))
        targets.append(candidate)

    return targets


def find_sources(
    directory: Path,
    *,
    extensions: set[str] | frozenset[str] | None = None,
    pattern: str | re.Pattern[str] | None = None,
    recursive: bool = False,
) -> list[Path]:
    """Collect candidate files under ``directory``.

    ``pattern`` is a regular expression searched against each file's path
    relative to ``directory`` (just the filename when not recursing).
    """
    regex = re.compile(pattern) if pattern is not None else None
    allowed = {normalize_extension(item) for item in extensions} if extensions else None

    candidates = directory.rglob("*") if recursive else directory.glob("*")
    matches: list[Path] = []
    for path in sorted(candidates):
        if not path.is_file():
            continue
        if allowed is not None and normalize_extension(path.suffix) not in allowed:
            continue
        if regex is not None and regex.search(path.relative_to(directory).as_posix()) is None:
            continue
        matches.append(path)

    return matches


def _sniff_format(path: Path) -> str | None:
    """Name the format a file's first bytes declare, or ``None`` if unsure."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return None

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head[:6] in {b"GIF87a", b"GIF89a"}:
        return "GIF"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    if head[:4] in {b"II*\x00", b"MM\x00*"}:
        return "TIFF"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in {b"avif", b"avis"}:
            return "AVIF"
        if brand in _HEIF_BRANDS:
            return "HEIF"
        return None  # `mif1` and the like are used by both families
    if head[:4] == b"\x00\x00\x01\x00":
        return "ICO"
    if head[:2] == b"BM":
        return "BMP"
    if head[:1] == b"P" and head[1:2] in b"123456" and head[2:3] in b" \t\r\n":
        return "PPM"
    return None


def _wrong_format(produced: Path, target: Path) -> str | None:
    """Explain an output whose content is another format than its name says.

    An ImageMagick build without an encoder for a format (HEIC on Windows)
    warns, exits 0 and writes the image in another format under that name.
    """
    extension = normalize_extension(target.suffix)
    expected = _SIGNATURE_FAMILIES.get(extension)
    found = _sniff_format(produced)
    if expected is None or found is None or found == expected:
        return None
    return (
        f"magick wrote {found} data instead of .{extension}; "
        "this ImageMagick build cannot write that format"
    )


def _convert_with_pillow(
    source: Path, target: Path, *, quality: int | None, overwrite: bool, dry_run: bool
) -> ConversionResult:
    if source.resolve() == target.resolve():
        return ConversionResult(source, target, (), "skipped", "source is the target")

    if target.exists() and not overwrite:
        return ConversionResult(source, target, (), "skipped", "target exists (use --overwrite)")

    if dry_run:
        return ConversionResult(source, target, (), "planned")

    # Imported here so that planning and the ImageMagick path never pay for
    # Pillow.
    from jzr_tools.core import pillow_engine

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        pillow_engine.convert(source, target, quality=quality)
    except (pillow_engine.ConversionError, OSError) as error:
        return ConversionResult(source, target, (), "failed", str(error))

    return ConversionResult(source, target, (), "converted")


def convert_file(
    source: Path,
    target: Path,
    *,
    backend: str,
    executable: list[str],
    quality: int | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> ConversionResult:
    """Convert one file, returning what happened instead of raising."""
    if backend == "pillow":
        return _convert_with_pillow(
            source, target, quality=quality, overwrite=overwrite, dry_run=dry_run
        )

    # ImageMagick expands `%d` escapes in an output path and has no escape for
    # them that works across versions. Such a target is written under a
    # `%`-free name from inside its own directory, then renamed.
    staged = "%" in str(target)
    if staged:
        work_dir = target.parent
        placeholder = Path(f"jzr-tmp-{secrets.token_hex(8)}{target.suffix}")
        program = executable[0]
        if os.path.dirname(program):
            program = os.path.abspath(program)
        command = tuple(
            build_command(
                backend,
                [program, *executable[1:]],
                # Absolute, because the backend runs in the target directory.
                Path(os.path.abspath(source)),
                placeholder,
                quality=quality,
                overwrite=overwrite,
            )
        )
        produced = work_dir / placeholder
    else:
        work_dir = None
        command = tuple(
            build_command(
                backend,
                executable,
                source,
                target,
                quality=quality,
                overwrite=overwrite,
            )
        )
        produced = target

    if source.resolve() == target.resolve():
        return ConversionResult(source, target, command, "skipped", "source is the target")

    if target.exists() and not overwrite:
        return ConversionResult(
            source, target, command, "skipped", "target exists (use --overwrite)"
        )

    if dry_run:
        detail = ""
        if staged:
            detail = (
                f"{backend} runs in {work_dir} and writes {placeholder}, "
                f"which is then renamed to {target.name}"
            )
        return ConversionResult(source, target, command, "planned", detail)

    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        list(command), capture_output=True, text=True, check=False, cwd=work_dir
    )
    if completed.returncode != 0:
        if staged:
            produced.unlink(missing_ok=True)
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        return ConversionResult(
            source,
            target,
            command,
            "failed",
            detail[-1] if detail else f"{backend} exited with {completed.returncode}",
        )

    if not produced.exists():
        return ConversionResult(
            source,
            target,
            command,
            "failed",
            f"{backend} reported success but did not write {target.name}",
        )

    wrong_format = _wrong_format(produced, target)
    if wrong_format is not None:
        produced.unlink(missing_ok=True)
        return ConversionResult(source, target, command, "failed", wrong_format)

    if staged:
        try:
            os.replace(produced, target)
        except OSError as error:
            produced.unlink(missing_ok=True)
            return ConversionResult(
                source, target, command, "failed", f"could not write {target.name}: {error}"
            )

    return ConversionResult(source, target, command, "converted")
