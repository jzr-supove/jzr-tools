"""Built-in image conversion engine, on Pillow.

Importing this module imports Pillow, so ``core.images`` only does it when a
file is actually converted. ``pillow_heif`` is heavier still and is loaded
only for HEIC/HEIF files.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import secrets
import warnings

from PIL import Image, ImageSequence, UnidentifiedImageError

# The files are the user's own; a 200-megapixel phone photo is not an attack.
Image.MAX_IMAGE_PIXELS = None

DEFAULT_QUALITY = 90
NOT_RESIZED_ANIMATED = (
    "animated images are not resized yet; jzr image convert keeps every frame"
)
NOT_COMPRESSED_ANIMATED = (
    "animated images are not compressed yet; jzr image convert keeps every frame"
)
WHITE = (255, 255, 255)

# Settings `image compress` adds to each format's quality: Huffman tables
# fitted to the picture for JPEG, the strongest zlib search for PNG.
COMPRESS_PARAMETERS: dict[str, dict[str, object]] = {
    "JPEG": {"optimize": True},
    "PNG": {"optimize": True},
}

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

# EXIF pointers to further blocks: camera settings and dates, GPS, interop.
_SUB_IFDS = frozenset({0x8769, 0x8825, 0xA005})

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


def _fit_modes(
    image: Image.Image, target_format: str, background: tuple[int, int, int] = WHITE
) -> Image.Image:
    """Return ``image`` in a pixel mode the target format can store.

    Transparent areas are composited over ``background`` when the target
    cannot keep them.
    """
    allowed = _MODES.get(target_format, _DEFAULT_MODES)
    transparent = image.has_transparency_data

    if target_format in _OPAQUE_FORMATS and transparent:
        layer = image.convert("RGBA")
        fill = Image.new("RGBA", layer.size, (*background, 255))
        return Image.alpha_composite(fill, layer).convert("RGB")

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


def _resized(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resample ``image`` to exactly ``size``, in a mode that keeps quality.

    Palette and 1-bit images would only be resampled by nearest neighbour,
    and 16-bit grey not at all, so they are widened first; ``_fit_modes``
    brings the result back to what the target stores.
    """
    if image.mode == "P":
        image = image.convert("RGBA" if image.has_transparency_data else "RGB")
    elif image.mode == "1":
        image = image.convert("L")
    elif image.mode.startswith("I;16"):
        mode = image.mode
        return image.convert("I").resize(size, Image.Resampling.LANCZOS).convert(mode)
    return image.resize(size, Image.Resampling.LANCZOS)


def probe(source: Path) -> tuple[int, int, int]:
    """Return the upright width and height and the frame count of ``source``.

    Only the header is read. Raises ``ConversionError`` when it is not a
    readable image.
    """
    if source.suffix.lstrip(".").lower() in _HEIF_EXTENSIONS:
        _register_heif()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with _open(source) as image:
                width, height = image.size
                try:
                    orientation = image.getexif().get(_ORIENTATION)
                except Exception:  # noqa: BLE001 - unreadable EXIF means upright
                    orientation = None
                frames = int(getattr(image, "n_frames", 1) or 1)
    except ConversionError:
        raise
    except Exception as error:  # noqa: BLE001 - any reader failure is this file's
        raise ConversionError("not a readable image") from error
    if orientation in {5, 6, 7, 8}:
        width, height = height, width
    return width, height, frames


class Prepared:
    """A decoded, upright image that can be encoded many times.

    ``width`` and ``height`` are the upright size. Each ``encode`` resamples
    from these same pixels, so trying several sizes never compounds blur.
    """

    def __init__(
        self,
        image: Image.Image,
        target_format: str,
        *,
        exif: bytes | None,
        sub_ifds: bool,
        profile: bytes | None,
        source_family: str,
        background: tuple[int, int, int],
        strip: bool,
    ) -> None:
        self._image = image
        self._format = target_format
        self._exif = exif
        self._sub_ifds = sub_ifds
        self._profile = profile
        self._family = source_family
        self._background = background
        self._strip = strip
        self.width, self.height = image.size

    def encode(self, quality: int | None, size: tuple[int, int]) -> bytes:
        """The file's bytes at ``quality`` (lossy formats only) and pixel ``size``."""
        image = self._image
        if tuple(size) != image.size:
            image = _resized(image, tuple(size))
        output = _fit_modes(image, self._format, self._background)
        if output is self._image:
            output = output.copy()
        output.info.pop("exif", None)
        output.info.pop("icc_profile", None)
        if self._strip:
            for key in ("xmp", "XML:com.adobe.xmp", "comment"):
                output.info.pop(key, None)

        parameters: dict[str, object] = dict(COMPRESS_PARAMETERS.get(self._format, {}))
        if self._format in _LOSSY_FORMATS:
            parameters["quality"] = DEFAULT_QUALITY if quality is None else quality
        if self._format == "TIFF":
            parameters["compression"] = "tiff_lzw"
        if self._format in _METADATA_FORMATS:
            if self._exif is not None:
                parameters["exif"] = self._exif
                if self._format == "TIFF" and self._sub_ifds:
                    parameters.pop("compression", None)
            if self._profile and _colour_family(output.mode) == self._family:
                parameters["icc_profile"] = self._profile

        buffer = io.BytesIO()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                output.save(buffer, format=self._format, **parameters)
        except Exception as error:  # noqa: BLE001 - an encoder failure is this file's
            message = str(error).strip().splitlines()
            raise ConversionError(message[-1] if message else type(error).__name__) from error
        return buffer.getvalue()


def prepare(
    source: Path,
    target_extension: str,
    *,
    background: tuple[int, int, int] | None = None,
    strip: bool = False,
) -> Prepared:
    """Decode ``source`` once, upright, for encoding into ``target_extension``.

    EXIF and the colour profile follow ``convert``'s rules; ``strip`` leaves
    out EXIF, XMP and comments. Raises ``ConversionError`` for an unreadable
    or animated image.
    """
    extension = target_extension.lstrip(".").lower()
    target_format = _FORMATS[extension]
    if extension in _HEIF_EXTENSIONS or source.suffix.lstrip(".").lower() in _HEIF_EXTENSIONS:
        _register_heif()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with _open(source) as image:
                if getattr(image, "n_frames", 1) > 1:
                    raise ConversionError(NOT_COMPRESSED_ANIMATED)
                image.load()
                exif = image.getexif()
                profile = image.info.get("icc_profile")
                family = _colour_family(image.mode)
                upright = _upright(image, exif)
                if upright is image:
                    upright = image.copy()
                metadata = None if strip else _exif_bytes(exif)
                sub_ifds = bool(_SUB_IFDS.intersection(exif))
    except ConversionError:
        raise
    except Exception as error:  # noqa: BLE001 - any reader failure is this file's
        raise ConversionError("not a readable image") from error
    return Prepared(
        upright,
        target_format,
        exif=metadata,
        sub_ifds=sub_ifds,
        profile=profile,
        source_family=family,
        background=background or WHITE,
        strip=strip,
    )


def write_bytes(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` through a temporary name and a rename.

    The target's directory is created first. On failure the temporary file is
    removed and the ``OSError`` raised; ``target`` is left as it was.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"jzr-tmp-{secrets.token_hex(8)}{target.suffix}")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


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


def convert(
    source: Path,
    target: Path,
    *,
    quality: int | None = None,
    background: tuple[int, int, int] | None = None,
    size: tuple[int, int] | None = None,
    strip: bool = False,
) -> None:
    """Write ``source`` to ``target`` in the format of the target's extension.

    Transparent areas become ``background`` (white when ``None``) in a target
    that cannot keep them; TIFF is written with lossless LZW compression.
    ``size`` resizes the upright image to exactly that width and height (a
    still image only). ``strip`` leaves out EXIF, XMP and comments; the
    colour profile is kept either way. The output appears at ``target`` only
    when it is complete. Raises ``ConversionError`` otherwise, leaving
    ``target`` as it was.
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
                if target_format == "TIFF":
                    parameters["compression"] = "tiff_lzw"

                if size is not None and getattr(image, "n_frames", 1) > 1:
                    raise ConversionError(NOT_RESIZED_ANIMATED)
                animation = {} if size is not None else _animation(image, target_format)
                if animation:
                    parameters.update(animation)
                    output = image
                else:
                    exif = image.getexif()
                    profile = image.info.get("icc_profile")
                    source_family = _colour_family(image.mode)

                    upright = _upright(image, exif)
                    if size is not None:
                        upright = _resized(upright, size)
                    output = _fit_modes(upright, target_format, background or WHITE)

                    # Writers fall back to whatever is in `info`, so it has
                    # to say exactly what should be stored.
                    output.info.pop("exif", None)
                    output.info.pop("icc_profile", None)
                    if strip:
                        for key in ("xmp", "XML:com.adobe.xmp", "comment"):
                            output.info.pop(key, None)
                    if target_format in _METADATA_FORMATS:
                        metadata = None if strip else _exif_bytes(exif)
                        if metadata is not None:
                            parameters["exif"] = metadata
                            if target_format == "TIFF" and _SUB_IFDS.intersection(exif):
                                # Compression goes through libtiff, which
                                # cannot write these; keeping the date taken
                                # and the GPS position wins over the bytes.
                                parameters.pop("compression", None)
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
