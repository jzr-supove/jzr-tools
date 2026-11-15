"""Plan an animated GIF made from a video.

The GIF is made in one ffmpeg run: the picture is split, one copy builds a
palette for this clip and the other is mapped onto it, so no palette file is
written. The width is capped (never scaled up), the frame rate lowered, and
the GIF loops forever. The time range works as in ``jzr video trim``.
"""

from __future__ import annotations

from dataclasses import dataclass

from jzr_tools.core.media import (
    MediaError,
    MediaInfo,
    VideoStream,
    _number,
    format_time,
)
from jzr_tools.core.trim import TrimPlan, TrimRange, fit_range, seconds_arg

GIF_WIDTH = 480
GIF_FPS = 15.0
# A GIF longer than this, in seconds, gets a note about its size.
LONG_GIF = 30.0
# Bytes per pixel per frame of a palette GIF, low and high: measured 0.10
# for a flat test pattern up to 0.84 for noisy video at 480x270.
LOW_BYTES_PER_PIXEL = 0.1
HIGH_BYTES_PER_PIXEL = 0.9

_PALETTE = (
    "split[a][b];[a]palettegen=stats_mode=diff[p];"
    "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle[g]"
)
_SIZE_CLASSES = (
    (1_000_000, "under 1 MB"),
    (10_000_000, "a few MB"),
    (100_000_000, "tens of MB"),
    (1_000_000_000, "hundreds of MB"),
)
# A class as the lower end of a range: "tens to hundreds of MB".
_RANGE_STARTS = {"a few MB": "a few", "tens of MB": "tens", "hundreds of MB": "hundreds"}


@dataclass(frozen=True)
class GifOptions:
    # Widest the GIF may be, in pixels; a narrower source keeps its size.
    width: int = GIF_WIDTH
    # Highest frame rate; a slower source keeps its rate.
    fps: float = GIF_FPS


def _displayed(video: VideoStream) -> tuple[int, int] | None:
    if not video.width or not video.height:
        return None
    if video.rotation in (90, 270):
        return video.height, video.width
    return video.width, video.height


def gif_size(video: VideoStream, width: int) -> tuple[int, int] | None:
    """Return the GIF's size for ``video`` at most ``width`` wide.

    The size is of the picture as displayed (ffmpeg turns it upright before
    the filters run); ``None`` when the source's size is unknown.
    """
    shown = _displayed(video)
    if shown is None:
        return None
    shown_width, shown_height = shown
    if shown_width <= width:
        return shown
    return width, max(1, round(shown_height * width / shown_width))


def estimate_size(
    width: int, height: int, fps: float, seconds: float, bytes_per_pixel: float
) -> int:
    """Size in bytes of a GIF of this size, rate and length."""
    return int(width * height * fps * seconds * bytes_per_pixel)


def size_class(size: int) -> str:
    """Name the order of magnitude of ``size`` bytes: ``tens of MB``."""
    for limit, name in _SIZE_CLASSES:
        if size < limit:
            return name
    return "over 1 GB"


def size_range(low: int, high: int) -> str:
    """Name the range from ``low`` to ``high`` bytes: ``a few to tens of MB``."""
    bottom, top = size_class(low), size_class(high)
    if bottom == top:
        return bottom
    if bottom == "under 1 MB":
        return f"up to {top}"
    if top == "over 1 GB":
        return f"{bottom} to over 1 GB"
    return f"{_RANGE_STARTS[bottom]} to {top}"


def plan_gif(
    info: MediaInfo, rng: TrimRange, options: GifOptions = GifOptions()
) -> TrimPlan:
    """Plan the GIF of ``rng`` in ``info`` with ``options``."""
    video = info.video
    if video is None:
        raise MediaError("no video track")
    length, range_notes = fit_range(info, rng)

    filters: list[str] = []
    fps = options.fps
    if video.fps is None or video.fps > options.fps:
        filters.append(f"fps={_number(options.fps)}")
    else:
        fps = video.fps
    size = gif_size(video, options.width)
    if size is not None and size != _displayed(video):
        filters.append(f"scale={size[0]}:{size[1]}:flags=lanczos")
    graph = f"[0:{video.index}]" + "".join(f"{f}," for f in filters) + _PALETTE

    args = ["-filter_complex", graph, "-map", "[g]"]
    if length is not None:
        args += ["-t", seconds_arg(length)]
    args += ["-loop", "0"]

    duration = length
    if duration is None and info.duration is not None:
        duration = info.duration - rng.start

    notes = [f"{size[0]}x{size[1]}, {_number(fps)} fps" if size else f"{_number(fps)} fps"]
    notes += range_notes
    if duration is not None and duration > LONG_GIF:
        if size is not None:
            width, height = size
            expected = size_range(
                estimate_size(width, height, fps, duration, LOW_BYTES_PER_PIXEL),
                estimate_size(width, height, fps, duration, HIGH_BYTES_PER_PIXEL),
            )
        else:
            expected = "a large file"
        notes.append(
            f"long GIF ({format_time(duration)}): expect {expected}; a shorter part, "
            "a smaller --width or a lower --fps makes it smaller"
        )

    seek = ("-ss", seconds_arg(rng.start)) if rng.start > 0 else ()
    return TrimPlan(seek, tuple(args), duration, "; ".join(notes))
