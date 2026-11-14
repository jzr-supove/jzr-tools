"""Pure media helpers: times, sizes, format tables and ffprobe results.

Nothing here starts a process or needs ffmpeg, so every function can be
tested from hand-written data. ``core.ffmpeg`` runs the tools and hands
their output to the functions here.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
from typing import Any

VIDEO_CONTAINERS = ("mp4", "mov", "mkv", "webm")
AUDIO_FORMATS = ("mp3", "m4a", "opus", "ogg", "flac", "wav")
VIDEO_TARGETS = VIDEO_CONTAINERS + AUDIO_FORMATS + ("gif",)

# Codec -> the ffmpeg encoder used for it. `formats` reports which of these
# the user's ffmpeg build has.
VIDEO_ENCODERS = {
    "h264": "libx264",
    "hevc": "libx265",
    "av1": "libsvtav1",
    "vp9": "libvpx-vp9",
    "gif": "gif",
}
AUDIO_ENCODERS = {
    "aac": "aac",
    "mp3": "libmp3lame",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "flac": "flac",
    "pcm_s16le": "pcm_s16le",
    "pcm_s24le": "pcm_s24le",
}

_HDR_TRANSFERS = {"smpte2084": "PQ", "arib-std-b67": "HLG"}


class MediaError(Exception):
    """A file could not be read or written; the message is for the user."""


class MissingToolError(MediaError):
    """ffmpeg or ffprobe could not be found."""


# --- times -----------------------------------------------------------------

_TIME_FORMS = "use seconds (90, 7.5) or [H:]MM:SS[.mmm] (1:30, 0:01:30.250)"
_SECONDS = re.compile(r"\d+(?:\.\d+)?|\.\d+")
_FIELD = re.compile(r"\d+")


def parse_time(text: str) -> float:
    """Read a time given as seconds or ``[H:]MM:SS[.fff]``, in seconds."""
    value = text.strip()
    parts = value.split(":")
    if len(parts) > 3 or not value:
        raise ValueError(f"{text!r} is not a time; {_TIME_FORMS}")

    *leading, last = parts
    if _SECONDS.fullmatch(last) is None or any(
        _FIELD.fullmatch(part) is None for part in leading
    ):
        raise ValueError(f"{text!r} is not a time; {_TIME_FORMS}")

    # Only the first field may exceed 59: `90:00` is ninety minutes.
    fields = [int(part) for part in leading] + [float(last)]
    if any(field >= 60 for field in fields[1:]):
        raise ValueError(f"{text!r} is not a time; {_TIME_FORMS}")

    seconds = 0.0
    for field in fields:
        seconds = seconds * 60 + field
    return seconds


def format_time(seconds: float, precision: int = 0) -> str:
    """Write ``seconds`` as ``M:SS`` or ``H:MM:SS``, with optional decimals."""
    if seconds < 0 or math.isnan(seconds):
        raise ValueError(f"not a duration: {seconds}")
    scale = 10**precision
    # Round once, in whole units of the last digit, so `59.999` carries into
    # the minutes instead of printing `0:60.00`.
    units = round(seconds * scale)
    whole, fraction = divmod(units, scale)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    tail = f".{fraction:0{precision}d}" if precision else ""
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}{tail}"
    return f"{minutes}:{secs:02d}{tail}"


# --- sizes -----------------------------------------------------------------

_SIZE = re.compile(r"(\d+(?:\.\d+)?|\.\d+) ?([a-z]*)")
_SIZE_UNITS = {
    "": 1,
    "b": 1,
    "k": 1000,
    "kb": 1000,
    "m": 1000**2,
    "mb": 1000**2,
    "g": 1000**3,
    "gb": 1000**3,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
}


def parse_size(text: str) -> int:
    """Read a size such as ``25MB``, ``8M`` or ``1.5GB`` as a byte count."""
    match = _SIZE.fullmatch(text.strip().lower())
    if match is None or match.group(2) not in _SIZE_UNITS:
        raise ValueError(
            f"{text!r} is not a size; use a number and a unit: 500KB, 25MB, 1.5GB"
        )
    size = round(float(match.group(1)) * _SIZE_UNITS[match.group(2)])
    if size < 1:
        raise ValueError(f"{text!r} is less than one byte")
    return size


def format_size(size: int) -> str:
    """Write a byte count in decimal units: ``999 B``, ``1.2 kB``, ``25.0 MB``."""
    if size < 1000:
        return f"{size} B"
    value = float(size)
    for unit in ("kB", "MB", "GB", "TB"):
        value /= 1000
        if value < 999.95 or unit == "TB":
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")


# --- ffprobe results ---------------------------------------------------------


@dataclass(frozen=True)
class VideoStream:
    index: int
    codec: str | None
    width: int | None
    height: int | None
    fps: float | None
    pixel_format: str | None
    # Degrees clockwise a player turns the picture, 0-359.
    rotation: int
    transfer: str | None
    bitrate: int | None

    @property
    def hdr(self) -> bool:
        return self.transfer in _HDR_TRANSFERS


@dataclass(frozen=True)
class AudioStream:
    index: int
    codec: str | None
    channels: int | None
    channel_layout: str | None
    sample_rate: int | None
    bitrate: int | None
    language: str | None


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    # ffprobe's format names (`mov,mp4,m4a,3gp,3g2,mj2`) and long name.
    container: str | None
    container_name: str | None
    duration: float | None
    size: int | None
    bitrate: int | None
    video: VideoStream | None
    audio: tuple[AudioStream, ...]
    # Labels for the streams that are neither the video nor audio.
    extra: tuple[str, ...]
    tags: dict[str, str]
    cover_art: bool


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rate(value: Any) -> float | None:
    """Read a frame rate such as ``30000/1001``; ``0/0`` means none."""
    if not isinstance(value, str):
        return None
    numerator, _, denominator = value.partition("/")
    top = _float(numerator)
    bottom = _float(denominator) if denominator else 1.0
    if not top or not bottom:
        return None
    return top / bottom


def _rotation(stream: dict[str, Any]) -> int:
    # ffprobe gives the display matrix angle anticlockwise; players and
    # people talk about the clockwise turn (the old `rotate` tag).
    for item in stream.get("side_data_list") or ():
        if "rotation" in item:
            angle = _float(item["rotation"])
            if angle is not None:
                return round(-angle) % 360
    angle = _float((stream.get("tags") or {}).get("rotate"))
    return round(angle) % 360 if angle is not None else 0


def _label(stream: dict[str, Any]) -> str:
    kind = stream.get("codec_type") or "unknown"
    details = [
        value
        for value in (stream.get("codec_name"), (stream.get("tags") or {}).get("language"))
        if value and value != "und"
    ]
    label = f"{kind} stream {stream.get('index')}"
    return f"{label} ({', '.join(details)})" if details else label


def parse_probe(data: dict[str, Any], path: Path) -> MediaInfo:
    """Turn ``ffprobe -show_format -show_streams`` JSON into a ``MediaInfo``."""
    streams = data.get("streams") or []
    if not streams:
        raise MediaError("not a readable media file")
    container = data.get("format") or {}

    video: VideoStream | None = None
    audio: list[AudioStream] = []
    extra: list[str] = []
    cover_art = False
    for stream in streams:
        kind = stream.get("codec_type")
        if (stream.get("disposition") or {}).get("attached_pic"):
            cover_art = True
            continue
        if kind == "video" and video is None:
            fps = _rate(stream.get("avg_frame_rate")) or _rate(stream.get("r_frame_rate"))
            video = VideoStream(
                index=_int(stream.get("index")) or 0,
                codec=stream.get("codec_name"),
                width=_int(stream.get("width")),
                height=_int(stream.get("height")),
                fps=fps,
                pixel_format=stream.get("pix_fmt"),
                rotation=_rotation(stream),
                transfer=stream.get("color_transfer"),
                bitrate=_int(stream.get("bit_rate")),
            )
        elif kind == "audio":
            language = (stream.get("tags") or {}).get("language")
            audio.append(
                AudioStream(
                    index=_int(stream.get("index")) or 0,
                    codec=stream.get("codec_name"),
                    channels=_int(stream.get("channels")),
                    channel_layout=stream.get("channel_layout"),
                    sample_rate=_int(stream.get("sample_rate")),
                    bitrate=_int(stream.get("bit_rate")),
                    language=language if language and language != "und" else None,
                )
            )
        else:
            extra.append(_label(stream))

    tags = {str(key).lower(): str(value) for key, value in (container.get("tags") or {}).items()}
    # Ogg keeps its tags on the audio stream rather than the container.
    first_audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if first_audio is not None:
        for key, value in (first_audio.get("tags") or {}).items():
            tags.setdefault(str(key).lower(), str(value))

    size = _int(container.get("size"))
    if size is None:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None

    return MediaInfo(
        path=path,
        container=container.get("format_name"),
        container_name=container.get("format_long_name"),
        duration=_float(container.get("duration")),
        size=size,
        bitrate=_int(container.get("bit_rate")),
        video=video,
        audio=tuple(audio),
        extra=tuple(extra),
        tags=tags,
        cover_art=cover_art,
    )


def info_as_dict(info: MediaInfo) -> dict[str, Any]:
    """Return the ``--json`` form of ``info``."""
    video = None
    if info.video is not None:
        stream = info.video
        video = {
            "index": stream.index,
            "codec": stream.codec,
            "width": stream.width,
            "height": stream.height,
            "fps": round(stream.fps, 3) if stream.fps is not None else None,
            "pixel_format": stream.pixel_format,
            "rotation": stream.rotation,
            "transfer": stream.transfer,
            "hdr": stream.hdr,
            "bitrate": stream.bitrate,
        }
    return {
        "file": str(info.path),
        "container": info.container,
        "container_name": info.container_name,
        "duration": info.duration,
        "size": info.size,
        "bitrate": info.bitrate,
        "video": video,
        "audio": [
            {
                "index": stream.index,
                "codec": stream.codec,
                "channels": stream.channels,
                "channel_layout": stream.channel_layout,
                "sample_rate": stream.sample_rate,
                "bitrate": stream.bitrate,
                "language": stream.language,
            }
            for stream in info.audio
        ],
        "extra": list(info.extra),
        "tags": dict(info.tags),
        "cover_art": info.cover_art,
    }


def _bitrate(bits: int) -> str:
    return f"{round(bits / 1000)} kb/s"


def _number(value: float) -> str:
    """``30`` for 30.0, ``29.97`` for 29.97003."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _video_row(stream: VideoStream) -> str:
    parts = [stream.codec or "unknown codec"]
    if stream.width and stream.height:
        parts.append(f"{stream.width}x{stream.height}")
    if stream.fps:
        parts.append(f"{_number(stream.fps)} fps")
    if stream.pixel_format:
        parts.append(stream.pixel_format)
    return ", ".join(parts)


def _audio_row(stream: AudioStream) -> str:
    parts = [stream.codec or "unknown codec"]
    if stream.channels:
        layout = f" ({stream.channel_layout})" if stream.channel_layout else ""
        parts.append(f"{stream.channels} ch{layout}")
    if stream.sample_rate:
        parts.append(f"{stream.sample_rate} Hz")
    if stream.bitrate:
        parts.append(_bitrate(stream.bitrate))
    return ", ".join(parts)


def _year(tags: dict[str, str]) -> str | None:
    date = tags.get("date") or tags.get("year")
    if not date:
        return None
    match = re.match(r"\d{4}", date)
    return match.group(0) if match else date


def info_rows(info: MediaInfo, *, tags: bool) -> list[tuple[str, str]]:
    """Return the text form of ``info`` as label and value pairs."""
    rows: list[tuple[str, str]] = []
    if info.container_name or info.container:
        rows.append(("container", info.container_name or info.container or ""))
    if info.duration is not None:
        rows.append(("duration", format_time(info.duration, 2)))
    if info.size is not None:
        rows.append(("size", format_size(info.size)))
    if info.bitrate:
        rows.append(("bitrate", _bitrate(info.bitrate)))

    if info.video is not None:
        rows.append(("video", _video_row(info.video)))
        if info.video.rotation:
            rows.append(("rotation", f"{info.video.rotation}° clockwise"))
        if info.video.hdr:
            transfer = info.video.transfer or ""
            rows.append(("hdr", f"{_HDR_TRANSFERS[transfer]} ({transfer})"))

    numbered = len(info.audio) > 1
    for number, stream in enumerate(info.audio, start=1):
        rows.append((f"audio {number}" if numbered else "audio", _audio_row(stream)))

    for label in info.extra:
        rows.append(("extra", label))

    if tags:
        values = {
            "title": info.tags.get("title"),
            "artist": info.tags.get("artist"),
            "album": info.tags.get("album"),
            "year": _year(info.tags),
            "track": info.tags.get("track") or info.tags.get("tracknumber"),
        }
        rows.extend((key, value) for key, value in values.items() if value)
        rows.append(("cover art", "yes" if info.cover_art else "no"))

    return rows
