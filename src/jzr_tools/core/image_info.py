"""What an image file holds: format, size, colour, animation and metadata.

Only the header and metadata are read; the pixels of a still image are never
decoded. Importing this module imports Pillow, so the command imports it only
when it runs.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path

from PIL import Image

from jzr_tools.core.media import format_size
from jzr_tools.core.pillow_engine import (
    _HEIF_EXTENSIONS,
    _LAYOUT_TAGS,
    _open,
    _register_heif,
)

_ORIENTATION = 0x0112
_MAKE = 0x010F
_MODEL = 0x0110
_SOFTWARE = 0x0131
_X_RESOLUTION = 0x011A
_DATE_TIME = 0x0132
_EXIF_IFD = 0x8769
_DATE_TIME_ORIGINAL = 0x9003
_GPS_IFD = 0x8825
_TIFF_XMP = 700
_TIFF_IPTC = 33723

# TIFF baseline tags that describe the file, not the photo: a TIFF holding
# only these has no EXIF worth naming.
_NOT_EXIF = _LAYOUT_TAGS | {282, 283, 296, _TIFF_XMP, _TIFF_IPTC}

_MODE_TEXT = {
    "1": "black and white, 1 bit",
    "L": "grey, 8 bits",
    "LA": "grey with alpha, 8 bits per channel",
    "I": "grey, 32-bit integer",
    "F": "grey, 32-bit float",
    "P": "palette, 8 bits",
    "RGB": "RGB, 8 bits per channel",
    "RGBA": "RGBA, 8 bits per channel",
    "CMYK": "CMYK, 8 bits per channel",
}

# `info` keys that hold metadata of their own kind, or that Pillow uses for
# its own bookkeeping, so they are not PNG text.
_NOT_TEXT = frozenset({"xmp", "XML:com.adobe.xmp", "icc_profile", "exif", "comment"})


@dataclass(frozen=True)
class ImageInfo:
    path: Path
    format: str
    width: int
    height: int
    orientation: int | None
    mode: str
    file_size: int
    frames: int
    loop: int | None
    duration_ms: int | None
    transparency: bool
    color_profile: str | None
    dpi: tuple[float, float] | None
    camera: str | None
    taken: str | None
    software: str | None
    gps: tuple[float, float] | None
    metadata: tuple[str, ...]

    @property
    def upright_size(self) -> tuple[int, int]:
        """Width and height as the picture is shown, after its orientation."""
        if self.orientation in {5, 6, 7, 8}:
            return self.height, self.width
        return self.width, self.height


def _text(value: object) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if not isinstance(value, str):
        return None
    cleaned = value.replace("\x00", " ").strip()
    return cleaned or None


def _camera(exif: Image.Exif) -> str | None:
    make = _text(exif.get(_MAKE))
    model = _text(exif.get(_MODEL))
    if make and model and model.lower().startswith(make.lower()):
        return model
    return " ".join(part for part in (make, model) if part) or None


def _degrees(value: object, reference: object, positive: str, negative: str) -> float:
    """Degrees from EXIF's degrees, minutes and seconds; south and west are negative."""
    degrees, minutes, seconds = (float(part) for part in value)  # type: ignore[union-attr]
    side = (_text(reference) or "").upper()
    if side not in {positive, negative}:
        raise ValueError(f"GPS reference {side!r}")
    total = degrees + minutes / 60 + seconds / 3600
    return -total if side == negative else total


def _gps(exif: Image.Exif) -> tuple[float, float] | None:
    try:
        tags = exif.get_ifd(_GPS_IFD)
        if not tags:
            return None
        latitude = _degrees(tags[2], tags[1], "N", "S")
        longitude = _degrees(tags[4], tags[3], "E", "W")
    except Exception:  # noqa: BLE001 - malformed GPS must not hide the rest
        return None
    return latitude, longitude


def _taken(exif: Image.Exif) -> str | None:
    try:
        original = _text(exif.get_ifd(_EXIF_IFD).get(_DATE_TIME_ORIGINAL))
    except Exception:  # noqa: BLE001
        original = None
    return original or _text(exif.get(_DATE_TIME))


def _profile_description(profile: bytes) -> str:
    try:
        from PIL import ImageCms

        description = ImageCms.getProfileDescription(
            ImageCms.ImageCmsProfile(io.BytesIO(profile))
        ).strip()
    except Exception:  # noqa: BLE001 - an odd profile still counts as one
        return "ICC profile"
    return description or "ICC profile"


def _exif(image: Image.Image) -> Image.Exif:
    # A PNG's eXIf chunk may follow the pixels, and Pillow would decode them
    # to look for it; only one before them is read.
    if image.format == "PNG" and "exif" not in image.info:
        return Image.Exif()
    try:
        return image.getexif()
    except Exception:  # noqa: BLE001 - unreadable EXIF is shown as none
        return Image.Exif()


def _metadata(image: Image.Image, exif: Image.Exif) -> tuple[str, ...]:
    info = image.info
    tiff_tags = getattr(image, "tag_v2", {}) if image.format == "TIFF" else {}
    applist = getattr(image, "applist", [])

    blocks = []
    if any(tag not in _NOT_EXIF for tag in exif) or info.get("exif"):
        blocks.append("EXIF")
    if info.get("xmp") or info.get("XML:com.adobe.xmp") or _TIFF_XMP in tiff_tags:
        blocks.append("XMP")
    if any(marker == "APP13" for marker, _ in applist) or _TIFF_IPTC in tiff_tags:
        blocks.append("IPTC")
    if info.get("icc_profile"):
        blocks.append("ICC")
    if info.get("comment"):
        blocks.append("comment")
    if image.format == "PNG" and any(
        isinstance(value, str) and key not in _NOT_TEXT for key, value in info.items()
    ):
        blocks.append("text")
    return tuple(blocks)


def _animation(image: Image.Image, frames: int) -> tuple[int | None, int | None]:
    if frames < 2:
        return None, None
    loop = image.info.get("loop")
    total = 0
    for index in range(frames):
        image.seek(index)
        total += int(image.info.get("duration", 100) or 0)
    image.seek(0)
    return (None if loop is None else int(loop)), total


def _dpi(image: Image.Image, exif: Image.Exif) -> tuple[float, float] | None:
    info = image.info
    # For a JPEG whose JFIF header gives no unit, Pillow reports 72 dpi when
    # EXIF exists, even if EXIF has no resolution either; that is a guess.
    if image.format == "JPEG" and info.get("jfif_unit", 0) == 0 and _X_RESOLUTION not in exif:
        return None
    # A TIFF without resolution tags is reported as 1 dpi.
    if image.format == "TIFF" and _X_RESOLUTION not in getattr(image, "tag_v2", {}):
        return None
    value = info.get("dpi")
    try:
        x, y = (float(part) for part in value)  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return None
    return (x, y) if x > 0 and y > 0 else None


def read_info(path: Path) -> ImageInfo:
    """Read the facts of one image file.

    Raises ``pillow_engine.ConversionError`` when it is not a readable image.
    """
    if path.suffix.lstrip(".").lower() in _HEIF_EXTENSIONS:
        _register_heif()
    file_size = path.stat().st_size
    with _open(path) as image:
        exif = _exif(image)
        orientation = exif.get(_ORIENTATION)
        frames = int(getattr(image, "n_frames", 1) or 1)
        loop, duration = _animation(image, frames)
        profile = image.info.get("icc_profile")
        return ImageInfo(
            path=path,
            format=image.format or "unknown",
            width=image.width,
            height=image.height,
            orientation=orientation if orientation in range(2, 9) else None,
            mode=image.mode,
            file_size=file_size,
            frames=frames,
            loop=loop,
            duration_ms=duration,
            transparency=bool(image.has_transparency_data),
            color_profile=_profile_description(profile) if profile else None,
            dpi=_dpi(image, exif),
            camera=_camera(exif),
            taken=_taken(exif),
            software=_text(exif.get(_SOFTWARE)),
            gps=_gps(exif),
            metadata=_metadata(image, exif),
        )


def _mode_text(mode: str) -> str:
    if mode.startswith("I;16"):
        return "grey, 16 bits"
    return _MODE_TEXT.get(mode, mode)


def _number(value: float) -> str:
    rounded = round(value, 1)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:.1f}"


def _frames_text(info: ImageInfo) -> str:
    if info.frames < 2:
        return str(info.frames)
    if info.loop is None:
        plays = "plays once"
    elif info.loop == 0:
        plays = "loops forever"
    else:
        plays = f"loops {info.loop} times"
    seconds = (info.duration_ms or 0) / 1000
    return f"{info.frames} ({plays}, {seconds:.1f} s)"


def _size_text(info: ImageInfo) -> str:
    text = f"{info.width}x{info.height}"
    if info.orientation in {5, 6, 7, 8}:
        width, height = info.upright_size
        return f"{text} (upright {width}x{height}, orientation {info.orientation})"
    if info.orientation is not None:
        return f"{text} (orientation {info.orientation})"
    return text


def info_rows(info: ImageInfo, *, gps: bool = True) -> list[tuple[str, str]]:
    """Return the text form of ``info`` as label and value pairs."""
    rows = [
        ("format", info.format),
        ("size", _size_text(info)),
        ("mode", _mode_text(info.mode)),
        ("file size", format_size(info.file_size)),
        ("frames", _frames_text(info)),
    ]
    if info.transparency:
        rows.append(("transparency", "yes"))
    if info.color_profile:
        if info.color_profile == "ICC profile":
            rows.append(("color", "ICC profile"))
        else:
            rows.append(("color", f'ICC profile "{info.color_profile}"'))
    if info.dpi is not None:
        x, y = (_number(part) for part in info.dpi)
        rows.append(("dpi", x if x == y else f"{x}x{y}"))
    for label, value in (
        ("camera", info.camera),
        ("taken", info.taken),
        ("software", info.software),
    ):
        if value:
            rows.append((label, value))
    if gps and info.gps is not None:
        rows.append(("gps", f"{info.gps[0]:.6f}, {info.gps[1]:.6f}"))
    if info.metadata:
        rows.append(("metadata", ", ".join(info.metadata)))
    return rows


def info_as_dict(info: ImageInfo, *, gps: bool = True) -> dict[str, object]:
    """Return the facts of ``info`` as plain JSON-ready values."""
    upright_width, upright_height = info.upright_size
    position = None
    if gps and info.gps is not None:
        position = {"latitude": info.gps[0], "longitude": info.gps[1]}
    return {
        "file": str(info.path),
        "format": info.format,
        "width": info.width,
        "height": info.height,
        "orientation": info.orientation,
        "upright_width": upright_width,
        "upright_height": upright_height,
        "mode": info.mode,
        "file_size": info.file_size,
        "frames": info.frames,
        "loop": info.loop,
        "duration_ms": info.duration_ms,
        "transparency": info.transparency,
        "color_profile": info.color_profile,
        "dpi": list(info.dpi) if info.dpi is not None else None,
        "camera": info.camera,
        "taken": info.taken,
        "software": info.software,
        "gps": position,
        "metadata": list(info.metadata),
    }
