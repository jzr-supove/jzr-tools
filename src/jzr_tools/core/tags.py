"""Read and edit the tags of audio files with mutagen.

One set of keys works across MP3 (ID3v2), M4A (MP4 atoms), FLAC and Ogg
Vorbis/Opus (Vorbis comments). Edits change only the tags: the file is
copied to a temporary name next to it, the copy is edited, and the copy
replaces the original, so a failed write leaves the original as it was.

mutagen is imported inside the functions, so importing this module is cheap.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import struct
from typing import Any, Callable, Mapping

KEYS = (
    "title",
    "artist",
    "album",
    "albumartist",
    "year",
    "track",
    "disc",
    "genre",
    "comment",
    "composer",
)
UNSET_KEYS = KEYS + ("lyrics",)
FORMATS = ("mp3", "m4a", "flac", "ogg", "opus")

_UNSUPPORTED = "not a supported audio file (" + ", ".join(FORMATS) + ")"
_YEAR = re.compile(r"\d{4}(?:-\d{2}(?:-\d{2})?)?")
_NUMBER = re.compile(r"(\d+)(?:/(\d+))?")
_SEPARATOR = "; "
_FRONT_COVER = 3


class TagError(Exception):
    """A file whose tags cannot be read or written; the message is for users."""


@dataclass(frozen=True)
class Cover:
    """An embedded picture: its type (``image/jpeg``, ``image/png``) and bytes."""

    mime: str
    data: bytes


@dataclass(frozen=True)
class TagSet:
    """The tags of one file in the common keys."""

    format: str
    values: dict[str, str]
    cover: Cover | None
    lyrics: str | None


@dataclass(frozen=True)
class TagEdit:
    """What to change. Applied in this order: clear, unset, values, cover, lyrics."""

    values: Mapping[str, str] = field(default_factory=dict)
    unset: frozenset[str] = frozenset()
    clear: bool = False
    cover: Cover | None = None
    remove_cover: bool = False
    lyrics: str | None = None


# --- values -----------------------------------------------------------------


def check_value(key: str, value: str) -> str:
    """Return ``value`` stripped, or raise ``ValueError`` if ``key`` cannot hold it."""
    text = value.strip()
    if not text:
        raise ValueError(f"{key} needs a value; use --unset {key} to remove it")
    if key == "year" and _YEAR.fullmatch(text) is None:
        raise ValueError(f"year must be YYYY, YYYY-MM or YYYY-MM-DD, not {value!r}")
    if key in ("track", "disc"):
        match = _NUMBER.fullmatch(text)
        numbers = [int(part) for part in match.groups() if part] if match else []
        if not numbers or not all(1 <= number <= 65535 for number in numbers):
            raise ValueError(f"{key} must be N or N/M (1 to 65535), not {value!r}")
        text = "/".join(str(number) for number in numbers)
    return text


def check_key(key: str, keys: tuple[str, ...] = KEYS) -> str:
    """Return ``key`` lower-cased, or raise ``ValueError`` naming the known keys."""
    name = key.strip().lower()
    if name not in keys:
        raise ValueError(f"unknown tag {key!r}; use one of: {', '.join(keys)}")
    return name


def parse_assignment(text: str) -> tuple[str, str]:
    """Split ``key=value`` into a checked key and value."""
    key, sep, value = text.partition("=")
    if not sep:
        raise ValueError(f"expected KEY=VALUE, got {text!r}")
    name = check_key(key)
    return name, check_value(name, value)


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def load_cover(path: Path) -> Cover:
    """Read a JPEG or PNG file, telling the type from its bytes."""
    data = Path(path).read_bytes()
    mime = _sniff(data)
    if mime is None:
        raise ValueError(f"{path} is not a JPEG or PNG image")
    return Cover(mime, data)


def _cover(data: bytes, declared: str = "") -> Cover:
    mime = _sniff(data) or (declared or "application/octet-stream").lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    return Cover(mime, bytes(data))


def _image_size(cover: Cover) -> tuple[int, int]:
    """Width and height from a PNG or JPEG header; ``(0, 0)`` when unknown."""
    data = cover.data
    if cover.mime == "image/png" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    index = 2
    while cover.mime == "image/jpeg" and index + 9 < len(data):
        if data[index] != 0xFF:
            break
        marker = data[index + 1]
        length = struct.unpack(">H", data[index + 2:index + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[index + 5:index + 9])
            return width, height
        index += 2 + length
    return 0, 0


# --- opening ----------------------------------------------------------------


def _sniff_format(path: Path) -> str | None:
    """Name the format from the first bytes; ``None`` when it is none of ours.

    mutagen's own guess weighs the file name heavily, so an M4A named
    ``.flac`` would be read as a broken FLAC; the content decides here.
    """
    with open(path, "rb") as handle:
        head = handle.read(64)
        if head.startswith(b"ID3") and len(head) >= 10:
            # An ID3 tag in front of FLAC happens; skip it to see what follows.
            size = 10 + sum((byte & 0x7F) << (7 * (3 - i)) for i, byte in enumerate(head[6:10]))
            handle.seek(size)
            if handle.read(4) == b"fLaC":
                return "flac"
            return "mp3"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        if b"OpusHead" in head:
            return "opus"
        if b"\x01vorbis" in head:
            return "ogg"
        return None
    if head[4:8] == b"ftyp":
        return "m4a"
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        return "mp3"
    return None


def _class_for(fmt: str) -> type:
    if fmt == "mp3":
        from mutagen.mp3 import MP3

        return MP3
    if fmt == "m4a":
        from mutagen.mp4 import MP4

        return MP4
    if fmt == "flac":
        from mutagen.flac import FLAC

        return FLAC
    if fmt == "opus":
        from mutagen.oggopus import OggOpus

        return OggOpus
    from mutagen.oggvorbis import OggVorbis

    return OggVorbis


def _os_reason(error: BaseException) -> str | None:
    """The OS error behind ``error`` (mutagen wraps them), as plain text."""
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, OSError) and not type(seen).__module__.startswith("mutagen"):
            return seen.strerror or str(seen)
        seen = seen.__cause__ or seen.__context__
    return None


def _open(path: Path) -> tuple[Any, str]:
    from mutagen import MutagenError

    try:
        fmt = _sniff_format(path)
        if fmt is None:
            raise TagError(_UNSUPPORTED)
        return _class_for(fmt)(path), fmt
    except (MutagenError, OSError) as error:
        reason = _os_reason(error)
        raise TagError(f"cannot open: {reason}" if reason else _UNSUPPORTED) from error


# --- reading ----------------------------------------------------------------

_ID3_TEXT = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "albumartist": "TPE2",
    "year": "TDRC",
    "track": "TRCK",
    "disc": "TPOS",
    "composer": "TCOM",
}
_MP4_TEXT = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "albumartist": "aART",
    "year": "\xa9day",
    "genre": "\xa9gen",
    "comment": "\xa9cmt",
    "composer": "\xa9wrt",
}
_MP4_NUMBERS = {"track": "trkn", "disc": "disk"}
# Vorbis comment names: the one written first, then names other taggers use.
_VORBIS = {
    "title": ("title",),
    "artist": ("artist",),
    "album": ("album",),
    "albumartist": ("albumartist", "album artist"),
    "year": ("date", "year"),
    "genre": ("genre",),
    "comment": ("comment", "description"),
    "composer": ("composer",),
    "lyrics": ("lyrics", "unsyncedlyrics"),
}
_VORBIS_NUMBERS = {
    "track": ("tracknumber", ("tracktotal", "totaltracks")),
    "disc": ("discnumber", ("disctotal", "totaldiscs")),
}


def _join(items: Any) -> str:
    return _SEPARATOR.join(str(item) for item in items if str(item).strip())


def _comm_frames(tags: Any) -> list[str]:
    return [key for key, frame in tags.items() if key.startswith("COMM") and frame.desc == ""]


def _read_id3(tags: Any) -> tuple[dict[str, str], Cover | None, str | None]:
    values: dict[str, str] = {}
    if tags is None:
        return values, None, None
    for key, frame_id in _ID3_TEXT.items():
        frame = tags.get(frame_id)
        if frame is not None:
            values[key] = _join(frame.text)
    genre = tags.get("TCON")
    if genre is not None:
        values["genre"] = _join(genre.genres)
    comments = _comm_frames(tags)
    if comments:
        values["comment"] = _join(tags[comments[0]].text)

    pictures = tags.getall("APIC")
    pictures.sort(key=lambda frame: frame.type != _FRONT_COVER)
    cover = _cover(pictures[0].data, pictures[0].mime) if pictures else None
    lyrics_frames = tags.getall("USLT")
    lyrics_frames.sort(key=lambda frame: frame.desc != "")
    lyrics = lyrics_frames[0].text if lyrics_frames else None
    return values, cover, lyrics


def _read_mp4(tags: Any) -> tuple[dict[str, str], Cover | None, str | None]:
    values: dict[str, str] = {}
    if tags is None:
        return values, None, None
    for key, atom in _MP4_TEXT.items():
        if atom in tags:
            values[key] = _join(tags[atom])
    for key, atom in _MP4_NUMBERS.items():
        pairs = tags.get(atom)
        if pairs and pairs[0][0]:
            number, total = (tuple(pairs[0]) + (0,))[:2]
            values[key] = f"{number}/{total}" if total else str(number)
    cover = None
    if tags.get("covr"):
        picture = tags["covr"][0]
        from mutagen.mp4 import MP4Cover

        declared = "image/png" if picture.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
        cover = _cover(bytes(picture), declared)
    lyrics = _join(tags["\xa9lyr"]) if tags.get("\xa9lyr") else None
    return values, cover, lyrics


def _vorbis_get(tags: Any, names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in tags:
            text = _join(tags[name])
            if text:
                return text
    return None


def _read_vorbis(tags: Any, pictures: list[Any]) -> tuple[dict[str, str], Cover | None, str | None]:
    values: dict[str, str] = {}
    lyrics = None
    if tags is not None:
        for key, names in _VORBIS.items():
            text = _vorbis_get(tags, names)
            if text is None:
                continue
            if key == "lyrics":
                lyrics = text
            else:
                values[key] = text
        for key, (number_name, total_names) in _VORBIS_NUMBERS.items():
            number = _vorbis_get(tags, (number_name,))
            if number is None:
                continue
            total = _vorbis_get(tags, total_names)
            values[key] = f"{number}/{total}" if total and "/" not in number else number

    pictures = sorted(pictures, key=lambda picture: picture.type != _FRONT_COVER)
    cover = _cover(pictures[0].data, pictures[0].mime) if pictures else None
    return values, cover, lyrics


def _ogg_pictures(tags: Any) -> list[Any]:
    """Pictures in Vorbis comments; a picture tag that does not decode is skipped."""
    from mutagen import MutagenError
    from mutagen.flac import Picture

    if tags is None:
        return []
    found = []
    for encoded in tags.get("metadata_block_picture", []):
        try:
            found.append(Picture(base64.b64decode(encoded)))
        except (ValueError, struct.error, MutagenError):
            continue
    if not found and "coverart" in tags:
        picture = Picture()
        try:
            picture.data = base64.b64decode(tags["coverart"][0])
        except ValueError:
            return found
        picture.mime = (tags.get("coverartmime") or [""])[0]
        found.append(picture)
    return found


def _read(audio: Any, fmt: str) -> tuple[dict[str, str], Cover | None, str | None]:
    if fmt == "mp3":
        return _read_id3(audio.tags)
    if fmt == "m4a":
        return _read_mp4(audio.tags)
    if fmt == "flac":
        return _read_vorbis(audio.tags, list(audio.pictures))
    return _read_vorbis(audio.tags, _ogg_pictures(audio.tags))


def read_tags(path: Path) -> TagSet:
    """Read the common tags, the cover and the lyrics of ``path``."""
    audio, fmt = _open(Path(path))
    values, cover, lyrics = _read(audio, fmt)
    ordered = {key: values[key] for key in KEYS if values.get(key)}
    return TagSet(fmt, ordered, cover, lyrics)


# --- editing ----------------------------------------------------------------


def _edit_id3(audio: Any, edit: TagEdit) -> None:
    from mutagen import id3

    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if edit.clear:
        tags.clear()

    def remove(key: str) -> None:
        if key == "comment":
            for frame_key in _comm_frames(tags):
                del tags[frame_key]
        elif key == "genre":
            tags.delall("TCON")
        elif key == "lyrics":
            tags.delall("USLT")
        else:
            tags.delall(_ID3_TEXT[key])

    for key in edit.unset:
        remove(key)
    for key, value in edit.values.items():
        remove(key)
        if key == "comment":
            tags.add(id3.COMM(encoding=3, lang="eng", desc="", text=[value]))
        elif key == "genre":
            tags.add(id3.TCON(encoding=3, text=[value]))
        else:
            frame_id = _ID3_TEXT[key]
            tags.add(getattr(id3, frame_id)(encoding=3, text=[value]))
    if edit.remove_cover or edit.cover is not None:
        tags.delall("APIC")
    if edit.cover is not None:
        tags.add(
            id3.APIC(
                encoding=3, mime=edit.cover.mime, type=_FRONT_COVER, desc="", data=edit.cover.data
            )
        )
    if edit.lyrics is not None:
        tags.delall("USLT")
        tags.add(id3.USLT(encoding=3, lang="eng", desc="", text=edit.lyrics))


def _edit_mp4(audio: Any, edit: TagEdit) -> None:
    from mutagen.mp4 import MP4Cover

    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if edit.clear:
        tags.clear()

    def atom(key: str) -> str:
        return "\xa9lyr" if key == "lyrics" else _MP4_TEXT.get(key) or _MP4_NUMBERS[key]

    for key in edit.unset:
        tags.pop(atom(key), None)
    for key, value in edit.values.items():
        if key in _MP4_NUMBERS:
            number, _, total = value.partition("/")
            tags[atom(key)] = [(int(number), int(total or 0))]
        else:
            tags[atom(key)] = [value]
    if edit.remove_cover:
        tags.pop("covr", None)
    if edit.cover is not None:
        image_format = MP4Cover.FORMAT_PNG if edit.cover.mime == "image/png" else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(edit.cover.data, imageformat=image_format)]
    if edit.lyrics is not None:
        tags["\xa9lyr"] = [edit.lyrics]


def _picture(cover: Cover) -> Any:
    from mutagen.flac import Picture

    picture = Picture()
    picture.type = _FRONT_COVER
    picture.mime = cover.mime
    picture.desc = ""
    picture.data = cover.data
    picture.width, picture.height = _image_size(cover)
    picture.depth = 24
    return picture


def _vorbis_names(key: str) -> tuple[str, ...]:
    if key in _VORBIS_NUMBERS:
        number_name, total_names = _VORBIS_NUMBERS[key]
        return (number_name, *total_names)
    return _VORBIS[key]


def _edit_vorbis(audio: Any, edit: TagEdit, *, flac: bool) -> None:
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if edit.clear:
        tags.clear()
        if flac:
            audio.clear_pictures()

    def remove(key: str) -> None:
        for name in _vorbis_names(key):
            if name in tags:
                del tags[name]

    for key in edit.unset:
        remove(key)
    for key, value in edit.values.items():
        remove(key)
        if key in _VORBIS_NUMBERS:
            number_name, total_names = _VORBIS_NUMBERS[key]
            number, _, total = value.partition("/")
            tags[number_name] = [number]
            if total:
                tags[total_names[0]] = [total]
        else:
            tags[_VORBIS[key][0]] = [value]

    if edit.remove_cover or edit.cover is not None:
        if flac:
            audio.clear_pictures()
        else:
            for name in ("metadata_block_picture", "coverart", "coverartmime"):
                if name in tags:
                    del tags[name]
    if edit.cover is not None:
        picture = _picture(edit.cover)
        if flac:
            if len(edit.cover.data) >= 1 << 24:
                raise TagError("the cover is too large for FLAC (16 MB at most)")
            audio.add_picture(picture)
        else:
            tags["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
    if edit.lyrics is not None:
        remove("lyrics")
        tags["lyrics"] = [edit.lyrics]


def _edit(audio: Any, fmt: str, edit: TagEdit) -> None:
    if fmt == "mp3":
        _edit_id3(audio, edit)
    elif fmt == "m4a":
        _edit_mp4(audio, edit)
    else:
        _edit_vorbis(audio, edit, flac=fmt == "flac")


def _save(audio: Any, fmt: str, clear: bool) -> None:
    if fmt == "mp3":
        # ID3v2.3 is what Windows and older players read best; an ID3v1 tag
        # is updated when there is one and removed by --clear.
        audio.save(v2_version=3, v1=0 if clear else 1)
    else:
        audio.save()


def _discard(temp: Path) -> None:
    try:
        os.chmod(temp, stat.S_IREAD | stat.S_IWRITE)
        temp.unlink()
    except OSError:
        pass


def _write_through_copy(target: Path, write: Callable[[Path], None]) -> None:
    """Let ``write`` fill a temporary file next to ``target``, then rename it over."""
    temp = target.with_name(f"jzr-tmp-{secrets.token_hex(8)}{target.suffix}")
    try:
        write(temp)
        os.replace(temp, target)
    except BaseException:
        _discard(temp)
        raise


def apply_edit(path: Path, edit: TagEdit) -> None:
    """Change the tags of ``path`` in place; on failure the file is unchanged."""
    import mutagen

    target = Path(path).resolve()
    audio, fmt = _open(target)
    if not os.access(target, os.W_OK):
        raise TagError("the file is read-only")
    cls = type(audio)

    def write(temp: Path) -> None:
        shutil.copyfile(target, temp)
        copy = cls(temp)
        _edit(copy, fmt, edit)
        _save(copy, fmt, edit.clear)
        shutil.copymode(target, temp)

    try:
        _write_through_copy(target, write)
    except TagError:
        raise
    except (mutagen.MutagenError, OSError) as error:
        reason = _os_reason(error) or str(error)
        raise TagError(f"could not write the tags: {reason}") from error


def export_cover(path: Path, target: Path) -> Cover:
    """Write the embedded picture of ``path`` to ``target`` and return it."""
    tagset = read_tags(path)
    cover = tagset.cover
    if cover is None:
        raise TagError("no cover art")
    target = Path(target)
    suffix = target.suffix.lower()
    wanted = {"image/jpeg": (".jpg", ".jpeg"), "image/png": (".png",)}.get(cover.mime)
    if wanted is not None and suffix not in wanted:
        kind = "JPEG" if cover.mime == "image/jpeg" else "PNG"
        raise TagError(f"the cover is a {kind} image; name the file {wanted[0]}")
    if target.exists():
        raise TagError(f"{target} already exists")
    try:
        _write_through_copy(target.resolve(), lambda temp: temp.write_bytes(cover.data))
    except OSError as error:
        raise TagError(f"could not write {target}: {error.strerror or error}") from error
    return cover


def tagset_as_dict(tagset: TagSet, path: Path) -> dict[str, Any]:
    """The JSON form of one file's tags."""
    cover = tagset.cover
    return {
        "file": str(path),
        "format": tagset.format,
        "tags": {key: tagset.values[key] for key in KEYS if key in tagset.values},
        "cover": None if cover is None else {"mime": cover.mime, "bytes": len(cover.data)},
        "lyrics": tagset.lyrics,
    }
