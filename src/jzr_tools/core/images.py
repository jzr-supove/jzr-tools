"""Plain Python image conversion helpers.

Conversions are delegated to an external backend: ImageMagick (``magick``) or
``ffmpeg``. Nothing here touches Typer, so the logic stays testable on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
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

# Targets that can hold more than one frame, so ffmpeg must not be told to stop
# after the first one.
ANIMATED_EXTENSIONS = frozenset({"apng", "avif", "gif", "webp"})

# Spellings of the same format, so `jzr convert jpeg2png` also picks up `.jpg`.
_EXTENSION_ALIASES = (
    frozenset({"jpg", "jpeg"}),
    frozenset({"tif", "tiff"}),
    frozenset({"heic", "heif"}),
)

BACKEND_PREFERENCE = ("magick", "ffmpeg")

# Formats a backend has no encoder or muxer for, so it fails on every file.
_UNWRITABLE_EXTENSIONS = {"ffmpeg": frozenset({"heic", "heif"})}

# Sources ffmpeg opens with its image2 demuxer, which reads `%` in a path as a
# numbered-sequence pattern unless told otherwise.
_FFMPEG_IMAGE2_EXTENSIONS = frozenset(
    {"bmp", "jpeg", "jpg", "png", "ppm", "tga", "tif", "tiff", "webp"}
)


class BackendUnavailableError(RuntimeError):
    """Raised when no usable conversion backend is on PATH."""


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
    """Return the backends available on PATH, mapped to their argv prefix."""
    found: dict[str, list[str]] = {}

    magick = shutil.which("magick")
    if magick is None and os.name != "nt":
        # ImageMagick 6 only ships `convert`; on Windows that name collides
        # with the filesystem conversion tool, so it is only trusted elsewhere.
        magick = shutil.which("convert")
    if magick is not None:
        found["magick"] = [magick]

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is not None:
        found["ffmpeg"] = [ffmpeg]

    return found


def resolve_backend(preference: str = "auto") -> tuple[str, list[str]]:
    """Pick a backend, honoring an explicit preference when given."""
    available = detect_backends()

    if preference != "auto":
        executable = available.get(preference)
        if executable is None:
            raise BackendUnavailableError(f"Backend not found on PATH: {preference}")
        return preference, executable

    for name in BACKEND_PREFERENCE:
        if name in available:
            return name, available[name]

    raise BackendUnavailableError(
        "No conversion backend found. Install ImageMagick (magick) or ffmpeg."
    )


def unsupported_target(backend: str, target_extension: str) -> str | None:
    """Explain why ``backend`` cannot write this format, or return ``None``."""
    extension = normalize_extension(target_extension)
    if extension not in _UNWRITABLE_EXTENSIONS.get(backend, frozenset()):
        return None
    return (
        f"{backend} cannot write .{extension} files. "
        "Use ImageMagick (--backend magick) or choose another format."
    )


def quality_caveat(backend: str, target_extension: str) -> str | None:
    """Explain how ``--quality`` behaves for this target, if it is surprising.

    Returns ``None`` for lossy targets, where quality means what users expect.
    """
    extension = normalize_extension(target_extension)
    if extension in LOSSY_EXTENSIONS:
        return None

    # ImageMagick reuses -quality for the zlib level of these lossless formats,
    # which is not the visual-quality knob it is for JPEG.
    if backend == "magick" and extension in {"png", "tif", "tiff"}:
        return (
            f".{extension} is lossless; --quality only tunes compression, "
            "not image fidelity"
        )

    return f"{backend} ignores --quality for .{extension} output"


def _scale_quality_to_qv(quality: int) -> int:
    """Map a 1-100 quality onto ffmpeg's inverted 2-31 mjpeg scale."""
    return 2 + round((100 - quality) * 29 / 99)


def _ffmpeg_quality_args(target_extension: str, quality: int | None) -> list[str]:
    if quality is None:
        return []

    extension = normalize_extension(target_extension)
    if extension in {"jpg", "jpeg"}:
        return ["-q:v", str(_scale_quality_to_qv(quality))]
    if extension == "webp":
        return ["-quality", str(quality)]
    if extension in {"avif", "heic", "heif"}:
        return ["-crf", str(round((100 - quality) * 63 / 99))]
    return []


def build_command(
    backend: str,
    executable: list[str],
    source: Path,
    target: Path,
    *,
    quality: int | None = None,
    overwrite: bool = True,
) -> list[str]:
    """Build the argv used to convert ``source`` into ``target``."""
    target_extension = normalize_extension(target.suffix)
    single_frame = target_extension not in ANIMATED_EXTENSIONS

    if backend == "magick":
        source_argument = str(source)
        if single_frame:
            # Without a frame selector a multi-frame source (GIF, ICO, TIFF)
            # is written as `name-0.ext`, `name-1.ext`, ... instead of the
            # target. A path carrying a selector has its `%` escapes expanded,
            # so literal percent signs must be doubled.
            source_argument = source_argument.replace("%", "%%") + "[0]"
        command = [*executable, source_argument]
        if quality is not None:
            command += ["-quality", str(quality)]
        # Output names always have `%` escapes expanded (`%d` is the frame
        # number), so a literal percent sign is doubled here too.
        command.append(str(target).replace("%", "%%"))
        return command

    if backend == "ffmpeg":
        command = [
            *executable,
            "-nostdin",
            "-loglevel",
            "error",
            "-y" if overwrite else "-n",
        ]
        if (
            "%" in str(source)
            and normalize_extension(source.suffix) in _FFMPEG_IMAGE2_EXTENSIONS
        ):
            command += ["-pattern_type", "none"]
        command += ["-i", str(source)]
        command += _ffmpeg_quality_args(target_extension, quality)
        if single_frame:
            # `-update 1` also makes the image2 muxer take the name literally.
            command += ["-frames:v", "1", "-update", "1"]
        elif target_extension == "webp" and "%" in str(target):
            # A `%d`-style name would otherwise select the image-sequence muxer.
            command += ["-f", "webp"]
        command.append(str(target))
        return command

    raise ValueError(f"Unknown backend: {backend}")


@dataclass(frozen=True)
class ConversionResult:
    """Outcome of a single file conversion."""

    source: Path
    target: Path
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

    if source.resolve() == target.resolve():
        return ConversionResult(source, target, command, "skipped", "source is the target")

    if target.exists() and not overwrite:
        return ConversionResult(
            source, target, command, "skipped", "target exists (use --overwrite)"
        )

    if dry_run:
        return ConversionResult(source, target, command, "planned")

    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(list(command), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        return ConversionResult(
            source,
            target,
            command,
            "failed",
            detail[-1] if detail else f"{backend} exited with {completed.returncode}",
        )

    if not target.exists():
        return ConversionResult(
            source,
            target,
            command,
            "failed",
            f"{backend} reported success but did not write {target.name}",
        )

    return ConversionResult(source, target, command, "converted")
