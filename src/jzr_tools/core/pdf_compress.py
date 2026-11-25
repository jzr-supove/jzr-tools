"""Compressing PDFs: levels, image rules and one file.

The whole document is cloned with pypdf, so text, fonts, links, bookmarks,
forms and annotations are kept as they are. What changes: page thumbnails
and unused objects go, unfiltered streams get zlib, identical objects are
stored once and, unless the level is lossless, images are re-encoded at the
size they are drawn. A result is kept only when it is smaller.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from jzr_tools.core.batch import ConversionResult, _path_key

if TYPE_CHECKING:
    from PIL import Image
    from pypdf import PdfWriter
    from pypdf.generic import StreamObject


@dataclass(frozen=True)
class Level:
    images: bool
    dpi: int = 0
    quality: int = 0
    cap_edge: int = 0


LEVELS: dict[str, Level] = {
    "lossless": Level(images=False),
    "recommended": Level(images=True, dpi=150, quality=75, cap_edge=2000),
    "strong": Level(images=True, dpi=100, quality=60, cap_edge=1400),
}
DEFAULT_LEVEL = "recommended"
COMPRESSED_STEM = "_compressed"
ALREADY_COMPACT = "already compact (no smaller version found)"
# Larger images are left as they are rather than decoded.
MAX_PIXELS = 200_000_000
# Unfiltered streams shorter than this are not worth a filter.
_SMALL_STREAM = 64

_RGB_SPACES = frozenset({"/DeviceRGB", "/CalRGB"})
_GRAY_SPACES = frozenset({"/DeviceGray", "/CalGray"})


def target_pixels(
    width: int, height: int, drawn: tuple[float, float] | None, level: Level
) -> tuple[int, int]:
    """The size to re-encode at: never larger, aspect ratio kept, at least 1 px a side.

    The level's DPI at the drawn size (the more demanding side decides) and
    its longest-side cap both apply; without a drawn size only the cap does.
    """
    if not level.images or width < 1 or height < 1:
        return width, height
    scale = level.cap_edge / max(width, height)
    if drawn is not None and drawn[0] > 0 and drawn[1] > 0:
        needed = max(
            level.dpi * drawn[0] / 72 / width,
            level.dpi * drawn[1] / 72 / height,
        )
        scale = min(scale, needed)
    if not scale < 1:
        return width, height
    return (
        min(width, max(1, round(width * scale))),
        min(height, max(1, round(height * scale))),
    )


# ---- image rules


class Plan(NamedTuple):
    kind: str  # "jpeg" or "flate"
    channels: int


def _resolved(value: object) -> object:
    from jzr_tools.core.pdfdoc import _resolve

    try:
        return _resolve(value)
    except Exception:
        return None


def _number(value: object) -> float | None:
    value = _resolved(value)
    if isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _filters(stream: StreamObject) -> list[str] | None:
    from pypdf.generic import ArrayObject, NameObject

    value = _resolved(stream.get("/Filter"))
    if value is None:
        return []
    if isinstance(value, NameObject):
        return [str(value)]
    if isinstance(value, ArrayObject):
        names = [_resolved(item) for item in value]
        return [str(name) for name in names] if all(isinstance(n, NameObject) for n in names) else None
    return None


def _colour_space(stream: StreamObject) -> tuple[str, int | None]:
    from pypdf.generic import ArrayObject, NameObject, StreamObject

    value = _resolved(stream.get("/ColorSpace"))
    if isinstance(value, NameObject):
        return str(value), None
    if isinstance(value, ArrayObject) and len(value) > 0:
        first = _resolved(value[0])
        if not isinstance(first, NameObject):
            return "?", None
        if first == "/ICCBased" and len(value) > 1:
            profile = _resolved(value[1])
            if isinstance(profile, StreamObject):
                n = _number(profile.get("/N"))
                return "/ICCBased", int(n) if n is not None else None
        return str(first), None
    return ("?" if value is not None else ""), None


def _default_decode(stream: StreamObject) -> bool:
    from pypdf.generic import ArrayObject

    value = _resolved(stream.get("/Decode"))
    if value is None:
        return True
    if not isinstance(value, ArrayObject):
        return False
    return all(_number(item) == (i % 2) for i, item in enumerate(value))


def classify(stream: StreamObject) -> Plan | str:
    """How an image can be re-encoded, or why it is left as it is."""
    from pypdf.generic import ArrayObject

    mask = _resolved(stream.get("/ImageMask"))
    if mask is True or getattr(mask, "value", None) is True:
        return "stencil-mask"
    space, components = _colour_space(stream)
    if space == "/DeviceCMYK" or (space == "/ICCBased" and components == 4):
        return "cmyk"
    filters = _filters(stream)
    if filters is None or len(filters) > 1 or (filters and filters[0] not in ("/DCTDecode", "/FlateDecode")):
        return "encoding"
    if space in _RGB_SPACES:
        channels = 3
    elif space in _GRAY_SPACES:
        channels = 1
    elif space == "/ICCBased" and components in (1, 3):
        channels = components
    else:
        return "colour-space"
    if _number(stream.get("/BitsPerComponent")) != 8:
        return "bit-depth"
    if not _default_decode(stream):
        return "custom-decode"
    if isinstance(_resolved(stream.get("/Mask")), ArrayObject):
        return "custom-decode"  # a colour key matches exact sample values
    return Plan("jpeg" if filters == ["/DCTDecode"] else "flate", channels)


def is_photographic(image: Image.Image) -> bool:
    """Photo or flat graphic, from a nearest-neighbour sample of at most 128 x 128 pixels.

    Photos have many distinct colours and few runs of equal neighbours.
    """
    from PIL import Image

    columns, rows = min(128, image.width), min(128, image.height)
    sample = image.resize((columns, rows), Image.NEAREST) if (columns, rows) != image.size else image
    channels = 3 if sample.mode == "RGB" else 1
    data = sample.tobytes()
    count = len(data) // channels
    if count < 2:
        return False
    if channels == 3:
        colours = [data[i : i + 3] for i in range(0, count * 3, 3)]
    else:
        colours = list(data)
    distinct = len(set(colours))
    repeats = sum(1 for a, b in zip(colours, colours[1:]) if a == b)
    possible = count if channels == 3 else min(count, 256)
    return distinct / possible > 0.25 and repeats / count < 0.5


def _decode(stream: StreamObject, plan: Plan) -> Image.Image | None:
    """An 8-bit L or RGB picture of the image, or None when its data cannot be read."""
    from PIL import Image

    from jzr_tools.core.pdf_content import _decoded
    from jzr_tools.core.pdf_streams import jpeg_components

    width = _number(stream.get("/Width"))
    height = _number(stream.get("/Height"))
    if width is None or height is None or not (width.is_integer() and height.is_integer()):
        return None
    width, height = int(width), int(height)
    if width < 1 or height < 1 or width * height > MAX_PIXELS:
        return None
    mode = "RGB" if plan.channels == 3 else "L"
    raw = stream._data  # type: ignore[attr-defined]
    try:
        if plan.kind == "jpeg":
            if jpeg_components(raw) != plan.channels:
                return None
            picture = Image.open(io.BytesIO(raw))
            if picture.format != "JPEG" or picture.size != (width, height):
                return None
            picture.load()
            if picture.mode != mode:
                return None
            return picture
        if "/Filter" not in stream:
            pixels = raw
        else:
            parms = _resolved(stream.get("/DecodeParms"))
            if parms is None:
                pixels = _decoded(stream)
            else:
                from pypdf.generic import ArrayObject, DictionaryObject

                if isinstance(parms, ArrayObject):
                    parms = _resolved(parms[0]) if len(parms) else None
                if isinstance(parms, DictionaryObject):
                    predictor = _number(parms.get("/Predictor")) or 1
                    if predictor > 1 and (
                        (_number(parms.get("/Colors")) or 1) != plan.channels
                        or (_number(parms.get("/BitsPerComponent")) or 8) != 8
                        or (_number(parms.get("/Columns")) or 1) != width
                    ):
                        return None
                pixels = stream.get_data()
        size = width * height * plan.channels
        if pixels is None or len(pixels) < size:
            return None
        return Image.frombytes(mode, (width, height), bytes(pixels[:size]))
    except Exception:  # a decoder error of any kind leaves the image alone
        return None


def _replace(
    writer: PdfWriter,
    number: int,
    original: StreamObject,
    data: bytes,
    *,
    width: int,
    height: int,
    filter_name: str,
    predicted_channels: int | None = None,
) -> None:
    """Put a new image stream at the same object number, keeping the other keys."""
    from pypdf.generic import (
        DictionaryObject,
        EncodedStreamObject,
        IndirectObject,
        NameObject,
        NumberObject,
    )

    stream = EncodedStreamObject()
    for key, value in original.items():
        if key not in ("/Length", "/DecodeParms", "/Filter"):
            stream[NameObject(key)] = value
    stream[NameObject("/Width")] = NumberObject(width)
    stream[NameObject("/Height")] = NumberObject(height)
    stream[NameObject("/BitsPerComponent")] = NumberObject(8)
    stream[NameObject("/Filter")] = NameObject(filter_name)
    if predicted_channels is not None:
        stream[NameObject("/DecodeParms")] = DictionaryObject(
            {
                NameObject("/Predictor"): NumberObject(15),
                NameObject("/Colors"): NumberObject(predicted_channels),
                NameObject("/BitsPerComponent"): NumberObject(8),
                NameObject("/Columns"): NumberObject(width),
            }
        )
    stream._data = data
    stream.indirect_reference = IndirectObject(number, 0, writer)
    writer._objects[number - 1] = stream


def _lossless(picture: Image.Image) -> tuple[bytes, bool]:
    """Raw or PNG-predicted Flate data, whichever is smaller, and whether it is predicted."""
    from jzr_tools.core.pdf_streams import flate_predicted, flate_raw

    raw = flate_raw(picture.tobytes())
    predicted = flate_predicted(picture)
    return (predicted, True) if len(predicted) < len(raw) else (raw, False)


@dataclass
class _Counts:
    resized: int = 0
    reencoded: int = 0
    left: int = 0

    def text(self) -> str:
        parts = []
        if self.resized:
            parts.append(f"{self.resized} image{'s' if self.resized != 1 else ''} resized")
        if self.reencoded:
            parts.append(f"{self.reencoded} re-encoded")
        if self.left:
            parts.append(f"{self.left} left as they were")
        return ", ".join(parts)


def _shrink_mask(
    writer: PdfWriter, number: int, size: tuple[int, int]
) -> None:
    """Scale a soft mask down to its image's new size; on any failure it stays as it was."""
    from PIL import Image
    from pypdf.generic import StreamObject

    from jzr_tools.core.pdf_streams import flate_raw

    mask = writer._objects[number - 1] if 0 < number <= len(writer._objects) else None
    if not isinstance(mask, StreamObject):
        return
    plan = classify(mask)
    if not isinstance(plan, Plan) or plan.channels != 1:
        return
    picture = _decode(mask, plan)
    if picture is None or (picture.width <= size[0] and picture.height <= size[1]):
        return
    try:
        smaller = picture.resize(size, Image.LANCZOS)
        data = flate_raw(smaller.tobytes())
    except Exception:
        return
    _replace(writer, number, mask, data, width=size[0], height=size[1], filter_name="/FlateDecode")


def _images(writer: PdfWriter, level: Level, counts: _Counts) -> None:
    from PIL import Image
    from pypdf.generic import IndirectObject, StreamObject

    from jzr_tools.core.pdf_content import drawn_sizes

    masks: set[int] = set()
    images: list[int] = []
    for index, item in enumerate(writer._objects):
        if not isinstance(item, StreamObject):
            continue
        for key in ("/SMask", "/Mask"):
            value = item.get(key)
            if isinstance(value, IndirectObject):
                masks.add(value.idnum)
        if item.get("/Subtype") == "/Image":
            images.append(index + 1)
    bases = [number for number in images if number not in masks]
    if not bases:
        return
    sizes = drawn_sizes(writer)
    shrunk_masks: set[int] = set()
    for number in bases:
        stream = writer._objects[number - 1]
        plan = classify(stream)  # type: ignore[arg-type]
        if not isinstance(plan, Plan):
            counts.left += 1
            continue
        picture = _decode(stream, plan)  # type: ignore[arg-type]
        if picture is None:
            counts.left += 1
            continue
        target = target_pixels(picture.width, picture.height, sizes.get(number), level)
        resize = target != picture.size
        try:
            photo = plan.kind == "jpeg" or is_photographic(picture)
            pixels = picture.resize(target, Image.LANCZOS) if resize else picture
            predicted = False
            if photo:
                buffer = io.BytesIO()
                pixels.save(buffer, "JPEG", quality=level.quality, optimize=True)
                data, filter_name = buffer.getvalue(), "/DCTDecode"
            else:
                data, predicted = _lossless(pixels)
                filter_name = "/FlateDecode"
        except Exception:
            counts.left += 1
            continue
        if len(data) >= len(stream._data):  # type: ignore[union-attr]
            counts.left += 1
            continue
        _replace(
            writer,
            number,
            stream,  # type: ignore[arg-type]
            data,
            width=target[0],
            height=target[1],
            filter_name=filter_name,
            predicted_channels=plan.channels if predicted else None,
        )
        if resize:
            counts.resized += 1
            mask = stream.get("/SMask")  # type: ignore[union-attr]
            if isinstance(mask, IndirectObject) and mask.idnum not in shrunk_masks:
                shrunk_masks.add(mask.idnum)
                _shrink_mask(writer, mask.idnum, target)
        else:
            counts.reencoded += 1


def _flate_unfiltered(writer: PdfWriter) -> None:
    from pypdf.generic import EncodedStreamObject, NameObject, StreamObject

    from jzr_tools.core.pdf_streams import flate_raw

    for index, item in enumerate(writer._objects):
        if not isinstance(item, StreamObject) or "/Filter" in item:
            continue
        if item.get("/Type") == "/Metadata":
            continue  # XMP stays readable as text
        raw = item._data
        if len(raw) < _SMALL_STREAM:
            continue
        packed = flate_raw(raw)
        if len(packed) >= len(raw):
            continue
        stream = EncodedStreamObject()
        for key, value in item.items():
            if key not in ("/Length", "/DecodeParms"):
                stream[NameObject(key)] = value
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
        stream._data = packed
        stream.indirect_reference = item.indirect_reference
        writer._objects[index] = stream


def _strip(writer: PdfWriter) -> None:
    writer.metadata = None
    root = writer._root_object
    for key in ("/Metadata", "/PieceInfo"):
        if key in root:
            del root[key]
    for page in writer.pages:
        if "/PieceInfo" in page:
            del page["/PieceInfo"]


def build(checked: object, level: Level, *, strip: bool) -> tuple[bytes, _Counts]:
    """The compressed document, written to memory, and what happened to its images."""
    from pypdf import PdfWriter

    counts = _Counts()
    writer = PdfWriter(clone_from=checked.reader)  # type: ignore[attr-defined]
    for page in writer.pages:
        if "/Thumb" in page:
            del page["/Thumb"]
    if strip:
        _strip(writer)
    if level.images:
        _images(writer, level, counts)
    _flate_unfiltered(writer)
    writer.compress_identical_objects()
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue(), counts


def compress_file(
    source: Path,
    target: Path,
    *,
    level: str,
    strip: bool,
    overwrite: bool,
    dry_run: bool,
) -> tuple[ConversionResult, list[str]]:
    """Compress one PDF into ``target``; returns what happened and the notes, never raises."""
    from jzr_tools.core import pdfdoc
    from jzr_tools.core.image_compress import change_text
    from jzr_tools.core.media import format_size

    def done(status: str, detail: str = "") -> ConversionResult:
        return ConversionResult(source, target, (), status, detail)

    if _path_key(source) == _path_key(target) or _same_file(source, target):
        return done("skipped", "source is the target"), []
    if target.exists() and not overwrite:
        return done("skipped", "target exists (use --overwrite)"), []
    try:
        checked = pdfdoc.open_checked(source)
    except pdfdoc.PdfError as error:
        return done("failed", str(error)), []
    notes = [f"{source}: {pdfdoc.FEATURES['signatures']}"] if "signatures" in checked.features else []
    if dry_run:
        return done("planned"), notes

    chosen = LEVELS[level]
    try:
        before = source.stat().st_size
        with pdfdoc.quiet():
            data, counts = build(checked, chosen, strip=strip)
        if len(data) >= before:
            return done("skipped", ALREADY_COMPACT), notes
        pdfdoc.verify(data, checked.pages)
        target.parent.mkdir(parents=True, exist_ok=True)
        pdfdoc.write_pdf(target, data)
    except pdfdoc.PdfError as error:
        return done("failed", str(error)), notes
    except OSError as error:
        return done("failed", error.strerror or str(error)), notes
    except Exception:  # pypdf fails in many ways on odd files
        return done("failed", pdfdoc.UNCHECKED), notes

    detail = f"{format_size(before)} -> {format_size(len(data))}, {change_text(before, len(data))}"
    images = counts.text() if chosen.images else ""
    if images:
        detail += f"; {images}"
    return done("compressed", detail), notes


def _same_file(first: Path, second: Path) -> bool:
    import os

    try:
        return first.exists() and second.exists() and os.path.samefile(first, second)
    except OSError:
        return False
