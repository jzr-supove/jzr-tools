"""The facts ``jzr pdf info`` shows: pages, sizes, metadata, text, images and features."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from jzr_tools.core import pdfdoc
from jzr_tools.core.pdfdoc import PageBox, PdfError, range_text

POINTS_PER_MM = 72 / 25.4

# Paper sizes in millimetres, portrait.
PAPERS: tuple[tuple[str, float, float], ...] = (
    ("A3", 297, 420),
    ("A4", 210, 297),
    ("A5", 148, 210),
    ("B5", 176, 250),
    ("Letter", 215.9, 279.4),
    ("Legal", 215.9, 355.6),
    ("Tabloid", 279.4, 431.8),
)

_METADATA = ("title", "author", "subject", "creator", "producer")
_DATE = re.compile(
    r"^(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
    r"(?:(Z)|([+-])(\d{2})'?(\d{2})?'?)?"
)


@dataclass
class PageSize:
    width_mm: float
    height_mm: float
    paper: str | None
    orientation: str | None
    pages: list[int] = field(default_factory=list)

    def text(self) -> str:
        size = f"{round(self.width_mm)} x {round(self.height_mm)} mm"
        if self.paper is None:
            return size
        return f"{self.paper} {self.orientation} ({size})"


@dataclass
class PdfInfo:
    path: Path
    file_size: int
    encrypted: bool = False
    version: str | None = None
    pages: int | None = None
    page_sizes: list[PageSize] = field(default_factory=list)
    rotated: dict[int, list[int]] = field(default_factory=dict)
    metadata: dict[str, str | None] = field(default_factory=dict)
    created: str | None = None
    modified: str | None = None
    text_pages: int | None = None
    images: int | None = None
    features: tuple[str, ...] | None = None
    problem: str | None = None


def paper_of(width_mm: float, height_mm: float) -> tuple[str | None, str | None]:
    short, long = sorted((width_mm, height_mm))
    for name, paper_short, paper_long in PAPERS:
        if abs(short - paper_short) <= 1 and abs(long - paper_long) <= 1:
            return name, "landscape" if width_mm > height_mm else "portrait"
    return None, None


def _sizes(boxes: list[PageBox]) -> list[PageSize]:
    sizes: dict[str, PageSize] = {}
    for number, box in enumerate(boxes, start=1):
        width = (box.crop[2] - box.crop[0]) / POINTS_PER_MM
        height = (box.crop[3] - box.crop[1]) / POINTS_PER_MM
        paper, orientation = paper_of(width, height)
        size = PageSize(round(width, 1), round(height, 1), paper, orientation)
        key = size.text()
        sizes.setdefault(key, size).pages.append(number)
    return list(sizes.values())


def pdf_date(value: object) -> str | None:
    """``D:20260102100000+05'00'`` as ``2026-01-02 10:00:00 +05:00``."""
    if value is None:
        return None
    match = _DATE.match(str(value).strip())
    if not match:
        return None
    year, month, day, hour, minute, second, zulu, sign, off_h, off_m = match.groups()
    text = (
        f"{year}-{month or '01'}-{day or '01'} "
        f"{hour or '00'}:{minute or '00'}:{second or '00'}"
    )
    if zulu:
        text += " +00:00"
    elif sign:
        text += f" {sign}{off_h}:{off_m or '00'}"
    return text


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _metadata(reader: object) -> tuple[dict[str, str | None], object, object]:
    info = reader.metadata or {}  # type: ignore[attr-defined]
    found: dict[str, str | None] = {
        name: _text(info.get(f"/{name.capitalize()}")) for name in _METADATA
    }
    if any(value is None for value in found.values()):
        try:
            xmp = reader.xmp_metadata  # type: ignore[attr-defined]
        except Exception:  # unreadable XMP adds nothing
            xmp = None
        if xmp is not None:
            try:
                fallbacks = {
                    "title": (xmp.dc_title or {}).get("x-default"),
                    "author": ", ".join(xmp.dc_creator or []) or None,
                    "subject": (xmp.dc_description or {}).get("x-default"),
                    "creator": xmp.xmp_creator_tool,
                    "producer": xmp.pdf_producer,
                }
            except Exception:  # unreadable XMP adds nothing
                fallbacks = {}
            for name, value in fallbacks.items():
                if found.get(name) is None:
                    found[name] = _text(value)
    return found, info.get("/CreationDate"), info.get("/ModDate")


def _image_count(reader: object) -> int:
    from pypdf.generic import DictionaryObject, IndirectObject

    seen: set[tuple[int, int]] = set()
    images: set[tuple[int, int]] = set()
    stack: list[object] = [page.get("/Resources") for page in reader.pages]  # type: ignore[attr-defined]
    while stack:
        resources = stack.pop()
        if isinstance(resources, IndirectObject):
            key = (resources.idnum, resources.generation)
            if key in seen:
                continue
            seen.add(key)
        resources = resources.get_object() if resources is not None else None
        if not isinstance(resources, DictionaryObject):
            continue
        xobjects = resources.get("/XObject")
        xobjects = xobjects.get_object() if xobjects is not None else None
        if not isinstance(xobjects, DictionaryObject):
            continue
        for name in xobjects:
            reference = xobjects.raw_get(name)
            target = reference.get_object()
            if not isinstance(target, DictionaryObject):
                continue
            key = (
                (reference.idnum, reference.generation)
                if isinstance(reference, IndirectObject)
                else (-1, id(target))
            )
            if target.get("/Subtype") == "/Image":
                images.add(key)
            elif target.get("/Subtype") == "/Form" and key not in seen:
                seen.add(key)
                stack.append(target.get("/Resources"))
    return len(images)


def _text_pages(data: bytes) -> int | None:
    """Pages with at least one character that is not whitespace (pypdfium2)."""
    try:
        import pypdfium2
    except ImportError:
        return None
    try:
        document = pypdfium2.PdfDocument(data)
    except Exception:  # pdfium could not open it; leave the row out
        return None
    count = 0
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                textpage = page.get_textpage()
                try:
                    if textpage.get_text_range().strip():
                        count += 1
                finally:
                    textpage.close()
            finally:
                page.close()
    finally:
        document.close()
    return count


def read_info(path: Path) -> PdfInfo:
    """The facts about ``path``; raises PdfError when it is not a readable PDF."""
    path = Path(path)
    try:
        found = pdfdoc.inspect(path)
    except PdfError as error:
        if str(error) == pdfdoc.ENCRYPTED:
            return PdfInfo(path, path.stat().st_size, encrypted=True)
        raise
    reader = found.reader
    with pdfdoc.quiet():
        metadata, created, modified = _metadata(reader)
        codes, _bad = pdfdoc.features(reader)
        images = _image_count(reader)
    rotated: dict[int, list[int]] = {}
    for number, box in enumerate(found.pages, start=1):
        if box.rotation:
            rotated.setdefault(box.rotation, []).append(number)
    return PdfInfo(
        path,
        len(found.data),
        version=pdfdoc.header_version(found.data),
        pages=len(found.pages),
        page_sizes=_sizes(found.pages),
        rotated=dict(sorted(rotated.items())),
        metadata=metadata,
        created=pdf_date(created),
        modified=pdf_date(modified),
        text_pages=_text_pages(found.data),
        images=images,
        features=codes,
        problem=found.problem,
    )


def info_rows(info: PdfInfo) -> list[tuple[str, str]]:
    """Label and value rows, in display order, leaving out empty ones.

    A continuation of the ``page size`` row has an empty label.
    """
    from jzr_tools.core.media import format_size

    if info.encrypted:
        return [("encrypted", "yes"), ("file size", format_size(info.file_size))]
    rows: list[tuple[str, str]] = []
    if info.version:
        rows.append(("version", info.version))
    rows.append(("pages", str(info.pages)))
    if len(info.page_sizes) == 1:
        rows.append(("page size", info.page_sizes[0].text()))
    else:
        for index, size in enumerate(info.page_sizes):
            rows.append(("page size" if index == 0 else "", f"{size.text()}: {range_text(size.pages)}"))
    if info.rotated:
        rows.append(
            (
                "rotated",
                "; ".join(f"{range_text(pages)} ({angle}°)" for angle, pages in info.rotated.items()),
            )
        )
    for name in _METADATA:
        value = info.metadata.get(name)
        if value:
            rows.append((name, value))
    if info.created:
        rows.append(("created", info.created))
    if info.modified:
        rows.append(("modified", info.modified))
    if info.text_pages is not None:
        rows.append(
            (
                "text",
                f"on {info.text_pages} of {info.pages} pages" if info.text_pages else "none (scanned?)",
            )
        )
    if info.images:
        rows.append(("images", str(info.images)))
    if info.features:
        rows.append(("features", ", ".join(info.features)))
    if info.problem:
        rows.append(("problem", info.problem))
    rows.append(("file size", format_size(info.file_size)))
    return rows


def info_as_dict(info: PdfInfo, *, file: str | None = None) -> dict[str, object]:
    return {
        "file": file if file is not None else str(info.path),
        "version": info.version,
        "pages": info.pages,
        "page_sizes": None
        if info.encrypted
        else [
            {
                "width_mm": size.width_mm,
                "height_mm": size.height_mm,
                "paper": size.paper,
                "orientation": size.orientation,
                "pages": size.pages,
            }
            for size in info.page_sizes
        ],
        "rotated": None
        if info.encrypted
        else {str(angle): pages for angle, pages in info.rotated.items()},
        **{name: info.metadata.get(name) for name in _METADATA},
        "created": info.created,
        "modified": info.modified,
        "text_pages": info.text_pages,
        "images": info.images,
        "features": None if info.features is None else list(info.features),
        "file_size": info.file_size,
        "encrypted": info.encrypted,
        "problem": info.problem,
    }
