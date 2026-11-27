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


class ToolNotFoundError(MissingToolError):
    """Neither the environment, jzr's own copy nor PATH has the tool."""


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


def with_written_size(details: str, path: str | os.PathLike[str]) -> str:
    """``details`` with the size of the file written at ``path`` as its last item.

    Result lines end with it so nobody has to look the file up afterwards.
    Details that already show that size (the compress lines' ``2.1 MB ->
    492.0 kB``) are returned as they are, and so are they when the file cannot
    be read.
    """
    try:
        size = format_size(Path(path).stat().st_size)
    except OSError:
        return details
    if re.search(rf"(?<![\d.]){re.escape(size)}", details):
        return details
    return f"{details}, {size}" if details else size


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
    # Bits per sample of the source (16, 24), when ffprobe knows it.
    bits: int | None = None


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
                    bits=_int(stream.get("bits_per_raw_sample"))
                    or _int(stream.get("bits_per_sample"))
                    or None,
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


# --- audio conversion ----------------------------------------------------------

# Extensions read as audio when scanning a directory or naming a pair command.
AUDIO_EXTENSIONS = frozenset(
    {"mp3", "m4a", "aac", "opus", "ogg", "oga", "flac", "wav", "aif", "aiff", "wma", "mka"}
)

_LOSSLESS_TARGETS = frozenset({"flac", "wav"})
# Codecs each target holds as they are, so they are copied rather than encoded.
_COPYABLE = {
    "mp3": frozenset({"mp3"}),
    "m4a": frozenset({"aac", "alac"}),
    "opus": frozenset({"opus"}),
    "ogg": frozenset({"vorbis"}),
    "flac": frozenset({"flac"}),
    "wav": frozenset(
        {"pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le"}
    ),
}
_COVER_TARGETS = frozenset({"mp3", "m4a", "flac"})
# Container tags MP4 sources carry that mean nothing in another file.
_MP4_BRAND_TAGS = ("major_brand", "minor_version", "compatible_brands")

_BITRATE = re.compile(r"(\d+)\s*(?:k|kbps|kb/s)?")


def parse_bitrate(text: str) -> str:
    """Read an audio bitrate in kb/s (``128k``, ``128``) as ffmpeg's ``128k``."""
    match = _BITRATE.fullmatch(text.strip().lower())
    rate = int(match.group(1)) if match else 0
    if not 8 <= rate <= 512:
        raise ValueError(f"{text!r} is not a bitrate; use kb/s from 8k to 512k, such as 128k")
    return f"{rate}k"


@dataclass(frozen=True)
class AudioOptions:
    # Normalised by ``parse_bitrate``; ``None`` keeps the target's default.
    bitrate: str | None = None


@dataclass(frozen=True)
class Plan:
    # ffmpeg arguments between ``-i <source>`` and the output path.
    args: tuple[str, ...]
    # What happened, for the result line: ``audio copied``.
    note: str


AudioPlan = Plan


def bitrate_caveat(target_ext: str) -> str | None:
    """Explain that ``--bitrate`` does nothing for a lossless target."""
    extension = target_ext.strip().lstrip(".").lower()
    if extension in _LOSSLESS_TARGETS:
        return f".{extension} is lossless; --bitrate is ignored"
    return None


def _container_names(info: MediaInfo) -> set[str]:
    return set((info.container or "").split(","))


def _encoder_args(target: str, stream: AudioStream, bitrate: str | None) -> tuple[str, ...]:
    deep = (stream.bits or 0) > 16
    if target == "mp3":
        quality = ("-b:a", bitrate) if bitrate else ("-q:a", "2")
        return ("-c:a", "libmp3lame", *quality)
    if target == "m4a":
        return ("-c:a", "aac", "-b:a", bitrate or "192k")
    if target == "opus":
        return ("-c:a", "libopus", "-b:a", bitrate or "128k")
    if target == "ogg":
        quality = ("-b:a", bitrate) if bitrate else ("-q:a", "5")
        return ("-c:a", "libvorbis", *quality)
    if target == "flac":
        return ("-c:a", "flac", "-sample_fmt", "s32" if deep else "s16", "-compression_level", "5")
    if target == "wav":
        return ("-c:a", "pcm_s24le" if deep else "pcm_s16le")
    raise ValueError(f"not an audio target: {target}")


def plan_audio_convert(info: MediaInfo, target_ext: str, options: AudioOptions) -> Plan:
    """Decide how to turn ``info``'s first audio stream into ``target_ext``.

    The stream is copied when the target holds its codec and no bitrate was
    asked for, otherwise encoded with the target's defaults. Tags go along;
    cover art goes into the formats that can hold it.
    """
    target = target_ext.strip().lstrip(".").lower()
    if target not in _COPYABLE:
        raise ValueError(f"not an audio target: {target_ext}")
    if not info.audio:
        raise MediaError("no audio track")
    stream = info.audio[0]
    containers = _container_names(info)

    args: list[str] = ["-map", "0:a:0"]
    # In an audio file a video stream is its cover; in a video it is the film.
    has_cover = info.cover_art and info.video is None
    if has_cover and target in _COVER_TARGETS:
        args += ["-map", "0:v", "-c:v", "copy", "-disposition:v", "attached_pic"]

    # Ogg keeps its tags on the audio stream; elsewhere they are the file's.
    args += ["-map_metadata", "0:s:a:0" if "ogg" in containers else "0"]
    if containers & {"mp4", "mov"}:
        for key in _MP4_BRAND_TAGS:
            args += ["-metadata", f"{key}="]

    lossless = target in _LOSSLESS_TARGETS
    if stream.codec in _COPYABLE[target] and (lossless or not options.bitrate):
        args += ["-c:a", "copy"]
        note = "audio copied"
    else:
        encoder = _encoder_args(target, stream, None if lossless else options.bitrate)
        args += encoder
        codec = {"libmp3lame": "mp3", "libopus": "opus", "libvorbis": "vorbis"}.get(
            encoder[1], encoder[1]
        )
        note = f"audio encoded to {codec}"

    if target == "mp3":
        # ID3v2.3 is the version every player and Windows Explorer read.
        args += ["-id3v2_version", "3"]

    if has_cover and target not in _COVER_TARGETS:
        note += f"; cover art dropped: .{target} cannot hold it"
    return Plan(tuple(args), note)


# --- video conversion ----------------------------------------------------------

# Extensions read as video when scanning a directory or naming a pair command.
VIDEO_EXTENSIONS = frozenset(
    {
        "mp4", "m4v", "mov", "mkv", "webm", "avi", "wmv", "flv",
        "mpg", "mpeg", "ts", "mts", "m2ts", "3gp", "ogv",
    }
)
VIDEO_CODECS = ("h264", "hevc", "av1", "vp9")
VIDEO_QUALITIES = ("high", "balanced", "small")

# Video codecs each container holds as they are, so they are copied.
_VIDEO_COPYABLE = {
    "mp4": frozenset({"h264", "hevc", "av1", "vp9", "mpeg4"}),
    "mov": frozenset({"h264", "hevc", "mpeg4", "prores", "mjpeg"}),
    "mkv": frozenset(
        {"h264", "hevc", "av1", "vp8", "vp9", "mpeg4", "mpeg2video", "prores", "mjpeg", "theora"}
    ),
    "webm": frozenset({"vp8", "vp9", "av1"}),
}
_VIDEO_AUDIO_COPYABLE = {
    "mp4": frozenset({"aac", "mp3", "opus", "flac", "alac", "ac3", "eac3"}),
    "mov": frozenset(
        {"aac", "mp3", "alac", "ac3", "pcm_s16le", "pcm_s24le", "pcm_s16be", "pcm_s24be"}
    ),
    "mkv": frozenset(
        {
            "aac", "mp3", "opus", "vorbis", "flac", "alac", "ac3", "eac3",
            "dts", "truehd", "pcm_s16le", "pcm_s24le",
        }
    ),
    "webm": frozenset({"opus", "vorbis"}),
}
# What `--codec` may put in each container, in the order the error lists them.
_CONTAINER_CODECS = {
    "mp4": ("h264", "hevc", "av1", "vp9"),
    "mov": ("h264", "hevc"),
    "mkv": ("h264", "hevc", "av1", "vp9"),
    "webm": ("vp9", "av1"),
}
# Codecs a container gets when its source streams do not fit.
_CONTAINER_DEFAULTS = {
    "mp4": ("h264", "aac"),
    "mov": ("h264", "aac"),
    "mkv": ("h264", "aac"),
    "webm": ("vp9", "opus"),
}
# Constant-quality level per codec for high, balanced and small; then the
# highest value the encoder takes.
_CRF = {
    "h264": ((18, 23, 28), 51),
    "hevc": ((22, 26, 30), 51),
    "av1": ((24, 32, 40), 63),
    "vp9": ((24, 32, 40), 63),
}
_VIDEO_AUDIO_ENCODERS = {
    "aac": ("-c:a", "aac", "-b:a", "192k"),
    "opus": ("-c:a", "libopus", "-b:a", "128k"),
}
_FAST_START = frozenset({"mp4", "mov"})


@dataclass(frozen=True)
class VideoOptions:
    # ``None`` copies the video when the container can hold it.
    codec: str | None = None
    # ``None`` means not given, which encodes at ``balanced``.
    quality: str | None = None
    crf: int | None = None
    # Short edge in pixels; never scales up.
    resize: int | None = None
    fps: float | None = None
    no_audio: bool = False


def _target(target_ext: str) -> str:
    return target_ext.strip().lstrip(".").lower()


def check_convert_options(
    target_ext: str, options: VideoOptions, *, bitrate: str | None = None
) -> None:
    """Reject option combinations that no file could satisfy.

    Raises ``ValueError`` with the message for the user.
    """
    target = _target(target_ext)
    if target == "gif":
        given = (
            ("--codec", options.codec is not None),
            ("--quality", options.quality is not None),
            ("--crf", options.crf is not None),
            ("--resize", options.resize is not None),
            ("--fps", options.fps is not None),
            ("--no-audio", options.no_audio),
            ("--bitrate", bitrate is not None),
        )
        for name, present in given:
            if present:
                raise ValueError(
                    f"{name} does not apply to .gif; 'jzr video gif' has the GIF options"
                )
        return
    if target in _COPYABLE:
        given = (
            ("--codec", options.codec is not None),
            ("--quality", options.quality is not None),
            ("--crf", options.crf is not None),
            ("--resize", options.resize is not None),
            ("--fps", options.fps is not None),
            ("--no-audio", options.no_audio),
        )
        for name, present in given:
            if present:
                raise ValueError(f"{name} applies only to video targets, not .{target}")
        return
    if target not in _CONTAINER_CODECS:
        raise ValueError(f"not a video or audio target: {target_ext}")

    if bitrate is not None:
        raise ValueError(f"--bitrate applies only to audio targets, not .{target}")
    if options.crf is not None and options.quality is not None:
        raise ValueError("--crf and --quality cannot be used together")
    allowed = _CONTAINER_CODECS[target]
    if options.codec is not None and options.codec not in allowed:
        raise ValueError(
            f".{target} cannot hold {options.codec}; choose from {', '.join(allowed)}"
        )
    codec = options.codec or _CONTAINER_DEFAULTS[target][0]
    highest = _CRF[codec][1]
    if options.crf is not None and not 0 <= options.crf <= highest:
        raise ValueError(f"--crf for {codec} goes from 0 to {highest}")


def _even_down(value: int) -> int:
    return value - value % 2


def _even_round(value: float) -> int:
    return max(2, round(value / 2) * 2)


def _deep_pixels(pixel_format: str | None) -> bool:
    return bool(pixel_format) and ("10" in pixel_format or "12" in pixel_format)


def _best_audio(streams: tuple[AudioStream, ...]) -> AudioStream | None:
    best: AudioStream | None = None
    for stream in streams:
        if best is None or (stream.channels or 0) > (best.channels or 0):
            best = stream
    return best


def _audio_label(stream: AudioStream) -> str:
    details = [value for value in (stream.codec, stream.language) if value]
    label = f"audio stream {stream.index}"
    return f"{label} ({', '.join(details)})" if details else label


def _scale(video: VideoStream, resize: int | None) -> tuple[str | None, bool]:
    """Return the ``scale=W:H`` filter for an encode, and whether ``resize``
    changes the picture."""
    if not video.width or not video.height:
        return None, False
    width, height = video.width, video.height
    # An encode is turned upright before the filters run, so they see the
    # picture as displayed.
    if video.rotation in (90, 270):
        width, height = height, width
    short, long = min(width, height), max(width, height)
    if resize is not None and short > resize:
        new_short = _even_down(resize)
        new_long = _even_round(long * resize / short)
        if width <= height:
            return f"scale={new_short}:{new_long}", True
        return f"scale={new_long}:{new_short}", True
    if width % 2 or height % 2:
        return f"scale={_even_down(width)}:{_even_down(height)}", False
    return None, False


def plan_convert(info: MediaInfo, target_ext: str, options: VideoOptions) -> Plan:
    """Decide how to turn ``info`` into the video container ``target_ext``.

    Each stream is copied when the container holds its codec, otherwise
    encoded to the container's default; ``--codec``, a real resize or a
    lower frame rate force a video encode.
    """
    target = _target(target_ext)
    if target not in _CONTAINER_CODECS:
        raise ValueError(f"not a video container: {target_ext}")
    video = info.video
    if video is None:
        raise MediaError("no video track")
    audio = None if options.no_audio else _best_audio(info.audio)
    default_video, default_audio = _CONTAINER_DEFAULTS[target]
    notes: list[str] = []

    resize_changes = False
    if options.resize is not None:
        _, resize_changes = _scale(video, options.resize)
    fps_changes = (
        options.fps is not None and video.fps is not None and video.fps > options.fps
    )
    encode = (
        options.codec is not None
        or resize_changes
        or fps_changes
        or video.codec not in _VIDEO_COPYABLE[target]
    )

    args: list[str] = ["-map", f"0:{video.index}"]
    if audio is not None:
        args += ["-map", f"0:{audio.index}"]

    if encode:
        codec = options.codec or default_video
        filters: list[str] = []
        if fps_changes:
            filters.append(f"fps={_number(options.fps)}")
        scale, _ = _scale(video, options.resize)
        if scale is not None:
            filters.append(scale)
        if filters:
            args += ["-vf", ",".join(filters)]
        levels, _ = _CRF[codec]
        crf = options.crf
        if crf is None:
            crf = levels[VIDEO_QUALITIES.index(options.quality or "balanced")]
        pixels = "yuv420p10le" if codec != "h264" and _deep_pixels(video.pixel_format) else "yuv420p"
        args += ["-c:v", VIDEO_ENCODERS[codec], "-crf", str(crf)]
        if codec == "vp9":
            args += ["-b:v", "0"]
        args += ["-pix_fmt", pixels]
        if codec == "hevc":
            args += ["-x265-params", "log-level=error"]
        elif codec == "vp9":
            args += ["-row-mt", "1", "-cpu-used", "4"]
        out_codec = codec
        summary = f"video encoded to {codec}"
    else:
        args += ["-c:v", "copy"]
        out_codec = video.codec
        summary = "video copied"
    if out_codec == "hevc" and target in _FAST_START:
        # Apple players only open HEVC in MP4 under this tag.
        args += ["-tag:v", "hvc1"]

    if audio is not None:
        if audio.codec in _VIDEO_AUDIO_COPYABLE[target]:
            args += ["-c:a", "copy"]
            summary += ", audio copied"
        else:
            args += _VIDEO_AUDIO_ENCODERS[default_audio]
            summary += f", audio encoded to {default_audio}"
    elif options.no_audio and info.audio:
        summary += ", audio dropped"
    notes.append(summary)

    if _container_names(info) & {"mp4", "mov"} and target not in _FAST_START:
        for key in _MP4_BRAND_TAGS:
            args += ["-metadata", f"{key}="]
    if target in _FAST_START:
        args += ["-movflags", "+faststart"]

    dropped: list[str] = []
    if not options.no_audio:
        dropped += [_audio_label(s) for s in info.audio if s is not audio]
    dropped += [label for label in info.extra if not label.startswith("data stream")]
    if info.cover_art:
        dropped.append("cover art")
    if dropped:
        notes.append("dropped: " + ", ".join(dropped))

    if not encode:
        if options.crf is not None:
            notes.append("--crf applies only when re-encoding")
        if options.quality is not None:
            notes.append("--quality applies only when re-encoding")
    if options.resize is not None and not resize_changes:
        notes.append(f"already {options.resize} px or smaller; not resized")
    if options.fps is not None and not fps_changes:
        notes.append(f"already {_number(options.fps)} fps or less; frame rate kept")
    if encode and out_codec == "h264" and video.hdr:
        notes.append("HDR source: 8-bit H.264 will look washed out")

    return Plan(tuple(args), "; ".join(notes))
