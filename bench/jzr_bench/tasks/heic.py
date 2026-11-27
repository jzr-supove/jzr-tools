"""Convert HEIC photos to WebP at most 1600 px on the longest side."""

from __future__ import annotations

from pathlib import Path

from ._pictures import photo_like
from .base import CheckResult, Task, sources_unchanged

PROMPT = "Convert the HEIC photos in this folder to WebP, at most 1600 pixels on the longest side. Keep the originals."
STEMS = ("IMG_0001", "IMG_0002", "IMG_0003")
SOURCES = [f"{stem}.HEIC" for stem in STEMS]
SIZE = (4032, 3024)
LONG, SHORT, SHORT_TOLERANCE = 1600, 1200, 2
THUMB = (32, 24)
CONTENT_LIMIT = 8.0  # mean absolute difference at THUMB; a correct WebP is about 1


def _heif():
    import pillow_heif

    pillow_heif.register_heif_opener()


def _prepare(fixture_dir: Path) -> None:
    _heif()
    for seed, name in enumerate(SOURCES, 1):
        photo_like(SIZE, seed, noise=4).save(fixture_dir / name, format="HEIF", quality=80)


def _thumb(image):
    from PIL import Image

    return image.convert("RGB").resize(THUMB, Image.Resampling.BOX)


def _distance(a, b) -> float:
    from PIL import ImageChops, ImageStat

    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def _find_outputs(work_dir: Path, stem: str) -> list[Path]:
    """WebP files named after the source: IMG_0001.webp, or IMG_0001_1600x1200.webp and the like."""
    found = []
    for path in sorted(work_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".webp":
            continue
        if path.stem == stem or (path.stem.startswith(stem) and path.stem[len(stem)] in "_-. "):
            found.append(path)
    found.sort(key=lambda p: p.stem != stem)  # the exact name first
    return found


def _judge(output: Path, stem: str, references: dict) -> str | None:
    """None when this output is right for its source, else what is wrong with it."""
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(output)
    except (UnidentifiedImageError, OSError) as error:
        return f"{output.name} is not a valid image: {error}"
    with image:
        if image.format != "WEBP":
            return f"{output.name} is not a WebP but {image.format}"
        width, height = image.size
        if max(width, height) != LONG:
            return f"{output.name} is {width}x{height}: its longest side must be {LONG}"
        if width < height:
            return f"{output.name} is {width}x{height}: rotated, the source is landscape"
        if abs(height - SHORT) > SHORT_TOLERANCE:
            return (
                f"{output.name} is {width}x{height}: the other side must be {SHORT} ± {SHORT_TOLERANCE} "
                "(aspect ratio kept)"
            )
        thumb = _thumb(image)
    distances = {other: _distance(thumb, ref) for other, ref in references.items()}
    closest = min(distances, key=distances.__getitem__)
    if closest != stem:
        return f"{output.name} looks like {closest}.HEIC, not its own source"
    if distances[stem] > CONTENT_LIMIT:
        return f"{output.name} does not match its source (difference {distances[stem]:.1f})"
    return None


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    from PIL import Image

    _heif()
    problems = sources_unchanged(work_dir, fixture_dir, SOURCES)
    notes: list[str] = []
    references = {}
    for stem, name in zip(STEMS, SOURCES, strict=True):
        with Image.open(fixture_dir / name) as source:
            references[stem] = _thumb(source)

    for stem in STEMS:
        outputs = _find_outputs(work_dir, stem)
        if not outputs:
            problems.append(f"{stem}.webp is missing (no WebP named after {stem})")
            continue
        verdicts = [(output, _judge(output, stem, references)) for output in outputs]
        good = [output for output, problem in verdicts if problem is None]
        if good:
            notes.append(f"{good[0].name} ok")
        else:
            problems.append(verdicts[0][1])
    if problems:
        return CheckResult(False, problems)
    return CheckResult(True, notes)


TASK = Task(name="heic", prompt=PROMPT, prepare_fixtures=_prepare, check=_check)
