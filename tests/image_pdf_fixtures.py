"""Builders for the pictures the image-to-PDF tests read.

Every builder returns the bytes of a file and is deterministic: noise is
seeded, nothing depends on the clock. The ``*_picture`` functions return the
pixels a file was made from, so a test can compare what it reads back.
Nothing here touches the disk.
"""

from __future__ import annotations

import io
import struct
import zlib

from PIL import Image

from tests import strip_fixtures as sf

RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
YELLOW = (255, 255, 0)


def encode(image: Image.Image, fmt: str, **options) -> bytes:  # noqa: ANN003
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def noise(width: int, height: int, seed: int = 1, mode: str = "RGB") -> Image.Image:
    return sf.noise(width, height, seed, mode)


# ---- pictures with a known layout


def quadrants(width: int = 96, height: int = 48) -> Image.Image:
    """Red top left, green top right, blue bottom left, yellow bottom right."""
    image = Image.new("RGB", (width, height))
    half_w, half_h = width // 2, height // 2
    for box, colour in (
        ((0, 0, half_w, half_h), RED),
        ((half_w, 0, width, half_h), GREEN),
        ((0, half_h, half_w, height), BLUE),
        ((half_w, half_h, width, height), YELLOW),
    ):
        image.paste(colour, box)
    return image


def solid(colour: tuple[int, ...], size: tuple[int, int] = (40, 30), mode: str = "RGB") -> Image.Image:
    return Image.new(mode, size, colour)


# ---- JPEG


def solid_jpeg(colour: tuple[int, ...] = RED, size: tuple[int, int] = (40, 30), quality: int = 95) -> bytes:
    return encode(solid(colour, size), "JPEG", quality=quality)


def grey_jpeg(size: tuple[int, int] = (40, 30), seed: int = 3) -> bytes:
    return encode(noise(size[0], size[1], seed, "L"), "JPEG", quality=90)


def jpeg_with_exif(
    orientation: int | None = 6,
    *,
    gps: bool = True,
    size: tuple[int, int] = (90, 40),
    image: Image.Image | None = None,
    seed: int = 1,
) -> bytes:
    """A JPEG with camera data, a GPS position and the given EXIF orientation.

    ``AcmeCam`` is the camera make and the GPS position is 41.5 N, 69.25 E.
    """
    picture = image if image is not None else noise(size[0], size[1], seed)
    plain = encode(picture, "JPEG", quality=90)
    block = sf.exif_app1(sf.photo_tiff(orientation, gps=gps))
    return plain[:2] + block + plain[2:]


def has_gps(jpeg: bytes) -> bool:
    with Image.open(io.BytesIO(jpeg)) as image:
        return bool(image.getexif().get_ifd(0x8825))


def cmyk_jpeg(size: tuple[int, int] = (32, 24)) -> bytes:
    """A CMYK JPEG whose colour is a strong red (cyan 0, magenta and yellow full)."""
    return encode(Image.new("CMYK", size, (0, 255, 255, 0)), "JPEG", quality=95)


def noisy_large_jpeg(size: tuple[int, int] = (2400, 1600), seed: int = 5) -> bytes:
    """A big JPEG of fine noise: much larger than 2000 px a side and heavy."""
    return encode(noise(size[0], size[1], seed), "JPEG", quality=95)


def tiny_jpeg(size: tuple[int, int] = (64, 64), seed: int = 2) -> bytes:
    """A small noisy JPEG at a low quality, so a quality-75 copy of it is larger."""
    return encode(noise(size[0], size[1], seed), "JPEG", quality=20, optimize=True)


# ---- PNG


def rgba_picture(width: int = 40, height: int = 30, seed: int = 4) -> Image.Image:
    """Random colours; the alpha runs from fully clear (left) to opaque (right)."""
    colour = noise(width, height, seed)
    alpha = Image.new("L", (width, height))
    alpha.putdata([x * 255 // (width - 1) for _ in range(height) for x in range(width)])
    colour.putalpha(alpha)
    return colour


def rgba_png() -> bytes:
    return encode(rgba_picture(), "PNG")


def opaque_rgba_png(width: int = 40, height: int = 30) -> bytes:
    picture = noise(width, height, 6)
    picture.putalpha(255)
    return encode(picture, "PNG")


def rgb_png(width: int = 40, height: int = 30, seed: int = 7) -> bytes:
    return encode(noise(width, height, seed), "PNG")


def grey_alpha_picture(width: int = 24, height: int = 16) -> Image.Image:
    picture = Image.new("LA", (width, height))
    picture.putdata([(x * 10 % 256, y * 15 % 256) for y in range(height) for x in range(width)])
    return picture


def grey_alpha_png() -> bytes:
    return encode(grey_alpha_picture(), "PNG")


def palette_picture(width: int = 32, height: int = 24) -> Image.Image:
    """Eight palette colours in 4 x 4 pixel blocks."""
    picture = Image.new("P", (width, height))
    palette = []
    for index in range(8):
        palette.extend(((index * 36) % 256, (255 - index * 30) % 256, (index * 90) % 256))
    picture.putpalette(palette + [0] * (768 - len(palette)))
    picture.putdata([(x // 4 + y // 4) % 8 for y in range(height) for x in range(width)])
    return picture


def palette_png(*, transparent_index: int | None = None) -> bytes:
    options = {} if transparent_index is None else {"transparency": transparent_index}
    return encode(palette_picture(), "PNG", **options)


def sixteen_bit_picture(width: int = 20, height: int = 4) -> Image.Image:
    """Grey from 0 (left) to 65535 (right)."""
    data = b"".join(
        struct.pack("<H", x * 65535 // (width - 1)) for _ in range(height) for x in range(width)
    )
    return Image.frombytes("I;16", (width, height), data)


def sixteen_bit_png(width: int = 20, height: int = 4) -> bytes:
    return encode(sixteen_bit_picture(width, height), "PNG")


def one_bit_png(width: int = 32, height: int = 16) -> bytes:
    picture = Image.new("1", (width, height), 0)
    picture.paste(1, (0, 0, width // 2, height))
    return encode(picture, "PNG")


def png_with_exif(orientation: int, image: Image.Image | None = None) -> bytes:
    picture = image if image is not None else quadrants()
    exif = Image.Exif()
    exif[0x0112] = orientation
    return encode(picture, "PNG", exif=exif)


def wide_png(width: int, height: int = 4) -> bytes:
    """A 1-bit picture of one colour, cheap to store however wide it is."""
    return encode(Image.new("1", (width, height), 1), "PNG")


def too_many_pixels_png(width: int = 14000, height: int = 14000) -> bytes:
    """A valid, tiny file that claims about 196 million pixels (past Pillow's limit)."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    row = b"\x00" + bytes((width + 7) // 8)
    compressor = zlib.compressobj(9)
    data = b"".join(compressor.compress(row) for _ in range(height)) + compressor.flush()
    header = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    return sf.PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", data) + chunk(b"IEND", b"")


# ---- other formats


def webp_rgb(size: tuple[int, int] = (48, 32)) -> bytes:
    return encode(quadrants(*size), "WEBP", quality=90)


def webp_rgba() -> bytes:
    # exact keeps the colour of fully clear pixels, which lossless WebP drops otherwise.
    return encode(rgba_picture(), "WEBP", lossless=True, exact=True)


def bmp(size: tuple[int, int] = (48, 32)) -> bytes:
    return encode(quadrants(*size), "BMP")


def tiff(size: tuple[int, int] = (48, 32)) -> bytes:
    return encode(quadrants(*size), "TIFF")


def gif_two_frames(size: tuple[int, int] = (24, 16)) -> bytes:
    """Frame one is red, frame two is blue."""
    frames = [Image.new("RGB", size, colour) for colour in (RED, BLUE)]
    return encode(frames[0], "GIF", save_all=True, append_images=frames[1:])


def heic(size: tuple[int, int] = (48, 32)) -> bytes:
    """A HEIC file of the four-colour picture; needs the HEIF encoder of pillow-heif."""
    import pillow_heif

    return encode_with(pillow_heif.register_heif_opener, quadrants(*size), "HEIF", quality=90)


def encode_with(register, image: Image.Image, fmt: str, **options) -> bytes:  # noqa: ANN001, ANN003
    register()
    return encode(image, fmt, **options)


def not_an_image() -> bytes:
    return b"this is plain text, not a picture\n" * 10


# ---- PDF pages to render


def _dump(writer) -> bytes:  # noqa: ANN001
    from tests import pdf_fixtures as fx

    return fx._dump(writer)


def drawn_pdf(
    pages: list[tuple[float, float, bytes]],
    rotate: dict[int, int] | None = None,
    annotation_on_first: bool = False,
) -> bytes:
    """Pages of (width, height, content stream) in points; ``rotate`` maps a 1-based page to /Rotate."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
    )

    writer = PdfWriter()
    for width, height, content in pages:
        page = writer.add_blank_page(width=width, height=height)
        stream = DecodedStreamObject()
        stream.set_data(content)
        page[NameObject("/Contents")] = writer._add_object(stream)
    for number, angle in (rotate or {}).items():
        writer.pages[number - 1][NameObject("/Rotate")] = NumberObject(angle)
    if annotation_on_first:
        appearance = DecodedStreamObject()
        appearance.set_data(b"0 1 0 rg 0 0 40 40 re f")
        appearance.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject([NumberObject(n) for n in (0, 0, 40, 40)]),
            }
        )
        annotation = writer._add_object(
            DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Annot"),
                    NameObject("/Subtype"): NameObject("/Stamp"),
                    NameObject("/Rect"): ArrayObject([FloatObject(n) for n in (0, 0, 40, 40)]),
                    NameObject("/F"): NumberObject(4),
                    NameObject("/AP"): DictionaryObject(
                        {NameObject("/N"): writer._add_object(appearance)}
                    ),
                }
            )
        )
        writer.pages[0][NameObject("/Annots")] = ArrayObject([annotation])
    return _dump(writer)


# A 200 x 100 pt page: white, with the top right quarter red.
RED_QUARTER = b"1 0 0 rg 100 50 100 50 re f"
BLACK_FILL = b"0 g 0 0 200 100 re f"


def red_quarter_pdf(rotate: int | None = None) -> bytes:
    return drawn_pdf([(200, 100, RED_QUARTER)], {1: rotate} if rotate else None)


def black_pdf() -> bytes:
    return drawn_pdf([(200, 100, BLACK_FILL)])


def green_stamp_pdf() -> bytes:
    """A white 200 x 100 page with a green stamp annotation in its bottom left corner."""
    return drawn_pdf([(200, 100, b"")], annotation_on_first=True)


# ---- other formats with an orientation


def tiff_with_orientation(orientation: int, size: tuple[int, int] = (96, 48)) -> bytes:
    return encode(quadrants(*size), "TIFF", tiffinfo={274: orientation})


def webp_with_orientation(orientation: int, size: tuple[int, int] = (96, 48)) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation
    return encode(quadrants(*size), "WEBP", exif=exif, quality=90)
