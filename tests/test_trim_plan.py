"""Pure tests of trimming: the range checks, target names, both planners, the
per-file step and the keyframe probe.

Every input is built by hand and ffmpeg, ffprobe and the cover step are
patched, so nothing here needs ffmpeg.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from jzr_tools.core import ffmpeg, trim
from jzr_tools.core.ffmpeg import OUTPUT, Job, Tool, Tools
from jzr_tools.core.media import AudioStream, MediaError, MediaInfo, VideoStream
from jzr_tools.core.trim import (
    TRIM_SUFFIX,
    TrimPlan,
    TrimRange,
    fit_range,
    is_trim_output,
    make_range,
    plan_audio_trim,
    plan_video_trim,
    seconds_arg,
    trim_file,
    trim_targets,
)

MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
MATROSKA = "matroska,webm"
FASTSTART = ("-movflags", "+faststart")


def sound(
    codec: str | None = "aac",
    index: int = 1,
    channels: int | None = 2,
    language: str | None = None,
    bitrate: int | None = None,
    bits: int | None = None,
) -> AudioStream:
    return AudioStream(
        index=index,
        codec=codec,
        channels=channels,
        channel_layout=None,
        sample_rate=48000,
        bitrate=bitrate,
        language=language,
        bits=bits,
    )


def picture(
    codec: str | None = "h264",
    width: int | None = 1920,
    height: int | None = 1080,
    pixel_format: str | None = "yuv420p",
    rotation: int = 0,
) -> VideoStream:
    return VideoStream(
        index=0,
        codec=codec,
        width=width,
        height=height,
        fps=30.0,
        pixel_format=pixel_format,
        rotation=rotation,
        transfer=None,
        bitrate=None,
    )


def video_info(
    video: VideoStream | None = None,
    audio: tuple[AudioStream, ...] | None = None,
    *,
    duration: float | None = 20.0,
    extra: tuple[str, ...] = (),
    cover: bool = False,
) -> MediaInfo:
    return MediaInfo(
        path=Path("source"),
        container=MP4_FAMILY,
        container_name=None,
        duration=duration,
        size=1000,
        bitrate=None,
        video=picture() if video is None else video,
        audio=(sound(),) if audio is None else audio,
        extra=extra,
        tags={},
        cover_art=cover,
    )


def audio_info(
    stream: AudioStream | None = None,
    *,
    container: str | None = "mp3",
    duration: float | None = 20.0,
    cover: bool = False,
    audio: tuple[AudioStream, ...] | None = None,
) -> MediaInfo:
    streams = (stream if stream is not None else sound("mp3", 0),) if audio is None else audio
    return MediaInfo(
        path=Path("source"),
        container=container,
        container_name=None,
        duration=duration,
        size=1000,
        bitrate=None,
        video=None,
        audio=streams,
        extra=(),
        tags={},
        cover_art=cover,
    )


def no_video_info() -> MediaInfo:
    return dataclasses.replace(video_info(), video=None)


# --- make_range ----------------------------------------------------------------


class MakeRangeTests(unittest.TestCase):
    def error(self, start, end, duration) -> str:  # noqa: ANN001
        with self.assertRaises(ValueError) as caught:
            make_range(start, end, duration)
        return str(caught.exception)

    def test_the_suffix_is_trim(self) -> None:
        self.assertEqual(TRIM_SUFFIX, "_trim")

    def test_nothing_given_asks_for_a_start_end_or_duration(self) -> None:
        self.assertEqual(self.error(None, None, None), "give --start, --end or --duration")

    def test_end_and_duration_together_are_refused(self) -> None:
        self.assertEqual(
            self.error(None, 10, 5), "--end and --duration cannot be used together"
        )
        self.assertEqual(
            self.error(2, 10, 5), "--end and --duration cannot be used together"
        )

    def test_a_duration_of_zero_or_less_is_refused(self) -> None:
        for duration in (0, 0.0, -3):
            with self.subTest(duration=duration):
                self.assertEqual(self.error(None, None, duration), "--duration must be more than 0")

    def test_an_end_before_the_start_names_both_times(self) -> None:
        self.assertEqual(self.error(10, 5, None), "--end 0:05.000 is not after --start 0:10.000")

    def test_an_end_equal_to_the_start_is_refused(self) -> None:
        self.assertEqual(self.error(7.5, 7.5, None), "--end 0:07.500 is not after --start 0:07.500")

    def test_an_end_at_zero_is_not_after_the_default_start(self) -> None:
        self.assertEqual(self.error(None, 0, None), "--end 0:00.000 is not after --start 0:00.000")

    def test_long_times_use_hours_in_the_message(self) -> None:
        self.assertEqual(
            self.error(3700, 60, None), "--end 1:00.000 is not after --start 1:01:40.000"
        )

    def test_a_start_alone_runs_to_the_end_of_the_file(self) -> None:
        self.assertEqual(make_range(5, None, None), TrimRange(5.0, None))

    def test_an_end_alone_starts_at_zero(self) -> None:
        self.assertEqual(make_range(None, 10, None), TrimRange(0.0, 10.0))

    def test_start_and_end_are_kept(self) -> None:
        self.assertEqual(make_range(2.5, 9.25, None), TrimRange(2.5, 9.25))

    def test_a_duration_becomes_an_end_after_the_start(self) -> None:
        self.assertEqual(make_range(2, None, 5), TrimRange(2.0, 7.0))

    def test_a_duration_alone_starts_at_zero(self) -> None:
        self.assertEqual(make_range(None, None, 5.5), TrimRange(0.0, 5.5))

    def test_a_range_defaults_to_the_whole_file_and_is_immutable(self) -> None:
        whole = TrimRange()
        self.assertEqual((whole.start, whole.end), (0.0, None))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            whole.start = 1.0  # type: ignore[misc]


class SecondsArgTests(unittest.TestCase):
    def test_forms(self) -> None:
        cases = {
            7: "7",
            7.0: "7",
            7.5: "7.5",
            90.25: "90.25",
            10: "10",
            100: "100",
            0: "0",
            0.0000001: "0",
            1 / 3: "0.333333",
            12.3456789: "12.345679",
            0.05: "0.05",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(seconds_arg(value), expected)


# --- fit_range -------------------------------------------------------------------


class FitRangeTests(unittest.TestCase):
    def test_a_range_inside_the_file_gives_its_length_and_no_notes(self) -> None:
        self.assertEqual(fit_range(video_info(duration=20), TrimRange(5, 12)), (7.0, []))

    def test_no_end_means_to_the_end_of_the_file(self) -> None:
        self.assertEqual(fit_range(video_info(duration=20), TrimRange(5, None)), (None, []))

    def test_an_end_exactly_at_the_duration_is_not_clamped(self) -> None:
        self.assertEqual(fit_range(video_info(duration=20), TrimRange(5, 20)), (15.0, []))

    def test_an_end_past_the_file_clamps_with_a_note(self) -> None:
        self.assertEqual(
            fit_range(video_info(duration=20), TrimRange(5, 30)),
            (None, ["the file ends at 0:20.00"]),
        )

    def test_the_clamp_note_uses_hours_for_a_long_file(self) -> None:
        self.assertEqual(
            fit_range(video_info(duration=3725.5), TrimRange(0, 4000)),
            (None, ["the file ends at 1:02:05.50"]),
        )

    def test_a_start_past_the_end_names_the_duration(self) -> None:
        with self.assertRaises(MediaError) as caught:
            fit_range(video_info(duration=20), TrimRange(30, None))
        self.assertEqual(
            str(caught.exception), "--start 0:30.000 is past the end of the file (0:20.00)"
        )

    def test_a_start_at_the_end_is_past_it(self) -> None:
        with self.assertRaises(MediaError) as caught:
            fit_range(video_info(duration=20), TrimRange(20, 25))
        self.assertEqual(
            str(caught.exception), "--start 0:20.000 is past the end of the file (0:20.00)"
        )

    def test_an_unknown_duration_checks_nothing(self) -> None:
        info = video_info(duration=None)
        self.assertEqual(fit_range(info, TrimRange(1000, 2000)), (1000.0, []))
        self.assertEqual(fit_range(info, TrimRange(1000, None)), (None, []))


# --- targets ---------------------------------------------------------------------


class TrimTargetsTests(unittest.TestCase):
    def test_the_target_is_beside_the_source_with_the_suffix(self) -> None:
        self.assertEqual(
            trim_targets([Path("/p/a.mp4"), Path("/p/sub/b.mkv")]),
            [Path("/p/a_trim.mp4"), Path("/p/sub/b_trim.mkv")],
        )

    def test_the_extension_is_lower_cased(self) -> None:
        self.assertEqual(trim_targets([Path("/p/Clip.MP4")]), [Path("/p/Clip_trim.mp4")])

    def test_outdir_without_root_flattens(self) -> None:
        self.assertEqual(
            trim_targets([Path("/p/a/x.mp4")], outdir=Path("/out")), [Path("/out/x_trim.mp4")]
        )

    def test_outdir_with_root_mirrors_the_sub_directories(self) -> None:
        root = Path("/p/root")
        sources = [root / "x.mp4", root / "sub" / "x.mp4", root / "sub" / "deep" / "y.mp3"]
        self.assertEqual(
            trim_targets(sources, outdir=Path("/out"), root=root),
            [
                Path("/out/x_trim.mp4"),
                Path("/out/sub/x_trim.mp4"),
                Path("/out/sub/deep/y_trim.mp3"),
            ],
        )

    def test_sources_that_would_share_a_target_get_a_counter(self) -> None:
        sources = [Path("/p/a/x.mp4"), Path("/p/b/x.mp4"), Path("/p/c/x.mp4")]
        self.assertEqual(
            trim_targets(sources, outdir=Path("/out")),
            [Path("/out/x_trim.mp4"), Path("/out/x_trim-2.mp4"), Path("/out/x_trim-3.mp4")],
        )

    def test_the_clash_check_ignores_case(self) -> None:
        sources = [Path("/p/a/Song.mp3"), Path("/p/b/song.mp3")]
        targets = trim_targets(sources, outdir=Path("/out"))
        self.assertEqual(targets[0], Path("/out/Song_trim.mp3"))
        self.assertEqual(targets[1], Path("/out/song_trim-2.mp3"))

    def test_the_same_stem_in_different_containers_does_not_clash(self) -> None:
        self.assertEqual(
            trim_targets([Path("/p/x.mp4"), Path("/p/x.mov")]),
            [Path("/p/x_trim.mp4"), Path("/p/x_trim.mov")],
        )


class IsTrimOutputTests(unittest.TestCase):
    def test_earlier_results_are_recognised(self) -> None:
        for name in ("a_trim.mp4", "a_trim-2.mp4", "a_trim-10.mp3", "my clip_trim.mkv"):
            with self.subTest(name=name):
                self.assertTrue(is_trim_output(Path("/p") / name))

    def test_other_files_are_not(self) -> None:
        for name in ("a.mp4", "a_trimmed.mp4", "trim.mp4", "a_trim.final.mp4", "a_trim_x.mp4", "a_trim-x.mp4"):
            with self.subTest(name=name):
                self.assertFalse(is_trim_output(Path("/p") / name))


# --- plan_video_trim -----------------------------------------------------------------


MAPS = ("-map", "0:0", "-map", "0:1")


class VideoExactTests(unittest.TestCase):
    def test_h264_in_mp4_is_re_encoded_at_high_quality(self) -> None:
        plan = plan_video_trim(video_info(), "mp4", TrimRange(7, 12))
        self.assertEqual(
            plan,
            TrimPlan(
                input_args=("-ss", "7"),
                args=(
                    *MAPS, "-t", "5",
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-copypriorss:a", "0",
                    *FASTSTART,
                ),
                duration=5.0,
                note="video encoded to h264, audio copied",
            ),
        )
        self.assertFalse(plan.copy_cover)

    def test_hevc_in_mov_is_tagged_hvc1(self) -> None:
        plan = plan_video_trim(video_info(picture("hevc")), "mov", TrimRange(0, 5))
        self.assertEqual(plan.input_args, ())
        self.assertEqual(
            plan.args,
            (
                *MAPS, "-t", "5",
                "-c:v", "libx265", "-crf", "22", "-pix_fmt", "yuv420p",
                "-x265-params", "log-level=error", "-tag:v", "hvc1",
                "-c:a", "copy",
                *FASTSTART,
            ),
        )
        self.assertEqual(plan.note, "video encoded to hevc, audio copied")

    def test_hevc_in_mkv_has_no_hvc1_tag_and_no_faststart(self) -> None:
        plan = plan_video_trim(video_info(picture("hevc")), "mkv", TrimRange(0, 5))
        self.assertEqual(
            plan.args,
            (
                *MAPS, "-t", "5",
                "-c:v", "libx265", "-crf", "22", "-pix_fmt", "yuv420p",
                "-x265-params", "log-level=error",
                "-c:a", "copy",
            ),
        )

    def test_vp9_in_webm_gets_its_extras_and_no_faststart(self) -> None:
        info = video_info(picture("vp9"), (sound("opus"),))
        plan = plan_video_trim(info, "webm", TrimRange(3, 8))
        self.assertEqual(plan.input_args, ("-ss", "3"))
        self.assertEqual(
            plan.args,
            (
                *MAPS, "-t", "5",
                "-c:v", "libvpx-vp9", "-crf", "24", "-b:v", "0", "-pix_fmt", "yuv420p",
                "-row-mt", "1", "-cpu-used", "4",
                "-c:a", "copy", "-copypriorss:a", "0",
            ),
        )
        self.assertEqual(plan.note, "video encoded to vp9, audio copied")

    def test_av1_in_mkv_uses_its_own_quality_level(self) -> None:
        plan = plan_video_trim(video_info(picture("av1")), "mkv", TrimRange(0, 5))
        self.assertEqual(
            plan.args,
            (
                *MAPS, "-t", "5",
                "-c:v", "libsvtav1", "-crf", "24", "-pix_fmt", "yuv420p",
                "-c:a", "copy",
            ),
        )

    def test_a_codec_without_an_encoder_becomes_h264(self) -> None:
        cases = {("mpeg4", "mp4"): "mp4", ("prores", "mov"): "mov", ("vp8", "mkv"): "mkv"}
        for (codec, ext) in cases:
            with self.subTest(codec=codec, ext=ext):
                plan = plan_video_trim(video_info(picture(codec)), ext, TrimRange(0, 5))
                self.assertEqual(plan.note, "video encoded to h264, audio copied")
                i = plan.args.index("-c:v")
                self.assertEqual(plan.args[i : i + 4], ("-c:v", "libx264", "-crf", "18"))

    def test_a_codec_without_an_encoder_in_webm_becomes_vp9(self) -> None:
        plan = plan_video_trim(
            video_info(picture("vp8"), (sound("vorbis"),)), "webm", TrimRange(0, 5)
        )
        self.assertEqual(plan.note, "video encoded to vp9, audio copied")
        self.assertIn("libvpx-vp9", plan.args)
        self.assertNotIn("libx264", plan.args)

    def test_an_exact_cut_of_theora_in_ogv_is_refused_with_the_way_out(self) -> None:
        info = video_info(picture("theora"), (sound("vorbis"),))
        with self.assertRaises(MediaError) as caught:
            plan_video_trim(info, "ogv", TrimRange(1, 5))
        self.assertEqual(
            str(caught.exception),
            "an exact cut cannot be written back into .ogv; use --fast",
        )

    def test_a_fast_cut_of_ogv_copies(self) -> None:
        info = video_info(picture("theora"), (sound("vorbis"),))
        plan = plan_video_trim(info, "ogv", TrimRange(1, 5), fast=True)
        self.assertIn("copy", plan.args)

    def test_ten_bit_hevc_keeps_ten_bit_pixels(self) -> None:
        info = video_info(picture("hevc", pixel_format="yuv420p10le"))
        plan = plan_video_trim(info, "mkv", TrimRange(0, 5))
        self.assertEqual(plan.args[plan.args.index("-pix_fmt") + 1], "yuv420p10le")

    def test_ten_bit_h264_is_written_as_eight_bit(self) -> None:
        info = video_info(picture("h264", pixel_format="yuv420p10le"))
        plan = plan_video_trim(info, "mkv", TrimRange(0, 5))
        self.assertEqual(plan.args[plan.args.index("-pix_fmt") + 1], "yuv420p")

    def test_an_odd_sized_picture_is_scaled_down_to_even(self) -> None:
        info = video_info(picture(width=1921, height=1081))
        plan = plan_video_trim(info, "mp4", TrimRange(0, 5))
        self.assertEqual(
            plan.args,
            (
                *MAPS, "-t", "5",
                "-vf", "scale=1920:1080",
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                *FASTSTART,
            ),
        )

    def test_an_even_sized_picture_has_no_filter(self) -> None:
        plan = plan_video_trim(video_info(), "mp4", TrimRange(0, 5))
        self.assertNotIn("-vf", plan.args)

    def test_a_rotated_odd_picture_is_scaled_as_displayed(self) -> None:
        info = video_info(picture(width=1281, height=721, rotation=90))
        plan = plan_video_trim(info, "mp4", TrimRange(0, 5))
        self.assertEqual(plan.args[plan.args.index("-vf") + 1], "scale=720:1280")

    def test_without_audio_there_is_no_audio_map_or_codec(self) -> None:
        plan = plan_video_trim(video_info(audio=()), "mp4", TrimRange(4, 9))
        self.assertEqual(
            plan.args,
            (
                "-map", "0:0", "-t", "5",
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                *FASTSTART,
            ),
        )
        self.assertEqual(plan.note, "video encoded to h264")

    def test_a_start_of_zero_has_no_seek_and_no_prior_sample_flag(self) -> None:
        plan = plan_video_trim(video_info(), "mp4", TrimRange(0, 5))
        self.assertEqual(plan.input_args, ())
        self.assertNotIn("-ss", plan.args)
        self.assertNotIn("-copypriorss:a", plan.args)

    def test_a_fractional_start_is_written_compactly(self) -> None:
        plan = plan_video_trim(video_info(), "mp4", TrimRange(7.5, 10.25))
        self.assertEqual(plan.input_args, ("-ss", "7.5"))
        self.assertEqual(plan.args[plan.args.index("-t") + 1], "2.75")

    def test_no_end_means_no_length_and_the_rest_of_the_file_is_the_duration(self) -> None:
        plan = plan_video_trim(video_info(duration=20), "mp4", TrimRange(6, None))
        self.assertNotIn("-t", plan.args)
        self.assertEqual(plan.duration, 14.0)

    def test_an_end_past_the_file_has_no_length_and_a_note(self) -> None:
        plan = plan_video_trim(video_info(duration=20), "mp4", TrimRange(6, 30))
        self.assertNotIn("-t", plan.args)
        self.assertEqual(plan.duration, 14.0)
        self.assertEqual(
            plan.note, "video encoded to h264, audio copied; the file ends at 0:20.00"
        )

    def test_a_start_past_the_end_fails_naming_the_duration(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_video_trim(video_info(duration=20), "mp4", TrimRange(30, None))
        self.assertEqual(
            str(caught.exception), "--start 0:30.000 is past the end of the file (0:20.00)"
        )

    def test_an_unknown_duration_gives_an_unknown_progress_length(self) -> None:
        plan = plan_video_trim(video_info(duration=None), "mp4", TrimRange(6, None))
        self.assertIsNone(plan.duration)
        self.assertNotIn("-t", plan.args)

    def test_the_audio_with_most_channels_is_kept_and_the_others_listed(self) -> None:
        audio = (sound("aac", 1, 2), sound("ac3", 2, 6, language="en"), sound("aac", 3, 1))
        plan = plan_video_trim(video_info(audio=audio), "mkv", TrimRange(0, 5))
        self.assertEqual(plan.args[:4], ("-map", "0:0", "-map", "0:2"))
        self.assertEqual(
            plan.note,
            "video encoded to h264, audio copied; "
            "dropped: audio stream 1 (aac), audio stream 3 (aac)",
        )

    def test_dropped_streams_are_listed_in_order_but_data_streams_are_not(self) -> None:
        info = video_info(
            audio=(sound("aac", 1, 6), sound("aac", 2, 2, language="fr")),
            extra=("subtitle stream 3 (subrip)", "data stream 4"),
            cover=True,
        )
        plan = plan_video_trim(info, "mkv", TrimRange(0, 5))
        self.assertEqual(
            plan.note,
            "video encoded to h264, audio copied; "
            "dropped: audio stream 2 (aac, fr), subtitle stream 3 (subrip), cover art",
        )

    def test_only_data_streams_leave_no_dropped_note(self) -> None:
        plan = plan_video_trim(video_info(extra=("data stream 2",)), "mkv", TrimRange(0, 5))
        self.assertEqual(plan.note, "video encoded to h264, audio copied")

    def test_a_file_without_video_points_to_audio_trim(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_video_trim(no_video_info(), "mp4", TrimRange(0, 5))
        self.assertEqual(str(caught.exception), "no video track; use 'jzr audio trim' for it")

    def test_faststart_only_for_mp4_m4v_and_mov(self) -> None:
        for ext, expected in (
            ("mp4", True), ("m4v", True), ("mov", True),
            ("mkv", False), ("webm", False), ("avi", False),
        ):
            with self.subTest(ext=ext):
                plan = plan_video_trim(video_info(picture("vp9")), ext, TrimRange(0, 5))
                self.assertEqual(plan.args[-2:] == FASTSTART, expected)
                self.assertEqual("-movflags" in plan.args, expected)

    def test_keyframe_is_ignored_unless_fast(self) -> None:
        plan = plan_video_trim(video_info(), "mp4", TrimRange(7, 12), keyframe=5.0)
        self.assertEqual(plan.note, "video encoded to h264, audio copied")
        self.assertEqual(plan.duration, 5.0)


class VideoFastTests(unittest.TestCase):
    def test_fast_copies_both_streams_and_zeroes_the_timestamps(self) -> None:
        plan = plan_video_trim(video_info(), "mkv", TrimRange(7, 12), fast=True)
        self.assertEqual(
            plan,
            TrimPlan(
                input_args=("-ss", "7"),
                args=(
                    *MAPS, "-t", "5",
                    "-c:v", "copy", "-c:a", "copy",
                    "-avoid_negative_ts", "make_zero",
                ),
                duration=5.0,
                note="video copied, audio copied",
            ),
        )

    def test_fast_in_mp4_ends_with_faststart(self) -> None:
        plan = plan_video_trim(video_info(), "mp4", TrimRange(7, 12), fast=True)
        self.assertEqual(
            plan.args[-4:], ("-avoid_negative_ts", "make_zero", "-movflags", "+faststart")
        )

    def test_fast_without_audio(self) -> None:
        plan = plan_video_trim(video_info(audio=()), "mkv", TrimRange(0, 5), fast=True)
        self.assertEqual(
            plan.args,
            ("-map", "0:0", "-t", "5", "-c:v", "copy", "-avoid_negative_ts", "make_zero"),
        )
        self.assertEqual(plan.note, "video copied")

    def test_fast_from_the_start_has_no_seek_but_still_zeroes_timestamps(self) -> None:
        plan = plan_video_trim(video_info(), "mkv", TrimRange(0, 5), fast=True)
        self.assertEqual(plan.input_args, ())
        self.assertEqual(plan.args[-2:], ("-avoid_negative_ts", "make_zero"))

    def test_fast_without_an_end_has_no_length(self) -> None:
        plan = plan_video_trim(video_info(duration=20), "mkv", TrimRange(7, None), fast=True)
        self.assertNotIn("-t", plan.args)
        self.assertEqual(plan.duration, 13.0)

    def test_a_keyframe_before_the_start_is_noted_and_the_pre_roll_counted(self) -> None:
        plan = plan_video_trim(
            video_info(), "mkv", TrimRange(7, 12), fast=True, keyframe=5.0
        )
        self.assertEqual(
            plan.note,
            "video copied, audio copied; starts at 0:05.000, the keyframe before 0:07.000",
        )
        self.assertEqual(plan.duration, 7.0)
        self.assertEqual(plan.input_args, ("-ss", "7"))
        self.assertEqual(plan.args[plan.args.index("-t") + 1], "5")

    def test_a_keyframe_within_half_a_millisecond_is_not_mentioned(self) -> None:
        for keyframe in (7.0, 6.9996, 7.0004):
            with self.subTest(keyframe=keyframe):
                plan = plan_video_trim(
                    video_info(), "mkv", TrimRange(7, 12), fast=True, keyframe=keyframe
                )
                self.assertEqual(plan.note, "video copied, audio copied")

    def test_a_keyframe_just_over_half_a_millisecond_away_is_mentioned(self) -> None:
        plan = plan_video_trim(
            video_info(), "mkv", TrimRange(7, 12), fast=True, keyframe=6.9994
        )
        self.assertEqual(
            plan.note,
            "video copied, audio copied; starts at 0:06.999, the keyframe before 0:07.000",
        )

    def test_a_missing_keyframe_adds_nothing(self) -> None:
        plan = plan_video_trim(video_info(), "mkv", TrimRange(7, 12), fast=True, keyframe=None)
        self.assertEqual(plan.note, "video copied, audio copied")
        self.assertEqual(plan.duration, 5.0)

    def test_the_pre_roll_is_added_to_a_length_that_runs_to_the_end(self) -> None:
        plan = plan_video_trim(
            video_info(duration=20), "mkv", TrimRange(7, None), fast=True, keyframe=5.0
        )
        self.assertEqual(plan.duration, 15.0)

    def test_note_parts_come_in_order(self) -> None:
        info = video_info(audio=(sound("aac", 1, 2), sound("aac", 2, 1)))
        plan = plan_video_trim(
            info, "mkv", TrimRange(7, 30), fast=True, keyframe=5.0
        )
        self.assertEqual(
            plan.note,
            "video copied, audio copied; the file ends at 0:20.00; "
            "dropped: audio stream 2 (aac); "
            "starts at 0:05.000, the keyframe before 0:07.000",
        )

    def test_a_start_past_the_end_fails_in_fast_mode_too(self) -> None:
        with self.assertRaises(MediaError):
            plan_video_trim(video_info(duration=20), "mkv", TrimRange(25, None), fast=True)

    def test_a_file_without_video_fails_in_fast_mode_too(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_video_trim(no_video_info(), "mkv", TrimRange(0, 5), fast=True)
        self.assertEqual(str(caught.exception), "no video track; use 'jzr audio trim' for it")


# --- plan_audio_trim -------------------------------------------------------------------

COVER_ARGS = ("-map", "0:v", "-c:v", "copy", "-disposition:v", "attached_pic")


class AudioLosslessTests(unittest.TestCase):
    def test_wav_16_bit_is_cut_to_the_sample(self) -> None:
        info = audio_info(sound("pcm_s16le", 0, bits=16), container="wav")
        plan = plan_audio_trim(info, "wav", TrimRange(1.5, 3.5))
        self.assertEqual(
            plan,
            TrimPlan(
                input_args=("-ss", "1.5"),
                args=("-map", "0:a:0", "-map_metadata", "0", "-t", "2", "-c:a", "pcm_s16le"),
                duration=2.0,
                note="cut to the sample",
            ),
        )

    def test_wav_24_bit_keeps_its_codec(self) -> None:
        info = audio_info(sound("pcm_s24le", 0, bits=24), container="wav")
        plan = plan_audio_trim(info, "wav", TrimRange(0, 2))
        self.assertEqual(plan.input_args, ())
        self.assertEqual(
            plan.args, ("-map", "0:a:0", "-map_metadata", "0", "-t", "2", "-c:a", "pcm_s24le")
        )

    def test_flac_16_bit_and_deeper(self) -> None:
        for bits, fmt in ((16, "s16"), (24, "s32"), (32, "s32"), (None, "s16")):
            with self.subTest(bits=bits):
                info = audio_info(sound("flac", 0, bits=bits), container="flac")
                plan = plan_audio_trim(info, "flac", TrimRange(2, 4))
                self.assertEqual(
                    plan.args,
                    (
                        "-map", "0:a:0", "-map_metadata", "0", "-t", "2",
                        "-c:a", "flac", "-sample_fmt", fmt, "-compression_level", "5",
                    ),
                )
                self.assertEqual(plan.note, "cut to the sample")

    def test_flac_with_cover_art_keeps_the_cover_mapping(self) -> None:
        info = audio_info(sound("flac", 0, bits=16), container="flac", cover=True)
        plan = plan_audio_trim(info, "flac", TrimRange(0, 2))
        self.assertEqual(
            plan.args,
            (
                "-map", "0:a:0", *COVER_ARGS, "-map_metadata", "0", "-t", "2",
                "-c:a", "flac", "-sample_fmt", "s16", "-compression_level", "5",
            ),
        )
        self.assertFalse(plan.copy_cover)
        self.assertEqual(plan.note, "cut to the sample")

    def test_wav_cannot_hold_cover_art_and_says_so(self) -> None:
        info = audio_info(sound("pcm_s16le", 0, bits=16), container="wav", cover=True)
        plan = plan_audio_trim(info, "wav", TrimRange(0, 2))
        self.assertNotIn("0:v", plan.args)
        self.assertNotIn("-disposition:v", plan.args)
        self.assertFalse(plan.copy_cover)
        self.assertEqual(plan.note, "cut to the sample; cover art dropped: .wav cannot hold it")

    def test_exact_on_a_lossless_file_adds_the_caveat(self) -> None:
        info = audio_info(sound("flac", 0, bits=16), container="flac")
        plan = plan_audio_trim(info, "flac", TrimRange(0, 2), exact=True)
        self.assertEqual(
            plan.note, "cut to the sample; --exact changes nothing: .flac is always cut to the sample"
        )
        self.assertEqual(
            plan.args[-6:],
            ("-c:a", "flac", "-sample_fmt", "s16", "-compression_level", "5"),
        )

    def test_the_caveat_names_the_wav_extension(self) -> None:
        info = audio_info(sound("pcm_s16le", 0, bits=16), container="wav")
        plan = plan_audio_trim(info, "wav", TrimRange(0, 2), exact=True)
        self.assertEqual(
            plan.note, "cut to the sample; --exact changes nothing: .wav is always cut to the sample"
        )


class AudioCopyTests(unittest.TestCase):
    def test_mp3_is_copied_without_prior_samples_and_ends_with_id3v23(self) -> None:
        plan = plan_audio_trim(audio_info(), "mp3", TrimRange(3, 8))
        self.assertEqual(
            plan,
            TrimPlan(
                input_args=("-ss", "3"),
                args=(
                    "-map", "0:a:0", "-map_metadata", "0", "-t", "5",
                    "-c:a", "copy", "-copypriorss:a", "0",
                    "-id3v2_version", "3",
                ),
                duration=5.0,
                note="audio copied",
            ),
        )

    def test_mp3_from_the_start_has_no_seek_and_no_prior_sample_flag(self) -> None:
        plan = plan_audio_trim(audio_info(), "mp3", TrimRange(0, 5))
        self.assertEqual(plan.input_args, ())
        self.assertEqual(
            plan.args,
            ("-map", "0:a:0", "-map_metadata", "0", "-t", "5", "-c:a", "copy", "-id3v2_version", "3"),
        )

    def test_mp3_cover_art_is_mapped_and_not_copied_by_hand(self) -> None:
        plan = plan_audio_trim(audio_info(cover=True), "mp3", TrimRange(0, 5))
        self.assertEqual(
            plan.args,
            (
                "-map", "0:a:0", *COVER_ARGS, "-map_metadata", "0", "-t", "5",
                "-c:a", "copy", "-id3v2_version", "3",
            ),
        )
        self.assertFalse(plan.copy_cover)

    def test_m4a_is_copied(self) -> None:
        info = audio_info(sound("aac", 0), container=MP4_FAMILY)
        plan = plan_audio_trim(info, "m4a", TrimRange(3, 8))
        self.assertEqual(
            plan.args,
            (
                "-map", "0:a:0", "-map_metadata", "0", "-t", "5",
                "-c:a", "copy", "-copypriorss:a", "0",
            ),
        )
        self.assertEqual(plan.note, "audio copied")

    def test_m4a_keeps_its_cover_mapping(self) -> None:
        info = audio_info(sound("aac", 0), container=MP4_FAMILY, cover=True)
        plan = plan_audio_trim(info, "m4a", TrimRange(0, 5))
        self.assertEqual(plan.args[:8], ("-map", "0:a:0", *COVER_ARGS))
        self.assertFalse(plan.copy_cover)

    def test_ogg_reads_its_tags_from_the_audio_stream(self) -> None:
        info = audio_info(sound("vorbis", 0), container="ogg")
        plan = plan_audio_trim(info, "ogg", TrimRange(3, 8))
        self.assertEqual(
            plan.args,
            (
                "-map", "0:a:0", "-map_metadata", "0:s:a:0", "-t", "5",
                "-c:a", "copy", "-copypriorss:a", "0",
            ),
        )
        self.assertFalse(plan.copy_cover)
        self.assertEqual(plan.note, "audio copied")

    def test_ogg_and_opus_cover_art_is_copied_with_mutagen_not_ffmpeg(self) -> None:
        for codec, ext in (("vorbis", "ogg"), ("opus", "opus")):
            with self.subTest(ext=ext):
                info = audio_info(sound(codec, 0), container="ogg", cover=True)
                plan = plan_audio_trim(info, ext, TrimRange(0, 5))
                self.assertTrue(plan.copy_cover)
                self.assertNotIn("0:v", plan.args)
                self.assertNotIn("-disposition:v", plan.args)
                self.assertEqual(plan.note, "audio copied")

    def test_an_end_past_the_file_has_no_length_and_a_note(self) -> None:
        plan = plan_audio_trim(audio_info(duration=20), "mp3", TrimRange(5, 30))
        self.assertNotIn("-t", plan.args)
        self.assertEqual(plan.duration, 15.0)
        self.assertEqual(plan.note, "audio copied; the file ends at 0:20.00")

    def test_a_start_past_the_end_fails_naming_the_duration(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_audio_trim(audio_info(duration=20), "mp3", TrimRange(20, None))
        self.assertEqual(
            str(caught.exception), "--start 0:20.000 is past the end of the file (0:20.00)"
        )

    def test_an_unknown_duration_has_no_progress_length_without_an_end(self) -> None:
        plan = plan_audio_trim(audio_info(duration=None), "mp3", TrimRange(5, None))
        self.assertIsNone(plan.duration)

    def test_a_real_video_points_to_video_trim(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_audio_trim(video_info(), "mp4", TrimRange(0, 5))
        self.assertEqual(str(caught.exception), "a video file; use 'jzr video trim' for it")

    def test_a_video_without_sound_is_still_a_video(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_audio_trim(video_info(audio=()), "mp4", TrimRange(0, 5))
        self.assertEqual(str(caught.exception), "a video file; use 'jzr video trim' for it")

    def test_a_file_without_audio_fails(self) -> None:
        with self.assertRaises(MediaError) as caught:
            plan_audio_trim(audio_info(audio=()), "mp3", TrimRange(0, 5))
        self.assertEqual(str(caught.exception), "no audio track")

    def test_the_first_audio_stream_is_the_one_used(self) -> None:
        info = audio_info(audio=(sound("mp3", 0, 1), sound("aac", 1, 6)))
        plan = plan_audio_trim(info, "mp3", TrimRange(0, 5))
        self.assertIn("-c:a", plan.args)
        self.assertEqual(plan.args[plan.args.index("-c:a") + 1], "copy")
        self.assertEqual(plan.args[:2], ("-map", "0:a:0"))


class AudioExactTests(unittest.TestCase):
    def exact(self, codec: str, ext: str, bitrate: int | None, start: float = 3.0, **kw):  # noqa: ANN003, ANN201
        info = audio_info(sound(codec, 0, bitrate=bitrate), container=kw.pop("container", ext))
        return plan_audio_trim(info, ext, TrimRange(start, start + 5), exact=True)

    def test_mp3_is_re_encoded_at_the_source_bitrate(self) -> None:
        plan = self.exact("mp3", "mp3", 128000)
        self.assertEqual(
            plan.args,
            (
                "-map", "0:a:0", "-map_metadata", "0", "-t", "5",
                "-c:a", "libmp3lame", "-b:a", "128k",
                "-id3v2_version", "3",
            ),
        )
        self.assertEqual(plan.input_args, ("-ss", "3"))
        self.assertEqual(plan.note, "audio encoded to mp3 at 128 kb/s")
        self.assertEqual(plan.duration, 5.0)

    def test_the_bitrate_is_rounded_to_whole_kilobits(self) -> None:
        plan = self.exact("mp3", "mp3", 127600)
        self.assertEqual(plan.args[plan.args.index("-b:a") + 1], "128k")
        self.assertTrue(plan.note.endswith("at 128 kb/s"))

    def test_mp3_bitrate_is_clamped_to_32_and_320(self) -> None:
        for bitrate, expected in ((400000, "320"), (8000, "32"), (320000, "320"), (32000, "32")):
            with self.subTest(bitrate=bitrate):
                plan = self.exact("mp3", "mp3", bitrate)
                self.assertEqual(plan.args[plan.args.index("-b:a") + 1], f"{expected}k")
                self.assertEqual(plan.note, f"audio encoded to mp3 at {expected} kb/s")

    def test_aac_in_m4a(self) -> None:
        plan = self.exact("aac", "m4a", 192000, container=MP4_FAMILY)
        self.assertEqual(
            plan.args,
            (
                "-map", "0:a:0", "-map_metadata", "0", "-t", "5",
                "-c:a", "aac", "-b:a", "192k",
            ),
        )
        self.assertEqual(plan.note, "audio encoded to aac at 192 kb/s")

    def test_opus_and_vorbis(self) -> None:
        opus = self.exact("opus", "opus", 96000, container="ogg")
        self.assertEqual(
            opus.args,
            (
                "-map", "0:a:0", "-map_metadata", "0:s:a:0", "-t", "5",
                "-c:a", "libopus", "-b:a", "96k",
            ),
        )
        self.assertEqual(opus.note, "audio encoded to opus at 96 kb/s")
        vorbis = self.exact("vorbis", "ogg", 160000)
        self.assertEqual(
            vorbis.args,
            (
                "-map", "0:a:0", "-map_metadata", "0:s:a:0", "-t", "5",
                "-c:a", "libvorbis", "-b:a", "160k",
            ),
        )
        self.assertEqual(vorbis.note, "audio encoded to vorbis at 160 kb/s")

    def test_aac_opus_and_vorbis_bitrates_are_clamped_to_8_and_512(self) -> None:
        cases = (
            ("aac", "m4a", MP4_FAMILY),
            ("opus", "opus", "ogg"),
            ("vorbis", "ogg", "ogg"),
        )
        for codec, ext, container in cases:
            for bitrate, expected in ((900000, "512"), (4000, "8")):
                with self.subTest(codec=codec, bitrate=bitrate):
                    plan = self.exact(codec, ext, bitrate, container=container)
                    self.assertEqual(plan.args[plan.args.index("-b:a") + 1], f"{expected}k")
                    self.assertTrue(plan.note.endswith(f"at {expected} kb/s"), plan.note)

    def test_without_a_stream_bitrate_the_convert_defaults_apply(self) -> None:
        cases = (
            ("mp3", "mp3", "mp3", ("-c:a", "libmp3lame", "-q:a", "2"), "audio encoded to mp3"),
            ("aac", "m4a", MP4_FAMILY, ("-c:a", "aac", "-b:a", "192k"), "audio encoded to aac"),
            ("opus", "opus", "ogg", ("-c:a", "libopus", "-b:a", "128k"), "audio encoded to opus"),
            ("vorbis", "ogg", "ogg", ("-c:a", "libvorbis", "-q:a", "5"), "audio encoded to vorbis"),
        )
        for codec, ext, container, encoder, note in cases:
            with self.subTest(codec=codec):
                plan = self.exact(codec, ext, None, container=container)
                i = plan.args.index("-c:a")
                self.assertEqual(plan.args[i : i + 4], encoder)
                self.assertEqual(plan.note, note)

    def test_re_encoding_has_no_prior_sample_flag(self) -> None:
        plan = self.exact("mp3", "mp3", 128000)
        self.assertNotIn("-copypriorss:a", plan.args)

    def test_exact_on_another_codec_fails(self) -> None:
        info = audio_info(sound("alac", 0), container=MP4_FAMILY)
        with self.assertRaises(MediaError) as caught:
            plan_audio_trim(info, "m4a", TrimRange(0, 5), exact=True)
        self.assertEqual(
            str(caught.exception),
            "--exact cannot re-encode alac; leave it out to cut by copying",
        )

    def test_the_same_codec_is_copied_without_exact(self) -> None:
        info = audio_info(sound("alac", 0), container=MP4_FAMILY)
        plan = plan_audio_trim(info, "m4a", TrimRange(0, 5))
        self.assertEqual(plan.args[-2:], ("-c:a", "copy"))

    def test_exact_mp3_keeps_the_cover_mapping(self) -> None:
        info = audio_info(sound("mp3", 0, bitrate=128000), cover=True)
        plan = plan_audio_trim(info, "mp3", TrimRange(0, 5), exact=True)
        self.assertEqual(plan.args[:8], ("-map", "0:a:0", *COVER_ARGS))
        self.assertEqual(plan.args[-2:], ("-id3v2_version", "3"))


class AudioNoteOrderTests(unittest.TestCase):
    def test_summary_then_fit_note_then_caveat_then_cover_note(self) -> None:
        info = audio_info(sound("pcm_s16le", 0, bits=16), container="wav", cover=True, duration=20)
        plan = plan_audio_trim(info, "wav", TrimRange(5, 30), exact=True)
        self.assertEqual(
            plan.note,
            "cut to the sample; the file ends at 0:20.00; "
            "--exact changes nothing: .wav is always cut to the sample; "
            "cover art dropped: .wav cannot hold it",
        )


# --- trim_file -----------------------------------------------------------------------

PROBE = "jzr_tools.core.ffmpeg.probe"
RUN = "jzr_tools.core.ffmpeg.run"
READ_TAGS = "jzr_tools.core.tags.read_tags"
APPLY_EDIT = "jzr_tools.core.tags.apply_edit"

TOOLS = Tools(Tool("ffmpeg-bin", "PATH"), Tool("ffprobe-bin", "PATH"))
PLAN = TrimPlan(
    input_args=("-ss", "7"),
    args=("-map", "0:0", "-t", "5", "-c:v", "copy"),
    duration=5.0,
    note="video copied",
)


class TrimFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name).resolve()
        self.source = self.folder / "clip.mp4"
        self.source.write_bytes(b"source")
        self.target = self.folder / "clip_trim.mp4"
        self.info = video_info(duration=20)

    def start(self, **patches: object) -> dict[str, mock.Mock]:
        mocks = {}
        for name, target in (("probe", PROBE), ("run", RUN)):
            patcher = mock.patch(target, **({"return_value": self.info} if name == "probe" else {}))
            mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        return mocks

    def go(self, plan=None, **kwargs: object):  # noqa: ANN001, ANN201
        planner = plan if plan is not None else mock.Mock(return_value=PLAN)
        return trim_file(
            self.source, self.target, tools=TOOLS, plan=planner, **kwargs  # type: ignore[arg-type]
        ), planner

    def test_the_same_file_is_skipped_before_anything_runs(self) -> None:
        mocks = self.start()
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                result = trim_file(
                    self.source, self.source, tools=TOOLS, plan=mock.Mock(), overwrite=overwrite
                )
                self.assertEqual((result.status, result.detail), ("skipped", "source is the target"))
        mocks["probe"].assert_not_called()
        mocks["run"].assert_not_called()

    def test_an_existing_target_is_skipped_without_overwrite(self) -> None:
        self.target.write_bytes(b"old")
        mocks = self.start()
        keyframe = mock.Mock()
        result, planner = self.go(keyframe=keyframe)
        self.assertEqual(
            (result.status, result.detail), ("skipped", "target exists (use --overwrite)")
        )
        self.assertEqual(result.source, self.source)
        self.assertEqual(result.target, self.target)
        for probe_like in (mocks["probe"], mocks["run"], planner, keyframe):
            probe_like.assert_not_called()

    def test_overwrite_lets_an_existing_target_through(self) -> None:
        self.target.write_bytes(b"old")
        mocks = self.start()
        result, _ = self.go(overwrite=True)
        self.assertEqual(result.status, "converted")
        mocks["run"].assert_called_once()

    def test_a_trim_converts_and_reports_the_plans_note(self) -> None:
        mocks = self.start()
        result, planner = self.go()
        self.assertEqual(result.status, "converted")
        self.assertEqual(result.detail, "video copied")
        self.assertEqual(result.source, self.source)
        self.assertEqual(result.target, self.target)
        mocks["probe"].assert_called_once_with(self.source, TOOLS.ffprobe)
        planner.assert_called_once_with(self.info, None)

    def test_the_job_puts_the_seek_before_the_input_and_the_rest_after_it(self) -> None:
        mocks = self.start()
        result, _ = self.go()
        (job,), kwargs = mocks["run"].call_args.args[:1], mocks["run"].call_args.kwargs
        self.assertEqual(mocks["run"].call_args.args[1], TOOLS)
        expected_step = (
            "-ss", "7", "-i", os.path.abspath(self.source),
            "-map", "0:0", "-t", "5", "-c:v", "copy", OUTPUT,
        )
        self.assertEqual(job, Job(steps=(expected_step,), target=self.target, duration=5.0))
        self.assertEqual(result.command[0], TOOLS.ffmpeg.path)
        self.assertIn(os.path.abspath(self.target), result.command)
        self.assertNotIn(OUTPUT, result.command)
        self.assertEqual(kwargs.get("on_progress"), None)

    def test_without_a_seek_the_step_starts_at_the_input(self) -> None:
        mocks = self.start()
        plan = TrimPlan((), ("-map", "0:a:0"), None, "audio copied")
        self.go(plan=mock.Mock(return_value=plan))
        job = mocks["run"].call_args.args[0]
        self.assertEqual(job.steps, (("-i", os.path.abspath(self.source), "-map", "0:a:0", OUTPUT),))
        self.assertIsNone(job.duration)

    def test_progress_is_passed_to_the_run(self) -> None:
        mocks = self.start()
        callback = mock.Mock()
        self.go(on_progress=callback)
        self.assertIs(mocks["run"].call_args.kwargs["on_progress"], callback)

    def test_a_dry_run_plans_without_running(self) -> None:
        mocks = self.start()
        result, _ = self.go(dry_run=True)
        self.assertEqual(result.status, "planned")
        self.assertEqual(result.detail, "video copied")
        self.assertIn("-ss", result.command)
        self.assertEqual(result.command[0], TOOLS.ffmpeg.path)
        mocks["run"].assert_not_called()

    def test_a_dry_run_does_not_copy_cover_art(self) -> None:
        self.start()
        plan = TrimPlan((), ("-map", "0:a:0"), None, "audio copied", copy_cover=True)
        with mock.patch(READ_TAGS) as read_tags, mock.patch(APPLY_EDIT) as apply_edit:
            result, _ = self.go(plan=mock.Mock(return_value=plan), dry_run=True)
        self.assertEqual(result.status, "planned")
        read_tags.assert_not_called()
        apply_edit.assert_not_called()

    def test_an_unreadable_file_fails_with_the_message(self) -> None:
        mocks = self.start()
        mocks["probe"].side_effect = MediaError("not a readable media file")
        result, planner = self.go()
        self.assertEqual((result.status, result.detail), ("failed", "not a readable media file"))
        self.assertEqual(result.command, ())
        planner.assert_not_called()
        mocks["run"].assert_not_called()

    def test_a_planning_error_fails_with_the_message(self) -> None:
        mocks = self.start()
        planner = mock.Mock(
            side_effect=MediaError("--start 0:30.000 is past the end of the file (0:20.00)")
        )
        result, _ = self.go(plan=planner)
        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.detail, "--start 0:30.000 is past the end of the file (0:20.00)"
        )
        self.assertEqual(result.command, ())
        mocks["run"].assert_not_called()

    def test_an_ffmpeg_failure_fails_with_the_message_and_keeps_the_command(self) -> None:
        mocks = self.start()
        mocks["run"].side_effect = MediaError("Invalid data found when processing input")
        result, _ = self.go()
        self.assertEqual(
            (result.status, result.detail), ("failed", "Invalid data found when processing input")
        )
        self.assertIn("-ss", result.command)

    def test_the_keyframe_step_runs_between_probe_and_plan(self) -> None:
        calls: list[str] = []
        self.start()
        probe = mock.patch(PROBE, side_effect=lambda *a, **k: calls.append("probe") or self.info)
        probe.start()
        self.addCleanup(probe.stop)

        def find(info: MediaInfo) -> float | None:
            calls.append("keyframe")
            self.assertIs(info, self.info)
            return 5.0

        def planner(info: MediaInfo, keyframe: float | None) -> TrimPlan:
            calls.append("plan")
            self.assertIs(info, self.info)
            self.assertEqual(keyframe, 5.0)
            return PLAN

        result, _ = self.go(plan=planner, keyframe=find)
        self.assertEqual(calls, ["probe", "keyframe", "plan"])
        self.assertEqual(result.status, "converted")

    def test_a_keyframe_step_that_finds_nothing_passes_none(self) -> None:
        self.start()
        planner = mock.Mock(return_value=PLAN)
        self.go(plan=planner, keyframe=lambda info: None)
        planner.assert_called_once_with(self.info, None)

    def test_the_job_duration_comes_from_the_plan(self) -> None:
        mocks = self.start()
        plan = dataclasses.replace(PLAN, duration=7.0)
        self.go(plan=mock.Mock(return_value=plan))
        self.assertEqual(mocks["run"].call_args.args[0].duration, 7.0)


class TrimFileCoverTests(unittest.TestCase):
    def setUp(self) -> None:
        from jzr_tools.core.tags import Cover, TagEdit, TagError, TagSet

        self.Cover, self.TagEdit, self.TagError, self.TagSet = Cover, TagEdit, TagError, TagSet
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        folder = Path(self._tmp.name).resolve()
        self.source = folder / "song.ogg"
        self.source.write_bytes(b"source")
        self.target = folder / "song_trim.ogg"
        self.info = audio_info(sound("vorbis", 0), container="ogg", cover=True)
        self.plan = TrimPlan((), ("-map", "0:a:0"), None, "audio copied", copy_cover=True)
        self.cover = Cover("image/jpeg", b"jpeg bytes")
        self.calls: list[str] = []

        probe = mock.patch(PROBE, return_value=self.info)
        self.run = mock.patch(RUN, side_effect=lambda *a, **k: self.calls.append("run")).start()
        probe.start()
        self.addCleanup(mock.patch.stopall)

    def trim(self):  # noqa: ANN201
        return trim_file(
            self.source, self.target, tools=TOOLS, plan=lambda info, keyframe: self.plan
        )

    def test_the_source_cover_is_written_to_the_target_after_ffmpeg(self) -> None:
        def read(path: Path):  # noqa: ANN202
            self.calls.append("read")
            return self.TagSet("ogg", {}, self.cover, None)

        with mock.patch(READ_TAGS, side_effect=read) as read_tags, mock.patch(
            APPLY_EDIT, side_effect=lambda *a, **k: self.calls.append("apply")
        ) as apply_edit:
            result = self.trim()
        read_tags.assert_called_once_with(self.source)
        apply_edit.assert_called_once_with(self.target, self.TagEdit(cover=self.cover))
        self.assertEqual(self.calls, ["run", "read", "apply"])
        self.assertEqual((result.status, result.detail), ("converted", "audio copied"))

    def test_without_copy_cover_the_tags_are_not_touched(self) -> None:
        self.plan = dataclasses.replace(self.plan, copy_cover=False)
        with mock.patch(READ_TAGS) as read_tags, mock.patch(APPLY_EDIT) as apply_edit:
            result = self.trim()
        read_tags.assert_not_called()
        apply_edit.assert_not_called()
        self.assertEqual(result.status, "converted")

    def test_a_failed_ffmpeg_run_does_not_reach_the_cover_step(self) -> None:
        self.run.side_effect = MediaError("boom")
        with mock.patch(READ_TAGS) as read_tags, mock.patch(APPLY_EDIT) as apply_edit:
            result = self.trim()
        self.assertEqual((result.status, result.detail), ("failed", "boom"))
        read_tags.assert_not_called()
        apply_edit.assert_not_called()

    def test_a_tag_error_while_reading_keeps_the_result_and_says_why(self) -> None:
        with mock.patch(READ_TAGS, side_effect=self.TagError("cannot read the cover")), mock.patch(
            APPLY_EDIT
        ) as apply_edit:
            result = self.trim()
        apply_edit.assert_not_called()
        self.assertEqual(result.status, "converted")
        self.assertEqual(result.detail, "audio copied; cover art not kept: cannot read the cover")

    def test_a_tag_error_while_writing_keeps_the_result_and_says_why(self) -> None:
        with mock.patch(
            READ_TAGS, return_value=self.TagSet("ogg", {}, self.cover, None)
        ), mock.patch(APPLY_EDIT, side_effect=self.TagError("file is read-only")):
            result = self.trim()
        self.assertEqual(result.status, "converted")
        self.assertEqual(result.detail, "audio copied; cover art not kept: file is read-only")
        self.assertTrue(result.ok)


# --- ffmpeg.keyframe_before ----------------------------------------------------------

RUN_PROCESS = "jzr_tools.core.ffmpeg.subprocess.run"


def probed(body: str | bytes, code: int = 0) -> subprocess.CompletedProcess:
    data = body.encode("utf-8") if isinstance(body, str) else body
    return subprocess.CompletedProcess(["x"], code, data, b"")


def key_packet(pts: str = "7.000000", flags: str = "K__", start: str | None = "0.000000") -> str:
    fmt = "{}" if start is None else f'{{"start_time": "{start}"}}'
    return f'{{"packets": [{{"pts_time": "{pts}", "flags": "{flags}"}}], "format": {fmt}}}'


class KeyframeBeforeTests(unittest.TestCase):
    TOOL = Tool("ffprobe-bin", "PATH")
    PATH = Path("clips") / "clip.mp4"

    def ask(self, outcome: subprocess.CompletedProcess, seconds: float = 7, stream: int = 0):  # noqa: ANN201
        with mock.patch(RUN_PROCESS, return_value=outcome) as run:
            found = ffmpeg.keyframe_before(self.PATH, self.TOOL, seconds, stream)
        return found, run

    def test_the_command_is_one_ffprobe_call_on_the_absolute_path(self) -> None:
        found, run = self.ask(probed(key_packet()))
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            [
                "ffprobe-bin", "-v", "error",
                "-select_streams", "0",
                "-show_entries", "packet=pts_time,flags:format=start_time",
                "-of", "json",
                "-read_intervals", "+7%+#1",
                "-i", os.path.abspath(self.PATH),
            ],
        )
        self.assertEqual(found, 7.0)

    def test_the_stream_index_and_a_fractional_time_are_passed_on(self) -> None:
        _, run = self.ask(probed(key_packet()), seconds=7.5, stream=2)
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("-select_streams") + 1], "2")
        self.assertEqual(argv[argv.index("-read_intervals") + 1], "+7.5%+#1")

    def test_the_container_start_time_is_taken_off(self) -> None:
        found, _ = self.ask(probed(key_packet(pts="8.400000", start="1.400000")))
        self.assertAlmostEqual(found, 7.0, places=6)

    def test_a_missing_start_time_counts_as_zero(self) -> None:
        found, _ = self.ask(probed(key_packet(pts="5.000000", start=None)))
        self.assertEqual(found, 5.0)

    def test_the_answer_is_never_below_zero(self) -> None:
        found, _ = self.ask(probed(key_packet(pts="0.000000", start="0.040000")), seconds=0.01)
        self.assertEqual(found, 0.0)

    def test_the_flags_only_need_to_start_with_k(self) -> None:
        found, _ = self.ask(probed(key_packet(flags="K_")))
        self.assertEqual(found, 7.0)

    def test_a_packet_that_is_not_a_keyframe_gives_none(self) -> None:
        found, _ = self.ask(probed(key_packet(flags="__")))
        self.assertIsNone(found)

    def test_no_packet_gives_none(self) -> None:
        found, _ = self.ask(probed('{"packets": [], "format": {"start_time": "0.000000"}}'))
        self.assertIsNone(found)
        found, _ = self.ask(probed('{"format": {"start_time": "0.000000"}}'))
        self.assertIsNone(found)

    def test_unreadable_output_gives_none(self) -> None:
        for body in ("not json at all", "", "[]"):
            with self.subTest(body=body):
                found, _ = self.ask(probed(body))
                self.assertIsNone(found)

    def test_a_failing_ffprobe_gives_none_even_with_output(self) -> None:
        found, _ = self.ask(probed(key_packet(), code=1))
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
