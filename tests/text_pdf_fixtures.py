"""Builders for PDFs with positioned text, for the ``jzr pdf text`` tests.

Pages are 612 x 792 points. Positions are given from the top of the page:
``top`` is the baseline, measured down from the top edge, which is also how
the text reader reports it. Text is drawn with the standard fonts Helvetica
(``/F1``) and Helvetica-Bold (``/F2``) in WinAnsi encoding, so no font file is
embedded.
"""

from __future__ import annotations

from dataclasses import dataclass
import io

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

WIDTH = 612
HEIGHT = 792


@dataclass(frozen=True)
class Line:
    """One piece of text; ``bold`` picks Helvetica-Bold."""

    text: str
    x: float
    top: float
    size: float = 12
    bold: bool = False


@dataclass(frozen=True)
class Link:
    """A link annotation over a box, given from the top of the page."""

    x0: float
    top0: float
    x1: float
    top1: float
    uri: str


def _font(writer: PdfWriter, name: str):  # noqa: ANN202
    return writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject(name),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )


def _escape(text: str) -> bytes:
    data = text.encode("cp1252")
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _show(line: Line) -> bytes:
    font = b"/F2" if line.bold else b"/F1"
    return (
        b"BT " + font + (" %g Tf %g %g Td (" % (line.size, line.x, HEIGHT - line.top)).encode()
        + _escape(line.text) + b") Tj ET\n"
    )


def _add_page(writer: PdfWriter, fonts: DictionaryObject, content: bytes, links: list[Link]) -> None:
    page = writer.add_blank_page(width=WIDTH, height=HEIGHT)
    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
    if links:
        annotations = []
        for link in links:
            rect = [link.x0, HEIGHT - link.top1, link.x1, HEIGHT - link.top0]
            annotations.append(
                writer._add_object(
                    DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Annot"),
                            NameObject("/Subtype"): NameObject("/Link"),
                            NameObject("/Rect"): ArrayObject([NumberObject(v) for v in rect]),
                            NameObject("/Border"): ArrayObject([NumberObject(0)] * 3),
                            NameObject("/A"): DictionaryObject(
                                {
                                    NameObject("/S"): NameObject("/URI"),
                                    NameObject("/URI"): TextStringObject(link.uri),
                                }
                            ),
                        }
                    )
                )
            )
        page[NameObject("/Annots")] = ArrayObject(annotations)


def _dump(writer: PdfWriter) -> bytes:
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def build(
    pages: list[list[Line]], links: dict[int, list[Link]] | None = None
) -> bytes:
    """One page per list of lines; ``links`` maps a 0-based page index to its links."""
    writer = PdfWriter()
    fonts = DictionaryObject(
        {
            NameObject("/F1"): _font(writer, "/Helvetica"),
            NameObject("/F2"): _font(writer, "/Helvetica-Bold"),
        }
    )
    for index, lines in enumerate(pages):
        content = b"".join(_show(line) for line in lines)
        _add_page(writer, fonts, content, (links or {}).get(index, []))
    return _dump(writer)


def raw(content: bytes, crop: tuple[float, float, float, float] | None = None) -> bytes:
    """One page whose content stream is exactly ``content`` (fonts /F1 and /F2 exist)."""
    writer = PdfWriter()
    fonts = DictionaryObject(
        {
            NameObject("/F1"): _font(writer, "/Helvetica"),
            NameObject("/F2"): _font(writer, "/Helvetica-Bold"),
        }
    )
    _add_page(writer, fonts, content, [])
    if crop is not None:
        writer.pages[0][NameObject("/CropBox")] = ArrayObject([NumberObject(v) for v in crop])
    return _dump(writer)


def scaled_text() -> bytes:
    """Text set at 12 points under a 2 x 2 scale, so it is drawn at 24 points."""
    return raw(b"q 2 0 0 2 0 0 cm BT /F1 12 Tf 20 300 Td (Scaled words) Tj ET Q\n")


def rotated_text() -> bytes:
    """A heading line and a sentence turned a quarter turn."""
    return raw(
        b"BT /F1 12 Tf 40 700 Td (Straight line of text) Tj ET\n"
        b"BT /F1 12 Tf 0 1 -1 0 300 200 Tm (Turned sideways here) Tj ET\n"
    )


def sideways_page() -> bytes:
    """A page stored with /Rotate 90 whose text runs up the page, so it reads across when shown.

    Shown, the page is 792 wide; the text starts 200 from the left and its
    baseline is 300 down from the top.
    """
    from pypdf import PdfReader

    data = raw(b"BT /F1 12 Tf 0 1 -1 0 300 200 Tm (Turned sideways here) Tj ET\n")
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.pages[0][NameObject("/Rotate")] = NumberObject(90)
    return _dump(writer)


def cropped_text(crop: tuple[float, float, float, float], x: float, y: float, text: str) -> bytes:
    """``text`` at PDF coordinates (x, y) on a page with the given CropBox."""
    return raw(b"BT /F1 12 Tf %g %g Td (" % (x, y) + _escape(text) + b") Tj ET\n", crop)


def with_spaces_only() -> bytes:
    """A page whose only drawn text is white space, then one word."""
    return raw(b"BT /F1 12 Tf 40 700 Td (     ) Tj ET\nBT /F1 12 Tf 40 600 Td (Word) Tj ET\n")


def huge_text(rows: int = 2200, per_row: int = 2400) -> bytes:
    """Page 1 holds ``rows * per_row`` letters (over five million by default), most of
    them below the page edge; page 2 has one short line."""
    writer = PdfWriter()
    fonts = DictionaryObject({NameObject("/F1"): _font(writer, "/Helvetica")})
    parts = [b"BT /F1 1 Tf 1 TL 10 780 Td\n"]
    row = b"(" + b"i" * per_row + b") Tj T*\n"
    parts.extend([row] * rows)
    parts.append(b"ET\n")
    _add_page(writer, fonts, b"".join(parts), [])
    _add_page(writer, fonts, _show(Line("Short page", 40, 100)), [])
    return _dump(writer)
