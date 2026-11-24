"""Builders for JPEG, PNG and WebP files that carry metadata.

The files are assembled from raw bytes around image data that Pillow encodes,
so a test knows exactly which segments, chunks and bytes are in a file and can
state what the cleaned copy must contain.
"""

from __future__ import annotations

import io
import random
import struct
import zlib

from PIL import Image, ImageCms

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# The marker of an unrelated MP4 box, as phones append after a motion photo.
MOTION_VIDEO = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x11" * 64


def noise(width: int = 48, height: int = 32, seed: int = 1, mode: str = "RGB") -> Image.Image:
    channels = len(mode)
    data = random.Random(seed).randbytes(width * height * channels)
    return Image.frombytes(mode, (width, height), data)


_PROFILE: list[bytes] = []


def icc_profile() -> bytes:
    """One sRGB profile for the whole run (a new one carries its creation time)."""
    if not _PROFILE:
        _PROFILE.append(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    return _PROFILE[0]


# --- EXIF -------------------------------------------------------------------


def ascii_entry(tag: int, text: str) -> tuple[int, int, int, bytes]:
    raw = text.encode("utf-8") + b"\0"
    return tag, 2, len(raw), raw


def short_entry(tag: int, value: int, *, big: bool = False) -> tuple[int, int, int, bytes]:
    return tag, 3, 1, struct.pack(">H" if big else "<H", value)


def long_entry(tag: int, value: int, *, big: bool = False) -> tuple[int, int, int, bytes]:
    return tag, 4, 1, struct.pack(">I" if big else "<I", value)


def rational_entry(
    tag: int, pairs: list[tuple[int, int]], *, big: bool = False
) -> tuple[int, int, int, bytes]:
    fmt = ">II" if big else "<II"
    return tag, 5, len(pairs), b"".join(struct.pack(fmt, n, d) for n, d in pairs)


def tiff_block(
    root: list[tuple[int, int, int, bytes]],
    subs: dict[int, list[tuple[int, int, int, bytes]]] | None = None,
    *,
    big: bool = False,
) -> bytes:
    """A TIFF structure: one root directory and the directories it points to."""
    e = ">" if big else "<"
    subs = subs or {}

    def block(entries: list[tuple[int, int, int, bytes]], base: int) -> bytes:
        entries = sorted(entries, key=lambda entry: entry[0])
        slots = b""
        data = b""
        offset = base + 2 + 12 * len(entries) + 4
        for tag, kind, count, raw in entries:
            if len(raw) <= 4:
                value = raw.ljust(4, b"\0")
            else:
                value = struct.pack(e + "I", offset)
                padded = raw + (b"\0" if len(raw) % 2 else b"")
                data += padded
                offset += len(padded)
            slots += struct.pack(e + "HHI", tag, kind, count) + value
        return struct.pack(e + "H", len(entries)) + slots + struct.pack(e + "I", 0) + data

    def root_block(pointers: dict[int, int]) -> bytes:
        pointer_entries = [(tag, 4, 1, struct.pack(e + "I", at)) for tag, at in pointers.items()]
        return block(list(root) + pointer_entries, 8)

    pointers = {tag: 0 for tag in subs}
    at = 8 + len(root_block(pointers))
    sub_blocks = []
    for tag, entries in subs.items():
        pointers[tag] = at
        sub_blocks.append(block(entries, at))
        at += len(sub_blocks[-1])
    header = struct.pack(e + "2sHI", b"MM" if big else b"II", 42, 8)
    return header + root_block(pointers) + b"".join(sub_blocks)


def photo_tiff(
    orientation: int | None = 6,
    *,
    big: bool = False,
    south_west: bool = False,
    gps: bool = True,
) -> bytes:
    """EXIF of a phone photo: camera, software, dates, Orientation and a GPS position.

    The GPS position is 41.5 N, 69.25 E (or S and W) so its text is exact.
    """
    root = [
        ascii_entry(0x010F, "AcmeCam"),
        ascii_entry(0x0110, "X1"),
        ascii_entry(0x0131, "1.2.3"),
        ascii_entry(0x0132, "2026:10:02 14:31:05"),
    ]
    if orientation is not None:
        root.append(short_entry(0x0112, orientation, big=big))
    subs = {0x8769: [ascii_entry(0x9003, "2026:10:03 09:00:00")]}
    if gps:
        subs[0x8825] = [
            ascii_entry(1, "S" if south_west else "N"),
            rational_entry(2, [(41, 1), (30, 1), (0, 1)], big=big),
            ascii_entry(3, "W" if south_west else "E"),
            rational_entry(4, [(69, 1), (15, 1), (0, 1)], big=big),
        ]
    return tiff_block(root, subs, big=big)


def orientation_only_tiff(value: int) -> bytes:
    """Byte for byte what a minimal Orientation block looks like."""
    return b"II*\0\x08\0\0\0\x01\0\x12\x01\x03\0\x01\0\0\0" + bytes([value]) + b"\0\0\0" + b"\0" * 4


# --- JPEG ---------------------------------------------------------------------


def segment(marker: int, payload: bytes) -> bytes:
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def jfif(
    *, units: int = 0, density: tuple[int, int] = (1, 1), thumbnail: tuple[int, int] = (0, 0)
) -> bytes:
    width, height = thumbnail
    pixels = bytes(range(1, 1 + 3 * width * height)) if width and height else b""
    return segment(
        0xE0,
        b"JFIF\0\x01\x02" + bytes([units]) + struct.pack(">HH", *density) + bytes([width, height]) + pixels,
    )


JFIF_CLEAN = jfif()
JFIF_DIRTY = jfif(units=1, density=(72, 72), thumbnail=(2, 2))


def exif_app1(tiff: bytes) -> bytes:
    return segment(0xE1, b"Exif\0\0" + tiff)


def xmp_packet(orientation: str | None = None) -> bytes:
    description = f' tiff:Orientation="{orientation}"' if orientation else ""
    return (
        b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description xmlns:tiff="http://ns.adobe.com/tiff/1.0/"'
        + description.encode()
        + b' xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Someone</dc:creator>'
        b"</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end=\"w\"?>"
    )


def xmp_app1(packet: bytes | None = None) -> bytes:
    return segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + (packet or xmp_packet()))


def icc_app2() -> bytes:
    return segment(0xE2, b"ICC_PROFILE\0\x01\x01" + icc_profile())


COMMENT = segment(0xFE, b"shot on a very nice phone")
IPTC = segment(0xED, b"Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00\x00\x08caption!")
UNKNOWN_APP5 = segment(0xE5, b"VENDOR\0private data that names the owner")
UNKNOWN_APP1 = segment(0xE1, b"Other\0not exif and not xmp")
ADOBE_APP14 = segment(0xEE, b"Adobe" + b"\x00\x64" + b"\x80\x00" + b"\x00\x00" + b"\x01")
LONG_ADOBE_APP14 = segment(0xEE, b"Adobe" + b"\x00\x64\x80\x00\x00\x00\x01" + b"extra")


def plain_jpeg(width: int = 48, height: int = 32, seed: int = 1, **options) -> bytes:  # noqa: ANN003
    buffer = io.BytesIO()
    noise(width, height, seed).save(buffer, "JPEG", quality=90, **options)
    return buffer.getvalue()


def jpeg_parts(data: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """The segments before the first scan as (marker, bytes), and the rest from that scan on."""
    assert data[:2] == SOI
    at = 2
    found: list[tuple[int, bytes]] = []
    while True:
        assert data[at] == 0xFF, at
        marker = data[at + 1]
        if marker == 0xDA:
            return found, data[at:]
        length = struct.unpack(">H", data[at + 2 : at + 4])[0]
        end = at + 2 + length
        found.append((marker, data[at:end]))
        at = end


def jpeg_body(data: bytes) -> bytes:
    """Everything after SOI except the APPn and COM segments (tables, frame, scan, EOI)."""
    found, scan = jpeg_parts(data)
    kept = b"".join(
        raw for marker, raw in found if not (0xE0 <= marker <= 0xEF or marker == 0xFE)
    )
    return kept + scan


def build_jpeg(segments: list[bytes], body: bytes, trailer: bytes = b"") -> bytes:
    return SOI + b"".join(segments) + body + trailer


def pixels(data: bytes) -> tuple[str, tuple[int, int], bytes]:
    """Mode, size and decoded bytes of an image, to compare two files' pictures."""
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return image.mode, image.size, image.tobytes()


# --- PNG --------------------------------------------------------------------------


def png_chunk(kind: bytes, payload: bytes, crc: int | None = None) -> bytes:
    if crc is None:
        crc = zlib.crc32(kind + payload)
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    assert data[:8] == PNG_SIGNATURE
    at = 8
    found = []
    while at < len(data):
        length = struct.unpack(">I", data[at : at + 4])[0]
        found.append((data[at + 4 : at + 8], data[at + 8 : at + 8 + length]))
        at += 12 + length
    return found


def plain_png(width: int = 24, height: int = 16, seed: int = 1, **options) -> bytes:  # noqa: ANN003
    buffer = io.BytesIO()
    noise(width, height, seed).save(buffer, "PNG", **options)
    return buffer.getvalue()


def png_header_and_data(data: bytes) -> tuple[bytes, bytes, bytes]:
    """The whole IHDR chunk, the IDAT chunks joined, and the IEND chunk of a PNG."""
    chunks = png_chunks(data)
    ihdr = png_chunk(b"IHDR", dict(chunks)[b"IHDR"])
    idat = b"".join(png_chunk(kind, payload) for kind, payload in chunks if kind == b"IDAT")
    return ihdr, idat, png_chunk(b"IEND", b"")


def text_chunk(keyword: str = "Comment", text: str = "taken at home") -> bytes:
    return png_chunk(b"tEXt", keyword.encode() + b"\0" + text.encode())


def ztxt_chunk() -> bytes:
    return png_chunk(b"zTXt", b"Description\0\0" + zlib.compress(b"a long note about the place"))


def itxt_chunk(keyword: str, text: bytes) -> bytes:
    return png_chunk(b"iTXt", keyword.encode() + b"\0\0\0\0\0" + text)


def iccp_chunk() -> bytes:
    return png_chunk(b"iCCP", b"sRGB\0\0" + zlib.compress(icc_profile()))


def build_png(*chunks: bytes) -> bytes:
    return PNG_SIGNATURE + b"".join(chunks)


# --- WebP ---------------------------------------------------------------------------


def webp_chunk(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")


def riff(*chunks: bytes) -> bytes:
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def vp8x(flags: int, width: int, height: int) -> bytes:
    payload = bytes([flags, 0, 0, 0]) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return webp_chunk(b"VP8X", payload)


VP8X_ICC = 0x20
VP8X_ALPHA = 0x10
VP8X_EXIF = 0x08
VP8X_XMP = 0x04
VP8X_ANIMATION = 0x02


def lossless_webp_image(width: int = 40, height: int = 30, seed: int = 1) -> tuple[bytes, bytes]:
    """The encoded VP8L chunk of a lossless picture, and the whole simple file."""
    buffer = io.BytesIO()
    noise(width, height, seed).save(buffer, "WEBP", lossless=True)
    data = buffer.getvalue()
    at = 12
    while at < len(data):
        kind = data[at : at + 4]
        length = struct.unpack("<I", data[at + 4 : at + 8])[0]
        if kind == b"VP8L":
            return data[at : at + 8 + length + length % 2], data
        at += 8 + length + length % 2
    raise AssertionError("Pillow wrote no VP8L chunk")
