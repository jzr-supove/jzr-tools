"""Builders for PDF files with images, forms and extras, for the compress tests.

Images are drawn at a known size through ``cm`` and ``Do``, so the size a
picture is shown at is a property of the file, not of the test. Noise is
seeded, so every builder gives the same bytes on every run. Files are built
with pypdf and Pillow and never touch the disk; the tests write them into
temporary folders.
"""

from __future__ import annotations

from dataclasses import dataclass
import functools
import io
import random
import zlib

from PIL import Image, ImageDraw
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

from tests import pdf_fixtures as fx

# About 14 kB of comment lines: content that does nothing, is not compressed
# in the source and shrinks a lot once it is.
PADDING = b"".join(f"% padding line {index}\n".encode() for index in range(700))


# ---- low level


def pdf_value(value):  # noqa: ANN001, ANN201
    """A Python value as the pypdf object it stands for; pypdf objects pass through."""
    if isinstance(value, bool):
        return BooleanObject(value)
    if isinstance(value, bytes):
        return ByteStringObject(value)
    if isinstance(value, int):
        return NumberObject(value)
    if isinstance(value, float):
        return FloatObject(value)
    if isinstance(value, str):
        return NameObject(value) if value.startswith("/") else TextStringObject(value)
    if isinstance(value, (list, tuple)):
        return ArrayObject([pdf_value(item) for item in value])
    if isinstance(value, dict):
        return DictionaryObject({NameObject(key): pdf_value(item) for key, item in value.items()})
    return value


def stream_object(data: bytes, entries: dict | None = None) -> DecodedStreamObject:
    """A stream whose bytes are stored exactly as given, with the given dictionary entries."""
    stream = DecodedStreamObject()
    stream.set_data(data)
    for key, value in (entries or {}).items():
        stream[NameObject(key)] = pdf_value(value)
    return stream


def noise(width: int, height: int, channels: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(width * height * channels)


def blotches(width: int, height: int, seed: int = 1, mode: str = "RGB", cell: int = 10) -> Image.Image:
    """A smooth, photograph-like picture: random colour cells blown up with a soft filter."""
    channels = 3 if mode == "RGB" else 1
    small_width, small_height = max(2, width // cell), max(2, height // cell)
    small = Image.frombytes(mode, (small_width, small_height), noise(small_width, small_height, channels, seed))
    return small.resize((width, height), Image.BICUBIC)


def jpeg_bytes(image: Image.Image, quality: int = 90) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def flat_screenshot(width: int, height: int) -> Image.Image:
    """Blocks of a few flat colours and thin lines, like a screenshot of a form."""
    image = Image.new("RGB", (width, height), (250, 250, 250))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, height // 8), fill=(30, 60, 140))
    draw.rectangle((width // 10, height // 4, width // 2, height // 2), fill=(220, 230, 245))
    draw.rectangle((width // 2 + 10, height // 4, width - width // 10, height - height // 8), fill=(255, 200, 80))
    for row in range(height // 3, height, max(4, height // 12)):
        draw.line((width // 10, row, width // 2 - 10, row), fill=(60, 60, 60), width=1)
    return image


def add_image(  # noqa: PLR0913
    writer: PdfWriter,
    width: int,
    height: int,
    data: bytes,
    *,
    filter: object = None,  # noqa: A002
    colorspace: object = "/DeviceRGB",
    bits: int | None = 8,
    parms: dict | None = None,
    extra: dict | None = None,
):  # noqa: ANN201
    """Add an image XObject holding ``data`` as it is stored; returns its reference."""
    entries: dict[str, object] = {
        "/Type": "/XObject",
        "/Subtype": "/Image",
        "/Width": width,
        "/Height": height,
    }
    if colorspace is not None:
        entries["/ColorSpace"] = colorspace
    if bits is not None:
        entries["/BitsPerComponent"] = bits
    if filter is not None:
        entries["/Filter"] = filter
    if parms:
        entries["/DecodeParms"] = parms
    entries.update(extra or {})
    return writer._add_object(stream_object(data, entries))


def icc_profile(writer: PdfWriter, components: int):  # noqa: ANN201
    return writer._add_object(stream_object(b"\x00" * 128, {"/N": components}))


def add_page(
    writer: PdfWriter,
    content: bytes | list[bytes],
    xobjects: dict | None = None,
    *,
    width: float = 612,
    height: float = 792,
    resources_on_page_tree: bool = False,
):  # noqa: ANN201
    """A page drawing ``content`` (one stream, or a list of streams) with ``xobjects`` in scope."""
    page = writer.add_blank_page(width=width, height=height)
    streams = content if isinstance(content, list) else [content]
    references = [writer._add_object(stream_object(part)) for part in streams]
    page[NameObject("/Contents")] = references[0] if len(references) == 1 else ArrayObject(references)
    resources = DictionaryObject()
    if xobjects:
        resources[NameObject("/XObject")] = pdf_value(dict(xobjects))
    if resources_on_page_tree:
        del page["/Resources"]
        writer._pages.get_object()[NameObject("/Resources")] = resources
    else:
        page[NameObject("/Resources")] = resources
    return page


def add_form(
    writer: PdfWriter,
    content: bytes,
    *,
    xobjects: dict | None = None,
    matrix: tuple[float, ...] | None = None,
):  # noqa: ANN201
    """A form XObject; without ``xobjects`` it has no resources of its own."""
    entries: dict[str, object] = {"/Type": "/XObject", "/Subtype": "/Form", "/BBox": [0, 0, 1000, 1000]}
    if matrix is not None:
        entries["/Matrix"] = list(matrix)
    if xobjects is not None:
        entries["/Resources"] = {"/XObject": dict(xobjects)}
    return writer._add_object(stream_object(content, entries))


def set_form_xobjects(form, xobjects: dict) -> None:  # noqa: ANN001
    form.get_object()[NameObject("/Resources")] = pdf_value({"/XObject": dict(xobjects)})


def tiny_image(writer: PdfWriter):  # noqa: ANN201
    """A 2 x 2 RGB image."""
    return add_image(writer, 2, 2, zlib.compress(bytes(range(12))), filter="/FlateDecode")


def dump(writer: PdfWriter) -> bytes:
    return fx._dump(writer)


def draw_cm(width: float, height: float, name: str = "Im1") -> bytes:
    return f"q {width} 0 0 {height} 20 20 cm /{name} Do Q\n".encode()


# ---- documents with one picture

# Colour cells of this many pixels make a picture soft enough for JPEG to keep it nearly intact.
PHOTO_CELL = 60


def single_image_pdf(
    width: int,
    height: int,
    data: bytes,
    *,
    drawn: tuple[float, float] | None = (216, 144),
    pad: bool = True,
    **image: object,
) -> bytes:
    """A page showing one image at ``drawn`` points (not shown at all when None)."""
    writer = PdfWriter()
    reference = add_image(writer, width, height, data, **image)  # type: ignore[arg-type]
    content = (draw_cm(*drawn) if drawn else b"") + (PADDING if pad else b"")
    add_page(writer, content, {"/Im1": reference})
    return dump(writer)


@functools.lru_cache(maxsize=None)
def photo_pdf(
    width: int = 3000,
    height: int = 2000,
    drawn: tuple[float, float] | None = (432, 288),
    quality: int = 90,
    seed: int = 1,
) -> bytes:
    """A JPEG photograph in an RGB colour space."""
    image = blotches(width, height, seed, cell=PHOTO_CELL)
    return single_image_pdf(
        width, height, jpeg_bytes(image, quality), drawn=drawn, filter="/DCTDecode"
    )


def photo_pixels(width: int, height: int, seed: int = 1) -> Image.Image:
    """The picture ``photo_pdf`` was made from, before JPEG."""
    return blotches(width, height, seed, cell=PHOTO_CELL)


@functools.lru_cache(maxsize=None)
def noisy_flate_pdf(width: int = 400, height: int = 300, drawn: tuple[float, float] | None = (400, 300)) -> bytes:
    """Random RGB samples, Flate coded: nothing about it is flat."""
    return single_image_pdf(
        width,
        height,
        zlib.compress(noise(width, height, 3, seed=3), 1),
        drawn=drawn,
        filter="/FlateDecode",
    )


@functools.lru_cache(maxsize=None)
def tiny_noise_pdf() -> bytes:
    """An 8 x 8 noisy image: any JPEG of it is bigger than its 200 bytes of Flate data."""
    return single_image_pdf(
        8, 8, zlib.compress(noise(8, 8, 3, seed=8), 9), drawn=(8, 8), filter="/FlateDecode"
    )


@functools.lru_cache(maxsize=None)
def unfiltered_noise_pdf(width: int = 400, height: int = 300) -> bytes:
    """Random RGB samples stored without any filter."""
    return single_image_pdf(width, height, noise(width, height, 3, seed=4), drawn=(width, height))


@functools.lru_cache(maxsize=None)
def gray_noise_pdf(width: int = 400, height: int = 300) -> bytes:
    return single_image_pdf(
        width,
        height,
        zlib.compress(noise(width, height, 1, seed=5), 1),
        drawn=(width, height),
        filter="/FlateDecode",
        colorspace="/DeviceGray",
    )


@functools.lru_cache(maxsize=None)
def icc_noise_pdf(components: int, width: int = 400, height: int = 300) -> bytes:
    """Random samples in an ICCBased colour space with ``components`` channels."""
    writer = PdfWriter()
    profile = icc_profile(writer, components)
    reference = add_image(
        writer,
        width,
        height,
        zlib.compress(noise(width, height, components, seed=6), 1),
        filter="/FlateDecode",
        colorspace=["/ICCBased", profile],
    )
    add_page(writer, draw_cm(width, height) + PADDING, {"/Im1": reference})
    return dump(writer)


FLAT_SIZE = (400, 300)


@functools.lru_cache(maxsize=None)
def flat_pdf() -> bytes:
    """A screenshot-like image whose Flate stream is stored without compression."""
    width, height = FLAT_SIZE
    raw = flat_screenshot(width, height).tobytes()
    return single_image_pdf(
        width, height, zlib.compress(raw, 0), drawn=(width, height), filter="/FlateDecode"
    )


def flat_raw() -> bytes:
    return flat_screenshot(*FLAT_SIZE).tobytes()


@functools.lru_cache(maxsize=None)
def flat_large_pdf(width: int = 1200, height: int = 900) -> bytes:
    """A big screenshot-like image shown at 2 x 1.5 in."""
    raw = flat_screenshot(width, height).tobytes()
    return single_image_pdf(
        width, height, zlib.compress(raw, 6), drawn=(144, 108), filter="/FlateDecode"
    )


@functools.lru_cache(maxsize=None)
def predicted_pdf(width: int = 400, height: int = 300) -> bytes:
    """A smooth picture stored with PNG "Up" predictor rows, without compression."""
    raw = blotches(width, height, seed=9).tobytes()
    row = width * 3
    rows = [raw[start : start + row] for start in range(0, len(raw), row)]
    previous = bytes(row)
    coded = bytearray()
    for current in rows:
        coded.append(2)
        coded.extend((a - b) & 255 for a, b in zip(current, previous))
        previous = current
    return single_image_pdf(
        width,
        height,
        zlib.compress(bytes(coded), 0),
        drawn=(width, height),
        filter="/FlateDecode",
        parms={"/Predictor": 15, "/Colors": 3, "/BitsPerComponent": 8, "/Columns": width},
    )


def predicted_pixels(width: int = 400, height: int = 300) -> Image.Image:
    return blotches(width, height, seed=9)


@functools.lru_cache(maxsize=None)
def soft_masked_pdf(width: int = 1500, height: int = 1000, drawn: tuple[float, float] = (216, 144)) -> bytes:
    """A JPEG base image with a Flate gray soft mask (dark at the top, light at the bottom)."""
    writer = PdfWriter()
    gradient = Image.linear_gradient("L").resize((width, height))
    mask = add_image(
        writer,
        width,
        height,
        zlib.compress(gradient.tobytes(), 1),
        filter="/FlateDecode",
        colorspace="/DeviceGray",
    )
    base = add_image(
        writer,
        width,
        height,
        jpeg_bytes(blotches(width, height, 2), 90),
        filter="/DCTDecode",
        extra={"/SMask": mask},
    )
    add_page(writer, draw_cm(*drawn) + PADDING, {"/Im1": base})
    return dump(writer)


@functools.lru_cache(maxsize=None)
def form_matrix_pdf() -> bytes:
    """A 3000 x 2000 photo drawn through a form whose /Matrix halves it.

    The page scales the form to 432 x 288 points and the form scales by one
    half, so the picture is seen at 216 x 144 points.
    """
    writer = PdfWriter()
    image = add_image(writer, 3000, 2000, jpeg_bytes(blotches(3000, 2000, 1), 90), filter="/DCTDecode")
    form = add_form(
        writer,
        b"q 432 0 0 288 0 0 cm /Im1 Do Q\n",
        xobjects={"/Im1": image},
        matrix=(0.5, 0, 0, 0.5, 0, 0),
    )
    add_page(writer, b"q 1 0 0 1 20 20 cm /Fm1 Do Q\n" + PADDING, {"/Fm1": form})
    return dump(writer)


@functools.lru_cache(maxsize=None)
def two_uses_pdf() -> bytes:
    """One 3000 x 2000 photo drawn small on page 1 and large on page 2."""
    writer = PdfWriter()
    image = add_image(writer, 3000, 2000, jpeg_bytes(blotches(3000, 2000, 1), 90), filter="/DCTDecode")
    add_page(writer, draw_cm(216, 144) + PADDING, {"/Im1": image})
    add_page(writer, draw_cm(432, 288), {"/Im1": image})
    return dump(writer)


@functools.lru_cache(maxsize=None)
def huge_drawn_pdf(drawn: bool) -> bytes:
    """A 4000 x 2000 photo, drawn very large (100 x 50 in) or listed but never drawn."""
    size = (7200, 3600) if drawn else None
    return photo_pdf(4000, 2000, size, 90, 1)


# ---- images the command leaves as they were


def _skipped_cases() -> dict[str, dict]:
    width, height = 800, 600
    return {
        "cmyk": dict(
            data=jpeg_bytes(Image.frombytes("CMYK", (width, height), noise(width, height, 4, 21)), 90),
            filter="/DCTDecode",
            colorspace="/DeviceCMYK",
        ),
        "cmyk-flate": dict(
            data=zlib.compress(noise(width, height, 4, 22), 1),
            filter="/FlateDecode",
            colorspace="/DeviceCMYK",
        ),
        "one-bit": dict(
            data=zlib.compress(noise((width + 7) // 8, height, 1, 23), 1),
            filter="/FlateDecode",
            colorspace="/DeviceGray",
            bits=1,
        ),
        "sixteen-bit": dict(
            data=zlib.compress(noise(width, height, 6, 24), 1),
            filter="/FlateDecode",
            bits=16,
        ),
        "indexed": dict(
            data=zlib.compress(noise(width, height, 1, 25), 1),
            filter="/FlateDecode",
            colorspace=["/Indexed", "/DeviceRGB", 255, bytes(768)],
        ),
        "separation": dict(
            data=zlib.compress(noise(width, height, 1, 26), 1),
            filter="/FlateDecode",
            colorspace=[
                "/Separation",
                "/Spot",
                "/DeviceGray",
                {"/FunctionType": 2, "/Domain": [0, 1], "/N": 1},
            ],
        ),
        "no-colour-space": dict(
            data=zlib.compress(noise(width, height, 3, 27), 1), filter="/FlateDecode", colorspace=None
        ),
        "jpx": dict(data=noise(100, 50, 1, 28), filter="/JPXDecode"),
        "ccitt": dict(
            data=noise(100, 50, 1, 29), filter="/CCITTFaxDecode", colorspace="/DeviceGray", bits=1
        ),
        "jbig2": dict(
            data=noise(100, 50, 1, 30), filter="/JBIG2Decode", colorspace="/DeviceGray", bits=1
        ),
        "lzw": dict(data=noise(100, 50, 1, 31), filter="/LZWDecode"),
        "two-filters": dict(
            data=zlib.compress(noise(width, height, 3, 32), 1).hex().encode() + b">",
            filter=["/ASCIIHexDecode", "/FlateDecode"],
        ),
        "decode-array": dict(
            data=zlib.compress(noise(width, height, 3, 33), 1),
            filter="/FlateDecode",
            extra={"/Decode": [1, 0, 1, 0, 1, 0]},
        ),
        "colour-key": dict(
            data=zlib.compress(noise(width, height, 3, 34), 1),
            filter="/FlateDecode",
            extra={"/Mask": [0, 10, 0, 10, 0, 10]},
        ),
        "stencil": dict(
            data=zlib.compress(noise((width + 7) // 8, height, 1, 35), 1),
            filter="/FlateDecode",
            colorspace=None,
            bits=1,
            extra={"/ImageMask": True},
        ),
        # Pictures that cannot be read are left as they are too.
        "garbage-flate": dict(data=noise(100, 50, 1, 36), filter="/FlateDecode"),
        "short-flate": dict(data=zlib.compress(noise(width, height // 2, 3, 37), 1), filter="/FlateDecode"),
        "garbage-jpeg": dict(data=noise(200, 50, 1, 38), filter="/DCTDecode"),
        # The stream is 300 x 200 but the dictionary says 800 x 600.
        "wrong-size-jpeg": dict(
            data=jpeg_bytes(blotches(300, 200, 3), 90),
            filter="/DCTDecode",
        ),
        "gray-jpeg-as-rgb": dict(
            data=jpeg_bytes(blotches(width, height, 4, mode="L"), 90),
            filter="/DCTDecode",
        ),
        "too-many-pixels": dict(
            data=zlib.compress(bytes(1000), 9),
            filter="/FlateDecode",
            size=(20000, 20000),
        ),
    }


SKIPPED_NAMES = tuple(_skipped_cases())


@functools.lru_cache(maxsize=None)
def skipped_image_pdf(kind: str) -> bytes:
    """A page with one 800 x 600 image of the given kind, shown at 2 x 1.5 in.

    Every kind is something the command must not touch, although the picture
    is far larger than the size it is shown at.
    """
    case = dict(_skipped_cases()[kind])
    width, height = case.pop("size", (800, 600))
    data = case.pop("data")
    return single_image_pdf(width, height, data, drawn=(144, 108), **case)


def with_unreadable_neighbour() -> bytes:
    """A shrinkable photo and a CMYK image on one page; padding is not needed."""
    writer = PdfWriter()
    photo = add_image(writer, 1500, 1000, jpeg_bytes(blotches(1500, 1000, 1), 90), filter="/DCTDecode")
    cmyk_image = Image.frombytes("CMYK", (300, 200), noise(300, 200, 4, 40))
    cmyk = add_image(
        writer, 300, 200, jpeg_bytes(cmyk_image, 90), filter="/DCTDecode", colorspace="/DeviceCMYK"
    )
    content = draw_cm(216, 144, "Im1") + draw_cm(100, 60, "Im2")
    add_page(writer, content, {"/Im1": photo, "/Im2": cmyk})
    return dump(writer)


def with_many_resizable(count: int = 3) -> bytes:
    """``count`` distinct photographs, each shown small enough to be resized."""
    writer = PdfWriter()
    names = {}
    content = b""
    for index in range(count):
        names[f"/Im{index}"] = add_image(
            writer,
            1000,
            700,
            jpeg_bytes(blotches(1000, 700, 10 + index), 90),
            filter="/DCTDecode",
        )
        content += draw_cm(144, 100, f"Im{index}")
    add_page(writer, content, names)
    return dump(writer)


# ---- documents without pictures


def _random_stream(size: int = 60000, seed: int = 7) -> DecodedStreamObject:
    return stream_object(random.Random(seed).randbytes(size))


JUNK_SIZE = 60000


def with_junk(data: bytes) -> bytes:
    """``data`` read and written again with a large object that nothing points to."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer._add_object(_random_stream(JUNK_SIZE))
    return dump(writer)


LABELS = ("alpha", "beta", "gamma")


@functools.lru_cache(maxsize=None)
def thumb_and_junk_pdf() -> bytes:
    """Text pages, an unused 60 kB object and a thumbnail on the first page."""
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
    for index, label in enumerate(LABELS):
        fx._text_page(writer, font, fx.marker_width(index + 1), fx.HEIGHT, label)
    writer.pages[0][NameObject("/Thumb")] = add_image(
        writer, 20, 20, noise(20, 20, 3, 50)
    )
    writer._add_object(_random_stream(JUNK_SIZE))
    return dump(writer)


@functools.lru_cache(maxsize=None)
def kept_parts_pdf() -> bytes:
    """Three text pages (one rotated, one cropped), bookmarks and a link, plus an unused object."""
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
    for index, label in enumerate(LABELS):
        fx._text_page(writer, font, fx.marker_width(index + 1), fx.HEIGHT, label)
    writer.pages[1][NameObject("/Rotate")] = NumberObject(90)
    writer.pages[0][NameObject("/CropBox")] = ArrayObject(
        [FloatObject(5), FloatObject(5), FloatObject(100), FloatObject(190)]
    )
    link = writer._add_object(fx._link(fx._uri("https://example.com/kept")))
    writer.pages[2][NameObject("/Annots")] = ArrayObject([link])
    writer.add_outline_item("Chapter one", 0)
    writer.add_outline_item("Chapter three", 2)
    writer._add_object(_random_stream(JUNK_SIZE))
    return dump(writer)


XMP = (
    b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Quarterly report</dc:title>'
    b"</rdf:Description></rdf:RDF></x:xmpmeta>\n"
    b'<?xpacket end="w"?>'
)


@functools.lru_cache(maxsize=None)
def metadata_pdf() -> bytes:
    """A padded page with document info, XMP and PieceInfo on the catalog and the page."""
    writer = PdfWriter()
    page = add_page(writer, b"0 0 m 10 10 l S\n" + PADDING)
    writer.add_metadata({"/Title": "Quarterly report", "/Author": "Ada Lovelace"})
    metadata = writer._add_object(stream_object(XMP, {"/Type": "/Metadata", "/Subtype": "/XML"}))
    writer._root_object[NameObject("/Metadata")] = metadata
    piece = {"/Private": {"/LastModified": "D:20200101000000Z"}}
    writer._root_object[NameObject("/PieceInfo")] = pdf_value(piece)
    page[NameObject("/PieceInfo")] = pdf_value(piece)
    return dump(writer)


@functools.lru_cache(maxsize=None)
def signed_pdf() -> bytes:
    """A file with a signature dictionary and /ByteRange, and an unused object."""
    return with_junk(fx.with_signature())


@functools.lru_cache(maxsize=None)
def duplicate_images_pdf() -> bytes:
    """Two pages that each hold their own copy of the same 30 kB image."""
    writer = PdfWriter()
    data = zlib.compress(noise(100, 100, 3, 60), 1)
    for _ in range(2):
        image = add_image(writer, 100, 100, data, filter="/FlateDecode")
        add_page(writer, draw_cm(100, 100), {"/Im1": image})
    return dump(writer)


CONTENT = b"BT /F1 12 Tf 20 700 Td (compressible text) Tj ET\n" + PADDING


@functools.lru_cache(maxsize=None)
def plain_content_pdf() -> bytes:
    """One page whose content stream is stored without a filter."""
    writer = PdfWriter()
    add_page(writer, CONTENT)
    return dump(writer)


@functools.lru_cache(maxsize=None)
def minimal_pdf() -> bytes:
    """A blank page written by hand with as little structure as a PDF allows."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 4\n0000000000 65535 f \n"
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % start
    return out


# ---- reading results back


@dataclass
class Found:
    """An image XObject of a file, as stored."""

    number: int
    stream: DictionaryObject
    width: int
    height: int
    filters: tuple[str, ...]
    raw: bytes

    @property
    def filter(self) -> str | None:
        return self.filters[0] if len(self.filters) == 1 else None

    @property
    def colorspace(self):  # noqa: ANN201
        return self.stream.get("/ColorSpace")

    def pixels(self) -> Image.Image:
        """The picture as pixels: Pillow for JPEG, the stored samples for the rest."""
        if self.filter == "/DCTDecode":
            return Image.open(io.BytesIO(self.raw))
        space = self.colorspace
        if isinstance(space, ArrayObject):
            space = space[0].get_object() if hasattr(space[0], "get_object") else space[0]
        gray = space == "/DeviceGray" or (
            isinstance(self.colorspace, ArrayObject)
            and self.colorspace[0] == "/ICCBased"
            and int(self.colorspace[1].get_object()["/N"]) == 1
        )
        channels = 1 if gray else 3
        samples = self.stream.get_data()[: self.width * self.height * channels]
        return Image.frombytes("L" if gray else "RGB", (self.width, self.height), samples)


def images_in(data: bytes) -> list[Found]:
    """Every image XObject of the file, in object order."""
    reader = PdfReader(io.BytesIO(data))
    found = []
    for number in range(1, int(reader.trailer["/Size"])):
        try:
            entry = reader.get_object(number)
        except Exception:  # a free or damaged slot  # noqa: BLE001
            continue
        if not isinstance(entry, DictionaryObject) or entry.get("/Subtype") != "/Image":
            continue
        filters = entry.get("/Filter")
        if filters is None:
            names: tuple[str, ...] = ()
        elif isinstance(filters, ArrayObject):
            names = tuple(str(item) for item in filters)
        else:
            names = (str(filters),)
        found.append(
            Found(
                number,
                entry,
                int(entry["/Width"]),
                int(entry["/Height"]),
                names,
                bytes(entry._data),  # type: ignore[attr-defined]
            )
        )
    return found


def page_image_ids(data: bytes) -> list[int]:
    """The object number of the /Im1 image of each page that has one."""
    ids = []
    for page in PdfReader(io.BytesIO(data)).pages:
        xobjects = page["/Resources"].get("/XObject")
        if xobjects is not None and "/Im1" in xobjects:
            ids.append(xobjects.raw_get("/Im1").idnum)
    return ids


def render(data: bytes, scale: float = 0.5) -> Image.Image:
    """The first page drawn on white by pdfium."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    try:
        return document[0].render(scale=scale).to_pil().convert("RGB")
    finally:
        document.close()


def mean_difference(first: Image.Image, second: Image.Image) -> float:
    """The mean absolute difference of two same-size pictures, over all channels (0-255)."""
    from PIL import ImageChops, ImageStat

    assert first.size == second.size, (first.size, second.size)
    difference = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
    return sum(ImageStat.Stat(difference).mean) / 3
