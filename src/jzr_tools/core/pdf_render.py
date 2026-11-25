"""Rendering PDF pages to pictures with pypdfium2.

``render_plan`` works out the names and pixel sizes from the page boxes
alone, so a dry run needs no renderer; ``render_page`` draws one page.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import math
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

    from jzr_tools.core.pdfdoc import PageBox

MAX_PIXELS = 16_000_000
MAX_EDGE = 8192
NOT_RENDERED = "the page could not be rendered"


@dataclass(frozen=True)
class PlannedPage:
    number: int  # 1-based, in the source
    target: Path
    width: int
    height: int
    problem: str | None = None


def digits(count: int) -> int:
    """How many digits page numbers get in names: at least 3."""
    return max(3, len(str(count)))


def page_name(stem: str, number: int, count: int, extension: str) -> str:
    return f"{stem}_page-{number:0{digits(count)}d}.{extension}"


def pixel_size(box: PageBox, dpi: int) -> tuple[int, int]:
    """The rendered size: the crop box turned by /Rotate, rounded up at ``dpi``."""
    left, bottom, right, top = box.crop
    width, height = right - left, top - bottom
    if box.rotation % 180 == 90:
        width, height = height, width
    # A tiny tolerance keeps 200.0000001 from becoming 201.
    return (
        max(1, math.ceil(width * dpi / 72 - 1e-6)),
        max(1, math.ceil(height * dpi / 72 - 1e-6)),
    )


def too_large(number: int, dpi: int, width: int, height: int) -> str | None:
    if width > MAX_EDGE or height > MAX_EDGE or width * height > MAX_PIXELS:
        return (
            f"page {number} is too large at {dpi} DPI ({width:,}x{height:,}); "
            "use a lower --dpi"
        )
    return None


def render_plan(
    source: Path,
    boxes: list[PageBox],
    pages: list[int],
    *,
    extension: str,
    dpi: int,
    outdir: Path | None,
) -> list[PlannedPage]:
    """One planned picture per page in ``pages`` (1-based), in that order."""
    folder = outdir if outdir is not None else source.parent
    plans = []
    for number in pages:
        width, height = pixel_size(boxes[number - 1], dpi)
        target = folder / page_name(source.stem, number, len(boxes), extension)
        plans.append(PlannedPage(number, target, width, height, too_large(number, dpi, width, height)))
    return plans


def open_document(data: bytes):  # noqa: ANN201
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    document.init_forms()
    return document


def render_page(document, plan: PlannedPage, *, dpi: int) -> Image.Image:  # noqa: ANN001
    """Draw one page on white, annotations and form fields included, at the planned size."""
    from PIL import Image

    page = document[plan.number - 1]
    try:
        bitmap = page.render(
            scale=dpi / 72,
            rotation=0,
            draw_annots=True,
            may_draw_forms=True,
            fill_color=(255, 255, 255, 255),
        )
        picture = bitmap.to_pil().convert("RGB")
    finally:
        page.close()
    if picture.size == (plan.width, plan.height):
        return picture
    if abs(picture.width - plan.width) > 1 or abs(picture.height - plan.height) > 1:
        raise ValueError(NOT_RENDERED)
    # pdfium rounds its own way: pad or cut the last pixel to the planned size.
    exact = Image.new("RGB", (plan.width, plan.height), (255, 255, 255))
    exact.paste(picture, (0, 0))
    return exact


def encode(picture: Image.Image, extension: str, *, dpi: int, quality: int) -> bytes:
    buffer = io.BytesIO()
    if extension == "jpg":
        picture.save(buffer, "JPEG", quality=quality, optimize=True, dpi=(dpi, dpi))
    else:
        picture.save(buffer, "PNG", compress_level=6, dpi=(dpi, dpi))
    return buffer.getvalue()
