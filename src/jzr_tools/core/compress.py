"""Make videos smaller: constant quality, or a size to stay under.

The planner is pure: it takes a ``MediaInfo`` and returns the ffmpeg
arguments, so it is tested without ffmpeg. ``compress_file`` is the per-file
step of ``jzr video compress``: it runs the plan, checks the size of the
result, retries once when a size was missed, and only then moves the result
into place, so a file over the size or larger than its source is never kept.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
import os
from pathlib import Path
import secrets

from jzr_tools.core import ffmpeg
from jzr_tools.core.batch import ConversionResult, _path_key
from jzr_tools.core.media import (
    VIDEO_QUALITIES,
    AudioStream,
    MediaError,
    MediaInfo,
    format_size,
    format_time,
)

# Named sizes for `--for`, in bytes (decimal, so a limit that is really MiB
# is still met). Mail clients count base64 (about a third more) against
# their 25 MB limit; Discord's free limit is 20 MB.
COMPRESS_PRESETS = {"email": 18_000_000, "discord": 20_000_000}
# Short edges tried for a size, highest first.
RESOLUTION_LADDER = (1080, 720, 480, 360)
# Fewest bits per pixel per frame that still looks acceptable in H.264.
MIN_BPP = 0.05
# Share of the size kept free for the container and the encoder missing.
SIZE_MARGIN = 0.04
AUDIO_BITRATE = 128_000
TIGHT_AUDIO_BITRATE = 96_000
# Short edge a quality encode is capped at unless --resize says otherwise.
QUALITY_SHORT_EDGE = 1080
# libx264 constant-quality level for high, balanced and small.
_CRF = {"high": 18, "balanced": 23, "small": 28}
# A retry aims this much lower than the straight ratio says.
_RETRY_FACTOR = 0.97


def _even_down(value: int) -> int:
    return max(2, value - value % 2)


def _even_round(value: float) -> int:
    return max(2, round(value / 2) * 2)


def output_size(width: int, height: int, short_edge: int) -> tuple[int, int]:
    """Even output size for ``short_edge``, keeping the aspect ratio; never larger."""
    short, long = min(width, height), max(width, height)
    if short_edge >= short:
        return _even_down(width), _even_down(height)
    new_short = _even_down(short_edge)
    new_long = _even_round(long * short_edge / short)
    return (new_short, new_long) if width <= height else (new_long, new_short)


def short_edge_candidates(width: int, height: int, top: int = QUALITY_SHORT_EDGE) -> list[int]:
    """Short edges to try, highest first: the source's (at most ``top``), then
    every ladder rung below it."""
    first = min(width, height, top)
    return [first, *(rung for rung in RESOLUTION_LADDER if rung < first)]


def _bitrate_for(width: int, height: int, fps: float, short_edge: int) -> float:
    out_width, out_height = output_size(width, height, short_edge)
    return out_width * out_height * fps * MIN_BPP


@dataclass(frozen=True)
class TargetPlan:
    video_bitrate: int
    audio_bitrate: int
    short_edge: int


@dataclass(frozen=True)
class Impossible:
    # The smallest size, in bytes, that the lowest resolution still fits.
    smallest_bytes: int


def plan_target_size(
    target_bytes: int,
    duration: float,
    width: int,
    height: int,
    fps: float | None,
    *,
    audio: bool,
    top: int = QUALITY_SHORT_EDGE,
) -> TargetPlan | Impossible:
    """Bitrates and short edge that fit ``target_bytes``.

    The budget is ``target × 8 × (1 − margin) / duration`` bits per second.
    Audio takes 128 kb/s unless that is more than a tenth of the budget, then
    96 kb/s. The highest short edge whose bits per pixel per frame reach
    ``MIN_BPP`` wins. ``width`` and ``height`` are the displayed size.
    """
    rate = fps if fps and fps > 0 else 30.0
    total = target_bytes * 8 * (1 - SIZE_MARGIN) / duration
    if not audio:
        audio_bitrate = 0
    elif AUDIO_BITRATE <= total * 0.1:
        audio_bitrate = AUDIO_BITRATE
    else:
        audio_bitrate = TIGHT_AUDIO_BITRATE
    video_bitrate = math.floor(total - audio_bitrate)
    candidates = short_edge_candidates(width, height, top)
    for edge in candidates:
        if video_bitrate > 0 and video_bitrate >= _bitrate_for(width, height, rate, edge):
            return TargetPlan(video_bitrate, audio_bitrate, edge)
    floor = _bitrate_for(width, height, rate, candidates[-1])
    floor += TIGHT_AUDIO_BITRATE if audio else 0
    return Impossible(math.ceil(floor * duration / 8 / (1 - SIZE_MARGIN)))


def retry_bitrate(previous: int, target_bytes: int, actual_bytes: int) -> int:
    """Video bitrate for a second try after a result of ``actual_bytes``."""
    return math.floor(previous * (target_bytes / actual_bytes) * _RETRY_FACTOR)


@dataclass(frozen=True)
class CompressOptions:
    # ``None`` means balanced; only without a size.
    quality: str | None = None
    # Bytes to stay at or under; ``None`` encodes at constant quality.
    size: int | None = None
    # Highest short edge in pixels; never scales up.
    resize: int | None = None
    no_audio: bool = False
    # Leave the source's metadata (title, location, dates) out.
    strip: bool = False


def check_compress_options(options: CompressOptions) -> None:
    """Reject combinations no file could satisfy; raises ``ValueError``."""
    if options.quality is not None and options.size is not None:
        raise ValueError("--quality applies only without --size")
    if options.quality is not None and options.quality not in VIDEO_QUALITIES:
        raise ValueError(f"--quality must be one of {', '.join(VIDEO_QUALITIES)}")


def compressed_name(source: Path) -> str:
    return f"{source.stem}_compressed.mp4"


def compress_targets(
    sources: Sequence[Path], *, outdir: Path | None = None, root: Path | None = None
) -> list[Path]:
    """One distinct ``<stem>_compressed.mp4`` per source, in order.

    With both ``outdir`` and ``root``, each source's directory relative to
    ``root`` is recreated under ``outdir``. A name already taken in this run
    gets ``-2``, ``-3``, ... after the stem.
    """
    claimed: set[str] = set()
    targets: list[Path] = []
    for source in sources:
        directory = outdir if outdir is not None else source.parent
        if outdir is not None and root is not None:
            try:
                directory = outdir / source.parent.relative_to(root)
            except ValueError:
                directory = outdir
        target = directory / compressed_name(source)
        candidate = target
        counter = 2
        while _path_key(candidate) in claimed:
            candidate = target.with_name(f"{target.stem}-{counter}{target.suffix}")
            counter += 1
        claimed.add(_path_key(candidate))
        targets.append(candidate)
    return targets


@dataclass(frozen=True)
class CompressPlan:
    # The ffmpeg arguments after ``-i <source>`` for each pass, output included.
    passes: tuple[tuple[str, ...], ...]
    # What happens, for the result line.
    note: str
    # The video bitrate of a size encode, so a retry can lower it.
    video_bitrate: int | None


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


def _kbps(bits: int) -> str:
    return f"{bits // 1000}k"


def _rate(bits: int) -> str:
    """``2.18 Mb/s`` or ``850 kb/s``."""
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.2f} Mb/s"
    return f"{round(bits / 1000)} kb/s"


def _about(size: int) -> str:
    """A size rounded up, for the smallest size that would work."""
    if size >= 1_000_000:
        return f"{math.ceil(size / 1_000_000)} MB"
    return f"{max(1, math.ceil(size / 1000))} kB"


def _audio_args(stream: AudioStream, bitrate: int) -> tuple[list[str], str]:
    channels = stream.channels or 0
    if (
        stream.codec == "aac"
        and stream.bitrate is not None
        and stream.bitrate <= bitrate
        and channels <= 2
    ):
        return ["-c:a", "copy"], "audio copied"
    args = ["-c:a", "aac", "-b:a", _kbps(bitrate)]
    if channels > 2:
        args += ["-ac", "2"]
    return args, f"audio encoded to aac {_kbps(bitrate)}"


def plan_compress(
    info: MediaInfo, options: CompressOptions, *, video_bitrate: int | None = None
) -> CompressPlan:
    """Plan an H.264 + AAC MP4 of ``info``: one constant-quality pass, or two
    passes at the bitrate that fits ``options.size``.

    ``video_bitrate`` replaces the planned bitrate (a retry). Raises
    ``MediaError`` when there is no video, or the size cannot be reached.
    """
    video = info.video
    if video is None or not video.width or not video.height:
        raise MediaError("no video track")
    audio = None if options.no_audio else _best_audio(info.audio)
    # An encode is turned upright before the filters run, so sizes are
    # those of the picture as displayed.
    width, height = video.width, video.height
    if video.rotation in (90, 270):
        width, height = height, width
    top = options.resize if options.resize is not None else QUALITY_SHORT_EDGE

    if options.size is not None:
        if not info.duration or info.duration <= 0:
            raise MediaError("the duration is unknown, so --size cannot be planned")
        target = plan_target_size(
            options.size, info.duration, width, height, video.fps,
            audio=audio is not None, top=top,
        )
        if isinstance(target, Impossible):
            message = (
                f"too small for a {format_time(info.duration)} video; "
                f"the smallest is about {_about(target.smallest_bytes)}"
            )
            if audio is not None:
                message += " (or use --no-audio)"
            raise MediaError(message)
        short_edge = target.short_edge
        audio_bitrate = target.audio_bitrate or AUDIO_BITRATE
        bitrate = video_bitrate if video_bitrate is not None else target.video_bitrate
    else:
        short_edge = min(width, height, top)
        audio_bitrate = AUDIO_BITRATE
        bitrate = None

    out_width, out_height = output_size(width, height, short_edge)
    scale = ["-vf", f"scale={out_width}:{out_height}"]
    if (out_width, out_height) == (width, height):
        scale = []

    maps = ["-map", f"0:{video.index}"]
    audio_args: list[str] = []
    notes: list[str] = []
    if bitrate is None:
        quality = options.quality or "balanced"
        summary = f"video encoded to h264 ({quality})"
    else:
        summary = f"video encoded to h264 at {_rate(bitrate)}"
    if scale:
        summary += f", {out_width}x{out_height}"
    if audio is not None:
        maps += ["-map", f"0:{audio.index}"]
        audio_args, audio_note = _audio_args(audio, audio_bitrate)
        summary += f", {audio_note}"
    elif options.no_audio and info.audio:
        summary += ", audio dropped"
    notes.append(summary)

    tail = ["-map_metadata", "-1"] if options.strip else []
    tail += ["-movflags", "+faststart", ffmpeg.OUTPUT]
    if bitrate is None:
        crf = _CRF[options.quality or "balanced"]
        passes: tuple[tuple[str, ...], ...] = (
            (
                *maps, *scale, "-c:v", "libx264", "-crf", str(crf),
                "-pix_fmt", "yuv420p", *audio_args, *tail,
            ),
        )
    else:
        rate = ["-c:v", "libx264", "-b:v", str(bitrate)]
        log = ["-passlogfile", ffmpeg.PASSLOG, "-pix_fmt", "yuv420p"]
        passes = (
            (
                "-map", f"0:{video.index}", *scale, *rate, "-pass", "1", *log,
                "-an", "-f", "null", "-",
            ),
            (*maps, *scale, *rate, "-pass", "2", *log, *audio_args, *tail),
        )

    dropped: list[str] = []
    if not options.no_audio:
        dropped += [_audio_label(s) for s in info.audio if s is not audio]
    dropped += [label for label in info.extra if not label.startswith("data stream")]
    if info.cover_art:
        dropped.append("cover art")
    if dropped:
        notes.append("dropped: " + ", ".join(dropped))
    if video.hdr:
        notes.append("HDR source: 8-bit H.264 will look washed out")

    return CompressPlan(passes, "; ".join(notes), bitrate)


@dataclass(frozen=True)
class CompressResult:
    result: ConversionResult
    # Every ffmpeg command line run, or planned for a dry run.
    commands: tuple[tuple[str, ...], ...]


def _same_file(source: Path, target: Path) -> bool:
    try:
        return target.exists() and os.path.samefile(source, target)
    except OSError:
        return False


def _job(source: Path, plan: CompressPlan, target: Path, duration: float | None) -> ffmpeg.Job:
    steps = tuple(("-i", os.path.abspath(source), *args) for args in plan.passes)
    return ffmpeg.Job(steps, target, duration)


def compress_file(
    source: Path,
    target: Path,
    *,
    tools: ffmpeg.Tools,
    options: CompressOptions,
    overwrite: bool = False,
    dry_run: bool = False,
    on_progress: Callable[[ffmpeg.Progress], None] | None = None,
) -> CompressResult:
    """Compress ``source`` into ``target``; never raises ``MediaError``."""

    def done(status: str, detail: str, commands: Sequence[Sequence[str]] = ()) -> CompressResult:
        lines = tuple(tuple(line) for line in commands)
        command = lines[-1] if lines else ()
        return CompressResult(ConversionResult(source, target, command, status, detail), lines)

    if _same_file(source, target):
        return done("skipped", "source is the target")
    if target.exists() and not overwrite:
        return done("skipped", "target exists (use --overwrite)")

    try:
        info = ffmpeg.probe(source, tools.ffprobe)
        source_size = info.size if info.size is not None else source.stat().st_size
        if options.size is not None and source_size <= options.size:
            return done(
                "skipped",
                f"already {format_size(source_size)}, within {format_size(options.size)}",
            )
        plan = plan_compress(info, options)
    except (MediaError, OSError) as error:
        return done("failed", str(error))

    staging = target.parent / f"jzr-tmp-{secrets.token_hex(8)}.mp4"
    job = _job(source, plan, staging, info.duration)
    commands = [list(line) for line in ffmpeg.command_lines(_job(source, plan, target, None), tools)]
    if dry_run:
        return done("planned", plan.note, commands)

    retried = ""
    try:
        try:
            ffmpeg.run(job, tools, on_progress=on_progress)
            size = staging.stat().st_size
            if options.size is not None and size > options.size and plan.video_bitrate:
                lower = retry_bitrate(plan.video_bitrate, options.size, size)
                staging.unlink(missing_ok=True)
                if lower <= 0:
                    return done(
                        "failed",
                        f"came out at {format_size(size)}, over {format_size(options.size)}",
                        commands,
                    )
                plan = plan_compress(info, options, video_bitrate=lower)
                job = _job(source, plan, staging, info.duration)
                commands = [
                    list(line)
                    for line in ffmpeg.command_lines(_job(source, plan, target, None), tools)
                ]
                retried = f"retried at {_rate(lower)}"
                ffmpeg.run(job, tools, on_progress=on_progress)
                size = staging.stat().st_size
                if size > options.size:
                    staging.unlink(missing_ok=True)
                    return done(
                        "failed",
                        f"came out at {format_size(size)}, over {format_size(options.size)} "
                        "even after a retry at a lower bitrate",
                        commands,
                    )
            if options.size is None and size >= source_size:
                staging.unlink(missing_ok=True)
                return done("skipped", "already smaller than the result", commands)
            os.replace(staging, target)
        except OSError as error:
            raise MediaError(
                f"could not write {target.name}: {error.strerror or error}"
            ) from error
    except MediaError as error:
        staging.unlink(missing_ok=True)
        return done("failed", str(error), commands)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise

    if options.size is not None:
        sized = f"{format_size(size)} of {format_size(options.size)}"
    else:
        sized = f"{format_size(size)}, was {format_size(source_size)}"
    detail = "; ".join(part for part in (plan.note, sized, retried) if part)
    return done("converted", detail, commands)
