"""Pure tests of the video conversion planner and its option checks.

Every input is built by hand, so nothing here needs ffmpeg.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from jzr_tools.core.media import (
    VIDEO_CODECS,
    VIDEO_EXTENSIONS,
    VIDEO_QUALITIES,
    AudioOptions,
    AudioPlan,
    AudioStream,
    MediaError,
    MediaInfo,
    Plan,
    VideoOptions,
    VideoStream,
    check_convert_options,
    plan_audio_convert,
    plan_convert,
)

MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
MATROSKA = "matroska,webm"
BRAND_RESET = (
    "-metadata", "major_brand=",
    "-metadata", "minor_version=",
    "-metadata", "compatible_brands=",
)
FASTSTART = ("-movflags", "+faststart")
MAPS = ("-map", "0:0", "-map", "0:1")
VIDEO_MAP = ("-map", "0:0")
H264_ARGS = ("-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p")
VP9_ARGS = (
    "-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-pix_fmt", "yuv420p",
    "-row-mt", "1", "-cpu-used", "4",
)
HEVC_ARGS = (
    "-c:v", "libx265", "-crf", "26", "-pix_fmt", "yuv420p", "-x265-params", "log-level=error",
)
AV1_ARGS = ("-c:v", "libsvtav1", "-crf", "32", "-pix_fmt", "yuv420p")
AAC_ARGS = ("-c:a", "aac", "-b:a", "192k")
OPUS_ARGS = ("-c:a", "libopus", "-b:a", "128k")
COPY_BOTH = ("-c:v", "copy", "-c:a", "copy")


def sound(
    codec: str | None = "aac",
    index: int = 1,
    channels: int | None = 2,
    language: str | None = None,
) -> AudioStream:
    return AudioStream(
        index=index,
        codec=codec,
        channels=channels,
        channel_layout=None,
        sample_rate=48000,
        bitrate=None,
        language=language,
    )


def picture(
    codec: str | None = "h264",
    width: int | None = 1920,
    height: int | None = 1080,
    fps: float | None = 30.0,
    pixel_format: str | None = "yuv420p",
    rotation: int = 0,
    transfer: str | None = None,
) -> VideoStream:
    return VideoStream(
        index=0,
        codec=codec,
        width=width,
        height=height,
        fps=fps,
        pixel_format=pixel_format,
        rotation=rotation,
        transfer=transfer,
        bitrate=None,
    )


def make_info(
    video: VideoStream | None = None,
    audio: tuple[AudioStream, ...] | None = None,
    *,
    container: str | None = MP4_FAMILY,
    extra: tuple[str, ...] = (),
    cover: bool = False,
) -> MediaInfo:
    return MediaInfo(
        path=Path("source"),
        container=container,
        container_name=None,
        duration=1.0,
        size=1000,
        bitrate=None,
        video=picture() if video is None else video,
        audio=(sound(),) if audio is None else audio,
        extra=extra,
        tags={},
        cover_art=cover,
    )


def plan(info: MediaInfo, target: str, **options: object) -> Plan:
    return plan_convert(info, target, VideoOptions(**options))  # type: ignore[arg-type]


def value_after(args: tuple[str, ...], flag: str) -> str:
    return args[args.index(flag) + 1]


class ConstantTests(unittest.TestCase):
    def test_video_extensions_are_exactly_the_readable_video_formats(self) -> None:
        self.assertEqual(
            VIDEO_EXTENSIONS,
            frozenset(
                {
                    "mp4", "m4v", "mov", "mkv", "webm", "avi", "wmv", "flv",
                    "mpg", "mpeg", "ts", "mts", "m2ts", "3gp", "ogv",
                }
            ),
        )

    def test_video_codecs_and_qualities_are_listed_in_order(self) -> None:
        self.assertEqual(VIDEO_CODECS, ("h264", "hevc", "av1", "vp9"))
        self.assertEqual(VIDEO_QUALITIES, ("high", "balanced", "small"))

    def test_the_audio_plan_is_the_same_type_as_the_plan(self) -> None:
        self.assertIs(AudioPlan, Plan)

    def test_an_audio_conversion_returns_a_plan(self) -> None:
        info = make_info(audio=(sound("flac"),), container="flac")
        result = plan_audio_convert(info, "mp3", AudioOptions())
        self.assertIsInstance(result, Plan)

    def test_a_video_plan_is_a_plan_and_cannot_be_changed(self) -> None:
        result = plan(make_info(), "mp4")
        self.assertIsInstance(result, Plan)
        with self.assertRaises(AttributeError):
            result.note = "other"  # type: ignore[misc]


class CopyTests(unittest.TestCase):
    def test_mov_to_mp4_copies_both_streams_and_moves_the_index_first(self) -> None:
        result = plan(make_info(), "mp4")
        self.assertEqual(result.args, (*MAPS, *COPY_BOTH, *FASTSTART))
        self.assertEqual(result.note, "video copied, audio copied")

    def test_copy_into_mov_also_moves_the_index_first(self) -> None:
        result = plan(make_info(), "mov")
        self.assertEqual(result.args, (*MAPS, *COPY_BOTH, *FASTSTART))
        self.assertEqual(result.note, "video copied, audio copied")

    def test_copy_into_mkv_clears_the_mp4_brand_tags_and_has_no_faststart(self) -> None:
        result = plan(make_info(), "mkv")
        self.assertEqual(result.args, (*MAPS, *COPY_BOTH, *BRAND_RESET))
        self.assertEqual(result.note, "video copied, audio copied")

    def test_vp9_and_opus_are_copied_into_webm_without_brand_tags_from_matroska(self) -> None:
        info = make_info(picture("vp9"), (sound("opus"),), container=MATROSKA)
        result = plan(info, "webm")
        self.assertEqual(result.args, (*MAPS, *COPY_BOTH))
        self.assertEqual(result.note, "video copied, audio copied")

    def test_vp9_and_opus_are_copied_into_mp4(self) -> None:
        info = make_info(picture("vp9"), (sound("opus"),), container=MATROSKA)
        result = plan(info, "mp4")
        self.assertEqual(result.args, (*MAPS, *COPY_BOTH, *FASTSTART))

    def test_mp4_sources_clear_their_brand_tags_for_webm_and_mkv_only(self) -> None:
        for source in ("mp4", "mov", MP4_FAMILY):
            for target, cleared in (("webm", True), ("mkv", True), ("mp4", False), ("mov", False)):
                with self.subTest(source=source, target=target):
                    info = make_info(picture("vp9"), (sound("opus"),), container=source)
                    self.assertEqual("-metadata" in plan(info, target).args, cleared)

    def test_non_mp4_sources_never_clear_brand_tags(self) -> None:
        for source in (MATROSKA, "avi", None):
            with self.subTest(source=source):
                info = make_info(picture("vp9"), (sound("opus"),), container=source)
                self.assertNotIn("-metadata", plan(info, "mkv").args)

    def test_the_streams_are_mapped_by_their_own_indexes(self) -> None:
        video = VideoStream(3, "h264", 64, 48, 30.0, "yuv420p", 0, None, None)
        info = make_info(video, (sound("aac", index=7),))
        self.assertEqual(plan(info, "mp4").args[:4], ("-map", "0:3", "-map", "0:7"))


class CodecChoiceTests(unittest.TestCase):
    def test_h264_and_aac_into_webm_become_vp9_and_opus(self) -> None:
        result = plan(make_info(), "webm")
        self.assertEqual(result.args, (*MAPS, *VP9_ARGS, *OPUS_ARGS, *BRAND_RESET))
        self.assertEqual(result.note, "video encoded to vp9, audio encoded to opus")

    def test_hevc_into_mp4_is_encoded_with_x265_and_tagged_hvc1(self) -> None:
        result = plan(make_info(), "mp4", codec="hevc")
        self.assertEqual(
            result.args,
            (*MAPS, *HEVC_ARGS, "-tag:v", "hvc1", "-c:a", "copy", *FASTSTART),
        )
        self.assertEqual(result.note, "video encoded to hevc, audio copied")

    def test_hevc_into_mov_is_tagged_hvc1(self) -> None:
        result = plan(make_info(), "mov", codec="hevc")
        self.assertEqual(
            result.args,
            (*MAPS, *HEVC_ARGS, "-tag:v", "hvc1", "-c:a", "copy", *FASTSTART),
        )

    def test_hevc_into_mkv_has_no_tag(self) -> None:
        result = plan(make_info(), "mkv", codec="hevc")
        self.assertEqual(result.args, (*MAPS, *HEVC_ARGS, "-c:a", "copy", *BRAND_RESET))
        self.assertNotIn("-tag:v", result.args)

    def test_a_copied_hevc_video_is_tagged_hvc1_in_mp4_and_mov(self) -> None:
        for target in ("mp4", "mov"):
            with self.subTest(target=target):
                result = plan(make_info(picture("hevc")), target)
                self.assertEqual(
                    result.args,
                    (*MAPS, "-c:v", "copy", "-tag:v", "hvc1", "-c:a", "copy", *FASTSTART),
                )
                self.assertEqual(result.note, "video copied, audio copied")

    def test_a_copied_hevc_video_has_no_tag_in_mkv(self) -> None:
        result = plan(make_info(picture("hevc")), "mkv")
        self.assertEqual(result.args, (*MAPS, *COPY_BOTH, *BRAND_RESET))

    def test_asking_for_the_source_codec_still_encodes(self) -> None:
        result = plan(make_info(), "mp4", codec="h264")
        self.assertEqual(result.args, (*MAPS, *H264_ARGS, "-c:a", "copy", *FASTSTART))
        self.assertEqual(result.note, "video encoded to h264, audio copied")

    def test_av1_is_encoded_with_svt(self) -> None:
        result = plan(make_info(), "mkv", codec="av1")
        self.assertEqual(result.args, (*MAPS, *AV1_ARGS, "-c:a", "copy", *BRAND_RESET))
        self.assertEqual(result.note, "video encoded to av1, audio copied")

    def test_vp9_is_encoded_with_libvpx_in_row_threaded_mode(self) -> None:
        result = plan(make_info(), "mp4", codec="vp9")
        self.assertEqual(result.args, (*MAPS, *VP9_ARGS, "-c:a", "copy", *FASTSTART))
        self.assertEqual(result.note, "video encoded to vp9, audio copied")

    def test_a_codec_the_container_cannot_hold_is_encoded_to_h264(self) -> None:
        for codec in ("av1", "vp9", "vp8"):
            with self.subTest(codec=codec):
                result = plan(make_info(picture(codec)), "mov")
                self.assertEqual(result.args, (*MAPS, *H264_ARGS, "-c:a", "copy", *FASTSTART))
                self.assertEqual(result.note, "video encoded to h264, audio copied")

    def test_codecs_a_container_holds_are_copied(self) -> None:
        cases = {
            "mp4": ("h264", "hevc", "av1", "vp9", "mpeg4"),
            "mov": ("h264", "hevc", "mpeg4", "prores", "mjpeg"),
            "mkv": (
                "h264", "hevc", "av1", "vp8", "vp9", "mpeg4", "mpeg2video", "prores",
                "mjpeg", "theora",
            ),
            "webm": ("vp8", "vp9", "av1"),
        }
        for target, codecs in cases.items():
            for codec in codecs:
                with self.subTest(target=target, codec=codec):
                    result = plan(make_info(picture(codec), (sound("opus"),)), target)
                    self.assertEqual(value_after(result.args, "-c:v"), "copy")
                    self.assertTrue(result.note.startswith("video copied"))

    def test_codecs_a_container_cannot_hold_are_encoded(self) -> None:
        cases = {
            "mp4": ("vp8", "prores", "mjpeg", "mpeg2video", "theora"),
            "mov": ("av1", "vp9", "vp8", "mpeg2video", "theora"),
            "webm": ("h264", "hevc", "mpeg4", "prores"),
        }
        for target, codecs in cases.items():
            for codec in codecs:
                with self.subTest(target=target, codec=codec):
                    result = plan(make_info(picture(codec), (sound("opus"),)), target)
                    self.assertNotEqual(value_after(result.args, "-c:v"), "copy")
                    self.assertTrue(result.note.startswith("video encoded to"))


class QualityTests(unittest.TestCase):
    def test_each_codec_has_its_crf_for_every_quality_level(self) -> None:
        table = {
            "h264": {"high": "18", "balanced": "23", "small": "28"},
            "hevc": {"high": "22", "balanced": "26", "small": "30"},
            "av1": {"high": "24", "balanced": "32", "small": "40"},
            "vp9": {"high": "24", "balanced": "32", "small": "40"},
        }
        for codec, levels in table.items():
            for quality, crf in levels.items():
                with self.subTest(codec=codec, quality=quality):
                    result = plan(make_info(), "mkv", codec=codec, quality=quality)
                    self.assertEqual(value_after(result.args, "-crf"), crf)

    def test_no_quality_means_balanced(self) -> None:
        for codec, crf in (("h264", "23"), ("hevc", "26"), ("av1", "32"), ("vp9", "32")):
            with self.subTest(codec=codec):
                result = plan(make_info(), "mkv", codec=codec)
                self.assertEqual(value_after(result.args, "-crf"), crf)

    def test_a_given_crf_replaces_the_quality_level(self) -> None:
        for codec in VIDEO_CODECS:
            with self.subTest(codec=codec):
                result = plan(make_info(), "mkv", codec=codec, crf=17)
                self.assertEqual(value_after(result.args, "-crf"), "17")

    def test_a_crf_of_zero_is_used_as_given(self) -> None:
        result = plan(make_info(), "mp4", codec="h264", crf=0)
        self.assertEqual(value_after(result.args, "-crf"), "0")

    def test_the_default_codec_for_a_forced_encode_uses_its_own_levels(self) -> None:
        result = plan(make_info(), "webm", quality="small")
        self.assertEqual(value_after(result.args, "-crf"), "40")

    def test_a_crf_with_everything_copied_is_explained_not_used(self) -> None:
        result = plan(make_info(), "mp4", crf=20)
        self.assertEqual(result.args, (*MAPS, *COPY_BOTH, *FASTSTART))
        self.assertEqual(
            result.note, "video copied, audio copied; --crf applies only when re-encoding"
        )

    def test_a_quality_with_everything_copied_is_explained_not_used(self) -> None:
        result = plan(make_info(), "mp4", quality="high")
        self.assertNotIn("-crf", result.args)
        self.assertEqual(
            result.note, "video copied, audio copied; --quality applies only when re-encoding"
        )

    def test_a_crf_with_an_encode_has_no_such_note(self) -> None:
        result = plan(make_info(), "mp4", codec="hevc", crf=20)
        self.assertEqual(result.note, "video encoded to hevc, audio copied")


class PixelFormatTests(unittest.TestCase):
    def test_h264_is_always_8_bit_420(self) -> None:
        for pixel_format in ("yuv420p10le", "yuv422p12le", "p010le", "yuv420p", None):
            with self.subTest(pixel_format=pixel_format):
                result = plan(make_info(picture(pixel_format=pixel_format)), "mp4", codec="h264")
                self.assertEqual(value_after(result.args, "-pix_fmt"), "yuv420p")

    def test_ten_and_twelve_bit_sources_keep_10_bit_for_the_other_codecs(self) -> None:
        for codec in ("hevc", "vp9", "av1"):
            for pixel_format in ("yuv420p10le", "p010le", "yuv422p12le"):
                with self.subTest(codec=codec, pixel_format=pixel_format):
                    info = make_info(picture(pixel_format=pixel_format))
                    result = plan(info, "mkv", codec=codec)
                    self.assertEqual(value_after(result.args, "-pix_fmt"), "yuv420p10le")

    def test_eight_bit_and_unknown_sources_stay_8_bit_for_the_other_codecs(self) -> None:
        for codec in ("hevc", "vp9", "av1"):
            for pixel_format in ("yuv420p", "yuvj420p", "yuv444p", None):
                with self.subTest(codec=codec, pixel_format=pixel_format):
                    info = make_info(picture(pixel_format=pixel_format))
                    result = plan(info, "mkv", codec=codec)
                    self.assertEqual(value_after(result.args, "-pix_fmt"), "yuv420p")


class ResizeTests(unittest.TestCase):
    def test_a_landscape_picture_is_scaled_to_the_short_edge(self) -> None:
        result = plan(make_info(), "mp4", resize=720)
        self.assertEqual(
            result.args,
            (*MAPS, "-vf", "scale=1280:720", *H264_ARGS, "-c:a", "copy", *FASTSTART),
        )
        self.assertEqual(result.note, "video encoded to h264, audio copied")

    def test_a_picture_turned_by_a_quarter_stays_portrait(self) -> None:
        for rotation in (90, 270):
            with self.subTest(rotation=rotation):
                result = plan(make_info(picture(rotation=rotation)), "mp4", resize=720)
                self.assertEqual(value_after(result.args, "-vf"), "scale=720:1280")

    def test_a_picture_turned_upside_down_stays_landscape(self) -> None:
        result = plan(make_info(picture(rotation=180)), "mp4", resize=720)
        self.assertEqual(value_after(result.args, "-vf"), "scale=1280:720")

    def test_a_native_portrait_picture_is_scaled_by_its_width(self) -> None:
        result = plan(make_info(picture(width=1080, height=1920)), "mp4", resize=720)
        self.assertEqual(value_after(result.args, "-vf"), "scale=720:1280")

    def test_the_long_side_is_rounded_to_the_nearest_even_number(self) -> None:
        result = plan(make_info(picture(width=64, height=48)), "mp4", resize=32)
        self.assertEqual(value_after(result.args, "-vf"), "scale=42:32")

    def test_an_odd_target_edge_is_rounded_down_to_even(self) -> None:
        result = plan(make_info(), "mp4", resize=721)
        self.assertEqual(value_after(result.args, "-vf"), "scale=1282:720")

    def test_a_picture_smaller_than_the_target_is_copied_and_says_so(self) -> None:
        result = plan(make_info(picture(width=1280, height=720)), "mp4", resize=1080)
        self.assertEqual(result.args, (*MAPS, *COPY_BOTH, *FASTSTART))
        self.assertEqual(
            result.note,
            "video copied, audio copied; already 1080 px or smaller; not resized",
        )

    def test_a_picture_exactly_the_target_size_is_not_resized(self) -> None:
        result = plan(make_info(picture(width=1280, height=720)), "mp4", resize=720)
        self.assertNotIn("-vf", result.args)
        self.assertEqual(value_after(result.args, "-c:v"), "copy")
        self.assertTrue(result.note.endswith("already 720 px or smaller; not resized"))

    def test_the_short_edge_of_a_rotated_picture_decides_the_no_op(self) -> None:
        info = make_info(picture(width=1280, height=720, rotation=90))
        result = plan(info, "mp4", resize=720)
        self.assertNotIn("-vf", result.args)
        self.assertEqual(value_after(result.args, "-c:v"), "copy")

    def test_a_no_op_resize_does_not_block_an_encode_that_was_asked_for(self) -> None:
        info = make_info(picture(width=1280, height=720))
        result = plan(info, "mp4", codec="hevc", resize=1080)
        self.assertNotIn("-vf", result.args)
        self.assertEqual(
            result.note,
            "video encoded to hevc, audio copied; already 1080 px or smaller; not resized",
        )

    def test_an_odd_picture_gets_an_even_scale_when_encoded(self) -> None:
        result = plan(make_info(picture(width=1919, height=1079)), "mp4", codec="h264")
        self.assertEqual(
            result.args,
            (*MAPS, "-vf", "scale=1918:1078", *H264_ARGS, "-c:a", "copy", *FASTSTART),
        )

    def test_only_the_odd_side_is_changed_by_the_even_scale(self) -> None:
        result = plan(make_info(picture(width=1920, height=1079)), "mp4", codec="h264")
        self.assertEqual(value_after(result.args, "-vf"), "scale=1920:1078")

    def test_the_even_scale_follows_the_displayed_orientation(self) -> None:
        info = make_info(picture(width=1919, height=1080, rotation=90))
        result = plan(info, "mp4", codec="h264")
        self.assertEqual(value_after(result.args, "-vf"), "scale=1080:1918")

    def test_an_odd_picture_that_is_copied_gets_no_filter(self) -> None:
        result = plan(make_info(picture(width=1919, height=1079)), "mp4")
        self.assertNotIn("-vf", result.args)

    def test_an_even_picture_that_is_encoded_gets_no_filter(self) -> None:
        result = plan(make_info(), "mp4", codec="h264")
        self.assertNotIn("-vf", result.args)

    def test_unknown_size_gives_no_filter(self) -> None:
        for width, height in ((None, None), (1920, None), (None, 1080)):
            with self.subTest(width=width, height=height):
                info = make_info(picture(width=width, height=height))
                self.assertNotIn("-vf", plan(info, "mp4", codec="hevc", resize=720).args)
                self.assertNotIn("-vf", plan(info, "mp4", codec="h264").args)


class FrameRateTests(unittest.TestCase):
    def test_a_lower_rate_forces_an_encode_with_an_fps_filter(self) -> None:
        result = plan(make_info(picture(fps=60.0)), "mp4", fps=30.0)
        self.assertEqual(
            result.args,
            (*MAPS, "-vf", "fps=30", *H264_ARGS, "-c:a", "copy", *FASTSTART),
        )
        self.assertEqual(result.note, "video encoded to h264, audio copied")

    def test_a_fractional_rate_is_written_with_its_decimals(self) -> None:
        result = plan(make_info(picture(fps=60.0)), "mp4", fps=29.97)
        self.assertEqual(value_after(result.args, "-vf"), "fps=29.97")

    def test_the_fps_filter_comes_before_the_scale_filter(self) -> None:
        result = plan(make_info(), "mp4", fps=15.0, resize=720)
        self.assertEqual(value_after(result.args, "-vf"), "fps=15,scale=1280:720")
        self.assertEqual(result.args.count("-vf"), 1)

    def test_a_higher_rate_is_copied_and_says_so(self) -> None:
        result = plan(make_info(picture(fps=30.0)), "mp4", fps=60.0)
        self.assertEqual(result.args, (*MAPS, *COPY_BOTH, *FASTSTART))
        self.assertEqual(
            result.note,
            "video copied, audio copied; already 60 fps or less; frame rate kept",
        )

    def test_the_same_rate_is_copied_and_says_so(self) -> None:
        result = plan(make_info(picture(fps=30.0)), "mp4", fps=30.0)
        self.assertNotIn("-vf", result.args)
        self.assertEqual(value_after(result.args, "-c:v"), "copy")
        self.assertTrue(result.note.endswith("already 30 fps or less; frame rate kept"))

    def test_a_fractional_rate_in_the_no_op_note_is_written_with_decimals(self) -> None:
        result = plan(make_info(picture(fps=24.0)), "mp4", fps=29.97)
        self.assertTrue(result.note.endswith("already 29.97 fps or less; frame rate kept"))

    def test_an_unknown_source_rate_does_not_force_an_encode(self) -> None:
        result = plan(make_info(picture(fps=None)), "mp4", fps=15.0)
        self.assertEqual(result.args, (*MAPS, *COPY_BOTH, *FASTSTART))

    def test_both_no_op_notes_come_resize_first(self) -> None:
        info = make_info(picture(width=1280, height=720, fps=30.0))
        result = plan(info, "mp4", resize=1080, fps=60.0)
        self.assertEqual(
            result.note,
            "video copied, audio copied; already 1080 px or smaller; not resized; "
            "already 60 fps or less; frame rate kept",
        )


class AudioChoiceTests(unittest.TestCase):
    def test_pcm_audio_in_mov_is_copied(self) -> None:
        info = make_info(audio=(sound("pcm_s16le"),))
        self.assertEqual(plan(info, "mov").args, (*MAPS, *COPY_BOTH, *FASTSTART))

    def test_pcm_audio_that_mp4_cannot_hold_is_encoded_to_aac(self) -> None:
        info = make_info(audio=(sound("pcm_s16le"),), container="mov")
        result = plan(info, "mp4")
        self.assertEqual(result.args, (*MAPS, "-c:v", "copy", *AAC_ARGS, *FASTSTART))
        self.assertEqual(result.note, "video copied, audio encoded to aac")

    def test_audio_webm_cannot_hold_is_encoded_to_opus(self) -> None:
        info = make_info(picture("vp9"), (sound("aac"),), container=MATROSKA)
        result = plan(info, "webm")
        self.assertEqual(result.args, (*MAPS, "-c:v", "copy", *OPUS_ARGS))
        self.assertEqual(result.note, "video copied, audio encoded to opus")

    def test_audio_mkv_cannot_hold_is_encoded_to_aac(self) -> None:
        info = make_info(audio=(sound("wmav2"),), container="asf")
        result = plan(info, "mkv")
        self.assertEqual(result.args, (*MAPS, "-c:v", "copy", *AAC_ARGS))

    def test_a_source_without_audio_maps_and_copies_the_video_only(self) -> None:
        result = plan(make_info(audio=()), "mp4")
        self.assertEqual(result.args, (*VIDEO_MAP, "-c:v", "copy", *FASTSTART))
        self.assertEqual(result.note, "video copied")

    def test_a_source_without_audio_encoded_says_nothing_about_audio(self) -> None:
        result = plan(make_info(audio=()), "webm")
        self.assertEqual(result.args, (*VIDEO_MAP, *VP9_ARGS, *BRAND_RESET))
        self.assertEqual(result.note, "video encoded to vp9")

    def test_no_audio_drops_the_audio_and_says_so(self) -> None:
        result = plan(make_info(), "mp4", no_audio=True)
        self.assertEqual(result.args, (*VIDEO_MAP, "-c:v", "copy", *FASTSTART))
        self.assertEqual(result.note, "video copied, audio dropped")

    def test_no_audio_on_a_silent_source_says_nothing_about_audio(self) -> None:
        result = plan(make_info(audio=()), "mp4", no_audio=True)
        self.assertEqual(result.note, "video copied")

    def test_the_stream_with_most_channels_is_used(self) -> None:
        audio = (sound("aac", 1, 2), sound("aac", 2, 6), sound("aac", 3, 1))
        result = plan(make_info(audio=audio), "mp4")
        self.assertEqual(result.args[:4], ("-map", "0:0", "-map", "0:2"))

    def test_the_first_stream_wins_a_tie(self) -> None:
        audio = (sound("aac", 1, 2), sound("aac", 2, 2))
        result = plan(make_info(audio=audio), "mp4")
        self.assertEqual(result.args[:4], ("-map", "0:0", "-map", "0:1"))

    def test_unknown_channels_count_as_none(self) -> None:
        audio = (sound("aac", 1, None), sound("aac", 2, 1))
        result = plan(make_info(audio=audio), "mp4")
        self.assertEqual(result.args[:4], ("-map", "0:0", "-map", "0:2"))

    def test_unknown_channels_on_every_stream_choose_the_first(self) -> None:
        audio = (sound("aac", 1, None), sound("aac", 2, None))
        result = plan(make_info(audio=audio), "mp4")
        self.assertEqual(result.args[:4], ("-map", "0:0", "-map", "0:1"))

    def test_the_codec_of_the_chosen_stream_decides_copy_or_encode(self) -> None:
        audio = (sound("aac", 1, 2), sound("pcm_s16le", 2, 6))
        result = plan(make_info(audio=audio, container="mov"), "mp4")
        self.assertEqual(
            result.args,
            (*VIDEO_MAP, "-map", "0:2", "-c:v", "copy", *AAC_ARGS, *FASTSTART),
        )


class DroppedStreamTests(unittest.TestCase):
    def test_unused_audio_streams_are_named_with_codec_and_language(self) -> None:
        audio = (sound("aac", 1, 2, "eng"), sound("ac3", 2, 6, "ger"))
        result = plan(make_info(audio=audio), "mp4")
        self.assertEqual(
            result.note, "video copied, audio copied; dropped: audio stream 1 (aac, eng)"
        )

    def test_a_label_lists_only_what_is_known(self) -> None:
        audio = (
            sound("ac3", 1, 6),
            sound("aac", 2, 2, "eng"),
            sound(None, 3, 2, "fra"),
            sound(None, 4, 2, None),
        )
        result = plan(make_info(audio=audio), "mp4")
        self.assertEqual(
            result.note,
            "video copied, audio copied; dropped: audio stream 2 (aac, eng), "
            "audio stream 3 (fra), audio stream 4",
        )

    def test_extra_labels_follow_the_audio_and_cover_art_comes_last(self) -> None:
        audio = (sound("aac", 1, 2, "eng"), sound("ac3", 2, 6))
        info = make_info(
            audio=audio,
            extra=("subtitle stream 3 (mov_text, eng)", "subtitle stream 4"),
            cover=True,
        )
        self.assertEqual(
            plan(info, "mkv").note,
            "video copied, audio copied; dropped: audio stream 1 (aac, eng), "
            "subtitle stream 3 (mov_text, eng), subtitle stream 4, cover art",
        )

    def test_data_streams_are_not_named(self) -> None:
        info = make_info(extra=("data stream 2 (bin_data)", "subtitle stream 3 (subrip)"))
        result = plan(info, "mkv")
        self.assertNotIn("data stream", result.note)
        self.assertEqual(
            result.note, "video copied, audio copied; dropped: subtitle stream 3 (subrip)"
        )

    def test_only_data_streams_leave_nothing_to_report(self) -> None:
        result = plan(make_info(extra=("data stream 2 (bin_data)",)), "mp4")
        self.assertEqual(result.note, "video copied, audio copied")

    def test_cover_art_alone_is_named(self) -> None:
        result = plan(make_info(cover=True), "mp4")
        self.assertEqual(result.note, "video copied, audio copied; dropped: cover art")

    def test_no_audio_does_not_list_the_audio_streams(self) -> None:
        audio = (sound("aac", 1, 2, "eng"), sound("ac3", 2, 6, "ger"))
        info = make_info(audio=audio, extra=("subtitle stream 3 (subrip)",))
        result = plan(info, "mp4", no_audio=True)
        self.assertEqual(
            result.note, "video copied, audio dropped; dropped: subtitle stream 3 (subrip)"
        )

    def test_the_dropped_part_comes_before_the_option_remarks(self) -> None:
        info = make_info(extra=("subtitle stream 3 (subrip)",))
        result = plan(info, "mp4", crf=20)
        self.assertEqual(
            result.note,
            "video copied, audio copied; dropped: subtitle stream 3 (subrip); "
            "--crf applies only when re-encoding",
        )

    def test_dropped_streams_are_never_mapped(self) -> None:
        audio = (sound("aac", 1, 2), sound("ac3", 2, 6))
        info = make_info(audio=audio, extra=("subtitle stream 3 (subrip)",), cover=True)
        self.assertEqual(plan(info, "mkv").args.count("-map"), 2)


class HdrTests(unittest.TestCase):
    HDR = "smpte2084"

    def test_hdr_encoded_to_h264_carries_the_washed_out_warning(self) -> None:
        for transfer in (self.HDR, "arib-std-b67"):
            with self.subTest(transfer=transfer):
                info = make_info(picture("hevc", pixel_format="yuv420p10le", transfer=transfer))
                result = plan(info, "mp4", codec="h264")
                self.assertEqual(
                    result.note,
                    "video encoded to h264, audio copied; "
                    "HDR source: 8-bit H.264 will look washed out",
                )

    def test_the_warning_comes_after_the_other_remarks(self) -> None:
        info = make_info(picture("hevc", transfer=self.HDR), cover=True)
        result = plan(info, "mp4", codec="h264", crf=20, resize=4000)
        self.assertEqual(
            result.note,
            "video encoded to h264, audio copied; dropped: cover art; "
            "already 4000 px or smaller; not resized; "
            "HDR source: 8-bit H.264 will look washed out",
        )

    def test_hdr_that_is_copied_has_no_warning(self) -> None:
        result = plan(make_info(picture("hevc", transfer=self.HDR)), "mp4")
        self.assertEqual(result.note, "video copied, audio copied")

    def test_hdr_encoded_to_hevc_av1_or_vp9_has_no_warning(self) -> None:
        for codec in ("hevc", "av1", "vp9"):
            with self.subTest(codec=codec):
                info = make_info(picture("hevc", transfer=self.HDR))
                self.assertNotIn("HDR", plan(info, "mkv", codec=codec).note)

    def test_sdr_encoded_to_h264_has_no_warning(self) -> None:
        result = plan(make_info(picture("hevc", transfer="bt709")), "mp4", codec="h264")
        self.assertNotIn("HDR", result.note)


class RefusalTests(unittest.TestCase):
    def test_a_source_without_a_video_track_is_refused(self) -> None:
        info = replace(make_info(), video=None)
        with self.assertRaises(MediaError) as caught:
            plan(info, "mp4")
        self.assertEqual(str(caught.exception), "no video track")

    def test_targets_that_are_not_video_containers_are_refused(self) -> None:
        for target in ("mp3", "wav", "gif", "avi", "txt"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    plan(make_info(), target)


class CheckOptionsTests(unittest.TestCase):
    def check(self, target: str, bitrate: str | None = None, **options: object) -> None:
        check_convert_options(target, VideoOptions(**options), bitrate=bitrate)  # type: ignore[arg-type]

    def refused(self, target: str, bitrate: str | None = None, **options: object) -> str:
        with self.assertRaises(ValueError) as caught:
            self.check(target, bitrate, **options)
        return str(caught.exception)

    def test_each_video_option_is_refused_for_an_audio_target_by_name(self) -> None:
        cases = {
            "--codec": {"codec": "h264"},
            "--quality": {"quality": "high"},
            "--crf": {"crf": 20},
            "--resize": {"resize": 720},
            "--fps": {"fps": 30.0},
            "--no-audio": {"no_audio": True},
        }
        for name, options in cases.items():
            for target in ("mp3", "m4a", "opus", "ogg", "flac", "wav"):
                with self.subTest(option=name, target=target):
                    self.assertEqual(
                        self.refused(target, **options),
                        f"{name} applies only to video targets, not .{target}",
                    )

    def test_the_first_video_option_in_the_usual_order_is_named(self) -> None:
        self.assertEqual(
            self.refused("mp3", codec="h264", fps=30.0),
            "--codec applies only to video targets, not .mp3",
        )
        self.assertEqual(
            self.refused("mp3", quality="high", resize=720, no_audio=True),
            "--quality applies only to video targets, not .mp3",
        )
        self.assertEqual(
            self.refused("flac", fps=30.0, no_audio=True),
            "--fps applies only to video targets, not .flac",
        )

    def test_a_video_option_on_an_audio_target_is_named_even_with_a_bitrate(self) -> None:
        self.assertEqual(
            self.refused("mp3", "128k", crf=20),
            "--crf applies only to video targets, not .mp3",
        )

    def test_an_audio_target_with_only_a_bitrate_is_fine(self) -> None:
        for target in ("mp3", "m4a", "opus", "ogg", "flac", "wav"):
            with self.subTest(target=target):
                self.assertIsNone(self.check(target, "128k"))
                self.assertIsNone(self.check(target))

    def test_a_bitrate_is_refused_for_every_video_target(self) -> None:
        for target in ("mp4", "mov", "mkv", "webm"):
            with self.subTest(target=target):
                self.assertEqual(
                    self.refused(target, "128k"),
                    f"--bitrate applies only to audio targets, not .{target}",
                )

    def test_a_bitrate_is_named_before_a_crf_and_quality_clash(self) -> None:
        self.assertEqual(
            self.refused("mp4", "128k", crf=20, quality="high"),
            "--bitrate applies only to audio targets, not .mp4",
        )

    def test_crf_and_quality_together_are_refused(self) -> None:
        self.assertEqual(
            self.refused("mp4", crf=20, quality="high"),
            "--crf and --quality cannot be used together",
        )

    def test_the_crf_and_quality_clash_is_named_before_an_unholdable_codec(self) -> None:
        self.assertEqual(
            self.refused("webm", codec="h264", crf=20, quality="high"),
            "--crf and --quality cannot be used together",
        )

    def test_a_codec_the_container_cannot_hold_lists_the_ones_it_can(self) -> None:
        cases = {
            ("webm", "h264"): ".webm cannot hold h264; choose from vp9, av1",
            ("webm", "hevc"): ".webm cannot hold hevc; choose from vp9, av1",
            ("mov", "av1"): ".mov cannot hold av1; choose from h264, hevc",
            ("mov", "vp9"): ".mov cannot hold vp9; choose from h264, hevc",
        }
        for (target, codec), message in cases.items():
            with self.subTest(target=target, codec=codec):
                self.assertEqual(self.refused(target, codec=codec), message)

    def test_every_codec_a_container_holds_is_accepted(self) -> None:
        allowed = {
            "mp4": ("h264", "hevc", "av1", "vp9"),
            "mov": ("h264", "hevc"),
            "mkv": ("h264", "hevc", "av1", "vp9"),
            "webm": ("vp9", "av1"),
        }
        for target, codecs in allowed.items():
            for codec in codecs:
                with self.subTest(target=target, codec=codec):
                    self.assertIsNone(self.check(target, codec=codec))

    def test_the_codec_is_named_before_the_crf_range(self) -> None:
        self.assertEqual(
            self.refused("webm", codec="h264", crf=60),
            ".webm cannot hold h264; choose from vp9, av1",
        )

    def test_the_crf_range_follows_the_container_default_codec(self) -> None:
        self.assertEqual(self.refused("mp4", crf=52), "--crf for h264 goes from 0 to 51")
        self.assertEqual(self.refused("mov", crf=52), "--crf for h264 goes from 0 to 51")
        self.assertEqual(self.refused("mkv", crf=63), "--crf for h264 goes from 0 to 51")
        self.assertEqual(self.refused("webm", crf=64), "--crf for vp9 goes from 0 to 63")

    def test_the_crf_range_follows_the_chosen_codec(self) -> None:
        self.assertEqual(
            self.refused("mp4", codec="hevc", crf=52), "--crf for hevc goes from 0 to 51"
        )
        self.assertEqual(
            self.refused("mkv", codec="av1", crf=64), "--crf for av1 goes from 0 to 63"
        )
        self.assertEqual(
            self.refused("mp4", codec="vp9", crf=64), "--crf for vp9 goes from 0 to 63"
        )
        self.assertEqual(
            self.refused("webm", codec="av1", crf=64), "--crf for av1 goes from 0 to 63"
        )

    def test_a_crf_at_the_top_of_the_range_is_accepted(self) -> None:
        self.assertIsNone(self.check("mp4", crf=51))
        self.assertIsNone(self.check("mkv", codec="hevc", crf=51))
        self.assertIsNone(self.check("webm", crf=63))
        self.assertIsNone(self.check("mp4", codec="av1", crf=63))
        self.assertIsNone(self.check("mkv", codec="vp9", crf=63))

    def test_a_crf_of_zero_is_accepted(self) -> None:
        self.assertIsNone(self.check("mp4", crf=0))

    def test_valid_combinations_return_nothing(self) -> None:
        self.assertIsNone(self.check("mp4"))
        self.assertIsNone(self.check("mkv", codec="av1", quality="small", resize=480))
        self.assertIsNone(self.check("webm", codec="vp9", crf=30, fps=24.0, no_audio=True))
        self.assertIsNone(self.check("mov", codec="hevc", quality="high"))

    def test_a_target_that_is_neither_audio_nor_video_is_refused(self) -> None:
        for target in ("gif", "avi", "txt"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    self.check(target)


if __name__ == "__main__":
    unittest.main()
