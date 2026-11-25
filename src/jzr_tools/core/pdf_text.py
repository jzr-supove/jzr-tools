"""Reading text from PDF pages with pypdfium2.

``plain_text`` gives pdfium's own reading order. ``text_runs`` turns the
characters of a page into word-sized runs with positions, for
``pdf_markdown.layout_page``. pypdfium2 is imported inside the functions.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
import re
from typing import TYPE_CHECKING, NamedTuple
import unicodedata

from jzr_tools.core.pdf_markdown import NoText, TextRun, safe_url

if TYPE_CHECKING:
    from jzr_tools.core.pdf_markdown import TextPage

__all__ = ["NoText", "PageRuns", "PlainText", "markdown_pages", "plain_text", "safe_url", "text_runs"]

_DROPPED = {"\r", "\n", "￾", "\x02"}
_FORCE_BOLD = 0x40000
_BOLD_NAME = re.compile(r"bold|demi|(?:black|heavy)(?:italic|oblique|mt)?(?:$|[-_,\s])", re.I)
_ROTATED = 0.1  # radians


class PageRuns(NamedTuple):
    runs: list[TextRun]
    rotated: bool
    fonts_missing: bool
    width: float  # of the page as shown, /Rotate applied


class _Placement:
    """Maps PDF user space to the page as shown: /Rotate applied, origin top left, y down."""

    def __init__(self, crop: tuple[float, float, float, float], rotation: int) -> None:
        self.left, self.bottom, right, top = crop
        self.page_width = right - self.left
        self.page_height = top - self.bottom
        self.rotation = rotation % 360
        turned = self.rotation in (90, 270)
        self.width = self.page_height if turned else self.page_width

    def point(self, x: float, y: float) -> tuple[float, float]:
        u, v = x - self.left, y - self.bottom
        if self.rotation == 90:
            return v, u
        if self.rotation == 180:
            return self.page_width - u, v
        if self.rotation == 270:
            return self.page_height - v, self.page_width - u
        return u, self.page_height - v

    def box(self, x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
        (ax, ay), (bx, by) = self.point(x0, y0), self.point(x1, y1)
        return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)

    def angle(self, angle: float) -> float:
        """pdfium's character angle (clockwise, radians) as shown, counter-clockwise in -pi..pi."""
        turned = (-angle - math.radians(self.rotation)) % (2 * math.pi)
        return turned - 2 * math.pi if turned > math.pi else turned


@dataclass
class PlainText:
    text: str
    empty_pages: list[int]


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("￾", "").replace("\x02", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


NOT_READ = "the text could not be read"


def open_document(data: bytes):  # noqa: ANN201
    import pypdfium2 as pdfium

    return pdfium.PdfDocument(data)


def plain_text(document, pages: list[int]) -> PlainText:  # noqa: ANN001
    """The text of ``pages`` (1-based), joined by a form feed on its own line."""
    parts: list[str] = []
    empty: list[int] = []
    for number in pages:
        page = document[number - 1]
        try:
            textpage = page.get_textpage()
            try:
                text = _tidy(textpage.get_text_range())
            finally:
                textpage.close()
        finally:
            page.close()
        if text:
            parts.append(text)
        else:
            empty.append(number)
    if not parts:
        raise NoText()
    return PlainText("\n\f\n".join(parts) + "\n", empty)


def count_characters(document, pages: list[int]) -> int:  # noqa: ANN001
    total = 0
    for number in pages:
        page = document[number - 1]
        try:
            textpage = page.get_textpage()
            try:
                total += textpage.count_chars()
            finally:
                textpage.close()
        finally:
            page.close()
    return total


def _direction(character: str) -> str | None:
    kind = unicodedata.bidirectional(character)
    if kind in ("R", "AL"):
        return "R"
    if kind == "L":
        return "L"
    return None


def _is_bold(name: str, flags: int, weight: int) -> bool:
    return bool(flags & _FORCE_BOLD) or weight >= 600 or _BOLD_NAME.search(name) is not None


@dataclass
class _Char:
    text: str
    x: float
    y: float
    right: float
    size: float
    font: str
    bold: bool
    direction: str | None


def _links(document, page, place: _Placement) -> list[tuple[float, float, float, float, str]]:  # noqa: ANN001
    """URI links of the page as (x0, y0, x1, y1) on the page as shown, with the address."""
    import pypdfium2.raw as pdfium_c

    found = []
    position = ctypes.c_int(0)
    link = pdfium_c.FPDF_LINK()
    while pdfium_c.FPDFLink_Enumerate(page.raw, position, link):
        action = pdfium_c.FPDFLink_GetAction(link)
        if not action or pdfium_c.FPDFAction_GetType(action) != pdfium_c.PDFACTION_URI:
            continue
        size = pdfium_c.FPDFAction_GetURIPath(document.raw, action, None, 0)
        if size <= 1 or size > 65536:
            continue
        buffer = ctypes.create_string_buffer(size)
        pdfium_c.FPDFAction_GetURIPath(document.raw, action, buffer, size)
        address = buffer.value.decode("latin-1")
        rect = pdfium_c.FS_RECTF()
        if not pdfium_c.FPDFLink_GetAnnotRect(link, rect):
            continue
        found.append((*place.box(rect.left, rect.bottom, rect.right, rect.top), address))
    return found


def text_runs(page) -> PageRuns:  # noqa: ANN001
    """The word-sized runs of one pdfium page, with y measured down to the baseline.

    Positions are on the page as it is shown: from the crop box's top left,
    with /Rotate applied, so a page stored sideways reads normally.

    A run ends at a space (kept as its last character), a change of font,
    size, line, link or writing direction, or a gap wider than 0.12 of the
    font size. Characters pdfium makes up, such as line breaks, are left out.
    """
    import pypdfium2.raw as pdfium_c

    place = _Placement(page.get_cropbox(), page.get_rotation())
    textpage = page.get_textpage()
    chars: list[_Char] = []
    rotated = False
    fonts_missing = False
    try:
        handle = textpage.raw
        count = pdfium_c.FPDFText_CountChars(handle)
        origin_x, origin_y = ctypes.c_double(), ctypes.c_double()
        box = pdfium_c.FS_RECTF()
        matrix = pdfium_c.FS_MATRIX()
        name_buffer = ctypes.create_string_buffer(256)
        flags = ctypes.c_int()
        glyph_width = ctypes.c_float()
        index = 0
        while index < count:
            current = index
            index += 1
            code = pdfium_c.FPDFText_GetUnicode(handle, current)
            if 0xD800 <= code < 0xDC00 and index < count:
                low = pdfium_c.FPDFText_GetUnicode(handle, index)
                if 0xDC00 <= low < 0xE000:
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    index += 1
            if 0xD800 <= code < 0xE000:
                continue
            text = chr(code)
            if text in _DROPPED or pdfium_c.FPDFText_IsGenerated(handle, current) == 1:
                continue
            if 0xFB00 <= code <= 0xFB06:
                text = unicodedata.normalize("NFKC", text)
            pdfium_c.FPDFText_GetCharOrigin(handle, current, origin_x, origin_y)
            pdfium_c.FPDFText_GetLooseCharBox(handle, current, box)
            font_size = size = pdfium_c.FPDFText_GetFontSize(handle, current)
            across = 1.0
            if pdfium_c.FPDFText_GetMatrix(handle, current, matrix):
                across = math.hypot(matrix.a, matrix.b)
                scale = math.hypot(matrix.c, matrix.d)
                if scale > 0 and abs(scale - 1) > 1e-9:
                    size *= scale
            # The font's advance gives the right edge. pdfium's boxes measure
            # text drawn sideways (as on a page stored with /Rotate) across the
            # wrong axis, so the loose box is only the fallback.
            advance = None
            text_object = pdfium_c.FPDFText_GetTextObject(handle, current)
            font_handle = pdfium_c.FPDFTextObj_GetFont(text_object) if text_object else None
            if (
                font_handle
                and pdfium_c.FPDFFont_GetGlyphWidth(font_handle, code, ctypes.c_float(font_size), glyph_width)
                and glyph_width.value > 0
            ):
                advance = glyph_width.value * across
            length = pdfium_c.FPDFText_GetFontInfo(handle, current, name_buffer, 256, flags)
            if length > 0:
                font = name_buffer.value.decode("latin-1", "replace")
                bold = _is_bold(font, flags.value, pdfium_c.FPDFText_GetFontWeight(handle, current))
            else:
                font, bold = "", False
                fonts_missing = True
            angle = pdfium_c.FPDFText_GetCharAngle(handle, current)
            if angle >= 0 and abs(place.angle(angle)) > _ROTATED and not text.isspace():
                rotated = True
            x, y = place.point(origin_x.value, origin_y.value)
            edge = x + advance if advance is not None else place.box(box.left, box.bottom, box.right, box.top)[2]
            chars.append(
                _Char(
                    text,
                    x,
                    y,
                    edge,
                    size,
                    font,
                    bold,
                    _direction(text),
                )
            )
        links = _links(page.pdf, page, place)
    finally:
        textpage.close()

    runs: list[TextRun] = []
    group: list[_Char] = []
    direction: str | None = None

    def flush() -> None:
        nonlocal direction
        if group:
            text = "".join(item.text for item in group)
            if text.strip():
                first = group[0]
                width = max(item.right for item in group) - first.x
                strong = [item.direction for item in group if item.direction]
                rtl = strong.count("R") > len(strong) / 2 if strong else False
                if rtl:
                    text = text[::-1]
                runs.append(TextRun(text, first.x, first.y, max(width, 0.0), first.size, rtl, None, first.bold))
            group.clear()
        direction = None

    for char in chars:
        if group:
            previous = group[-1]
            if (
                char.font != previous.font
                or abs(char.size - previous.size) > 1e-6
                or abs(char.y - previous.y) > 0.35 * previous.size
                or char.x - previous.right > 0.12 * previous.size
                or char.x < previous.x
                or (char.direction and direction and char.direction != direction)
            ):
                flush()
        group.append(char)
        if char.direction and direction is None:
            direction = char.direction
        if char.text.isspace():
            flush()
    flush()

    if links:
        for number, run in enumerate(runs):
            cx, cy = run.x + run.width / 2, run.y - run.size / 2
            for x0, y0, x1, y1, address in links:
                if x0 <= cx <= x1 and y0 <= cy <= y1:
                    runs[number] = TextRun(
                        run.text, run.x, run.y, run.width, run.size, run.rtl, address, run.bold
                    )
                    break
    return PageRuns(runs, rotated, fonts_missing, place.width)


def markdown_pages(document, pages: list[int]) -> tuple[list[TextPage], bool]:  # noqa: ANN001
    """Laid-out pages for ``pdf_markdown.pages_to_markdown``, and whether font data was missing."""
    from jzr_tools.core.pdf_markdown import layout_page

    laid_out = []
    fonts_missing = False
    for number in pages:
        page = document[number - 1]
        try:
            found = text_runs(page)
            laid_out.append(layout_page(number, found.width, found.runs, found.rotated))
        finally:
            page.close()
        fonts_missing = fonts_missing or found.fonts_missing
    return laid_out, fonts_missing
