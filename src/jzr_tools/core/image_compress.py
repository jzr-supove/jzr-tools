"""Compressing images: formats, names, the search for a file size, one file.

The names, the arithmetic and the size search need no Pillow, so they are
tested and planned without it. The search works on any ``encode`` function;
``compress_file`` hands it the Pillow engine's, and imports the engine only
when it reads a file.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Callable, Sequence

from jzr_tools.core.batch import ConversionResult, _path_key
from jzr_tools.core.media import format_size

COMPRESSIBLE = frozenset({"jpg", "jpeg", "png", "webp", "avif", "heic", "heif", "tif", "tiff"})
TARGETS = ("jpg", "png", "webp", "avif", "heic")
# Default quality of each lossy format; the formats not listed are lossless.
DEFAULT_QUALITY = {"jpg": 82, "jpeg": 82, "webp": 80, "avif": 60, "heic": 50, "heif": 50}

SEARCH_HIGH = 92
SEARCH_LOW = 40
SEARCH_ENCODES = 8
SHRINK_ROUNDS = 3
SHRINK_MARGIN = 0.85
MIN_SIDE = 32

# Stems of earlier results (`photo_compressed`), which a directory scan skips.
COMPRESSED_STEM = "_compressed"

_SAME = {"jpeg": "jpg", "tiff": "tif", "heif": "heic"}

Encode = Callable[[int | None, tuple[int, int]], bytes]


@dataclass(frozen=True)
class Attempt:
    """One encode: its quality (``None`` when lossless), size and bytes."""

    quality: int | None
    width: int
    height: int
    data: bytes


def _canonical(extension: str) -> str:
    extension = extension.lstrip(".").lower()
    return _SAME.get(extension, extension)


def same_format(a: str, b: str) -> bool:
    """Whether two extensions name one format (jpg/jpeg, tif/tiff, heic/heif)."""
    return _canonical(a) == _canonical(b)


def is_lossy(extension: str) -> bool:
    return extension.lstrip(".").lower() in DEFAULT_QUALITY


def not_compressible(extension: str) -> str | None:
    """Why files with this extension are not compressed in their own format."""
    extension = extension.lstrip(".").lower()
    if extension in COMPRESSIBLE:
        return None
    return f".{extension} is not compressed; add --to webp (or jpg, png)"


def compressed_name(source: Path, extension: str) -> str:
    """``<stem>_compressed.<extension>``, with the extension as given."""
    return f"{source.stem}{COMPRESSED_STEM}.{extension}"


def compress_targets(
    sources: Sequence[Path],
    *,
    extension: str | None,
    outdir: Path | None,
    root: Path | None,
) -> list[Path]:
    """One distinct output path per source, in order.

    ``extension`` replaces each source's own one (kept as written when
    ``None``). With both ``outdir`` and ``root``, each source's directory
    relative to ``root`` is recreated under ``outdir``. A name already taken
    in this run gets ``-2``, ``-3``, ... after the stem.
    """
    claimed: set[str] = set()
    targets: list[Path] = []
    for source in sources:
        directory = outdir if outdir is not None else source.parent
        if outdir is not None and root is not None:
            try:
                directory = outdir / source.parent.relative_to(root)
            except ValueError:
                directory = outdir
        suffix = extension if extension is not None else source.suffix.lstrip(".")
        target = directory / compressed_name(source, suffix)
        candidate = target
        counter = 2
        while _path_key(candidate) in claimed:
            candidate = target.with_name(f"{target.stem}-{counter}{target.suffix}")
            counter += 1
        claimed.add(_path_key(candidate))
        targets.append(candidate)
    return targets


def _side(value: float) -> int:
    return max(1, round(value))


def max_side_size(width: int, height: int, max_side: int) -> tuple[int, int] | None:
    """The size that fits inside ``max_side`` square, or ``None`` if it already does."""
    if max(width, height) <= max_side:
        return None
    if width >= height:
        return max_side, _side(height * max_side / width)
    return _side(width * max_side / height), max_side


def shrunk_size(width: int, height: int, limit: int, smallest: int) -> tuple[int, int] | None:
    """The next, smaller size to try when ``smallest`` bytes is over ``limit``.

    The sides are scaled by the square root of the size ratio, with a margin.
    The shorter side stays at least ``MIN_SIDE``. ``None`` when no smaller
    size is left to try.
    """
    if min(width, height) < MIN_SIDE:
        return None
    factor = math.sqrt(limit / smallest) * SHRINK_MARGIN
    new_width, new_height = _side(width * factor), _side(height * factor)
    if min(new_width, new_height) < MIN_SIDE:
        floor = MIN_SIDE / min(width, height)
        new_width, new_height = _side(width * floor), _side(height * floor)
        if width <= height:
            new_width = MIN_SIDE
        else:
            new_height = MIN_SIDE
    if max(new_width, new_height) >= max(width, height):
        return None
    return new_width, new_height


def search_quality(encode: Encode, size: tuple[int, int], limit: int) -> tuple[Attempt, bool]:
    """The highest quality from 92 to 40 whose encode at ``size`` fits ``limit``.

    Returns that attempt and ``True``, or the attempt at 40 and ``False``.
    """

    def attempt(quality: int) -> Attempt:
        return Attempt(quality, size[0], size[1], encode(quality, size))

    top = attempt(SEARCH_HIGH)
    if len(top.data) <= limit:
        return top, True
    best = attempt(SEARCH_LOW)
    if len(best.data) > limit:
        return best, False

    fits, misses, encodes = SEARCH_LOW, SEARCH_HIGH, 2
    while misses - fits > 1 and encodes < SEARCH_ENCODES:
        middle = (fits + misses) // 2
        trial = attempt(middle)
        encodes += 1
        if len(trial.data) <= limit:
            fits, best = middle, trial
        else:
            misses = middle
    return best, True


def fit_to_size(
    encode: Encode, size: tuple[int, int], limit: int, *, lossy: bool
) -> tuple[Attempt, bool]:
    """Find an encode within ``limit`` bytes, lowering quality, then the size.

    Returns the attempt that fits and ``True``, or the smallest attempt made
    and ``False``.
    """

    def one_round(at: tuple[int, int]) -> tuple[Attempt, bool]:
        if lossy:
            return search_quality(encode, at, limit)
        data = encode(None, at)
        return Attempt(None, at[0], at[1], data), len(data) <= limit

    attempt, fits = one_round(size)
    smallest = attempt
    current = size
    for _ in range(SHRINK_ROUNDS):
        if fits:
            return attempt, True
        smaller = shrunk_size(current[0], current[1], limit, len(smallest.data))
        if smaller is None:
            break
        current = smaller
        attempt, fits = one_round(current)
        if len(attempt.data) < len(smallest.data):
            smallest = attempt
    if fits:
        return attempt, True
    return smallest, False


def change_text(before: int, after: int) -> str:
    """``-74%``, ``+12%`` or ``same size``; never ``-0%`` for a real change."""
    if before == after:
        return "same size"
    change = max(1, round(abs(after - before) * 100 / before))
    return f"-{change}%" if after < before else f"+{change}%"


def compress_file(
    source: Path,
    target: Path,
    *,
    quality: int | None,
    size_limit: int | None,
    max_side: int | None,
    background: tuple[int, int, int] | None,
    strip: bool,
    overwrite: bool,
    dry_run: bool,
) -> ConversionResult:
    """Compress one file into ``target``, returning what happened instead of raising.

    The target's extension sets the format. A copy in the source's own format
    is kept only when it is smaller.
    """

    def done(status: str, detail: str = "") -> ConversionResult:
        return ConversionResult(source, target, (), status, detail)

    if _path_key(source) == _path_key(target):
        return done("skipped", "source is the target")
    if target.exists() and not overwrite:
        return done("skipped", "target exists (use --overwrite)")
    if dry_run:
        return done("planned")

    from jzr_tools.core import pillow_engine

    source_extension = source.suffix.lstrip(".").lower()
    target_extension = target.suffix.lstrip(".").lower() or source_extension
    changed = not same_format(source_extension, target_extension)
    lossy = is_lossy(target_extension)

    try:
        width, height, frames = pillow_engine.probe(source)
        if frames > 1:
            return done("failed", pillow_engine.NOT_COMPRESSED_ANIMATED)
        limited = max_side_size(width, height, max_side) if max_side is not None else None
        base = limited or (width, height)
        source_size = source.stat().st_size

        if size_limit is not None and not changed and limited is None and source_size <= size_limit:
            return done("skipped", f"already under {format_size(size_limit)}")

        prepared = pillow_engine.prepare(
            source, target_extension, background=background, strip=strip
        )
        fitted = False
        if size_limit is None:
            setting = (quality if quality is not None else DEFAULT_QUALITY[target_extension]) if lossy else None
            result = Attempt(setting, base[0], base[1], prepared.encode(setting, base))
        else:
            result, fits = fit_to_size(prepared.encode, base, size_limit, lossy=lossy)
            if not fits:
                return done(
                    "failed",
                    f"could not get under {format_size(size_limit)}; the smallest attempt "
                    f"was {format_size(len(result.data))} at {result.width}x{result.height}",
                )
            fitted = (result.width, result.height) != base

        size = len(result.data)
        if not changed and size >= source_size:
            return done(
                "skipped",
                f"already small (compressed copy was {format_size(size)}, "
                f"source is {format_size(source_size)})",
            )
        pillow_engine.write_bytes(target, result.data)
    except pillow_engine.ConversionError as error:
        return done("failed", str(error))
    except OSError as error:
        return done("failed", error.strerror or str(error))

    parts = [f"{format_size(source_size)} -> {format_size(size)}", change_text(source_size, size)]
    if size_limit is not None and lossy:
        parts.append(f"quality {result.quality}")
    if (result.width, result.height) != (width, height):
        parts.append(f"resized to {result.width}x{result.height}" + (" to fit" if fitted else ""))
    if changed and size > source_size:
        parts.append("larger than the source")
    return done("compressed", ", ".join(parts))
