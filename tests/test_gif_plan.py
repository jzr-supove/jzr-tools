"""Pure tests of the GIF planner: sizes, size classes, the ffmpeg arguments and
note for a clip, and the convert options that do not apply to a GIF.

Every input is built by hand, so nothing here needs ffmpeg.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
import unittest

from jzr_tools.core import gif
from jzr_tools.core.gif import (
    GIF_FPS,
    GIF_WIDTH,
    HIGH_BYTES_PER_PIXEL,
    LONG_GIF,
    LOW_BYTES_PER_PIXEL,
    GifOptions,
    estimate_size,
    gif_size,
    plan_gif,
    size_class,
    size_range,
)
from jzr_tools.core.media import (
    AudioStream,
    MediaError,
    MediaInfo,
    VideoOptions,
    VideoStream,
    check_convert_options,
    format_time,
)
from jzr_tools.core.trim import TrimPlan, TrimRange

TAIL = (
    "split[a][b];[a]palettegen=stats_mode=diff[p];"
    "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle[g]"
)
ADVICE = "a shorter part, a smaller --width or a lower --fps makes it smaller"


def graph(filters: str = "", index: int = 0) -> str:
    return f"[0:{index}]{filters + ',' if filters else ''}{TAIL}"


def picture(
    width: int | None = 1920,
    height: int | None = 1080,
    fps: float | None = 30.0,
    rotation: int = 0,
    index: int = 0,
) -> VideoStream:
    return VideoStream(
        index=index,
        codec="h264",
        width=width,
        height=height,
        fps=fps,
        pixel_format="yuv420p",
        rotation=rotation,
        transfer=None,
        bitrate=None,
    )


def sound(index: int = 1) -> AudioStream:
    return AudioStream(
        index=index,
        codec="aac",
        channels=2,
        channel_layout=None,
        sample_rate=48000,
        bitrate=None,
        language=None,
        bits=None,
    )


def clip(
    video: VideoStream | None = None,
    *,
    duration: float | None = 20.0,
    cover: bool = False,
    audio: tuple[AudioStream, ...] | None = None,
) -> MediaInfo:
    return MediaInfo(
        path=Path("source"),
        container="mov,mp4,m4a,3gp,3g2,mj2",
        container_name=None,
        duration=duration,
        size=1000,
        bitrate=None,
        video=picture() if video is None else video,
        audio=(sound(),) if audio is None else audio,
        extra=(),
        tags={},
        cover_art=cover,
    )


def audio_only(*, cover: bool = False, duration: float | None = 20.0) -> MediaInfo:
    return dataclasses.replace(clip(duration=duration, cover=cover), video=None)


WHOLE = TrimRange()


# --- gif_size ----------------------------------------------------------------------


class GifSizeTests(unittest.TestCase):
    def test_a_wide_picture_is_scaled_down_to_the_width(self) -> None:
        self.assertEqual(gif_size(picture(1920, 1080), 480), (480, 270))

    def test_a_chosen_width_is_used(self) -> None:
        self.assertEqual(gif_size(picture(640, 480), 320), (320, 240))

    def test_a_narrower_picture_is_never_scaled_up(self) -> None:
        self.assertEqual(gif_size(picture(64, 64), 480), (64, 64))

    def test_a_picture_exactly_as_wide_is_kept(self) -> None:
        self.assertEqual(gif_size(picture(300, 200), 300), (300, 200))

    def test_the_height_is_rounded_and_not_forced_to_even(self) -> None:
        self.assertEqual(gif_size(picture(481, 271), 480), (480, 270))
        self.assertEqual(gif_size(picture(1000, 331), 333), (333, 110))
        self.assertEqual(gif_size(picture(1000, 333), 333), (333, 111))

    def test_a_turned_picture_is_measured_as_displayed(self) -> None:
        self.assertEqual(gif_size(picture(1280, 720, rotation=90), 480), (480, 853))
        self.assertEqual(gif_size(picture(1280, 720, rotation=270), 480), (480, 853))

    def test_a_turned_picture_that_is_wide_when_upright_is_scaled(self) -> None:
        self.assertEqual(gif_size(picture(1080, 1920, rotation=90), 480), (480, 270))

    def test_a_half_turn_keeps_the_orientation(self) -> None:
        self.assertEqual(gif_size(picture(1920, 1080, rotation=180), 480), (480, 270))

    def test_a_very_flat_picture_keeps_at_least_one_row(self) -> None:
        self.assertEqual(gif_size(picture(4000, 2), 400), (400, 1))

    def test_a_missing_width_or_height_gives_no_size(self) -> None:
        self.assertIsNone(gif_size(picture(None, 1080), 480))
        self.assertIsNone(gif_size(picture(1920, None), 480))
        self.assertIsNone(gif_size(picture(None, None), 480))


# --- estimate_size, size_class and size_range ------------------------------------------------------


class EstimateSizeTests(unittest.TestCase):
    def test_the_estimate_is_pixels_times_frames_times_a_per_pixel_cost(self) -> None:
        self.assertEqual(estimate_size(480, 270, 15, 60, 0.1), 11_664_000)
        self.assertEqual(estimate_size(480, 270, 15, 60, 0.9), 104_976_000)

    def test_the_estimate_is_a_whole_number_of_bytes(self) -> None:
        value = estimate_size(100, 100, 12.5, 3.3, 0.3)
        self.assertIsInstance(value, int)
        self.assertEqual(value, int(100 * 100 * 12.5 * 3.3 * 0.3))

    def test_a_longer_clip_is_estimated_larger(self) -> None:
        self.assertGreater(
            estimate_size(480, 270, 15, 61, 0.1), estimate_size(480, 270, 15, 60, 0.1)
        )

    def test_a_higher_cost_per_pixel_is_estimated_larger(self) -> None:
        self.assertGreater(
            estimate_size(480, 270, 15, 60, 0.9), estimate_size(480, 270, 15, 60, 0.1)
        )

    def test_the_cost_per_pixel_must_be_given(self) -> None:
        with self.assertRaises(TypeError):
            estimate_size(480, 270, 15, 60)  # type: ignore[call-arg]


class SizeClassTests(unittest.TestCase):
    def test_each_class_and_its_boundaries(self) -> None:
        cases = {
            0: "under 1 MB",
            999_999: "under 1 MB",
            1_000_000: "a few MB",
            9_999_999: "a few MB",
            10_000_000: "tens of MB",
            99_999_999: "tens of MB",
            100_000_000: "hundreds of MB",
            999_999_999: "hundreds of MB",
            1_000_000_000: "over 1 GB",
            50_000_000_000: "over 1 GB",
        }
        for size, expected in cases.items():
            with self.subTest(size=size):
                self.assertEqual(size_class(size), expected)


class SizeRangeTests(unittest.TestCase):
    def test_one_class_for_both_ends_is_named_once(self) -> None:
        cases = (
            (0, 999_999, "under 1 MB"),
            (1_000_000, 9_999_999, "a few MB"),
            (10_000_000, 99_999_999, "tens of MB"),
            (100_000_000, 999_999_999, "hundreds of MB"),
            (1_000_000_000, 50_000_000_000, "over 1 GB"),
            (20_000_000, 20_000_000, "tens of MB"),
        )
        for low, high, expected in cases:
            with self.subTest(low=low, high=high):
                self.assertEqual(size_range(low, high), expected)

    def test_a_low_end_under_1_mb_reads_as_up_to_the_high_class(self) -> None:
        cases = (
            (999_999, 1_000_000, "up to a few MB"),
            (500_000, 5_000_000, "up to a few MB"),
            (500_000, 50_000_000, "up to tens of MB"),
            (0, 100_000_000, "up to hundreds of MB"),
            (0, 1_000_000_000, "up to over 1 GB"),
        )
        for low, high, expected in cases:
            with self.subTest(low=low, high=high):
                self.assertEqual(size_range(low, high), expected)

    def test_a_high_end_over_1_gb_reads_as_to_over_1_gb(self) -> None:
        cases = (
            (100_000_000, 1_000_000_000, "hundreds of MB to over 1 GB"),
            (999_999_999, 5_000_000_000, "hundreds of MB to over 1 GB"),
            (10_000_000, 1_000_000_000, "tens of MB to over 1 GB"),
            (1_000_000, 1_000_000_000, "a few MB to over 1 GB"),
        )
        for low, high, expected in cases:
            with self.subTest(low=low, high=high):
                self.assertEqual(size_range(low, high), expected)

    def test_two_neighbouring_or_distant_classes_share_the_unit(self) -> None:
        cases = (
            (1_000_000, 10_000_000, "a few to tens of MB"),
            (9_999_999, 10_000_000, "a few to tens of MB"),
            (1_000_000, 100_000_000, "a few to hundreds of MB"),
            (10_000_000, 100_000_000, "tens to hundreds of MB"),
            (99_999_999, 999_999_999, "tens to hundreds of MB"),
        )
        for low, high, expected in cases:
            with self.subTest(low=low, high=high):
                self.assertEqual(size_range(low, high), expected)


class ConstantsTests(unittest.TestCase):
    def test_the_defaults(self) -> None:
        self.assertEqual(GIF_WIDTH, 480)
        self.assertEqual(GIF_FPS, 15.0)
        self.assertEqual(LONG_GIF, 30.0)
        self.assertEqual(LOW_BYTES_PER_PIXEL, 0.1)
        self.assertEqual(HIGH_BYTES_PER_PIXEL, 0.9)

    def test_the_single_per_pixel_cost_is_gone(self) -> None:
        self.assertFalse(hasattr(gif, "BYTES_PER_PIXEL"))

    def test_the_options_default_to_the_constants(self) -> None:
        options = GifOptions()
        self.assertEqual((options.width, options.fps), (480, 15.0))

    def test_the_options_are_immutable(self) -> None:
        with self.assertRaises(dataclasses.FrozenInstanceError):
            GifOptions().width = 100  # type: ignore[misc]

    def test_the_module_needs_no_command_line_library(self) -> None:
        self.assertFalse(hasattr(gif, "click"))


# --- plan_gif: arguments ---------------------------------------------------------------


class PlanArgumentsTests(unittest.TestCase):
    def test_a_large_fast_clip_is_resampled_and_scaled(self) -> None:
        plan = plan_gif(clip(), WHOLE)
        self.assertEqual(
            plan.args,
            (
                "-filter_complex", graph("fps=15,scale=480:270:flags=lanczos"),
                "-map", "[g]", "-loop", "0",
            ),
        )
        self.assertEqual(plan.input_args, ())
        self.assertIsInstance(plan, TrimPlan)

    def test_the_filter_graph_is_written_out_in_full(self) -> None:
        plan = plan_gif(clip(), WHOLE)
        self.assertEqual(
            plan.args[1],
            "[0:0]fps=15,scale=480:270:flags=lanczos,split[a][b];"
            "[a]palettegen=stats_mode=diff[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle[g]",
        )

    def test_a_small_slow_clip_has_no_filters_before_the_split(self) -> None:
        plan = plan_gif(clip(picture(64, 64, fps=10.0)), WHOLE)
        self.assertEqual(
            plan.args, ("-filter_complex", graph(), "-map", "[g]", "-loop", "0")
        )
        self.assertTrue(plan.args[1].startswith("[0:0]split[a][b];"))

    def test_a_large_slow_clip_is_only_scaled(self) -> None:
        plan = plan_gif(clip(picture(1920, 1080, fps=10.0)), WHOLE)
        self.assertEqual(plan.args[1], graph("scale=480:270:flags=lanczos"))

    def test_a_small_fast_clip_is_only_resampled(self) -> None:
        plan = plan_gif(clip(picture(64, 64, fps=60.0)), WHOLE)
        self.assertEqual(plan.args[1], graph("fps=15"))

    def test_a_source_at_exactly_the_wanted_rate_is_not_resampled(self) -> None:
        plan = plan_gif(clip(picture(64, 64, fps=15.0)), WHOLE)
        self.assertEqual(plan.args[1], graph())

    def test_an_unknown_source_rate_gets_the_wanted_rate(self) -> None:
        plan = plan_gif(clip(picture(64, 64, fps=None)), WHOLE)
        self.assertEqual(plan.args[1], graph("fps=15"))

    def test_a_fractional_rate_keeps_its_decimals(self) -> None:
        plan = plan_gif(clip(picture(64, 64, fps=60.0)), WHOLE, GifOptions(fps=12.5))
        self.assertEqual(plan.args[1], graph("fps=12.5"))

    def test_a_whole_rate_has_no_trailing_zero(self) -> None:
        plan = plan_gif(clip(picture(64, 64, fps=60.0)), WHOLE, GifOptions(fps=24.0))
        self.assertEqual(plan.args[1], graph("fps=24"))

    def test_a_chosen_width_changes_the_scale(self) -> None:
        plan = plan_gif(clip(picture(1920, 1080, fps=10.0)), WHOLE, GifOptions(width=320))
        self.assertEqual(plan.args[1], graph("scale=320:180:flags=lanczos"))

    def test_a_clip_exactly_as_wide_as_asked_is_not_scaled(self) -> None:
        plan = plan_gif(clip(picture(480, 270, fps=10.0)), WHOLE)
        self.assertEqual(plan.args[1], graph())

    def test_a_turned_clip_is_scaled_as_displayed(self) -> None:
        for rotation in (90, 270):
            with self.subTest(rotation=rotation):
                plan = plan_gif(clip(picture(1280, 720, fps=10.0, rotation=rotation)), WHOLE)
                self.assertEqual(plan.args[1], graph("scale=480:853:flags=lanczos"))

    def test_a_turned_clip_that_already_fits_is_not_scaled(self) -> None:
        plan = plan_gif(clip(picture(640, 360, fps=10.0, rotation=90)), WHOLE)
        self.assertEqual(plan.args[1], graph())

    def test_an_unknown_size_is_not_scaled(self) -> None:
        plan = plan_gif(clip(picture(None, None, fps=10.0)), WHOLE)
        self.assertEqual(plan.args[1], graph())
        plan = plan_gif(clip(picture(None, None, fps=30.0)), WHOLE)
        self.assertEqual(plan.args[1], graph("fps=15"))

    def test_the_picture_is_taken_from_its_own_stream_index(self) -> None:
        plan = plan_gif(clip(picture(64, 64, fps=10.0, index=2)), WHOLE)
        self.assertEqual(plan.args[1], graph(index=2))
        self.assertTrue(plan.args[1].startswith("[0:2]"))

    def test_a_real_video_with_cover_art_uses_the_video_stream(self) -> None:
        plan = plan_gif(clip(picture(64, 64, fps=10.0, index=1), cover=True), WHOLE)
        self.assertEqual(plan.args[1], graph(index=1))

    def test_no_audio_is_mapped_and_the_gif_loops_forever(self) -> None:
        plan = plan_gif(clip(audio=(sound(1), sound(2))), WHOLE)
        self.assertEqual(plan.args[2:4], ("-map", "[g]"))
        self.assertEqual(plan.args[-2:], ("-loop", "0"))
        self.assertEqual(plan.args.count("-map"), 1)
        self.assertNotIn("-c:a", plan.args)

    def test_the_plan_does_not_ask_for_a_cover_copy(self) -> None:
        self.assertFalse(plan_gif(clip(), WHOLE).copy_cover)
        self.assertFalse(plan_gif(clip(cover=True), WHOLE).copy_cover)


# --- plan_gif: range ----------------------------------------------------------------


class PlanRangeTests(unittest.TestCase):
    def test_the_whole_file_has_no_seek_and_no_length(self) -> None:
        plan = plan_gif(clip(duration=20), WHOLE)
        self.assertEqual(plan.input_args, ())
        self.assertNotIn("-t", plan.args)
        self.assertEqual(plan.duration, 20.0)

    def test_a_start_is_a_seek_before_the_input(self) -> None:
        plan = plan_gif(clip(duration=20), TrimRange(5, None))
        self.assertEqual(plan.input_args, ("-ss", "5"))
        self.assertNotIn("-ss", plan.args)
        self.assertNotIn("-t", plan.args)
        self.assertEqual(plan.duration, 15.0)

    def test_a_fractional_start_is_written_compactly(self) -> None:
        plan = plan_gif(clip(duration=20), TrimRange(2.5, 4))
        self.assertEqual(plan.input_args, ("-ss", "2.5"))
        self.assertEqual(plan.args[-4:], ("-t", "1.5", "-loop", "0"))

    def test_an_end_gives_a_length_before_the_loop_option(self) -> None:
        plan = plan_gif(clip(duration=20), TrimRange(5, 12))
        self.assertEqual(plan.input_args, ("-ss", "5"))
        self.assertEqual(
            plan.args,
            (
                "-filter_complex", graph("fps=15,scale=480:270:flags=lanczos"),
                "-map", "[g]", "-t", "7", "-loop", "0",
            ),
        )
        self.assertEqual(plan.duration, 7.0)

    def test_an_end_from_zero_has_a_length_and_no_seek(self) -> None:
        plan = plan_gif(clip(duration=20), TrimRange(0, 3))
        self.assertEqual(plan.input_args, ())
        self.assertEqual(plan.args[-4:], ("-t", "3", "-loop", "0"))
        self.assertEqual(plan.duration, 3.0)

    def test_an_end_past_the_file_has_no_length_and_says_where_it_ends(self) -> None:
        plan = plan_gif(clip(duration=20), TrimRange(6, 30))
        self.assertNotIn("-t", plan.args)
        self.assertEqual(plan.duration, 14.0)
        self.assertEqual(plan.note, "480x270, 15 fps; the file ends at 0:20.00")

    def test_an_unknown_file_duration_gives_an_unknown_progress_length(self) -> None:
        plan = plan_gif(clip(duration=None), TrimRange(6, None))
        self.assertIsNone(plan.duration)
        self.assertNotIn("-t", plan.args)

    def test_an_unknown_file_duration_with_an_end_still_has_the_length(self) -> None:
        plan = plan_gif(clip(duration=None), TrimRange(6, 9))
        self.assertEqual(plan.duration, 3.0)
        self.assertEqual(plan.args[-4:], ("-t", "3", "-loop", "0"))

    def test_a_start_past_the_end_fails_naming_the_length(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_gif(clip(duration=20), TrimRange(30, None))
        self.assertEqual(
            str(caught.exception), "--start 0:30.000 is past the end of the file (0:20.00)"
        )

    def test_a_start_at_the_end_is_past_it(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_gif(clip(duration=20), TrimRange(20, 25))
        self.assertEqual(
            str(caught.exception), "--start 0:20.000 is past the end of the file (0:20.00)"
        )


# --- plan_gif: files without a picture -----------------------------------------------------


class PlanNoPictureTests(unittest.TestCase):
    def test_an_audio_file_has_no_video_track(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_gif(audio_only(), WHOLE)
        self.assertEqual(str(caught.exception), "no video track")

    def test_an_audio_file_with_only_cover_art_has_no_video_track(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_gif(audio_only(cover=True), WHOLE)
        self.assertEqual(str(caught.exception), "no video track")

    def test_the_missing_picture_is_reported_before_the_range_is_checked(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_gif(audio_only(duration=10), TrimRange(50, None))
        self.assertEqual(str(caught.exception), "no video track")


# --- plan_gif: note ---------------------------------------------------------------------


class PlanNoteTests(unittest.TestCase):
    def test_a_short_clip_says_its_size_and_rate(self) -> None:
        self.assertEqual(plan_gif(clip(duration=10), WHOLE).note, "480x270, 15 fps")

    def test_a_clip_kept_as_it_is_says_so_with_the_source_rate(self) -> None:
        plan = plan_gif(clip(picture(64, 64, fps=10.0), duration=5), WHOLE)
        self.assertEqual(plan.note, "64x64, 10 fps")

    def test_the_rate_shows_its_decimals(self) -> None:
        plan = plan_gif(
            clip(picture(640, 480, fps=29.97), duration=5), WHOLE, GifOptions(width=320, fps=60)
        )
        self.assertEqual(plan.note, "320x240, 29.97 fps")

    def test_a_turned_clip_notes_the_displayed_size(self) -> None:
        plan = plan_gif(clip(picture(1280, 720, rotation=90), duration=5), WHOLE)
        self.assertEqual(plan.note, "480x853, 15 fps")

    def test_an_unknown_size_leaves_the_size_out(self) -> None:
        plan = plan_gif(clip(picture(None, None), duration=5), WHOLE)
        self.assertEqual(plan.note, "15 fps")

    def test_a_long_clip_gets_the_size_range_and_advice(self) -> None:
        plan = plan_gif(clip(duration=60), WHOLE)
        self.assertEqual(
            plan.note,
            "480x270, 15 fps; long GIF (1:00): expect tens to hundreds of MB; "
            "a shorter part, a smaller --width or a lower --fps makes it smaller",
        )

    def test_exactly_thirty_seconds_is_not_long(self) -> None:
        self.assertEqual(plan_gif(clip(duration=30.0), WHOLE).note, "480x270, 15 fps")

    def test_a_little_over_thirty_seconds_is_long(self) -> None:
        plan = plan_gif(clip(duration=30.5), WHOLE)
        self.assertEqual(
            plan.note,
            f"480x270, 15 fps; long GIF ({format_time(30.5)}): expect a few to tens of MB; {ADVICE}",
        )

    def test_the_length_shown_is_the_part_not_the_whole_file(self) -> None:
        plan = plan_gif(clip(duration=120), TrimRange(10, 41))
        self.assertEqual(
            plan.note,
            f"480x270, 15 fps; long GIF (0:31): expect a few to tens of MB; {ADVICE}",
        )

    def test_a_short_part_of_a_long_file_gets_no_long_note(self) -> None:
        plan = plan_gif(clip(duration=600), TrimRange(10, 20))
        self.assertEqual(plan.note, "480x270, 15 fps")

    def test_a_start_can_bring_a_long_file_under_the_limit(self) -> None:
        plan = plan_gif(clip(duration=40), TrimRange(15, None))
        self.assertEqual(plan.note, "480x270, 15 fps")

    def test_a_smaller_width_moves_the_range_down(self) -> None:
        plan = plan_gif(clip(picture(640, 480), duration=45), WHOLE, GifOptions(width=320))
        self.assertEqual(
            plan.note,
            f"320x240, 15 fps; long GIF (0:45): expect a few to tens of MB; {ADVICE}",
        )

    def test_the_range_follows_the_estimate(self) -> None:
        cases = (
            (clip(picture(16, 16, fps=10.0), duration=31), "under 1 MB", "0:31"),
            (clip(picture(160, 90, fps=15.0), duration=40), "up to a few MB", "0:40"),
            (clip(picture(64, 64, fps=10.0), duration=31), "up to a few MB", "0:31"),
            (clip(duration=400), "tens to hundreds of MB", "6:40"),
            (clip(duration=3725), "hundreds of MB to over 1 GB", "1:02:05"),
        )
        for info, expected, shown in cases:
            with self.subTest(shown=shown):
                note = plan_gif(info, WHOLE).note
                self.assertIn(f"long GIF ({shown}): expect {expected}; {ADVICE}", note)

    def test_the_estimate_uses_the_rate_of_the_output(self) -> None:
        # At 1 fps the 400 s clip is between 5 and 47 MB; at the default 15 fps it
        # would be about 78 to 700 MB.
        slow = clip(picture(1920, 1080, fps=1.0), duration=400)
        self.assertEqual(
            plan_gif(slow, WHOLE).note,
            f"480x270, 1 fps; long GIF (6:40): expect a few to tens of MB; {ADVICE}",
        )

    def test_a_long_clip_of_unknown_size_expects_a_large_file(self) -> None:
        plan = plan_gif(clip(picture(None, None), duration=60), WHOLE)
        self.assertEqual(plan.note, f"15 fps; long GIF (1:00): expect a large file; {ADVICE}")

    def test_an_unknown_length_never_gets_the_long_note(self) -> None:
        plan = plan_gif(clip(duration=None), WHOLE)
        self.assertEqual(plan.note, "480x270, 15 fps")

    def test_the_parts_come_in_order(self) -> None:
        plan = plan_gif(clip(duration=400), TrimRange(10, 500))
        self.assertEqual(
            plan.note,
            "480x270, 15 fps; the file ends at 6:40.00; "
            f"long GIF (6:30): expect tens to hundreds of MB; {ADVICE}",
        )


# --- check_convert_options with a GIF target --------------------------------------------


def refusal(name: str) -> str:
    return f"{name} does not apply to .gif; 'jzr video gif' has the GIF options"


VIDEO_OPTIONS = (
    ("codec", "h264", "--codec"),
    ("quality", "high", "--quality"),
    ("crf", 20, "--crf"),
    ("resize", 720, "--resize"),
    ("fps", 24.0, "--fps"),
    ("no_audio", True, "--no-audio"),
)


class GifConvertOptionsTests(unittest.TestCase):
    def error(self, target: str = "gif", bitrate: str | None = None, **options: object) -> str:
        with self.assertRaises(ValueError) as caught:
            check_convert_options(target, VideoOptions(**options), bitrate=bitrate)  # type: ignore[arg-type]
        return str(caught.exception)

    def test_no_options_are_accepted(self) -> None:
        self.assertIsNone(check_convert_options("gif", VideoOptions()))
        self.assertIsNone(check_convert_options("gif", VideoOptions(), bitrate=None))

    def test_each_video_option_is_refused_by_name(self) -> None:
        for key, value, name in VIDEO_OPTIONS:
            with self.subTest(name=name):
                self.assertEqual(self.error(**{key: value}), refusal(name))

    def test_a_bitrate_is_refused(self) -> None:
        self.assertEqual(self.error(bitrate="128k"), refusal("--bitrate"))

    def test_a_crf_of_zero_still_counts_as_given(self) -> None:
        self.assertEqual(self.error(crf=0), refusal("--crf"))

    def test_the_first_given_in_a_fixed_order_is_named(self) -> None:
        for drop in range(len(VIDEO_OPTIONS)):
            remaining = VIDEO_OPTIONS[drop:]
            given = {key: value for key, value, _ in remaining}
            with self.subTest(first=remaining[0][2]):
                self.assertEqual(self.error(bitrate="128k", **given), refusal(remaining[0][2]))

    def test_a_bitrate_is_named_only_when_nothing_else_is_given(self) -> None:
        self.assertEqual(self.error(bitrate="128k", no_audio=True), refusal("--no-audio"))
        self.assertEqual(self.error(bitrate="128k", fps=10.0), refusal("--fps"))

    def test_the_target_may_be_written_with_a_dot_or_in_capitals(self) -> None:
        for target in (".gif", "GIF", ".GIF"):
            with self.subTest(target=target):
                self.assertIsNone(check_convert_options(target, VideoOptions()))
                self.assertEqual(self.error(target, codec="vp9"), refusal("--codec"))

    def test_other_targets_keep_their_own_wording(self) -> None:
        with self.assertRaises(ValueError) as caught:
            check_convert_options("mp3", VideoOptions(codec="h264"))
        self.assertEqual(str(caught.exception), "--codec applies only to video targets, not .mp3")


if __name__ == "__main__":
    unittest.main()
