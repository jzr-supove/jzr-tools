"""Compress a photo under a size limit without resizing it (held out)."""

from __future__ import annotations

from pathlib import Path

from ._pictures import photo_like
from .base import CheckResult, Task, sources_unchanged

PROMPT = "Make a copy of photo.jpg that is under 500 kB, same dimensions, still a JPEG. Save it as photo_small.jpg."
SIZE = (4000, 3000)
LIMIT = 500_000
THUMB = (80, 60)
CONTENT_LIMIT = 10.0  # mean absolute difference at THUMB; quality 30 is under 6, a wrong picture over 15


def _prepare(fixture_dir: Path) -> None:
    photo_like(SIZE, 11, noise=6).save(fixture_dir / "photo.jpg", format="JPEG", quality=92)


def _thumb(image):
    from PIL import Image

    return image.convert("RGB").resize(THUMB, Image.Resampling.BOX)


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError

    problems = sources_unchanged(work_dir, fixture_dir, ["photo.jpg"])
    small = work_dir / "photo_small.jpg"
    if not small.is_file():
        problems.append("photo_small.jpg does not exist")
        return CheckResult(False, problems)
    try:
        image = Image.open(small)
    except (UnidentifiedImageError, OSError) as error:
        problems.append(f"photo_small.jpg is not a valid image (empty or unreadable): {error}")
        return CheckResult(False, problems)
    with image:
        if image.format != "JPEG":
            problems.append(f"photo_small.jpg is not a JPEG but {image.format}")
            return CheckResult(False, problems)
        size = small.stat().st_size
        if size >= LIMIT:
            problems.append(f"photo_small.jpg is {size:,} bytes, must be under {LIMIT:,} (500 kB)")
        if image.size != SIZE:
            problems.append(f"photo_small.jpg is {image.size[0]}x{image.size[1]}, dimensions must stay 4000x3000")
            return CheckResult(False, problems)
        thumb = _thumb(image)
    with Image.open(fixture_dir / "photo.jpg") as source:
        reference = _thumb(source)
    difference = sum(ImageStat.Stat(ImageChops.difference(thumb, reference)).mean) / 3
    if difference > CONTENT_LIMIT:
        problems.append(f"photo_small.jpg does not look like photo.jpg (difference {difference:.1f})")
    if problems:
        return CheckResult(False, problems)
    return CheckResult(True, [f"{size:,} bytes, difference {difference:.1f}"])


TASK = Task(name="compress", prompt=PROMPT, prepare_fixtures=_prepare, check=_check)
