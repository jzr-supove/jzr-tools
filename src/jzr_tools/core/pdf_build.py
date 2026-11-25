"""Building a PDF from pictures: one picture per page.

JPEGs go in as they are (only their metadata is cleaned), PNGs losslessly,
and every other format Pillow reads becomes PNG-like Flate data (with
transparency) or a JPEG. EXIF orientation of embedded JPEGs and PNGs is
honoured by the placement, so their stored pixels are never rotated.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

A4 = (210 * 72 / 25.4, 297 * 72 / 25.4)
LETTER = (612.0, 792.0)
MAX_PAGE = 14_400
NOT_AN_IMAGE = "not an image Pillow can read"
TOO_LARGE_FOR_PAGE = "image is too large for a page at 96 DPI"
CONVERT_QUALITY = 92
SMALLER_QUALITY = 75
SMALLER_EDGE = 2000
# Pillow's own decompression-bomb limit; jzr's image commands lift it, so it is checked here.
MAX_PICTURE_PIXELS = 2 * 89_478_485

# How the unit square of the stored pixels maps onto the upright picture for
# each EXIF orientation 1-8 (PDF coordinates, origin at the bottom left).
_ORIENTATION_MATRICES = (
    (1, 0, 0, 1, 0, 0),
    (-1, 0, 0, 1, 1, 0),
    (-1, 0, 0, -1, 1, 1),
    (1, 0, 0, -1, 0, 1),
    (0, -1, -1, 0, 1, 1),
    (0, -1, 1, 0, 0, 1),
    (0, 1, 1, 0, 0, 0),
    (0, 1, -1, 0, 1, 0),
)


class ImageError(Exception):
    """A picture that cannot go into the PDF; the text says why."""


@dataclass(frozen=True)
class Layout:
    page_width: float
    page_height: float
    draw_width: float
    draw_height: float
    x: float
    y: float
    matrix: tuple[float, float, float, float, float, float]


def layout(
    width: int, height: int, exif: int, paper: str, page_orientation: str, margin: float
) -> Layout:
    """Place a picture of ``width`` x ``height`` stored pixels on a page.

    ``margin`` is in millimetres and ignored by ``original``, which makes the
    page the upright picture's size at 96 DPI.
    """
    if not 1 <= exif <= 8:
        exif = 1
    swapped = exif >= 5
    upright_width, upright_height = (height, width) if swapped else (width, height)
    if paper == "original":
        page_width = upright_width * 72 / 96
        page_height = upright_height * 72 / 96
        gap = 0.0
    else:
        page_width, page_height = A4 if paper == "a4" else LETTER
        if page_orientation == "landscape" or (
            page_orientation == "auto" and upright_width > upright_height
        ):
            page_width, page_height = page_height, page_width
        gap = margin * 72 / 25.4
    scale = min(
        (page_width - 2 * gap) / upright_width, (page_height - 2 * gap) / upright_height
    )
    draw_width, draw_height = upright_width * scale, upright_height * scale
    x = (page_width - draw_width) / 2
    y = (page_height - draw_height) / 2
    a, b, c, d, e, f = _ORIENTATION_MATRICES[exif - 1]
    matrix = (
        a * draw_width,
        b * draw_height,
        c * draw_width,
        d * draw_height,
        e * draw_width + x,
        f * draw_height + y,
    )
    return Layout(page_width, page_height, draw_width, draw_height, x, y, matrix)


@dataclass
class Embedded:
    """One picture as PDF image data, with how to place it."""

    width: int
    height: int
    orientation: int
    data: bytes
    filter_name: str
    colour_space: str
    predicted: bool = False
    mask: bytes | None = None  # raw Flate gray samples, same size


def _orientation(picture: Image.Image) -> int:
    try:
        value = picture.getexif().get(0x0112, 1)
    except Exception:
        return 1
    return value if isinstance(value, int) and 1 <= value <= 8 else 1


def _lossless(picture: Image.Image, orientation: int) -> Embedded:
    """Flate data for an 8-bit picture, with its alpha as a soft mask when it has one."""
    from jzr_tools.core.pdf_streams import flate_predicted, flate_raw

    alpha = None
    if picture.mode in ("RGBA", "LA"):
        alpha = picture.getchannel("A")
        picture = picture.convert("RGB" if picture.mode == "RGBA" else "L")
        if alpha.getextrema() == (255, 255):
            alpha = None
    raw = flate_raw(picture.tobytes())
    predicted = flate_predicted(picture)
    use_predicted = len(predicted) < len(raw)
    return Embedded(
        width=picture.width,
        height=picture.height,
        orientation=orientation,
        data=predicted if use_predicted else raw,
        filter_name="/FlateDecode",
        colour_space="/DeviceRGB" if picture.mode == "RGB" else "/DeviceGray",
        predicted=use_predicted,
        mask=flate_raw(alpha.tobytes()) if alpha is not None else None,
    )


def _eight_bit(picture: Image.Image) -> Image.Image:
    """The picture as L, LA, RGB or RGBA, 16-bit tones scaled rather than clipped."""
    mode = picture.mode
    if mode in ("L", "LA", "RGB", "RGBA"):
        return picture
    if mode in ("I;16", "I;16B", "I;16L", "I;16N", "I"):
        return picture.convert("I").point(lambda value: value * (1 / 257)).convert("L")
    if mode == "1":
        return picture.convert("L")
    if mode == "P":
        return picture.convert("RGBA" if "transparency" in picture.info else "RGB")
    if mode == "PA":
        return picture.convert("RGBA")
    if mode in ("La",):
        return picture.convert("LA")
    if mode in ("RGBa",):
        return picture.convert("RGBA")
    return picture.convert("RGBA" if "A" in mode or "transparency" in picture.info else "RGB")


def _jpeg(picture: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    picture.save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _smaller_jpeg(picture: Image.Image) -> Image.Image:
    from PIL import Image

    longest = max(picture.size)
    if longest <= SMALLER_EDGE:
        return picture
    scale = SMALLER_EDGE / longest
    size = (max(1, round(picture.width * scale)), max(1, round(picture.height * scale)))
    return picture.resize(size, Image.LANCZOS)


def _as_jpeg(picture: Image.Image, data: bytes, orientation: int, smaller: bool) -> Embedded:
    """``data`` is the JPEG to use; with ``smaller`` a quality-75 copy replaces it when smaller."""
    width, height = picture.size
    if smaller:
        shrunk = _smaller_jpeg(picture)
        candidate = _jpeg(shrunk, SMALLER_QUALITY)
        if len(candidate) < len(data):
            data, (width, height) = candidate, shrunk.size
    return Embedded(
        width=width,
        height=height,
        orientation=orientation,
        data=data,
        filter_name="/DCTDecode",
        colour_space="/DeviceRGB" if picture.mode == "RGB" else "/DeviceGray",
    )


def read_image(path: Path, *, smaller: bool = False) -> Embedded:
    """Turn one picture file into PDF image data; raise ImageError or OSError otherwise."""
    import warnings

    from PIL import Image, ImageOps, UnidentifiedImageError

    from jzr_tools.core import pillow_engine
    from jzr_tools.core.image_strip import StripError, strip_bytes

    data = Path(path).read_bytes()
    pillow_engine._register_heif()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        try:
            picture = Image.open(io.BytesIO(data))
            pixels = picture.width * picture.height
            if pixels > MAX_PICTURE_PIXELS:
                raise ImageError(f"image is too large ({round(pixels / 1_000_000)} MP)")
            picture.load()
        except Image.DecompressionBombError as error:
            found = re.search(r"\((\d+) pixels\)", str(error))
            megapixels = round(int(found.group(1)) / 1_000_000) if found else 0
            raise ImageError(f"image is too large ({megapixels} MP)") from error
        except (UnidentifiedImageError, SyntaxError, ValueError, EOFError) as error:
            raise ImageError(NOT_AN_IMAGE) from error
        except OSError as error:
            if isinstance(error, (FileNotFoundError, PermissionError)):
                raise
            raise ImageError(NOT_AN_IMAGE) from error

        kind = picture.format
        orientation = _orientation(picture)
        if kind == "JPEG" and picture.mode in ("L", "RGB"):
            try:
                cleaned = strip_bytes(data).data
            except StripError:
                cleaned = data
            return _as_jpeg(picture, cleaned, orientation, smaller)
        if kind == "PNG":
            return _lossless(_eight_bit(picture), orientation)

        # Any other format: turned upright, then lossless with alpha, else JPEG.
        try:
            upright = ImageOps.exif_transpose(picture)
        except Exception:
            upright = picture
        upright = _eight_bit(upright)
        if upright.mode in ("RGBA", "LA"):
            if upright.getchannel("A").getextrema() != (255, 255):
                return _lossless(upright, 1)
            upright = upright.convert("RGB" if upright.mode == "RGBA" else "L")
        if upright.mode not in ("RGB", "L"):
            upright = upright.convert("RGB")
        return _as_jpeg(upright, _jpeg(upright, CONVERT_QUALITY), 1, smaller)


def check_page(embedded: Embedded, paper: str) -> None:
    if paper != "original":
        return
    swapped = embedded.orientation >= 5
    width, height = (embedded.height, embedded.width) if swapped else (embedded.width, embedded.height)
    if max(width, height) * 72 / 96 > MAX_PAGE:
        raise ImageError(TOO_LARGE_FOR_PAGE)


def build(
    pictures: list[Embedded], *, paper: str, page_orientation: str, margin: float
) -> tuple[bytes, list]:
    """The PDF bytes and the expected page boxes."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        EncodedStreamObject,
        NameObject,
        NumberObject,
    )

    from jzr_tools.core.pdfdoc import PageBox

    def image_stream(width: int, height: int, data: bytes, filter_name: str, space: str) -> EncodedStreamObject:
        stream = EncodedStreamObject()
        stream[NameObject("/Type")] = NameObject("/XObject")
        stream[NameObject("/Subtype")] = NameObject("/Image")
        stream[NameObject("/Width")] = NumberObject(width)
        stream[NameObject("/Height")] = NumberObject(height)
        stream[NameObject("/ColorSpace")] = NameObject(space)
        stream[NameObject("/BitsPerComponent")] = NumberObject(8)
        stream[NameObject("/Filter")] = NameObject(filter_name)
        stream._data = data
        return stream

    writer = PdfWriter()
    boxes = []
    for picture in pictures:
        placed = layout(picture.width, picture.height, picture.orientation, paper, page_orientation, margin)
        page = writer.add_blank_page(placed.page_width, placed.page_height)
        stream = image_stream(
            picture.width, picture.height, picture.data, picture.filter_name, picture.colour_space
        )
        if picture.predicted:
            stream[NameObject("/DecodeParms")] = DictionaryObject(
                {
                    NameObject("/Predictor"): NumberObject(15),
                    NameObject("/Colors"): NumberObject(3 if picture.colour_space == "/DeviceRGB" else 1),
                    NameObject("/BitsPerComponent"): NumberObject(8),
                    NameObject("/Columns"): NumberObject(picture.width),
                }
            )
        if picture.mask is not None:
            mask = image_stream(picture.width, picture.height, picture.mask, "/FlateDecode", "/DeviceGray")
            stream[NameObject("/SMask")] = writer._add_object(mask)
        reference = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): reference})}
        )
        content = DecodedStreamObject()
        numbers = " ".join(f"{value:.4f}".rstrip("0").rstrip(".") or "0" for value in placed.matrix)
        content.set_data(f"q {numbers} cm /Im0 Do Q".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
        box = tuple(float(value) for value in page.mediabox)
        boxes.append(PageBox(box, box, 0))  # type: ignore[arg-type]
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue(), boxes
