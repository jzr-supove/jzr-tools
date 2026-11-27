"""Rename six photos to a numbered pattern, oldest first by modification time (held out)."""

from __future__ import annotations

import os
from pathlib import Path

from ._pictures import photo_like
from .base import CheckResult, Task, sha256

PROMPT = "Rename the six photos in this folder to trip-001.jpg up to trip-006.jpg, oldest first by modification time."
LISTED = ("IMG 2024 (1).JPG", "DSC_0042.jpeg", "photo-b.jpg", "Scan 3.JPEG", "holiday.jpg", "z_last.Jpg")
# oldest first: neither the listed nor the alphabetical order, with or without case
OLDEST_FIRST = ("photo-b.jpg", "z_last.Jpg", "IMG 2024 (1).JPG", "holiday.jpg", "DSC_0042.jpeg", "Scan 3.JPEG")
TRIPS = tuple(f"trip-{n:03}.jpg" for n in range(1, len(LISTED) + 1))
SIZE = (320, 240)
FIRST_MTIME = 1_717_236_000  # 2024-06-01 10:00 UTC
STEP_S = 3600
IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".jpe", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif"}
)


def _prepare(fixture_dir: Path) -> None:
    for seed, name in enumerate(LISTED, 21):
        photo_like(SIZE, seed, noise=4).save(fixture_dir / name, format="JPEG", quality=88)
    for place, name in enumerate(OLDEST_FIRST):
        when = FIRST_MTIME + place * STEP_S
        os.utime(fixture_dir / name, (when, when))


def _is_image(path: Path, fixture_hashes: set[str]) -> bool:
    """A photo by its extension, or a file with the bytes of one of the photos whatever its name."""
    return path.suffix.lower() in IMAGE_SUFFIXES or sha256(path) in fixture_hashes


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    order = sorted(LISTED, key=lambda name: (fixture_dir / name).stat().st_mtime_ns)
    hashes = {name: sha256(fixture_dir / name) for name in LISTED}
    by_hash = {digest: name for name, digest in hashes.items()}
    images = sorted(p.name for p in work_dir.iterdir() if p.is_file() and _is_image(p, set(hashes.values())))

    problems = []
    for place, (trip, want) in enumerate(zip(TRIPS, order, strict=True), 1):
        path = work_dir / trip
        if not path.is_file():
            problems.append(f"{trip} is missing: it should be {want}, the photo number {place} by modification time")
            continue
        digest = sha256(path)
        if digest == hashes[want]:
            continue
        if digest in by_hash:
            had = by_hash[digest]
            problems.append(
                f"{trip} holds {had}, which is number {order.index(had) + 1} by modification time; "
                f"it should hold {want} (wrong order, oldest first by mtime)"
            )
        else:
            problems.append(
                f"{trip} has bytes that are not those of any photo: its content changed, it should be {want}"
            )
    extra = [name for name in images if name not in TRIPS]
    if extra:
        problems.append(
            f"{len(images)} image files in the folder, not 6: unexpected extra or leftover old names: {', '.join(extra)}"
        )
    if problems:
        return CheckResult(False, problems)
    return CheckResult(True, [f"{TRIPS[0]} to {TRIPS[-1]} hold the photos oldest first"])


TASK = Task(name="rename", prompt=PROMPT, prepare_fixtures=_prepare, check=_check)
