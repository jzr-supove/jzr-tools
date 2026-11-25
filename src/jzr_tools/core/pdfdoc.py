"""Opening, checking and writing PDF files for the ``jzr pdf`` commands.

Every source is checked before anything is built from it: the file must look
like a PDF from the outside, open without a password, have a sound page tree
and pages whose size, scaling and rotation the commands can keep. The check
also lists the features (bookmarks, forms, signatures, scripts...) that may
not survive a rewrite, so the commands can say so.

pypdf is imported inside the functions that need it, so importing this module
stays cheap.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import io
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import TYPE_CHECKING, Iterator
import warnings

if TYPE_CHECKING:
    from pypdf import PdfReader

DAMAGED = "damaged or not a PDF"
ENCRYPTED = "encrypted PDFs are not supported; use an unencrypted copy"
UNSUPPORTED = "unsupported page size, scaling or rotation"
TOO_COMPLEX = "this PDF is too complex to process safely"
UNCHECKED = "the result could not be checked; nothing written"

MAX_OBJECTS = 1_000_000
MAX_DEPTH = 64
MAX_EDGE = 14_400
TOLERANCE = 0.001

PAGES_USAGE = "use page numbers and ranges such as 1-3,5 (pages 1 to {count})"
MAX_PAGES_TEXT = 4096
MAX_GROUPS = 100

# Feature codes in the order they are reported, with the note for each.
FEATURES: dict[str, str] = {
    "structure": "bookmarks, page labels or accessibility tags may be lost or changed",
    "annotations": "comments, annotations or internal links may be lost or changed",
    "opening-view": "the preferred opening page and zoom are not kept",
    "links-removed": "malformed web or email links are disabled; the page content is kept",
    "forms": "form fields and their values may be lost or changed",
    "signatures": "digital signatures will not validate in the new PDF",
    "active-content": "scripts and interactive actions may be kept, lost or changed; "
    "jzr does not run or remove them",
    "attachments": "attachments and embedded media may be lost or changed",
}

_HEADER = re.compile(rb"%PDF-(1\.[0-7]|2\.0)(?![0-9])")
_TRAILER = re.compile(rb"startxref\s+\d+\s+%%EOF\s*$")
_BAD_URI = re.compile(r"[\x00-\x20\x7f]")

# Actions and keys that run something or reach outside the document.
_ACTIVE_ACTIONS = frozenset(
    {
        "/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/ResetForm",
        "/Rendition", "/Sound", "/Movie", "/RichMediaExecute", "/GoToE", "/GoToR",
        "/Hide", "/SetOCGState", "/Trans", "/GoTo3DView",
    }
)
_ACTIVE_KEYS = frozenset({"/JS", "/AA", "/JavaScript"})
_MEDIA_SUBTYPES = frozenset(
    {"/FileAttachment", "/RichMedia", "/Movie", "/Sound", "/Screen", "/3D"}
)
_ATTACHMENT_KEYS = frozenset({"/EmbeddedFiles", "/EF", "/AF", "/Collection"})
_STRUCTURE_KEYS = ("/Outlines", "/PageLabels", "/StructTreeRoot", "/MarkInfo")


class PdfError(Exception):
    """A PDF that cannot be used; ``str()`` is the text shown for it."""


@dataclass(frozen=True)
class PageBox:
    """The MediaBox, CropBox (left, bottom, right, top) and rotation of a page."""

    media: tuple[float, float, float, float]
    crop: tuple[float, float, float, float]
    rotation: int


@dataclass
class Checked:
    """A source that passed every check, ready to be copied from."""

    path: Path
    reader: PdfReader
    pages: list[PageBox]
    features: tuple[str, ...]
    bad_links: int


@dataclass
class Inspected:
    """What a look at a PDF found, including a page problem that does not stop ``info``."""

    path: Path
    data: bytes
    reader: PdfReader
    pages: list[PageBox]
    problem: str | None


@contextlib.contextmanager
def quiet() -> Iterator[None]:
    """Keep pypdf's warnings about odd files off the terminal."""
    logger = logging.getLogger("pypdf")
    level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    finally:
        logger.setLevel(level)


def envelope_ok(data: bytes) -> bool:
    """Whether ``data`` starts and ends the way a PDF file does."""
    return bool(_HEADER.search(data[:1024])) and bool(_TRAILER.search(data[-1024:]))


def header_version(data: bytes) -> str | None:
    match = _HEADER.search(data[:1024])
    return match[1].decode() if match else None


# ---- the page tree


def _numbers(value: object) -> tuple[float, ...] | None:
    from pypdf.generic import ArrayObject

    value = _resolve(value)
    if not isinstance(value, ArrayObject):
        return None
    numbers = []
    for item in value:
        item = _resolve(item)
        try:
            numbers.append(float(item))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    return tuple(numbers)


def _resolve(value: object) -> object:
    from pypdf.generic import IndirectObject

    seen = 0
    while isinstance(value, IndirectObject):
        value = value.get_object()
        seen += 1
        if seen > MAX_DEPTH:
            raise PdfError(DAMAGED)
    return value


def normalise(box: tuple[float, ...]) -> tuple[float, float, float, float]:
    left, bottom, right, top = box
    return (
        float(min(left, right)),
        float(min(bottom, top)),
        float(max(left, right)),
        float(max(bottom, top)),
    )


def _page_box(page: dict, inherited: dict) -> tuple[PageBox, bool]:
    """The page's boxes and rotation, and whether the commands can keep them."""
    media_value = page.get("/MediaBox", inherited.get("/MediaBox"))
    if media_value is None:
        raise PdfError(DAMAGED)
    media_numbers = _numbers(media_value)
    if media_numbers is None or len(media_numbers) != 4:
        raise PdfError(DAMAGED)
    media = normalise(media_numbers)

    crop_value = page.get("/CropBox", inherited.get("/CropBox"))
    crop = media
    ok = True
    if crop_value is not None:
        crop_numbers = _numbers(crop_value)
        if crop_numbers is None or len(crop_numbers) != 4:
            ok = False
        else:
            crop = normalise(crop_numbers)

    rotate_value = _resolve(page.get("/Rotate", inherited.get("/Rotate", 0)))
    try:
        rotate = int(rotate_value)  # type: ignore[call-overload]
        if rotate != float(rotate_value):  # type: ignore[arg-type]
            ok = False
    except (TypeError, ValueError):
        rotate, ok = 0, False
    if rotate % 90:
        ok = False

    unit = _resolve(page.get("/UserUnit", 1))
    try:
        if abs(float(unit) - 1) > TOLERANCE:  # type: ignore[arg-type]
            ok = False
    except (TypeError, ValueError):
        ok = False

    for box in (media, crop):
        width, height = box[2] - box[0], box[3] - box[1]
        if width <= 0 or height <= 0 or width > MAX_EDGE or height > MAX_EDGE:
            ok = False
    if (
        crop[0] < media[0] - TOLERANCE
        or crop[1] < media[1] - TOLERANCE
        or crop[2] > media[2] + TOLERANCE
        or crop[3] > media[3] + TOLERANCE
    ):
        ok = False
    return PageBox(media, crop, rotate % 360), ok


def _walk_pages(reader: PdfReader) -> tuple[list[PageBox], bool]:
    """Walk ``/Root /Pages`` by hand, checking what pypdf would forgive."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    root = _resolve(reader.trailer.get("/Root"))
    if not isinstance(root, DictionaryObject):
        raise PdfError(DAMAGED)
    top = root.raw_get("/Pages") if "/Pages" in root else None
    if not isinstance(top, IndirectObject):
        raise PdfError(DAMAGED)

    boxes: list[PageBox] = []
    all_ok = True
    seen: set[tuple[int, int]] = set()
    inheritable = ("/MediaBox", "/CropBox", "/Rotate")

    def visit(reference: IndirectObject, parent: IndirectObject | None, inherited: dict, depth: int) -> int:
        nonlocal all_ok
        if depth > MAX_DEPTH:
            raise PdfError(TOO_COMPLEX)
        key = (reference.idnum, reference.generation)
        if key in seen:
            raise PdfError(DAMAGED)
        seen.add(key)
        node = reference.get_object()
        if not isinstance(node, DictionaryObject):
            raise PdfError(DAMAGED)
        if parent is not None:
            back = node.raw_get("/Parent") if "/Parent" in node else None
            if not isinstance(back, IndirectObject) or (back.idnum, back.generation) != (
                parent.idnum,
                parent.generation,
            ):
                raise PdfError(DAMAGED)
        kind = node.get("/Type")
        if kind == "/Page":
            box, ok = _page_box(node, inherited)
            boxes.append(box)
            all_ok = all_ok and ok
            return 1
        if kind != "/Pages":
            raise PdfError(DAMAGED)
        here = dict(inherited)
        for name in inheritable:
            if name in node:
                here[name] = node[name]
        kids = _resolve(node.get("/Kids"))
        if not isinstance(kids, ArrayObject):
            raise PdfError(DAMAGED)
        leaves = 0
        for kid in kids:
            if not isinstance(kid, IndirectObject):
                raise PdfError(DAMAGED)
            leaves += visit(kid, reference, here, depth + 1)
        count = _resolve(node.get("/Count"))
        try:
            if int(count) != leaves:  # type: ignore[call-overload]
                raise PdfError(DAMAGED)
        except (TypeError, ValueError) as error:
            raise PdfError(DAMAGED) from error
        return leaves

    visit(top, None, {}, 0)
    if not boxes:
        raise PdfError(DAMAGED)
    return boxes, all_ok


def inspect(path: Path) -> Inspected:
    """Read ``path`` and run every check but the page one, which is reported, not raised.

    Raises PdfError with DAMAGED, ENCRYPTED or TOO_COMPLEX.
    """
    from pypdf import PdfReader

    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise PdfError(error.strerror or DAMAGED) from error
    if not envelope_ok(data):
        raise PdfError(DAMAGED)
    with quiet():
        try:
            reader = PdfReader(io.BytesIO(data), strict=False)
            encrypted = reader.is_encrypted
        except Exception as error:  # pypdf raises many kinds for broken files
            raise PdfError(DAMAGED) from error
        if encrypted:
            raise PdfError(ENCRYPTED)
        try:
            size = int(_resolve(reader.trailer.get("/Size", 0)))  # type: ignore[call-overload]
        except (TypeError, ValueError) as error:
            raise PdfError(DAMAGED) from error
        if size > MAX_OBJECTS:
            raise PdfError(TOO_COMPLEX)
        try:
            pages, ok = _walk_pages(reader)
            if len(reader.pages) != len(pages):
                raise PdfError(DAMAGED)
        except PdfError:
            raise
        except RecursionError as error:
            raise PdfError(TOO_COMPLEX) from error
        except Exception as error:
            raise PdfError(DAMAGED) from error
    return Inspected(Path(path), data, reader, pages, None if ok else UNSUPPORTED)


def open_checked(path: Path) -> Checked:
    """Check ``path`` and return it ready to copy from; raise PdfError otherwise."""
    found = inspect(path)
    if found.problem is not None:
        raise PdfError(found.problem)
    codes, bad_links = features(found.reader)
    return Checked(found.path, found.reader, found.pages, codes, bad_links)


# ---- features


def bad_uri(action: object) -> bool:
    """Whether a link action is a URI action with a malformed address."""
    from pypdf.generic import DictionaryObject

    action = _resolve(action)
    if not isinstance(action, DictionaryObject) or action.get("/S") != "/URI":
        return False
    uri = _resolve(action.get("/URI"))
    if uri is None:
        return False
    text = uri.decode("latin-1") if isinstance(uri, bytes) else str(uri)
    return bool(_BAD_URI.search(text))


def features(reader: PdfReader) -> tuple[tuple[str, ...], int]:
    """The feature codes found in the document, in FEATURES order, and its malformed links."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    found: set[str] = set()
    bad_links = 0
    with quiet():
        root = _resolve(reader.trailer.get("/Root"))
        if not isinstance(root, DictionaryObject):
            return (), 0

        if any(key in root for key in _STRUCTURE_KEYS):
            found.add("structure")
        if "/OpenAction" in root:
            found.add("opening-view")
        if "/Perms" in root:
            found.add("signatures")
        names = _resolve(root.get("/Names"))
        if isinstance(names, DictionaryObject):
            if "/Dests" in names:
                found.add("annotations")
            if "/JavaScript" in names:
                found.add("active-content")
            if "/EmbeddedFiles" in names:
                found.add("attachments")
        if "/Dests" in root:
            found.add("annotations")
        form = _resolve(root.get("/AcroForm"))
        if isinstance(form, DictionaryObject):
            fields = _resolve(form.get("/Fields"))
            if "/XFA" in form or (isinstance(fields, ArrayObject) and len(fields)):
                found.add("forms")

        for page in reader.pages:
            annots = _resolve(page.get("/Annots"))
            if not isinstance(annots, ArrayObject) or not len(annots):
                continue
            found.add("annotations")
            for item in annots:
                annot = _resolve(item)
                if isinstance(annot, DictionaryObject) and annot.get("/Subtype") == "/Link":
                    if bad_uri(annot.get("/A")):
                        bad_links += 1
        if bad_links:
            found.add("links-removed")

        # Look at every object reachable from the catalog and the info
        # dictionary. Stream data is never read, only stream dictionaries.
        stack: list[object] = [root, reader.trailer.get("/Info")]
        seen: set[tuple[int, int]] = set()
        visited = 0
        while stack:
            value = stack.pop()
            if isinstance(value, IndirectObject):
                key = (value.idnum, value.generation)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    value = value.get_object()
                except Exception:  # an unreadable object adds nothing to find
                    continue
            visited += 1
            if visited > MAX_OBJECTS:
                raise PdfError(TOO_COMPLEX)
            if isinstance(value, DictionaryObject):
                _look_at(value, found)
                for name, item in value.items():
                    if name != "/Parent":
                        stack.append(value.raw_get(name) if name in value else item)
            elif isinstance(value, ArrayObject):
                stack.extend(value)
    return tuple(code for code in FEATURES if code in found), bad_links


def _look_at(entry: dict, found: set[str]) -> None:
    kind = entry.get("/Type")
    subtype = entry.get("/Subtype")
    field = entry.get("/FT")
    if field is not None or subtype == "/Widget":
        found.add("forms")
    if (
        "/ByteRange" in entry
        or kind in ("/Sig", "/DocTimeStamp")
        or field == "/Sig"
    ):
        found.add("signatures")
    if any(key in entry for key in _ACTIVE_KEYS) or entry.get("/S") in _ACTIVE_ACTIONS:
        found.add("active-content")
    if (
        any(key in entry for key in _ATTACHMENT_KEYS)
        or subtype in _MEDIA_SUBTYPES
        or kind == "/Filespec"
    ):
        found.add("attachments")


# ---- page lists


def parse_pages(
    text: str, count: int, *, groups: bool = False, backwards: bool = False
) -> list[list[int]]:
    """Read ``1-3,5`` (or ``1-3;4,6;7-`` with ``groups``) into 1-based page lists.

    ``N-`` runs to the last page; with ``backwards`` a range such as ``12-1``
    runs down. A page may appear once in a group. Raises ValueError with the
    usage text otherwise.
    """
    usage = PAGES_USAGE.format(count=count)
    if len(text) > MAX_PAGES_TEXT:
        raise ValueError(usage)
    parts = text.split(";") if groups else [text]
    if ";" in text and not groups:
        raise ValueError(usage)
    if len(parts) > MAX_GROUPS:
        raise ValueError(usage)

    def number(item: str) -> int:
        if not re.fullmatch(r"[1-9][0-9]*", item):
            raise ValueError(usage)
        value = int(item)
        if value > count:
            raise ValueError(usage)
        return value

    result: list[list[int]] = []
    for part in parts:
        pages: list[int] = []
        for raw in part.split(","):
            item = raw.strip()
            if "-" in item:
                start_text, _, end_text = item.partition("-")
                start_text, end_text = start_text.strip(), end_text.strip()
                start = number(start_text)
                end = count if end_text == "" else number(end_text)
                if start <= end:
                    pages.extend(range(start, end + 1))
                elif backwards:
                    pages.extend(range(start, end - 1, -1))
                else:
                    raise ValueError(usage)
            else:
                pages.append(number(item))
        if len(set(pages)) != len(pages):
            raise ValueError(usage)
        result.append(pages)
    return result


def range_text(pages: list[int]) -> str:
    """``[1, 2, 3, 5]`` as ``1-3, 5``."""
    runs: list[str] = []
    index = 0
    while index < len(pages):
        end = index
        while end + 1 < len(pages) and pages[end + 1] == pages[end] + 1:
            end += 1
        runs.append(str(pages[index]) if end == index else f"{pages[index]}-{pages[end]}")
        index = end + 1
    return ", ".join(runs)


# ---- outputs


def output_name(
    source: Path,
    action: str,
    *,
    index: int | None = None,
    digits: int = 3,
    outdir: Path | None = None,
) -> Path:
    """``<stem>_<action>.pdf``, or ``<stem>_<action>-001.pdf`` with an index."""
    source = Path(source)
    suffix = "" if index is None else f"-{index:0{digits}d}"
    folder = outdir if outdir is not None else source.parent
    return Path(folder) / f"{source.stem}_{action}{suffix}.pdf"


def read_boxes(data: bytes) -> list[PageBox]:
    """The boxes and rotations of the pages of ``data``, as pypdf reads them back."""
    from pypdf import PdfReader

    with quiet():
        reader = PdfReader(io.BytesIO(data), strict=False)
        boxes = []
        for page in reader.pages:
            media = normalise(tuple(float(v) for v in page.mediabox))
            crop = normalise(tuple(float(v) for v in page.cropbox))
            boxes.append(PageBox(media, crop, int(page.rotation) % 360))
    return boxes


def verify(data: bytes, expected: list[PageBox]) -> None:
    """Raise PdfError(UNCHECKED) unless ``data`` reads back with the expected pages."""
    if not envelope_ok(data):
        raise PdfError(UNCHECKED)
    try:
        found = read_boxes(data)
    except Exception as error:
        raise PdfError(UNCHECKED) from error
    if len(found) != len(expected):
        raise PdfError(UNCHECKED)
    for got, want in zip(found, expected):
        if got.rotation != want.rotation % 360:
            raise PdfError(UNCHECKED)
        for a, b in ((got.media, want.media), (got.crop, want.crop)):
            if any(abs(x - y) > TOLERANCE for x, y in zip(a, b)):
                raise PdfError(UNCHECKED)


def write_pdf(target: Path, data: bytes) -> None:
    """Write ``data`` under a temporary name in the target's folder, then rename it."""
    target = Path(target)
    folder = target.parent if str(target.parent) else Path(".")
    handle, temporary = tempfile.mkstemp(prefix=".jzr-tmp-", suffix=".pdf", dir=folder)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
