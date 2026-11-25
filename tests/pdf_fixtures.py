"""Builders for the PDF files the tests read.

Every builder returns the bytes of a file. Pages are told apart by their size:
``marked`` gives page ``n`` a MediaBox that is ``100 + 10 * n`` points wide and
200 high, so ``marker`` can tell which source page an output page came from.
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    ByteStringObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

HEIGHT = 200


def _dump(writer: PdfWriter) -> bytes:
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def marker_width(number: int) -> int:
    return 100 + 10 * number


def marker(page) -> int:  # noqa: ANN001
    """The source page number a page built by ``marked`` stands for."""
    box = page.mediabox
    return round((float(box.right) - float(box.left) - 100) / 10)


def markers(data: bytes) -> list[int]:
    return [marker(page) for page in PdfReader(io.BytesIO(data)).pages]


def rotations(data: bytes) -> list[int]:
    return [int(page.rotation) % 360 for page in PdfReader(io.BytesIO(data)).pages]


def blank(sizes: list[tuple[float, float]], rotate: dict[int, int] | None = None) -> bytes:
    """Blank pages of the given sizes; ``rotate`` maps a 1-based page to /Rotate."""
    writer = PdfWriter()
    for width, height in sizes:
        writer.add_blank_page(width=width, height=height)
    for number, angle in (rotate or {}).items():
        writer.pages[number - 1][NameObject("/Rotate")] = NumberObject(angle)
    return _dump(writer)


def marked(count: int, rotate: dict[int, int] | None = None, first: int = 1) -> bytes:
    """``count`` blank pages; page i is the marker ``first + i - 1``."""
    sizes = [(marker_width(first + i), HEIGHT) for i in range(count)]
    return blank(sizes, rotate)


def _text_page(writer: PdfWriter, font, width: float, height: float, text: str) -> None:  # noqa: ANN001
    page = writer.add_blank_page(width=width, height=height)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 18 Tf 20 {height / 2:.0f} Td ({text}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )


def text_pdf(labels: list[str], first: int = 1) -> bytes:
    """One page per label, each with that label as text and one shared font."""
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    for index, label in enumerate(labels):
        _text_page(writer, font, marker_width(first + index), HEIGHT, label)
    return _dump(writer)


def font_ids(data: bytes) -> list[int]:
    """The object number of the /F1 font of each page."""
    ids = []
    for page in PdfReader(io.BytesIO(data)).pages:
        reference = page["/Resources"]["/Font"].raw_get("/F1")
        ids.append(reference.idnum)
    return ids


def with_image(count: int = 1) -> bytes:
    """Pages that each draw the same 2x2 RGB image."""
    writer = PdfWriter()
    image = DecodedStreamObject()
    image.set_data(bytes(range(12)))
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(2),
            NameObject("/Height"): NumberObject(2),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    reference = writer._add_object(image)
    for _ in range(count):
        page = writer.add_blank_page(width=200, height=200)
        content = DecodedStreamObject()
        content.set_data(b"q 100 0 0 100 10 10 cm /Im1 Do Q")
        page[NameObject("/Contents")] = writer._add_object(content)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): reference})}
        )
    return _dump(writer)


def with_outline(count: int = 3) -> bytes:
    writer = PdfWriter()
    for i in range(count):
        writer.add_blank_page(width=marker_width(i + 1), height=HEIGHT)
    writer.add_outline_item("Chapter one", 0)
    writer.add_outline_item("Chapter two", 1)
    return _dump(writer)


def outline_titles(data: bytes) -> list[str]:
    def walk(items) -> list[str]:  # noqa: ANN001
        titles: list[str] = []
        for item in items:
            if isinstance(item, list):
                titles.extend(walk(item))
            else:
                titles.append(str(item.title))
        return titles

    return walk(PdfReader(io.BytesIO(data)).outline)


def _annotated(annotation: DictionaryObject, count: int = 1) -> bytes:
    writer = PdfWriter()
    for i in range(count):
        writer.add_blank_page(width=marker_width(i + 1), height=HEIGHT)
    reference = writer._add_object(annotation)
    writer.pages[0][NameObject("/Annots")] = ArrayObject([reference])
    return _dump(writer)


def _link(action: DictionaryObject) -> DictionaryObject:
    return DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/Rect"): ArrayObject([NumberObject(10), NumberObject(10), NumberObject(60), NumberObject(30)]),
            NameObject("/Border"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]),
            NameObject("/A"): action,
        }
    )


def _uri(text: str) -> DictionaryObject:
    return DictionaryObject(
        {NameObject("/S"): NameObject("/URI"), NameObject("/URI"): TextStringObject(text)}
    )


def with_link(uri: str = "https://example.com/") -> bytes:
    """One page with a link annotation to ``uri``."""
    return _annotated(_link(_uri(uri)))


def with_bad_link() -> bytes:
    """A link whose address contains a space."""
    return with_link("http://exa mple.com/path")


def link_uris(data: bytes) -> list[str | None]:
    """The URI of each link annotation of the file, or None when it has no action."""
    found: list[str | None] = []
    for page in PdfReader(io.BytesIO(data)).pages:
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if annot.get("/Subtype") != "/Link":
                continue
            action = annot.get("/A")
            found.append(str(action.get_object().get("/URI")) if action is not None else None)
    return found


def with_javascript_action() -> bytes:
    action = DictionaryObject(
        {
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert(1)"),
        }
    )
    return _annotated(_link(action))


def with_document_javascript() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    action = writer._add_object(
        DictionaryObject(
            {
                NameObject("/S"): NameObject("/JavaScript"),
                NameObject("/JS"): TextStringObject("app.alert('hi');"),
            }
        )
    )
    scripts = DictionaryObject(
        {NameObject("/Names"): ArrayObject([TextStringObject("start"), action])}
    )
    writer._root_object[NameObject("/Names")] = DictionaryObject(
        {NameObject("/JavaScript"): scripts}
    )
    return _dump(writer)


def with_open_action() -> bytes:
    writer = PdfWriter()
    for i in range(2):
        writer.add_blank_page(width=marker_width(i + 1), height=HEIGHT)
    writer._root_object[NameObject("/OpenAction")] = ArrayObject(
        [writer.pages[1].indirect_reference, NameObject("/Fit")]
    )
    return _dump(writer)


def with_attachment() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_attachment("note.txt", b"hello")
    return _dump(writer)


def with_form_field() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    field = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/FT"): NameObject("/Tx"),
                NameObject("/T"): TextStringObject("name"),
                NameObject("/V"): TextStringObject("Ada"),
                NameObject("/Rect"): ArrayObject([NumberObject(10), NumberObject(10), NumberObject(100), NumberObject(30)]),
                NameObject("/P"): page.indirect_reference,
            }
        )
    )
    page[NameObject("/Annots")] = ArrayObject([field])
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
        {NameObject("/Fields"): ArrayObject([field])}
    )
    return _dump(writer)


def with_signature() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    value = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Sig"),
                NameObject("/Filter"): NameObject("/Adobe.PPKLite"),
                NameObject("/SubFilter"): NameObject("/adbe.pkcs7.detached"),
                NameObject("/ByteRange"): ArrayObject([NumberObject(n) for n in (0, 10, 20, 30)]),
                NameObject("/Contents"): ByteStringObject(b"\x00" * 16),
            }
        )
    )
    field = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/FT"): NameObject("/Sig"),
                NameObject("/T"): TextStringObject("Signature1"),
                NameObject("/V"): value,
                NameObject("/Rect"): ArrayObject([NumberObject(0)] * 4),
                NameObject("/P"): page.indirect_reference,
            }
        )
    )
    page[NameObject("/Annots")] = ArrayObject([field])
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
        {NameObject("/Fields"): ArrayObject([field]), NameObject("/SigFlags"): NumberObject(3)}
    )
    return _dump(writer)


def encrypted(count: int = 2) -> bytes:
    """Encrypted with an empty user password."""
    writer = PdfWriter()
    for i in range(count):
        writer.add_blank_page(width=marker_width(i + 1), height=HEIGHT)
    writer.encrypt("", algorithm="RC4-128")
    return _dump(writer)


def with_metadata(**fields: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595.276, height=841.89)
    writer.add_metadata({f"/{key}": value for key, value in fields.items()})
    return _dump(writer)


# ---- pages that fail the checks


def _one_page_with(key: str, value) -> bytes:  # noqa: ANN001
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.pages[0][NameObject(key)] = value
    return _dump(writer)


def _numbers(*values: float) -> ArrayObject:
    return ArrayObject([FloatObject(v) for v in values])


def huge_media_box(width: float = 20000) -> bytes:
    return _one_page_with("/MediaBox", _numbers(0, 0, width, 100))


def empty_media_box() -> bytes:
    return _one_page_with("/MediaBox", _numbers(0, 0, 0, 100))


def reversed_media_box() -> bytes:
    return _one_page_with("/MediaBox", _numbers(200, 300, 0, 0))


def crop_box(left: float, bottom: float, right: float, top: float) -> bytes:
    """A 200 x 300 page with the given CropBox."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.pages[0][NameObject("/CropBox")] = _numbers(left, bottom, right, top)
    return _dump(writer)


def user_unit(value: float) -> bytes:
    return _one_page_with("/UserUnit", FloatObject(value))


def rotate(angle: int) -> bytes:
    return _one_page_with("/Rotate", NumberObject(angle))


def inherited_boxes() -> bytes:
    """Two pages that take MediaBox and Rotate from the page tree root."""
    writer = PdfWriter()
    for _ in range(2):
        page = writer.add_blank_page(width=300, height=400)
        del page["/MediaBox"]
    root = writer._pages.get_object()
    root[NameObject("/MediaBox")] = _numbers(0, 0, 300, 400)
    root[NameObject("/Rotate")] = NumberObject(90)
    return _dump(writer)


# ---- broken files


def truncated(data: bytes | None = None) -> bytes:
    data = data if data is not None else marked(3)
    return data[: len(data) // 2]


def not_a_pdf() -> bytes:
    return b"this is a text file, not a PDF\n" * 20


def without_eof(data: bytes | None = None) -> bytes:
    data = data if data is not None else marked(2)
    stripped = data.rstrip()
    assert stripped.endswith(b"%%EOF")
    return stripped[: -len(b"%%EOF")]


def with_version(version: bytes, data: bytes | None = None) -> bytes:
    data = data if data is not None else marked(1)
    return re.sub(rb"^%PDF-\d\.\d", b"%PDF-" + version, data, count=1)


def with_size(size: int, data: bytes | None = None) -> bytes:
    """The trailer of the file claims ``size`` objects (it sits after the xref table)."""
    data = data if data is not None else marked(2)
    changed, count = re.subn(rb"/Size \d+", b"/Size %d" % size, data)
    assert count >= 1
    return changed


def wrong_count(count: int = 5) -> bytes:
    writer = PdfWriter()
    for i in range(3):
        writer.add_blank_page(width=marker_width(i + 1), height=HEIGHT)
    writer._pages.get_object()[NameObject("/Count")] = NumberObject(count)
    return _dump(writer)


def page_tree_cycle() -> bytes:
    """The page tree lists its own root among its kids."""
    writer = PdfWriter()
    for i in range(2):
        writer.add_blank_page(width=marker_width(i + 1), height=HEIGHT)
    root = writer._pages.get_object()
    root["/Kids"].append(writer._pages)
    return _dump(writer)


def wrong_parent() -> bytes:
    """A page whose /Parent does not point back at the node that lists it."""
    writer = PdfWriter()
    for i in range(2):
        writer.add_blank_page(width=marker_width(i + 1), height=HEIGHT)
    other = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Pages")}))
    writer.pages[1][NameObject("/Parent")] = other
    return _dump(writer)


def leaf_without_type() -> bytes:
    writer = PdfWriter()
    for i in range(2):
        writer.add_blank_page(width=marker_width(i + 1), height=HEIGHT)
    writer.pages[1][NameObject("/Type")] = NameObject("/Catalog")
    return _dump(writer)
