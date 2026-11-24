"""Removing metadata from JPEG, PNG and WebP files without re-encoding.

The container is walked block by block: blocks that carry metadata are left
out, everything else is copied byte for byte, so the pixels and the quality
stay exactly as they were. Only the orientation is carried over, as a
minimal EXIF block that holds nothing else. Files the walk cannot vouch for
are refused rather than half cleaned.

Pure Python on bytes: no Pillow, no Click.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
import struct
from typing import Sequence
import zlib

from jzr_tools.core.batch import ConversionResult, _path_key
from jzr_tools.core.media import format_size

STRIPPABLE = frozenset({"jpg", "jpeg", "png", "webp"})
CLEAN_STEM = "_clean"
# A guard against crafted files with endless tiny blocks.
SEGMENT_LIMIT = 10_000

DAMAGED = "damaged or unsupported container; nothing written"
UNREADABLE = "some metadata could not be read safely; nothing written"
ANIMATED = "animated images are not supported"

# The words on the result line, in the order they are listed.
LABELS = (
    "EXIF",
    "GPS",
    "XMP",
    "IPTC",
    "comments",
    "text",
    "timestamp",
    "thumbnail",
    "print density",
    "colour profile",
    "other blocks",
    "video after the image",
    "data after the image",
)

_XMP_LIMIT = 1024 * 1024
_XMP_ORIENTATION = re.compile(rb"tiff:Orientation(?:\s*=\s*[\"']([1-8])[\"']|\s*>\s*([1-8])\s*<)")
_UNSAFE_TEXT = re.compile("[\x00-\x1f\x7f‪-‮⁦-⁩]")
_TIFF_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
_EXIF_ENTRIES = 512

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_KEPT = frozenset(
    {"IHDR", "PLTE", "IDAT", "IEND", "tRNS", "cHRM", "gAMA", "sRGB", "sBIT", "bKGD", "cICP", "mDCV", "cLLI"}
)
_PNG_FIXED = {"cHRM": 32, "gAMA": 4, "sRGB": 1, "cICP": 4, "mDCV": 24, "cLLI": 8}
_PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
_PNG_XMP_KEYWORD = b"XML:com.adobe.xmp\0"
_PNG_CHUNK_TYPE = re.compile(rb"[A-Za-z]{4}")


class StripError(Exception):
    """The file cannot be cleaned; ``str(error)`` is the text for its result line."""


@dataclass(frozen=True)
class Stripped:
    kind: str  # "jpeg", "png" or "webp"
    data: bytes
    removed: tuple[str, ...]  # in LABELS order
    kept: tuple[str, ...]  # "orientation", "colour profile"
    changed: bool


@dataclass
class ExifFields:
    camera: str | None = None
    date: str | None = None
    software: str | None = None
    gps: str | None = None
    orientation: int | None = None
    unreadable: bool = False


def orientation_exif(value: int) -> bytes:
    """A little-endian TIFF block whose only entry is Orientation = ``value``."""
    return bytes([73, 73, 42, 0, 8, 0, 0, 0, 1, 0, 18, 1, 3, 0, 1, 0, 0, 0, value, 0, 0, 0, 0, 0, 0, 0])


def read_exif(data: bytes) -> ExifFields:
    """Read a few fields from an EXIF (TIFF) block, with or without ``Exif\\0\\0``.

    The reader is bounded: it visits each IFD once, follows only the EXIF and
    GPS pointers, and never decodes thumbnails. Anything it cannot read sets
    ``unreadable``.
    """
    block = data[6:] if data[:6] == b"Exif\0\0" else data
    fields = ExifFields()
    try:
        _read_tiff(block, fields)
    except (ValueError, struct.error):
        fields.unreadable = True
    return fields


def _read_tiff(block: bytes, fields: ExifFields) -> None:
    if len(block) < 8 or block[:2] not in (b"II", b"MM"):
        raise ValueError
    order = "<" if block[:2] == b"II" else ">"

    def u16(at: int) -> int:
        return struct.unpack_from(order + "H", block, at)[0]

    def u32(at: int) -> int:
        return struct.unpack_from(order + "I", block, at)[0]

    if u16(2) != 42:
        raise ValueError
    visited: set[int] = set()
    entries = 0
    Entry = tuple[int, int, int]  # type, count, offset of the value

    def ifd(offset: int) -> dict[int, Entry]:
        nonlocal entries
        if offset < 8 or offset in visited:
            raise ValueError
        visited.add(offset)
        count = u16(offset)
        entries += count
        if entries > _EXIF_ENTRIES or offset + 2 + count * 12 + 4 > len(block):
            raise ValueError
        tags: dict[int, Entry] = {}
        for index in range(count):
            at = offset + 2 + index * 12
            tag, kind, number = u16(at), u16(at + 2), u32(at + 4)
            size = _TIFF_SIZES.get(kind)
            if not size:
                continue
            length = number * size
            value_at = at + 8 if length <= 4 else u32(at + 8)
            if length > len(block) or value_at + length > len(block) or tag in tags:
                raise ValueError
            tags[tag] = (kind, number, value_at)
        return tags

    def text(entry: Entry | None) -> str | None:
        if entry is None or entry[0] != 2 or not entry[1]:
            return None
        raw = block[entry[2] : entry[2] + min(entry[1], 256)].split(b"\0", 1)[0]
        return _UNSAFE_TEXT.sub("", raw.decode("utf-8", "replace")).strip() or None

    def pointer(entry: Entry | None) -> int:
        return u32(entry[2]) if entry is not None and entry[0] == 4 and entry[1] == 1 else 0

    root = ifd(u32(4))
    fields.camera = " ".join(part for part in (text(root.get(0x010F)), text(root.get(0x0110))) if part) or None
    fields.date = text(root.get(0x0132))
    fields.software = text(root.get(0x0131))
    orientation = root.get(0x0112)
    if orientation is not None:
        # The standard says SHORT; Samsung screenshots write a LONG, often 0
        # ("unspecified"), which viewers draw upright.
        if orientation[0] not in (3, 4) or orientation[1] != 1:
            raise ValueError
        value = u16(orientation[2]) if orientation[0] == 3 else u32(orientation[2])
        if value > 8:
            raise ValueError
        fields.orientation = value or None
    exif = pointer(root.get(0x8769))
    if exif:
        fields.date = text(ifd(exif).get(0x9003)) or fields.date
    gps = pointer(root.get(0x8825))
    if gps:
        tags = ifd(gps)

        def coordinate(tag: int, reference: int, limit: int) -> float:
            entry, direction = tags.get(tag), text(tags.get(reference))
            allowed = ("N", "S") if limit == 90 else ("E", "W")
            if entry is None or entry[0] != 5 or entry[1] != 3 or direction not in allowed:
                raise ValueError
            parts = []
            for index in range(3):
                denominator = u32(entry[2] + index * 8 + 4)
                if not denominator:
                    raise ValueError
                parts.append(u32(entry[2] + index * 8) / denominator)
            if parts[1] >= 60 or parts[2] >= 60:
                raise ValueError
            value = parts[0] + parts[1] / 60 + parts[2] / 3600
            if value > limit:
                raise ValueError
            return -value if direction in ("S", "W") else value

        # Cameras without GPS (and Photoshop) write a directory that holds
        # only the version: that is no position, not an unreadable one.
        if any(tag in tags for tag in (1, 2, 3, 4)):
            fields.gps = f"{coordinate(2, 1, 90):.6f}, {coordinate(4, 3, 180):.6f}"


class _Walk:
    """What one walk over a file has found so far."""

    def __init__(self, data: bytes, keep_color_profile: bool) -> None:
        self.data = data
        self.keep_color_profile = keep_color_profile
        self.parts: list[bytes] = []
        self.removed: set[str] = set()
        self.kept: set[str] = set()
        self.orientation: int | None = None
        self.unreadable = False
        self.exif_seen = False
        self.gps = False
        self.trailing: bytes | None = None
        # Where a fresh orientation block goes in ``parts``.
        self.orientation_at = 1
        self._segments = 0

    def count(self) -> None:
        self._segments += 1
        if self._segments > SEGMENT_LIMIT:
            raise StripError(DAMAGED)

    def exif(self, block: bytes) -> None:
        found = read_exif(block)
        if found.unreadable:
            # It may hold the orientation; never turn a photo sideways silently.
            self.unreadable = True
        self._orientation(found.orientation)
        if found.gps:
            self.gps = True
        self.exif_seen = True
        tiff = block[6:] if block[:6] == b"Exif\0\0" else block
        minimal = orientation_exif(found.orientation) if found.orientation and found.orientation != 1 else None
        if tiff != minimal:
            self.removed.add("EXIF")

    def xmp(self, block: bytes) -> None:
        self.removed.add("XMP")
        if len(block) > _XMP_LIMIT:
            self.unreadable = True
            return
        for match in _XMP_ORIENTATION.finditer(block):
            self._orientation(int(match.group(1) or match.group(2)))

    def _orientation(self, value: int | None) -> None:
        if not value:
            return
        if self.orientation and self.orientation != value:
            raise StripError(DAMAGED)
        self.orientation = value


def strip_bytes(data: bytes, *, keep_color_profile: bool = True) -> Stripped:
    """Return ``data`` without its metadata, or raise StripError."""
    walk = _Walk(data, keep_color_profile)
    if data[:2] == b"\xff\xd8":
        kind = "jpeg"
        _walk_jpeg(walk)
    elif data[:1] == b"\x89" and data[1:4] == b"PNG":
        kind = "png"
        _walk_png(walk)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = "webp"
        _walk_webp(walk)
    else:
        raise StripError(DAMAGED)

    if walk.unreadable:
        raise StripError(UNREADABLE)
    parts = walk.parts
    if walk.orientation and walk.orientation != 1:
        block = orientation_exif(walk.orientation)
        if kind == "png":
            chunk = struct.pack(">I", len(block)) + b"eXIf" + block + struct.pack(">I", zlib.crc32(b"eXIf" + block))
        elif kind == "webp":
            chunk = b"EXIF" + struct.pack("<I", len(block)) + block
        else:
            chunk = b"\xff\xe1" + struct.pack(">H", len(block) + 8) + b"Exif\0\0" + block
        parts.insert(walk.orientation_at, chunk)
        walk.kept.add("orientation")
        if kind == "webp":
            if parts[1][:4] != b"VP8X":
                raise StripError(DAMAGED)
            header = bytearray(parts[1])
            header[8] |= 8  # the EXIF flag
            parts[1] = bytes(header)
    if walk.gps and "EXIF" in walk.removed:
        walk.removed.add("GPS")
    if walk.trailing is not None:
        # An MP4 after the image is a phone's motion photo.
        walk.removed.add("video after the image" if b"ftyp" in walk.trailing[:4096] else "data after the image")

    output = b"".join(parts)
    if kind == "webp":
        output = output[:4] + struct.pack("<I", len(output) - 8) + output[8:]
    kept = tuple(label for label in ("orientation", "colour profile") if label in walk.kept)
    removed = tuple(label for label in LABELS if label in walk.removed)
    return Stripped(kind, output, removed, kept, output != data)


def _walk_jpeg(walk: _Walk) -> None:
    data, size = walk.data, len(walk.data)
    walk.parts.append(data[:2])
    at, frame, scan, end = 2, False, False, False
    while at < size:
        walk.count()
        start = at
        if data[at] != 0xFF:
            raise StripError(DAMAGED)
        at += 1
        while at < size and data[at] == 0xFF:
            at += 1
        if at >= size:
            raise StripError(DAMAGED)
        marker = data[at]
        at += 1
        if marker == 0xD9:  # EOI
            walk.parts.append(data[start:at])
            end = True
            if at < size:
                walk.trailing = data[at:]
            break
        if marker in (0xD8, 0x00) or 0xD0 <= marker <= 0xD7:
            raise StripError(DAMAGED)
        if marker == 0x01:  # TEM has no length
            walk.parts.append(data[start:at])
            continue
        if at + 2 > size:
            raise StripError(DAMAGED)
        length = struct.unpack_from(">H", data, at)[0]
        following = at + length
        if length < 2 or following > size:
            raise StripError(DAMAGED)
        body = data[at + 2 : following]
        if marker == 0xE1:
            if body.startswith(b"Exif\0\0"):
                walk.exif(body)
            elif body.startswith(b"http://ns.adobe.com/"):
                walk.xmp(body)
            else:
                walk.removed.add("other blocks")
        elif marker == 0xE2 and body.startswith(b"ICC_PROFILE\0") and walk.keep_color_profile:
            walk.kept.add("colour profile")
            walk.parts.append(data[start:following])
        elif marker == 0xE0 and body.startswith(b"JFIF\0"):
            walk.parts.append(_jfif_header(walk, data[start : at + 2 + 14], body))
            walk.orientation_at = len(walk.parts)
        elif marker == 0xEE and body.startswith(b"Adobe") and len(body) == 12:
            walk.parts.append(data[start:following])
        elif 0xE0 <= marker <= 0xEF or marker == 0xFE:
            if marker == 0xED:
                walk.removed.add("IPTC")
            elif marker == 0xFE:
                walk.removed.add("comments")
            elif marker == 0xE2 and body.startswith(b"ICC_PROFILE\0"):
                walk.removed.add("colour profile")
            else:
                walk.removed.add("other blocks")
        else:
            if marker in (0xC0, 0xC1, 0xC2):
                if len(body) < 6:
                    raise StripError(DAMAGED)
                frame = True
            walk.parts.append(data[start:following])
        at = following
        if marker == 0xDA:  # SOS: copy the entropy-coded data up to the next marker
            scan = True
            entropy = at
            at = _end_of_scan(data, at)
            walk.parts.append(data[entropy:at])
    if not (end and frame and scan):
        raise StripError(DAMAGED)


def _end_of_scan(data: bytes, at: int) -> int:
    """The offset of the first real marker after entropy-coded data at ``at``."""
    size = len(data)
    while True:
        at = data.find(b"\xff", at)
        if at < 0:
            return size
        following = at + 1
        while following < size and data[following] == 0xFF:
            following += 1
        if following < size and (data[following] == 0x00 or 0xD0 <= data[following] <= 0xD7):
            # A stuffed zero or a restart marker is part of the scan.
            at = following + 1
            continue
        return at


def _jfif_header(walk: _Walk, segment: bytes, body: bytes) -> bytes:
    """The JFIF segment cut to its 14-byte header, with no density and no thumbnail."""
    if len(body) < 14:
        raise StripError(DAMAGED)
    header = bytearray(segment)
    start = len(header) - 14
    header[start - 2 : start] = b"\x00\x10"  # length 16
    header[start + 7] = 0  # no density unit
    header[start + 8 : start + 12] = b"\x00\x01\x00\x01"  # density 1 x 1
    header[start + 12] = header[start + 13] = 0  # no thumbnail
    if len(body) > 14:
        walk.removed.add("thumbnail")
    if body[7] or body[8] or body[9] != 1 or body[10] or body[11] != 1:
        walk.removed.add("print density")
    return bytes(header)


def _walk_png(walk: _Walk) -> None:
    data, size = walk.data, len(walk.data)
    if data[:8] != _PNG_SIGNATURE:
        raise StripError(DAMAGED)
    walk.parts.append(data[:8])
    at, image_data, end, header = 8, False, False, False
    while at + 12 <= size:
        walk.count()
        length = struct.unpack_from(">I", data, at)[0]
        raw_type = data[at + 4 : at + 8]
        following = at + 12 + length
        if (
            following > size
            or not _PNG_CHUNK_TYPE.fullmatch(raw_type)
            or zlib.crc32(data[at + 4 : following - 4]) != struct.unpack_from(">I", data, following - 4)[0]
        ):
            raise StripError(DAMAGED)
        kind = raw_type.decode("ascii")
        if not header and (kind != "IHDR" or length != 13):
            raise StripError(DAMAGED)
        if kind == "IHDR":
            width, height = struct.unpack_from(">II", data, at + 8)
            depth, colour = data[at + 16], data[at + 17]
            if (
                header
                or not width
                or not height
                or depth not in _PNG_DEPTHS.get(colour, ())
                or data[at + 18]
                or data[at + 19]
                or data[at + 20] > 1
            ):
                raise StripError(DAMAGED)
            header = True
        _check_png_chunk(kind, length)
        body = data[at + 8 : following - 4]
        if kind == "eXIf":
            walk.exif(body)
        elif kind == "iCCP" and walk.keep_color_profile:
            walk.parts.append(data[at:following])
            walk.kept.add("colour profile")
        elif kind in _PNG_KEPT:
            walk.parts.append(data[at:following])
            if kind == "IHDR":
                walk.orientation_at = len(walk.parts)
        else:
            if kind[0].isupper():  # an unknown critical chunk
                raise StripError(DAMAGED)
            if kind == "iCCP":
                walk.removed.add("colour profile")
            elif kind in ("tEXt", "zTXt"):
                walk.removed.add("text")
            elif kind == "iTXt":
                walk.removed.add("XMP" if body.startswith(_PNG_XMP_KEYWORD) else "text")
            elif kind == "tIME":
                walk.removed.add("timestamp")
            else:
                walk.removed.add("other blocks")
        if kind == "IDAT":
            image_data = True
        at = following
        if kind == "IEND":
            if length:
                raise StripError(DAMAGED)
            end = True
            if at < size:
                walk.trailing = data[at:]
            break
    if not (end and image_data):
        raise StripError(DAMAGED)


def _check_png_chunk(kind: str, length: int) -> None:
    if kind in ("acTL", "fcTL", "fdAT"):
        raise StripError(ANIMATED)
    if (
        (kind in _PNG_FIXED and length != _PNG_FIXED[kind])
        or (kind == "PLTE" and (not length or length > 768 or length % 3))
        or (kind == "tRNS" and (not length or length > 256))
        or (kind == "bKGD" and length not in (1, 2, 6))
        or (kind == "sBIT" and not 1 <= length <= 4)
    ):
        raise StripError(DAMAGED)


def _walk_webp(walk: _Walk) -> None:
    data, size = walk.data, len(walk.data)
    riff_end = struct.unpack_from("<I", data, 4)[0] + 8 if size >= 20 else 0
    if riff_end < 20 or riff_end > size:
        raise StripError(DAMAGED)
    if riff_end < size:
        # Some phones append a trailer after the RIFF payload.
        walk.trailing = data[riff_end:]
    walk.parts.append(data[:12])
    at, images, extended = 12, 0, False
    while at + 8 <= riff_end:
        walk.count()
        kind = data[at : at + 4]
        length = struct.unpack_from("<I", data, at + 4)[0]
        following = at + 8 + length + length % 2
        if following > riff_end:
            raise StripError(DAMAGED)
        body = data[at + 8 : at + 8 + length]
        if kind in (b"ANIM", b"ANMF") or (kind == b"VP8X" and body[:1] and body[0] & 2):
            raise StripError(ANIMATED)
        if kind == b"EXIF":
            walk.exif(body)
        elif kind == b"XMP ":
            walk.xmp(body)
        elif kind == b"VP8X":
            if extended or at != 12 or length != 10:
                raise StripError(DAMAGED)
            extended = True
            chunk = bytearray(data[at:following])
            # Clear the EXIF and XMP flags, and the ICC one when the profile goes.
            chunk[8] &= ~(8 | 4 | (0 if walk.keep_color_profile else 32)) & 0xFF
            walk.parts.append(bytes(chunk))
        elif kind == b"ICCP" and walk.keep_color_profile:
            walk.parts.append(data[at:following])
            walk.kept.add("colour profile")
        elif kind in (b"VP8 ", b"VP8L", b"ALPH"):
            if kind != b"ALPH":
                images += 1
            walk.parts.append(data[at:following])
        elif kind == b"ICCP":
            walk.removed.add("colour profile")
        else:
            walk.removed.add("other blocks")
        at = following
    if at != riff_end or images != 1 or (walk.exif_seen and not extended):
        raise StripError(DAMAGED)
    walk.orientation_at = len(walk.parts)


def not_strippable(extension: str) -> str | None:
    """None for JPEG, PNG and WebP; otherwise the result text for the file."""
    name = extension.lstrip(".").lower()
    if name in STRIPPABLE:
        return None
    shown = f".{name}" if name else "this format"
    return (
        f"{shown} metadata cannot be removed without re-encoding; "
        "use jzr image compress --strip (or convert)"
    )


def clean_name(source: Path) -> str:
    return f"{source.stem}{CLEAN_STEM}{source.suffix}"


def clean_targets(
    sources: Sequence[Path], *, outdir: Path | None, root: Path | None
) -> list[Path]:
    """One distinct output path per source, in order, as compress names them."""
    claimed: set[str] = set()
    targets: list[Path] = []
    for source in sources:
        directory = outdir if outdir is not None else source.parent
        if outdir is not None and root is not None:
            try:
                directory = outdir / source.parent.relative_to(root)
            except ValueError:
                directory = outdir
        target = directory / clean_name(source)
        candidate = target
        counter = 2
        while _path_key(candidate) in claimed:
            candidate = target.with_name(f"{target.stem}-{counter}{target.suffix}")
            counter += 1
        claimed.add(_path_key(candidate))
        targets.append(candidate)
    return targets


def removed_text(stripped: Stripped, before: int, *, dry_run: bool = False) -> str:
    """``removed EXIF, GPS; 48 KB smaller`` (``would remove ...`` in a dry run)."""
    text = ("would remove " if dry_run else "removed ") + ", ".join(stripped.removed)
    saved = before - len(stripped.data)
    return f"{text}; {format_size(saved)} smaller" if saved > 0 else text


def _write(target: Path, data: bytes) -> None:
    """Write through a temporary name in the target's folder and a rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"jzr-tmp-{secrets.token_hex(8)}{target.suffix}")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def strip_file(
    source: Path,
    target: Path,
    *,
    keep_color_profile: bool,
    overwrite: bool,
    dry_run: bool,
) -> ConversionResult:
    """Clean one file into ``target``, returning what happened instead of raising.

    ``target`` equal to ``source`` with ``overwrite`` cleans the file in place.
    """

    def done(status: str, detail: str = "") -> ConversionResult:
        return ConversionResult(source, target, (), status, detail)

    reason = not_strippable(source.suffix)
    if reason is not None:
        return done("failed", reason)
    in_place = _path_key(source) == _path_key(target)
    if in_place and not overwrite:
        return done("skipped", "source is the target")
    if not in_place and target.exists() and not overwrite:
        return done("skipped", "target exists (use --overwrite)")
    try:
        data = source.read_bytes()
        stripped = strip_bytes(data, keep_color_profile=keep_color_profile)
        if not stripped.changed:
            return done("skipped", "no metadata to remove")
        if dry_run:
            return done("planned", removed_text(stripped, len(data), dry_run=True))
        _write(target, stripped.data)
    except StripError as error:
        return done("failed", str(error))
    except OSError as error:
        return done("failed", error.strerror or str(error))
    return done("cleaned", removed_text(stripped, len(data)))
