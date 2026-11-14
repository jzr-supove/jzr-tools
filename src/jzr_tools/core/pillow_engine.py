"""Built-in image conversion engine, on Pillow.

Importing this module imports Pillow, so ``core.images`` only does it when a
file is actually converted. ``pillow_heif`` is heavier still and is loaded
only for HEIC/HEIF files.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import warnings

from PIL import Image, ImageSequence, UnidentifiedImageError

# The files are the user's own; a 200-megapixel phone photo is not an attack.
Image.MAX_IMAGE_PIXELS = None

DEFAULT_QUALITY = 90

_FORMATS = {
    "avif": "AVIF",
    "bmp": "BMP",
    "gif": "GIF",
    "heic": "HEIF",
    "heif": "HEIF",
    "ico": "ICO",
    "jpeg": "JPEG",
    "jpg": "JPEG",
    "png": "PNG",
    "ppm": "PPM",
    "tga": "TGA",
    "tif": "TIFF",
    "tiff": "TIFF",
    "webp": "WEBP",
}

_HEIF_EXTENSIONS = frozenset({"heic", "heif"})
_LOSSY_FORMATS = frozenset({"AVIF", "HEIF", "JPEG", "WEBP"})
_ANIMATED_FORMATS = frozenset({"AVIF", "GIF", "WEBP"})
# Formats with no way to store transparency.
_OPAQUE_FORMATS = frozenset({"BMP", "JPEG", "PPM"})
_METADATA_FORMATS = frozenset({"AVIF", "HEIF", "JPEG", "PNG", "TIFF", "WEBP"})

# Pixel modes each writer accepts as they are. Formats not listed take RGB
# and RGBA.
_MODES = {
    "BMP": frozenset({"1", "L", "P", "RGB"}),
    "GIF": frozenset({"1", "L", "P", "RGB", "RGBA"}),
    "JPEG": frozenset({"L", "RGB", "CMYK"}),
    "PNG": frozenset({"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}),
    "PPM": frozenset({"1", "L", "RGB"}),
    "TGA": frozenset({"L", "LA", "P", "RGB", "RGBA"}),
    "TIFF": frozenset({"1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I", "I;16", "F"}),
}
_DEFAULT_MODES = frozenset({"RGB", "RGBA"})

_ORIENTATION = 0x0112
_TRANSPOSITIONS = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}

# TIFF describes its own pixel layout with tags that Pillow reports as EXIF.
# They say nothing true about a file in another format, and 34675 is a second
# copy of the colour profile.
_LAYOUT_TAGS = frozenset(
    {256, 257, 258, 259, 262, 273, 277, 278, 279, 284, 317, 320, 322, 323, 324, 325, 338, 339, 34675}
)

_ICO_LIMIT = 256

_heif_registered = False


class ConversionError(Exception):
    """A file could not be converted; the message says why."""


def _register_heif() -> bool:
    """Teach Pillow HEIC/HEIF. Returns whether this call did the registering."""
    global _heif_registered
    if _heif_registered:
        return False
    import pillow_heif

    pillow_heif.register_heif_opener()
    _heif_registered = True
    return True


def _open(source: Path) -> Image.Image:
    try:
        return Image.open(source)
    except UnidentifiedImageError:
        # The extension can lie: phones and chat apps hand out HEIC files
        # named `.jpg`.
        if _register_heif():
            try:
                return Image.open(source)
            except UnidentifiedImageError:
                pass
        raise ConversionError("not a readable image") from None


def _colour_family(mode: str) -> str:
    if mode == "CMYK":
        return "cmyk"
    if mode in {"1", "L", "LA", "La", "F"} or mode.startswith("I"):
        return "grey"
    return "rgb"


def _upright(image: Image.Image, exif: Image.Exif) -> Image.Image:
    """Apply the EXIF orientation to the pixels and drop the tag."""
    method = _TRANSPOSITIONS.get(exif.get(_ORIENTATION))
    exif.pop(_ORIENTATION, None)
    return image if method is None else image.transpose(method)


def _exif_bytes(exif: Image.Exif) -> bytes | None:
    """Serialize what is worth carrying over, or ``None`` when nothing is."""
    for tag in _LAYOUT_TAGS:
        exif.pop(tag, None)
    if not len(exif):
        return None
    try:
        return exif.tobytes()
    except Exception:  # noqa: BLE001 - odd metadata must not cost the picture
        return None


def _fit_modes(image: Image.Image, target_format: str) -> Image.Image:
    """Return ``image`` in a pixel mode the target format can store."""
    allowed = _MODES.get(target_format, _DEFAULT_MODES)
    transparent = image.has_transparency_data

    if target_format in _OPAQUE_FORMATS and transparent:
        layer = image.convert("RGBA")
        white = Image.new("RGBA", layer.size, (255, 255, 255, 255))
        return Image.alpha_composite(white, layer).convert("RGB")

    # Transparency given as a palette index or a colour key, not as an alpha
    # channel, survives only in the two formats that have the same notion.
    keyed = transparent and "A" not in image.mode.upper()
    if image.mode in allowed and not (keyed and target_format not in {"GIF", "PNG"}):
        return image

    if image.mode.startswith("I"):
        # 16-bit grey. A plain convert() clips everything above 255 to white.
        image = image.convert("I").point(lambda value: value * (1 / 256)).convert("L")
        if "L" in allowed:
            return image

    return image.convert("RGBA" if transparent else "RGB")


def _animation(image: Image.Image, target_format: str) -> dict[str, object]:
    """Save parameters that keep every frame, or nothing for a still image."""
    if target_format not in _ANIMATED_FORMATS or getattr(image, "n_frames", 1) < 2:
        return {}

    durations = []
    for frame in ImageSequence.Iterator(image):
        frame.load()  # some readers only learn a frame's duration here
        durations.append(frame.info.get("duration", 100))
    image.seek(0)

    parameters: dict[str, object] = {"save_all": True, "duration": durations}
    loop = image.info.get("loop")
    if loop is not None:
        parameters["loop"] = loop
    elif target_format != "GIF":
        # A GIF without a loop count plays once; the other writers would
        # loop forever unless told.
        parameters["loop"] = 1
    return parameters


def convert(source: Path, target: Path, *, quality: int | None = None) -> None:
    """Write ``source`` to ``target`` in the format of the target's extension.

    The output appears at ``target`` only when it is complete. Raises
    ``ConversionError`` otherwise, leaving ``target`` as it was.
    """
    extension = target.suffix.lstrip(".").lower()
    target_format = _FORMATS[extension]
    if extension in _HEIF_EXTENSIONS or source.suffix.lstrip(".").lower() in _HEIF_EXTENSIONS:
        _register_heif()

    temporary = target.with_name(f"jzr-tmp-{secrets.token_hex(8)}{target.suffix}")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with _open(source) as image:
                parameters: dict[str, object] = {}
                if target_format in _LOSSY_FORMATS:
                    parameters["quality"] = DEFAULT_QUALITY if quality is None else quality

                animation = _animation(image, target_format)
                if animation:
                    parameters.update(animation)
                    output = image
                else:
                    exif = image.getexif()
                    profile = image.info.get("icc_profile")
                    source_family = _colour_family(image.mode)

                    output = _fit_modes(_upright(image, exif), target_format)

                    # Writers fall back to whatever is in `info`, so it has
                    # to say exactly what should be stored.
                    output.info.pop("exif", None)
                    output.info.pop("icc_profile", None)
                    if target_format in _METADATA_FORMATS:
                        metadata = _exif_bytes(exif)
                        if metadata is not None:
                            parameters["exif"] = metadata
                        if profile and _colour_family(output.mode) == source_family:
                            parameters["icc_profile"] = profile
                    if target_format == "ICO":
                        width, height = output.size
                        parameters["sizes"] = [(min(width, _ICO_LIMIT), min(height, _ICO_LIMIT))]

                output.save(temporary, format=target_format, **parameters)
        os.replace(temporary, target)
    except BaseException as error:
        temporary.unlink(missing_ok=True)
        if isinstance(error, ConversionError) or not isinstance(error, Exception):
            raise
        # Any decoder or encoder failure fails this one file, not the batch.
        message = str(error).strip().splitlines()
        raise ConversionError(message[-1] if message else type(error).__name__) from error
