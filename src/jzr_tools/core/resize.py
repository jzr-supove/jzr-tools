"""Resizing images: the size asked for, the size that results, and the names.

The arithmetic and the names need no Pillow, so they are tested and planned
without it; ``resize_file`` imports the engine only when it writes a file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Sequence

from jzr_tools.core.batch import ConversionResult, _path_key

# Stems of earlier results (`photo_1600x1200`), which a directory scan skips.
RESIZED_STEM = re.compile(r"_\d+x\d+$")

_ONE_SIZE = "give one size: --width and/or --height, --percent or --max-side"


@dataclass(frozen=True)
class SizeRequest:
    """The size options of one run."""

    width: int | None = None
    height: int | None = None
    percent: float | None = None
    max_side: int | None = None
    upscale: bool = False


@dataclass(frozen=True)
class SizePlan:
    """The pixel size one image is resized to."""

    width: int
    height: int
    enlarged: bool = False


def check_request(request: SizeRequest) -> None:
    """Raise ``ValueError`` unless exactly one kind of size is given."""
    kinds = [
        request.width is not None or request.height is not None,
        request.percent is not None,
        request.max_side is not None,
    ]
    if sum(kinds) != 1:
        raise ValueError(_ONE_SIZE)


def _side(value: float) -> int:
    return max(1, round(value))


def _fit(width: int, height: int, box_width: int, box_height: int) -> tuple[int, int]:
    """The largest size with the same proportions that fits inside the box."""
    if box_width / width <= box_height / height:
        return box_width, _side(height * box_width / width)
    return _side(width * box_height / height), box_height


def plan_size(width: int, height: int, request: SizeRequest) -> SizePlan | str:
    """Plan the new size of an upright ``width`` x ``height`` image.

    Returns the plan, or the reason the image is left alone.
    """
    if request.percent is not None:
        size = (_side(width * request.percent / 100), _side(height * request.percent / 100))
    elif request.max_side is not None:
        size = _fit(width, height, request.max_side, request.max_side)
    elif request.width is not None and request.height is not None:
        size = _fit(width, height, request.width, request.height)
    elif request.width is not None:
        size = (request.width, _side(height * request.width / width))
    elif request.height is not None:
        size = (_side(width * request.height / height), request.height)
    else:
        raise ValueError(_ONE_SIZE)

    if size == (width, height):
        return f"already {width}x{height}"
    enlarged = size[0] > width or size[1] > height
    if enlarged and not request.upscale:
        return f"already {width}x{height}; not enlarged (add --upscale)"
    return SizePlan(size[0], size[1], enlarged)


def resized_name(source: Path, width: int, height: int, extension: str) -> str:
    """``<stem>_<W>x<H>.<extension>``, with the extension as given."""
    return f"{source.stem}_{width}x{height}.{extension}"


def resize_targets(
    items: Sequence[tuple[Path, SizePlan]],
    *,
    extension: str | None,
    outdir: Path | None,
    root: Path | None,
) -> list[Path]:
    """One distinct output path per planned source, in order.

    ``extension`` replaces each source's own one (kept as written when
    ``None``). With both ``outdir`` and ``root``, each source's directory
    relative to ``root`` is recreated under ``outdir``. A name already taken
    in this run gets ``-2``, ``-3``, ... after the stem.
    """
    claimed: set[str] = set()
    targets: list[Path] = []
    for source, size in items:
        directory = outdir if outdir is not None else source.parent
        if outdir is not None and root is not None:
            try:
                directory = outdir / source.parent.relative_to(root)
            except ValueError:
                directory = outdir
        suffix = extension if extension is not None else source.suffix.lstrip(".")
        target = directory / resized_name(source, size.width, size.height, suffix)
        candidate = target
        counter = 2
        while _path_key(candidate) in claimed:
            candidate = target.with_name(f"{target.stem}-{counter}{target.suffix}")
            counter += 1
        claimed.add(_path_key(candidate))
        targets.append(candidate)
    return targets


def resize_file(
    source: Path,
    target: Path,
    plan: SizePlan,
    *,
    quality: int | None,
    background: tuple[int, int, int] | None,
    strip: bool,
    overwrite: bool,
    dry_run: bool,
) -> ConversionResult:
    """Resize one file to ``plan``, returning what happened instead of raising."""

    def done(status: str, detail: str = "") -> ConversionResult:
        return ConversionResult(source, target, (), status, detail)

    if _path_key(source) == _path_key(target):
        return done("skipped", "source is the target")
    if target.exists() and not overwrite:
        return done("skipped", "target exists (use --overwrite)")
    if dry_run:
        return done("planned")

    from jzr_tools.core import pillow_engine

    try:
        width, height, _ = pillow_engine.probe(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        pillow_engine.convert(
            source,
            target,
            quality=quality,
            background=background,
            size=(plan.width, plan.height),
            strip=strip,
        )
    except (pillow_engine.ConversionError, OSError) as error:
        return done("failed", str(error))

    detail = f"{width}x{height} -> {plan.width}x{plan.height}"
    if plan.enlarged:
        detail += ", enlarged; may look soft"
    return done("resized", detail)
