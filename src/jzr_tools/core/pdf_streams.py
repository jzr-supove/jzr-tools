"""Image stream data for PDFs: Flate encodings and JPEG headers.

Shared by `jzr pdf compress` and the image-to-PDF tools. Pillow is imported
only by the function that needs it.
"""

from __future__ import annotations

import io
import struct
from typing import TYPE_CHECKING
import zlib

if TYPE_CHECKING:
    from PIL import Image


def flate_raw(pixels: bytes) -> bytes:
    """Samples compressed with zlib level 9; read without DecodeParms."""
    return zlib.compress(pixels, 9)


def flate_predicted(image: Image.Image) -> bytes:
    """PNG-predicted rows (PDF Predictor 15) of an 8-bit L or RGB image.

    Pillow writes the image as PNG, choosing a filter for each row; the data
    of its IDAT chunks is exactly that zlib stream.
    """
    buffer = io.BytesIO()
    image.save(buffer, "PNG", compress_level=9)
    data = buffer.getvalue()
    parts = []
    at = 8
    while at + 8 <= len(data):
        (length,) = struct.unpack(">I", data[at : at + 4])
        kind = data[at + 4 : at + 8]
        if kind == b"IDAT":
            parts.append(data[at + 8 : at + 8 + length])
        elif kind == b"IEND":
            break
        at += 12 + length
    return b"".join(parts)


def jpeg_components(data: bytes) -> int | None:
    """The number of colour components in the first SOF segment, or None when unreadable."""
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i + 3 < n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        if marker in (0xD9, 0xDA):
            return None
        length = (data[i + 2] << 8) | data[i + 3]
        if length < 2:
            return None
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return data[i + 9] if i + 9 < n else None
        i += 2 + length
    return None
