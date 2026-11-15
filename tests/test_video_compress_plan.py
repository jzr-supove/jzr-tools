"""Pure tests of video compression planning: sizes, bitrates, names and ffmpeg arguments.

Every input is built by hand, so nothing here needs ffmpeg.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile
import unittest

from jzr_tools.core.compress import (
    COMPRESS_PRESETS,
    RESOLUTION_LADDER,
    CompressOptions,
    CompressPlan,
    Impossible,
    TargetPlan,
    check_compress_options,
    compress_targets,
    compressed_name,
    output_size,
    plan_compress,
    plan_target_size,
    retry_bitrate,
    short_edge_candidates,
)
from jzr_tools.core.ffmpeg import OUTPUT, PASSLOG
from jzr_tools.core.media import AudioStream, MediaError, MediaInfo, VideoStream

MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
FASTSTART = ("-movflags", "+faststart")
H264 = ("-c:v", "libx264")
PIX = ("-pix_fmt", "yuv420p")
EIGHTEEN_MB = 18_000_000


def sound(
    codec: str | None = "aac",
    index: int = 1,
    channels: int | None = 2,
    bitrate: int | None = None,
    language: str | None = None,
) -> AudioStream:
    return AudioStream(
        index=index,
        codec=codec,
        channels=channels,
        channel_layout=None,
        sample_rate=48000,
        bitrate=bitrate,
        language=language,
    )


def picture(
    width: int | None = 1920,
    height: int | None = 1080,
    fps: float | None = 30.0,
    rotation: int = 0,
    transfer: str | None = None,
    codec: str | None = "h264",
) -> VideoStream:
    return VideoStream(
        index=0,
        codec=codec,
        width=width,
        height=height,
        fps=fps,
        pixel_format="yuv420p",
        rotation=rotation,
        transfer=transfer,
        bitrate=None,
    )


def make_info(
    video: VideoStream | None = None,
    audio: tuple[AudioStream, ...] | None = None,
    *,
    duration: float | None = 60.0,
    extra: tuple[str, ...] = (),
) -> MediaInfo:
    return MediaInfo(
        path=Path("source"),
        container=MP4_FAMILY,
        container_name=None,
        duration=duration,
        size=100_000_000,
        bitrate=None,
        video=picture() if video is None else video,
        audio=(sound(),) if audio is None else audio,
        extra=extra,
        tags={},
        cover_art=False,
    )


def value_after(args: tuple[str, ...], flag: str) -> str:
    return args[args.index(flag) + 1]


def plan_size(
    target: int,
    duration: float,
    width: int = 1920,
    height: int = 1080,
    fps: float | None = 30.0,
    *,
    audio: bool = True,
    top: int = 1080,
) -> TargetPlan | Impossible:
    return plan_target_size(target, duration, width, height, fps, audio=audio, top=top)


def reachable(
    target: int,
    duration: float,
    width: int = 1920,
    height: int = 1080,
    fps: float | None = 30.0,
    *,
    audio: bool = True,
) -> TargetPlan:
    result = plan_size(target, duration, width, height, fps, audio=audio)
    assert isinstance(result, TargetPlan), result
    return result


class ConstantTests(unittest.TestCase):
    def test_the_named_sizes_are_decimal_byte_counts(self) -> None:
        self.assertEqual(COMPRESS_PRESETS, {"email": 18_000_000, "discord": 20_000_000})

    def test_the_resolution_ladder_goes_from_high_to_low(self) -> None:
        self.assertEqual(RESOLUTION_LADDER, (1080, 720, 480, 360))

    def test_plan_results_cannot_be_changed(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            TargetPlan(1, 2, 3).short_edge = 4  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            Impossible(1).smallest_bytes = 2  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            CompressOptions().strip = True  # type: ignore[misc]

    def test_options_default_to_nothing_asked(self) -> None:
        options = CompressOptions()
        self.assertIsNone(options.quality)
        self.assertIsNone(options.size)
        self.assertIsNone(options.resize)
        self.assertFalse(options.no_audio)
        self.assertFalse(options.strip)


class OutputSizeTests(unittest.TestCase):
    def test_a_landscape_picture_is_scaled_to_the_short_edge(self) -> None:
        self.assertEqual(output_size(1920, 1080, 720), (1280, 720))

    def test_a_portrait_picture_keeps_its_orientation(self) -> None:
        self.assertEqual(output_size(1080, 1920, 480), (480, 854))

    def test_the_long_side_is_rounded_to_the_nearest_even_number(self) -> None:
        # 1920 * 480 / 1080 = 853.3
        self.assertEqual(output_size(1920, 1080, 480), (854, 480))
        # 1281.78 rounds up to 1282, not down to 1280
        self.assertEqual(output_size(1920, 1080, 721), (1282, 720))

    def test_a_picture_is_never_scaled_up(self) -> None:
        self.assertEqual(output_size(640, 360, 1080), (640, 360))
        self.assertEqual(output_size(1280, 720, 720), (1280, 720))
        self.assertEqual(output_size(1920, 1080, 1080), (1920, 1080))
        self.assertEqual(output_size(1080, 1920, 4000), (1080, 1920))

    def test_an_unscaled_picture_with_odd_sides_is_rounded_down_to_even(self) -> None:
        self.assertEqual(output_size(641, 361, 1080), (640, 360))
        self.assertEqual(output_size(1919, 1080, 1080), (1918, 1080))

    def test_a_side_is_never_less_than_two(self) -> None:
        self.assertEqual(output_size(3, 1, 1080), (2, 2))

    def test_a_square_picture_is_scaled_on_both_sides(self) -> None:
        self.assertEqual(output_size(500, 500, 480), (480, 480))


class ShortEdgeCandidatesTests(unittest.TestCase):
    def test_full_hd_offers_every_rung(self) -> None:
        self.assertEqual(short_edge_candidates(1920, 1080), [1080, 720, 480, 360])

    def test_a_picture_at_the_bottom_rung_offers_only_itself(self) -> None:
        self.assertEqual(short_edge_candidates(640, 360), [360])

    def test_an_off_ladder_size_is_offered_first(self) -> None:
        self.assertEqual(short_edge_candidates(500, 500), [500, 480, 360])

    def test_a_larger_picture_is_capped_at_the_top(self) -> None:
        self.assertEqual(short_edge_candidates(3840, 2160), [1080, 720, 480, 360])

    def test_a_lower_top_cuts_the_list(self) -> None:
        self.assertEqual(short_edge_candidates(1920, 1080, top=240), [240])
        self.assertEqual(short_edge_candidates(1920, 1080, top=720), [720, 480, 360])

    def test_the_short_edge_decides_for_a_portrait_picture(self) -> None:
        self.assertEqual(short_edge_candidates(1080, 1920), [1080, 720, 480, 360])
        self.assertEqual(short_edge_candidates(720, 1280), [720, 480, 360])


class PlanTargetSizeTests(unittest.TestCase):
    def assertBudgetAddsUp(self, result: TargetPlan, target: int, duration: float) -> None:
        total = target * 8 * 0.96 / duration
        self.assertIsInstance(result.video_bitrate, int)
        self.assertLessEqual(abs(result.video_bitrate + result.audio_bitrate - total), 2)

    def test_a_tight_budget_uses_the_lower_audio_bitrate(self) -> None:
        result = reachable(3_000_000, 20)
        self.assertEqual(result.audio_bitrate, 96_000)
        self.assertLessEqual(abs(result.video_bitrate - 1_056_000), 2)
        self.assertEqual(result.short_edge, 480)
        self.assertBudgetAddsUp(result, 3_000_000, 20)

    def test_a_roomy_budget_keeps_the_full_audio_bitrate(self) -> None:
        result = reachable(18_000_000, 60)
        self.assertEqual(result.audio_bitrate, 128_000)
        self.assertEqual(result.short_edge, 720)
        self.assertBudgetAddsUp(result, 18_000_000, 60)

    def test_without_audio_the_whole_budget_goes_to_the_video(self) -> None:
        with_audio = reachable(18_000_000, 60)
        without = reachable(18_000_000, 60, audio=False)
        self.assertEqual(without.audio_bitrate, 0)
        self.assertGreater(without.video_bitrate, with_audio.video_bitrate)
        self.assertBudgetAddsUp(without, 18_000_000, 60)

    def test_a_short_video_keeps_full_resolution(self) -> None:
        self.assertEqual(reachable(18_000_000, 30).short_edge, 1080)

    def test_the_highest_picture_size_with_enough_bits_is_chosen(self) -> None:
        # 700 kb/s: 854x480 needs 615 kb/s, 1280x720 needs 1382 kb/s
        self.assertEqual(reachable(5_468_750, 60, audio=False).short_edge, 480)
        self.assertEqual(reachable(5_468_750, 60, 1080, 1920, audio=False).short_edge, 480)

    def test_the_lowest_rung_is_reachable_with_just_enough_bits(self) -> None:
        # 400 kb/s against the 346 kb/s 640x360 needs
        self.assertEqual(reachable(3_125_000, 60, audio=False).short_edge, 360)

    def test_too_few_bits_for_the_lowest_rung_is_impossible(self) -> None:
        self.assertIsInstance(plan_size(2_343_750, 60, audio=False), Impossible)

    def test_a_lower_frame_rate_allows_a_larger_picture(self) -> None:
        self.assertEqual(reachable(6_250_000, 60, fps=15.0, audio=False).short_edge, 720)
        self.assertEqual(reachable(6_250_000, 60, fps=30.0, audio=False).short_edge, 480)

    def test_an_unknown_or_zero_frame_rate_counts_as_thirty(self) -> None:
        expected = plan_size(6_250_000, 60, fps=30.0, audio=False)
        for fps in (None, 0, 0.0, -1.0):
            with self.subTest(fps=fps):
                self.assertEqual(plan_size(6_250_000, 60, fps=fps, audio=False), expected)

    def test_a_huge_budget_keeps_the_picture_size(self) -> None:
        cases = {
            (640, 360): 360,
            (1280, 720): 720,
            (854, 480): 480,
            (720, 1280): 720,
            (3840, 2160): 1080,
            (500, 500): 500,
            (320, 240): 240,
        }
        for (width, height), edge in cases.items():
            with self.subTest(width=width, height=height):
                self.assertEqual(reachable(500_000_000, 60, width, height).short_edge, edge)

    def test_the_top_edge_caps_the_choice(self) -> None:
        result = plan_size(500_000_000, 60, top=240)
        self.assertIsInstance(result, TargetPlan)
        self.assertEqual(result.short_edge, 240)  # type: ignore[union-attr]

    def test_audio_can_make_the_difference_between_possible_and_impossible(self) -> None:
        self.assertIsInstance(plan_size(2_750_000, 60), Impossible)
        self.assertIsInstance(plan_size(2_750_000, 60, audio=False), TargetPlan)

    def test_a_long_video_in_a_small_file_is_impossible_and_names_a_bigger_size(self) -> None:
        result = plan_size(10_000_000, 3600)
        self.assertIsInstance(result, Impossible)
        assert isinstance(result, Impossible)
        self.assertGreater(result.smallest_bytes, 10_000_000)
        self.assertGreaterEqual(result.smallest_bytes, 96_000 * 3600 // 8)

    def test_a_tiny_target_is_impossible(self) -> None:
        self.assertIsInstance(plan_size(100_000, 600, 1280, 720), Impossible)

    def test_the_smallest_size_is_the_lowest_rung_plus_audio_with_the_margin(self) -> None:
        with_audio = plan_size(2_000_000, 60)
        without = plan_size(2_000_000, 60, audio=False)
        assert isinstance(with_audio, Impossible) and isinstance(without, Impossible)
        # (640*360*30*0.05 + 96000) * 60 / 8 / 0.96
        self.assertLessEqual(abs(with_audio.smallest_bytes - 3_450_000), 2)
        # 640*360*30*0.05 * 60 / 8 / 0.96
        self.assertLessEqual(abs(without.smallest_bytes - 2_700_000), 2)

    def test_the_smallest_size_really_is_enough(self) -> None:
        impossible = plan_size(2_000_000, 60)
        assert isinstance(impossible, Impossible)
        self.assertIsInstance(plan_size(impossible.smallest_bytes, 60), TargetPlan)

    def test_the_smallest_size_follows_the_picture_size(self) -> None:
        small = plan_size(100_000, 60, 640, 360, audio=False)
        large = plan_size(100_000, 60, 1920, 1080, audio=False)
        assert isinstance(small, Impossible) and isinstance(large, Impossible)
        # Both can drop to the 360 rung, so the floor is the same.
        self.assertEqual(small.smallest_bytes, large.smallest_bytes)


class RetryBitrateTests(unittest.TestCase):
    def test_the_bitrate_is_scaled_by_how_far_off_the_size_was_with_a_margin(self) -> None:
        # half the size was allowed: 1,000,000 * 0.5 * 0.97
        self.assertAlmostEqual(retry_bitrate(1_000_000, 10_000_000, 20_000_000), 485_000, delta=1)

    def test_the_result_is_a_whole_number(self) -> None:
        result = retry_bitrate(2_000_000, 10_000_000, 12_000_000)
        self.assertIsInstance(result, int)
        # 2,000,000 * 10/12 * 0.97 = 1,616,666.67
        self.assertAlmostEqual(result, 1_616_666, delta=1)

    def test_the_retry_is_always_lower_than_the_first_try(self) -> None:
        for actual in (10_000_001, 10_500_000, 15_000_000):
            with self.subTest(actual=actual):
                self.assertLess(retry_bitrate(1_000_000, 10_000_000, actual), 1_000_000)


class CheckCompressOptionsTests(unittest.TestCase):
    def test_a_quality_with_a_size_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            check_compress_options(CompressOptions(quality="high", size=25_000_000))
        self.assertEqual(str(caught.exception), "--quality applies only without --size")

    def test_every_other_combination_is_accepted(self) -> None:
        for options in (
            CompressOptions(),
            CompressOptions(quality="small"),
            CompressOptions(size=25_000_000),
            CompressOptions(size=25_000_000, resize=720, no_audio=True, strip=True),
            CompressOptions(quality="high", resize=480, no_audio=True, strip=True),
        ):
            with self.subTest(options=options):
                self.assertIsNone(check_compress_options(options))


class CompressedNameTests(unittest.TestCase):
    def test_the_name_is_the_stem_with_a_suffix_and_an_mp4_extension(self) -> None:
        self.assertEqual(compressed_name(Path("clip.mov")), "clip_compressed.mp4")
        self.assertEqual(compressed_name(Path("clip.mp4")), "clip_compressed.mp4")

    def test_only_the_last_extension_is_replaced(self) -> None:
        self.assertEqual(compressed_name(Path("holiday.2024.mkv")), "holiday.2024_compressed.mp4")

    def test_the_folder_is_not_part_of_the_name(self) -> None:
        self.assertEqual(compressed_name(Path("a") / "b" / "clip.avi"), "clip_compressed.mp4")

    def test_any_characters_in_the_stem_are_kept(self) -> None:
        self.assertEqual(compressed_name(Path("видео 100%.mov")), "видео 100%_compressed.mp4")
        self.assertEqual(compressed_name(Path("-leading dash.mov")), "-leading dash_compressed.mp4")


class CompressTargetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def test_each_output_is_beside_its_source(self) -> None:
        sources = [self.root / "one.mov", self.root / "sub" / "two.mkv"]
        self.assertEqual(
            compress_targets(sources),
            [self.root / "one_compressed.mp4", self.root / "sub" / "two_compressed.mp4"],
        )

    def test_an_outdir_without_a_root_gathers_everything_in_it(self) -> None:
        out = self.root / "out"
        sources = [self.root / "one.mov", self.root / "sub" / "two.mkv"]
        self.assertEqual(
            compress_targets(sources, outdir=out),
            [out / "one_compressed.mp4", out / "two_compressed.mp4"],
        )

    def test_an_outdir_with_a_root_mirrors_the_sub_directories(self) -> None:
        out = self.root / "out"
        sources = [self.root / "one.mov", self.root / "a" / "b" / "two.mkv"]
        self.assertEqual(
            compress_targets(sources, outdir=out, root=self.root),
            [out / "one_compressed.mp4", out / "a" / "b" / "two_compressed.mp4"],
        )

    def test_two_sources_with_the_same_stem_never_share_an_output(self) -> None:
        sources = [self.root / "clip.mov", self.root / "clip.mp4", self.root / "clip.mkv"]
        self.assertEqual(
            compress_targets(sources),
            [
                self.root / "clip_compressed.mp4",
                self.root / "clip_compressed-2.mp4",
                self.root / "clip_compressed-3.mp4",
            ],
        )

    def test_names_that_differ_only_by_case_clash(self) -> None:
        sources = [self.root / "Clip.mov", self.root / "clip.mp4"]
        first, second = compress_targets(sources)
        self.assertEqual(first, self.root / "Clip_compressed.mp4")
        self.assertEqual(second, self.root / "clip_compressed-2.mp4")

    def test_the_same_stem_in_different_folders_is_not_a_clash(self) -> None:
        sources = [self.root / "a" / "clip.mov", self.root / "b" / "clip.mov"]
        self.assertEqual(
            compress_targets(sources),
            [self.root / "a" / "clip_compressed.mp4", self.root / "b" / "clip_compressed.mp4"],
        )

    def test_the_same_stem_in_different_folders_clashes_when_gathered_in_one_outdir(self) -> None:
        out = self.root / "out"
        sources = [self.root / "a" / "clip.mov", self.root / "b" / "clip.mov"]
        self.assertEqual(
            compress_targets(sources, outdir=out),
            [out / "clip_compressed.mp4", out / "clip_compressed-2.mp4"],
        )

    def test_there_is_one_output_per_source_in_order(self) -> None:
        self.assertEqual(compress_targets([]), [])


class QualityPlanTests(unittest.TestCase):
    def plan(self, info: MediaInfo | None = None, **options: object) -> CompressPlan:
        return plan_compress(info or make_info(), CompressOptions(**options))  # type: ignore[arg-type]

    def test_a_full_hd_aac_source_is_encoded_in_one_pass_at_balanced_quality(self) -> None:
        result = self.plan()
        self.assertEqual(
            result.passes,
            (
                (
                    "-map", "0:0", "-map", "0:1",
                    *H264, "-crf", "23", *PIX,
                    "-c:a", "aac", "-b:a", "128k",
                    *FASTSTART, OUTPUT,
                ),
            ),
        )
        self.assertIsNone(result.video_bitrate)
        self.assertIn("video encoded to h264", result.note)
        self.assertIn("audio encoded to aac", result.note)

    def test_each_quality_level_has_its_crf(self) -> None:
        for quality, crf in (("high", "18"), ("balanced", "23"), ("small", "28")):
            with self.subTest(quality=quality):
                result = self.plan(quality=quality)
                self.assertEqual(value_after(result.passes[0], "-crf"), crf)

    def test_aac_audio_at_the_target_bitrate_or_below_is_copied(self) -> None:
        for bitrate in (96_000, 128_000):
            with self.subTest(bitrate=bitrate):
                info = make_info(audio=(sound("aac", bitrate=bitrate),))
                result = self.plan(info)
                self.assertEqual(
                    result.passes[0],
                    (
                        "-map", "0:0", "-map", "0:1",
                        *H264, "-crf", "23", *PIX,
                        "-c:a", "copy",
                        *FASTSTART, OUTPUT,
                    ),
                )
                self.assertIn("audio copied", result.note)

    def test_aac_audio_above_the_target_bitrate_or_of_unknown_bitrate_is_encoded(self) -> None:
        for bitrate in (192_000, None):
            with self.subTest(bitrate=bitrate):
                info = make_info(audio=(sound("aac", bitrate=bitrate),))
                args = self.plan(info).passes[0]
                self.assertEqual(args[args.index("-c:a") :][:4], ("-c:a", "aac", "-b:a", "128k"))

    def test_other_audio_codecs_are_encoded_to_aac(self) -> None:
        info = make_info(audio=(sound("ac3", bitrate=96_000),))
        args = self.plan(info).passes[0]
        self.assertEqual(args[args.index("-c:a") :][:4], ("-c:a", "aac", "-b:a", "128k"))

    def test_a_five_one_source_is_mixed_down_to_stereo(self) -> None:
        info = make_info(audio=(sound("aac", channels=6, bitrate=96_000),))
        self.assertEqual(
            self.plan(info).passes[0],
            (
                "-map", "0:0", "-map", "0:1",
                *H264, "-crf", "23", *PIX,
                "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                *FASTSTART, OUTPUT,
            ),
        )

    def test_a_stereo_or_mono_source_gets_no_downmix(self) -> None:
        for channels in (1, 2):
            with self.subTest(channels=channels):
                info = make_info(audio=(sound("ac3", channels=channels),))
                self.assertNotIn("-ac", self.plan(info).passes[0])

    def test_the_audio_stream_with_most_channels_is_used(self) -> None:
        audio = (sound("aac", 1, 2), sound("aac", 2, 6), sound("aac", 3, 1))
        result = self.plan(make_info(audio=audio))
        self.assertEqual(result.passes[0][:4], ("-map", "0:0", "-map", "0:2"))
        self.assertEqual(result.passes[0].count("-map"), 2)

    def test_the_first_audio_stream_wins_a_tie(self) -> None:
        audio = (sound("aac", 1, 2), sound("aac", 2, 2))
        self.assertEqual(self.plan(make_info(audio=audio)).passes[0][:4], ("-map", "0:0", "-map", "0:1"))

    def test_unused_audio_streams_are_named_in_the_note(self) -> None:
        audio = (sound("aac", 1, 2, language="eng"), sound("ac3", 2, 6))
        self.assertIn("dropped: audio stream 1 (aac, eng)", self.plan(make_info(audio=audio)).note)

    def test_other_streams_are_named_in_the_note_and_never_mapped(self) -> None:
        info = make_info(extra=("subtitle stream 3 (subrip)",))
        result = self.plan(info)
        self.assertIn("dropped: subtitle stream 3 (subrip)", result.note)
        self.assertEqual(result.passes[0].count("-map"), 2)

    def test_no_audio_leaves_out_the_audio_stream_and_its_arguments(self) -> None:
        result = self.plan(no_audio=True)
        self.assertEqual(
            result.passes[0],
            ("-map", "0:0", *H264, "-crf", "23", *PIX, *FASTSTART, OUTPUT),
        )
        self.assertIn("audio dropped", result.note)

    def test_no_audio_on_a_silent_source_does_not_report_dropped_audio(self) -> None:
        result = self.plan(make_info(audio=()), no_audio=True)
        self.assertNotIn("audio dropped", result.note)
        self.assertNotIn("-c:a", result.passes[0])

    def test_a_silent_source_is_encoded_without_audio_arguments(self) -> None:
        result = self.plan(make_info(audio=()))
        self.assertEqual(
            result.passes[0],
            ("-map", "0:0", *H264, "-crf", "23", *PIX, *FASTSTART, OUTPUT),
        )

    def test_metadata_is_kept_by_default(self) -> None:
        self.assertNotIn("-map_metadata", self.plan().passes[0])

    def test_strip_removes_metadata(self) -> None:
        result = self.plan(strip=True, no_audio=True)
        self.assertEqual(
            result.passes[0],
            (
                "-map", "0:0", *H264, "-crf", "23", *PIX,
                "-map_metadata", "-1", *FASTSTART, OUTPUT,
            ),
        )

    def test_a_picture_within_full_hd_is_not_scaled(self) -> None:
        for width, height in ((1920, 1080), (1280, 720), (640, 360), (1080, 1920)):
            with self.subTest(width=width, height=height):
                info = make_info(picture(width, height))
                self.assertNotIn("-vf", self.plan(info).passes[0])

    def test_a_larger_picture_is_capped_at_full_hd_on_its_short_edge(self) -> None:
        info = make_info(picture(3840, 2160))
        self.assertEqual(
            self.plan(info).passes[0][:5], ("-map", "0:0", "-map", "0:1", "-vf")
        )
        self.assertEqual(value_after(self.plan(info).passes[0], "-vf"), "scale=1920:1080")
        portrait = make_info(picture(2160, 3840))
        self.assertEqual(value_after(self.plan(portrait).passes[0], "-vf"), "scale=1080:1920")

    def test_resize_sets_the_short_edge(self) -> None:
        result = self.plan(resize=720)
        self.assertEqual(
            result.passes[0],
            (
                "-map", "0:0", "-map", "0:1",
                "-vf", "scale=1280:720",
                *H264, "-crf", "23", *PIX,
                "-c:a", "aac", "-b:a", "128k",
                *FASTSTART, OUTPUT,
            ),
        )

    def test_a_resize_larger_than_the_picture_never_scales_up(self) -> None:
        self.assertNotIn("-vf", self.plan(resize=2160).passes[0])
        self.assertNotIn("-vf", self.plan(make_info(picture(1280, 720)), resize=1080).passes[0])

    def test_a_resize_above_full_hd_lifts_the_default_cap(self) -> None:
        info = make_info(picture(3840, 2160))
        self.assertEqual(value_after(self.plan(info, resize=1440).passes[0], "-vf"), "scale=2560:1440")

    def test_an_odd_picture_gets_an_even_scale(self) -> None:
        info = make_info(picture(1919, 1079))
        self.assertEqual(value_after(self.plan(info).passes[0], "-vf"), "scale=1918:1078")

    def test_a_picture_turned_by_a_quarter_is_sized_as_displayed(self) -> None:
        for rotation in (90, 270):
            with self.subTest(rotation=rotation):
                info = make_info(picture(1920, 1080, rotation=rotation))
                self.assertNotIn("-vf", self.plan(info).passes[0])
                result = self.plan(info, resize=720)
                self.assertEqual(value_after(result.passes[0], "-vf"), "scale=720:1280")

    def test_a_picture_turned_upside_down_stays_landscape(self) -> None:
        info = make_info(picture(1920, 1080, rotation=180))
        self.assertEqual(value_after(self.plan(info, resize=720).passes[0], "-vf"), "scale=1280:720")

    def test_a_rotated_four_k_source_is_capped_on_its_displayed_short_edge(self) -> None:
        info = make_info(picture(3840, 2160, rotation=90))
        self.assertEqual(value_after(self.plan(info).passes[0], "-vf"), "scale=1080:1920")

    def test_a_missing_duration_does_not_matter_without_a_size(self) -> None:
        result = self.plan(make_info(duration=None))
        self.assertEqual(len(result.passes), 1)

    def test_hdr_is_flagged_in_the_note(self) -> None:
        info = make_info(picture(transfer="smpte2084", codec="hevc"))
        self.assertIn("HDR source", self.plan(info).note)

    def test_sdr_has_no_hdr_note(self) -> None:
        self.assertNotIn("HDR", self.plan().note)

    def test_a_source_without_a_video_track_is_refused(self) -> None:
        info = replace(make_info(), video=None)
        with self.assertRaises(MediaError) as caught:
            self.plan(info)
        self.assertEqual(str(caught.exception), "no video track")

    def test_streams_are_mapped_by_their_own_indexes(self) -> None:
        video = VideoStream(3, "h264", 1920, 1080, 30.0, "yuv420p", 0, None, None)
        info = make_info(video, (sound("aac", index=7),))
        self.assertEqual(self.plan(info).passes[0][:4], ("-map", "0:3", "-map", "0:7"))


class SizePlanTests(unittest.TestCase):
    def plan(
        self,
        info: MediaInfo | None = None,
        *,
        size: int = EIGHTEEN_MB,
        video_bitrate: int | None = None,
        **options: object,
    ) -> CompressPlan:
        return plan_compress(
            info or make_info(),
            CompressOptions(size=size, **options),  # type: ignore[arg-type]
            video_bitrate=video_bitrate,
        )

    def test_a_given_bitrate_makes_two_passes_with_exact_arguments(self) -> None:
        info = make_info(audio=(sound("aac", bitrate=96_000),))
        result = self.plan(info, video_bitrate=1_500_000)
        self.assertEqual(result.video_bitrate, 1_500_000)
        self.assertEqual(
            result.passes,
            (
                (
                    "-map", "0:0",
                    "-vf", "scale=1280:720",
                    *H264, "-b:v", "1500000", "-pass", "1", "-passlogfile", PASSLOG,
                    *PIX, "-an", "-f", "null", "-",
                ),
                (
                    "-map", "0:0", "-map", "0:1",
                    "-vf", "scale=1280:720",
                    *H264, "-b:v", "1500000", "-pass", "2", "-passlogfile", PASSLOG,
                    *PIX, "-c:a", "copy",
                    *FASTSTART, OUTPUT,
                ),
            ),
        )
        self.assertIn("video encoded to h264", result.note)
        self.assertIn("audio copied", result.note)

    def test_the_planned_bitrate_comes_from_the_size_and_duration(self) -> None:
        result = self.plan()
        self.assertIsInstance(result.video_bitrate, int)
        # 18 MB over 60 s with 4% margin is 2,304,000 b/s, less 128 kb/s of audio
        self.assertLessEqual(abs(result.video_bitrate - 2_176_000), 2)
        first, second = result.passes
        self.assertEqual(value_after(first, "-b:v"), str(result.video_bitrate))
        self.assertEqual(value_after(second, "-b:v"), str(result.video_bitrate))
        self.assertEqual(value_after(first, "-vf"), "scale=1280:720")
        self.assertEqual(value_after(second, "-vf"), "scale=1280:720")

    def test_the_first_pass_writes_nothing_and_the_last_writes_the_output(self) -> None:
        first, second = self.plan().passes
        self.assertEqual(first[-3:], ("-f", "null", "-"))
        self.assertNotIn(OUTPUT, first)
        self.assertNotIn("-map_metadata", first)
        self.assertEqual(second[-1], OUTPUT)
        self.assertEqual(value_after(first, "-pass"), "1")
        self.assertEqual(value_after(second, "-pass"), "2")
        self.assertEqual(value_after(first, "-passlogfile"), PASSLOG)
        self.assertEqual(value_after(second, "-passlogfile"), PASSLOG)

    def test_a_tight_budget_encodes_the_audio_at_the_lower_bitrate(self) -> None:
        info = make_info(audio=(sound("aac", bitrate=128_000),), duration=20.0)
        result = self.plan(info, size=3_000_000)
        second = result.passes[1]
        self.assertEqual(second[second.index("-c:a") :][:4], ("-c:a", "aac", "-b:a", "96k"))
        self.assertEqual(value_after(second, "-vf"), "scale=854:480")
        self.assertLessEqual(abs(result.video_bitrate - 1_056_000), 2)

    def test_audio_already_at_or_below_the_planned_bitrate_is_copied(self) -> None:
        for bitrate in (64_000, 96_000):
            with self.subTest(bitrate=bitrate):
                info = make_info(audio=(sound("aac", bitrate=bitrate),), duration=20.0)
                second = self.plan(info, size=3_000_000).passes[1]
                self.assertEqual(value_after(second, "-c:a"), "copy")

    def test_a_roomy_budget_copies_aac_at_128k(self) -> None:
        info = make_info(audio=(sound("aac", bitrate=128_000),))
        self.assertEqual(value_after(self.plan(info).passes[1], "-c:a"), "copy")

    def test_a_five_one_source_is_mixed_down_to_stereo(self) -> None:
        info = make_info(audio=(sound("aac", channels=6, bitrate=96_000),))
        second = self.plan(info).passes[1]
        self.assertEqual(
            second[second.index("-c:a") :][:6], ("-c:a", "aac", "-b:a", "128k", "-ac", "2")
        )

    def test_no_audio_gives_the_video_the_whole_budget(self) -> None:
        with_audio = self.plan()
        without = self.plan(no_audio=True)
        self.assertGreater(without.video_bitrate, with_audio.video_bitrate)
        second = without.passes[1]
        self.assertEqual(second.count("-map"), 1)
        self.assertNotIn("-c:a", second)
        self.assertIn("audio dropped", without.note)

    def test_a_silent_source_gets_the_whole_budget_too(self) -> None:
        silent = self.plan(make_info(audio=()))
        self.assertEqual(silent.video_bitrate, self.plan(no_audio=True).video_bitrate)

    def test_strip_removes_metadata_from_the_output_pass(self) -> None:
        first, second = self.plan(strip=True).passes
        self.assertEqual(second[second.index("-map_metadata") :][:2], ("-map_metadata", "-1"))
        self.assertLess(second.index("-map_metadata"), second.index("-movflags"))
        self.assertNotIn("-map_metadata", first)

    def test_metadata_is_kept_by_default(self) -> None:
        self.assertNotIn("-map_metadata", self.plan().passes[1])

    def test_resize_caps_the_picture_size_that_can_be_chosen(self) -> None:
        # 18 MB over 30 s has the bits for full HD
        info = make_info(duration=30.0)
        self.assertNotIn("-vf", self.plan(info).passes[1])
        self.assertEqual(value_after(self.plan(info, resize=720).passes[1], "-vf"), "scale=1280:720")

    def test_a_source_at_the_chosen_size_is_not_scaled(self) -> None:
        info = make_info(picture(1280, 720), duration=60.0)
        self.assertNotIn("-vf", self.plan(info).passes[0])

    def test_a_rotated_source_is_sized_as_displayed(self) -> None:
        info = make_info(picture(1920, 1080, rotation=90), (), duration=60.0)
        result = self.plan(info, size=5_468_750)
        self.assertEqual(value_after(result.passes[0], "-vf"), "scale=480:854")

    def test_the_frame_rate_of_the_source_counts(self) -> None:
        slow = make_info(picture(fps=15.0), (), duration=60.0)
        fast = make_info(picture(fps=30.0), (), duration=60.0)
        self.assertEqual(value_after(self.plan(slow, size=6_250_000).passes[0], "-vf"), "scale=1280:720")
        self.assertEqual(value_after(self.plan(fast, size=6_250_000).passes[0], "-vf"), "scale=854:480")

    def test_the_most_channels_stream_is_used_and_others_are_named(self) -> None:
        audio = (sound("aac", 1, 2, language="eng"), sound("aac", 2, 6))
        result = self.plan(make_info(audio=audio))
        self.assertEqual(result.passes[1][:4], ("-map", "0:0", "-map", "0:2"))
        self.assertIn("dropped: audio stream 1 (aac, eng)", result.note)

    def test_a_missing_duration_is_refused(self) -> None:
        for duration in (None, 0.0):
            with self.subTest(duration=duration):
                with self.assertRaises(MediaError) as caught:
                    self.plan(make_info(duration=duration))
                self.assertIn("duration", str(caught.exception))

    def test_a_source_without_a_video_track_is_refused(self) -> None:
        with self.assertRaises(MediaError) as caught:
            self.plan(replace(make_info(), video=None))
        self.assertEqual(str(caught.exception), "no video track")

    def test_an_impossible_size_names_the_smallest_that_works_and_offers_no_audio(self) -> None:
        with self.assertRaises(MediaError) as caught:
            self.plan(make_info(duration=724.0), size=1_000_000)
        self.assertEqual(
            str(caught.exception),
            "too small for a 12:04 video; the smallest is about 42 MB (or use --no-audio)",
        )

    def test_without_audio_the_message_does_not_offer_to_drop_it(self) -> None:
        for options in ({"no_audio": True}, {}):
            with self.subTest(options=options):
                info = make_info(audio=() if not options else None, duration=724.0)
                with self.assertRaises(MediaError) as caught:
                    self.plan(info, size=1_000_000, **options)
                self.assertEqual(
                    str(caught.exception),
                    "too small for a 12:04 video; the smallest is about 33 MB",
                )

    def test_a_smallest_size_under_one_megabyte_is_given_in_kilobytes(self) -> None:
        info = make_info(picture(640, 360), (), duration=10.0)
        with self.assertRaises(MediaError) as caught:
            self.plan(info, size=100_000)
        message = str(caught.exception)
        self.assertRegex(message, r"^too small for a 0:10 video; the smallest is about 45[01] kB$")

    def test_a_long_video_uses_hours_in_the_message(self) -> None:
        with self.assertRaises(MediaError) as caught:
            self.plan(make_info(duration=3725.0), size=1_000_000)
        self.assertTrue(str(caught.exception).startswith("too small for a 1:02:05 video;"))
        self.assertRegex(str(caught.exception), r"about \d+ MB \(or use --no-audio\)$")


if __name__ == "__main__":
    unittest.main()
