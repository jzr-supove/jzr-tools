"""Convert a tree of pictures in five formats to JPEGs in another folder (held out)."""

from __future__ import annotations

from pathlib import Path

from ._pictures import photo_like
from .base import CheckResult, Task, sources_unchanged

PROMPT = (
    "Convert every image under photos/ (subfolders too) to JPEG and put the JPEGs in out/. "
    "Two of them have the same name: keep both. Leave photos/ as it is."
)
# source -> the format it is saved in
SOURCES = {
    "a.png": "PNG",
    "b.heic": "HEIF",
    "c.tiff": "TIFF",
    "d.bmp": "BMP",
    "sub/a.png": "PNG",
    "sub/e.png": "PNG",
    "sub/deep/f.webp": "WEBP",
}
SIZE = (800, 600)
THUMB = (32, 24)
MATCH_LIMIT = 8.0  # mean absolute difference at THUMB; a JPEG of its source is under 4, two sources over 16
JPEG_SUFFIXES = (".jpg", ".jpeg")


def _heif() -> None:
    import pillow_heif

    pillow_heif.register_heif_opener()


def _prepare(fixture_dir: Path) -> None:
    _heif()
    photos = fixture_dir / "photos"
    for seed, (rel, kind) in enumerate(SOURCES.items(), 31):
        path = photos / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        options = {"quality": 90} if kind in ("HEIF", "WEBP") else {}
        photo_like(SIZE, seed, noise=4).save(path, format=kind, **options)


def _thumb(image):
    from PIL import Image

    return image.convert("RGB").resize(THUMB, Image.Resampling.BOX)


def _distance(a, b) -> float:
    from PIL import ImageChops, ImageStat

    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def _relative_files(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} if folder.is_dir() else set()


def _photos_problems(work_dir: Path, fixture_dir: Path) -> list[str]:
    """photos/ must be exactly as it was: every source unchanged, nothing added."""
    problems = sources_unchanged(work_dir, fixture_dir, [f"photos/{rel}" for rel in SOURCES])
    photos = work_dir / "photos"
    added = sorted(_relative_files(photos) - _relative_files(fixture_dir / "photos"))
    for rel in added:
        problems.append(f"photos/{rel} was added: photos/ must be left as it is")
    return problems


def _read_jpeg(path: Path, name: str) -> tuple[object | None, str | None]:
    """(thumbnail, problem): the thumbnail when the file decodes, the problem when it is not a right JPEG."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            image.load()
            kind, size = image.format, image.size
            thumb = _thumb(image)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        return None, f"{name} is not a valid image that can be read (not a JPEG): {error}"
    if kind != "JPEG":
        return thumb, f"{name} is not a JPEG but {kind}"
    if size != SIZE:
        return thumb, f"{name} is {size[0]}x{size[1]}, its size must stay 800x600"
    return thumb, None


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    from PIL import Image

    _heif()
    problems = []
    out = work_dir / "out"
    if not out.is_dir():
        problems.append("out/ does not exist: the JPEGs must be put in out/")
        problems.extend(_photos_problems(work_dir, fixture_dir))
        return CheckResult(False, problems)

    jpegs = sorted(p for p in out.rglob("*") if p.is_file() and p.suffix.lower() in JPEG_SUFFIXES)
    if len(jpegs) != len(SOURCES):
        problems.append(f"out/ holds {len(jpegs)} JPEG files, expected exactly 7 (one for each of the seven images)")

    thumbs = {}
    for path in jpegs:
        name = f"out/{path.relative_to(out).as_posix()}"
        thumb, problem = _read_jpeg(path, name)
        if problem is not None:
            problems.append(problem)
        if thumb is not None:
            thumbs[name] = thumb

    matched: dict[str, str] = {}  # JPEG -> source
    for rel in SOURCES:
        with Image.open(fixture_dir / "photos" / rel) as source:
            reference = _thumb(source)
        if not thumbs:
            problems.append(f"photos/{rel} resembles none of the JPEGs in out/ (no JPEG to match)")
            continue
        distances = {name: _distance(reference, thumb) for name, thumb in thumbs.items()}
        closest = min(distances, key=distances.__getitem__)
        if distances[closest] >= MATCH_LIMIT:
            problems.append(
                f"photos/{rel} resembles none of the JPEGs in out/ "
                f"(closest {closest}, difference {distances[closest]:.1f}): no JPEG matches it"
            )
        elif closest in matched:
            problems.append(f"photos/{rel} and photos/{matched[closest]} both match {closest}: a duplicate")
        else:
            matched[closest] = rel

    problems.extend(_photos_problems(work_dir, fixture_dir))
    if problems:
        return CheckResult(False, problems)
    return CheckResult(True, [f"{len(matched)} JPEGs of 800x600 in out/, one for each source; photos/ unchanged"])


TASK = Task(name="batch", prompt=PROMPT, prepare_fixtures=_prepare, check=_check)
