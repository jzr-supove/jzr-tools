"""Pure tests of the audio conversion planner and its helpers.

Every input is built by hand, so nothing here needs ffmpeg.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from jzr_tools.core.media import (
    AUDIO_EXTENSIONS,
    AudioOptions,
    AudioStream,
    MediaError,
    MediaInfo,
    VideoStream,
    bitrate_caveat,
    parse_bitrate,
    parse_probe,
    plan_audio_convert,
)

MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
BRAND_RESET = (
    "-metadata", "major_brand=",
    "-metadata", "minor_version=",
    "-metadata", "compatible_brands=",
)
COVER = ("-map", "0:v", "-c:v", "copy", "-disposition:v", "attached_pic")
FIRST_AUDIO = ("-map", "0:a:0")
TAGS = ("-map_metadata", "0")


def stream(codec: str = "pcm_s16le", bits: int | None = 16, index: int = 0) -> AudioStream:
    return AudioStream(
        index=index,
        codec=codec,
        channels=2,
        channel_layout="stereo",
        sample_rate=44100,
        bitrate=None,
        language=None,
        bits=bits,
    )


def picture() -> VideoStream:
    return VideoStream(
        index=0, codec="h264", width=64, height=48, fps=10.0,
        pixel_format="yuv420p", rotation=0, transfer=None, bitrate=None,
    )


def make_info(
    codec: str = "pcm_s16le",
    *,
    bits: int | None = 16,
    container: str | None = "wav",
    cover: bool = False,
    video: VideoStream | None = None,
    audio: tuple[AudioStream, ...] | None = None,
) -> MediaInfo:
    return MediaInfo(
        path=Path("source"),
        container=container,
        container_name=None,
        duration=1.0,
        size=1000,
        bitrate=None,
        video=video,
        audio=(stream(codec, bits),) if audio is None else audio,
        extra=(),
        tags={},
        cover_art=cover,
    )


def plan(info: MediaInfo, target: str, bitrate: str | None = None):  # noqa: ANN201
    return plan_audio_convert(info, target, AudioOptions(bitrate=bitrate))


class ExtensionTests(unittest.TestCase):
    def test_audio_extensions_are_exactly_the_readable_audio_formats(self) -> None:
        self.assertEqual(
            AUDIO_EXTENSIONS,
            frozenset(
                {"mp3", "m4a", "aac", "opus", "ogg", "oga", "flac", "wav", "aif", "aiff", "wma", "mka"}
            ),
        )
        self.assertNotIn("mp4", AUDIO_EXTENSIONS)


class ParseBitrateTests(unittest.TestCase):
    def test_accepted_spellings_become_a_rate_in_kilobits(self) -> None:
        cases = {
            "128k": "128k",
            "128K": "128k",
            "128": "128k",
            "128kbps": "128k",
            " 96k ": "96k",
            "8": "8k",
            "512k": "512k",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_bitrate(text), expected)

    def test_rates_outside_8_to_512_are_refused_naming_the_input(self) -> None:
        for text in ("7", "513k", "0", "10000"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as caught:
                    parse_bitrate(text)
                self.assertIn(text, str(caught.exception))

    def test_other_text_is_refused_naming_the_input(self) -> None:
        for text in ("fast", "", "12.5k", "-128k", "128m", "k128"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as caught:
                    parse_bitrate(text)
                self.assertIn(text, str(caught.exception))


class BitrateCaveatTests(unittest.TestCase):
    def test_lossless_targets_say_the_bitrate_is_ignored(self) -> None:
        self.assertEqual(bitrate_caveat("flac"), ".flac is lossless; --bitrate is ignored")
        self.assertEqual(bitrate_caveat("wav"), ".wav is lossless; --bitrate is ignored")

    def test_lossy_targets_have_no_caveat(self) -> None:
        for target in ("mp3", "m4a", "opus", "ogg"):
            with self.subTest(target=target):
                self.assertIsNone(bitrate_caveat(target))


class ProbeBitsTests(unittest.TestCase):
    def bits_of(self, **fields: object) -> int | None:
        data = {
            "streams": [{"index": 0, "codec_type": "audio", "codec_name": "pcm_s24le", **fields}],
            "format": {"format_name": "wav"},
        }
        return parse_probe(data, Path("x.wav")).audio[0].bits

    def test_bits_per_sample_is_read(self) -> None:
        self.assertEqual(self.bits_of(bits_per_sample=24), 24)

    def test_raw_sample_depth_wins_over_the_container_depth(self) -> None:
        self.assertEqual(self.bits_of(bits_per_raw_sample="24", bits_per_sample=32), 24)

    def test_a_missing_raw_depth_falls_back_to_the_sample_depth(self) -> None:
        self.assertEqual(self.bits_of(bits_per_raw_sample="N/A", bits_per_sample=16), 16)

    def test_zero_not_available_and_missing_mean_unknown(self) -> None:
        self.assertIsNone(self.bits_of(bits_per_sample=0))
        self.assertIsNone(self.bits_of(bits_per_raw_sample="N/A", bits_per_sample=0))
        self.assertIsNone(self.bits_of(bits_per_raw_sample="N/A"))
        self.assertIsNone(self.bits_of())

    def test_a_stream_built_without_bits_has_none(self) -> None:
        bare = AudioStream(0, "mp3", 2, "stereo", 44100, None, None)
        self.assertIsNone(bare.bits)


class MapAndTagTests(unittest.TestCase):
    def test_only_the_first_audio_stream_is_mapped(self) -> None:
        info = make_info(audio=(stream("mp3", None, 0), stream("aac", None, 1)))
        result = plan(info, "mp3")
        self.assertEqual(result.args[:2], FIRST_AUDIO)
        self.assertEqual(result.args.count("-map"), 1)
        self.assertEqual(result.note, "audio copied")

    def test_the_second_stream_does_not_decide_the_codec(self) -> None:
        info = make_info(audio=(stream("flac", 24, 0), stream("mp3", None, 1)))
        self.assertEqual(
            plan(info, "mp3").args,
            (*FIRST_AUDIO, *TAGS, "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3"),
        )

    def test_ogg_sources_take_tags_from_their_audio_stream(self) -> None:
        for container in ("ogg", "ogg,oga"):
            with self.subTest(container=container):
                result = plan(make_info("vorbis", container=container), "mp3")
                self.assertEqual(
                    result.args,
                    (
                        *FIRST_AUDIO, "-map_metadata", "0:s:a:0",
                        "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3",
                    ),
                )

    def test_other_containers_use_the_file_tags(self) -> None:
        for container in ("wav", "mp3", "flac", "matroska,webm", None):
            with self.subTest(container=container):
                result = plan(make_info("flac", container=container), "wav")
                self.assertEqual(result.args[2:4], TAGS)

    def test_mp4_family_sources_get_their_brand_tags_cleared(self) -> None:
        for container in (MP4_FAMILY, "mp4", "mov"):
            with self.subTest(container=container):
                result = plan(make_info("aac", bits=None, container=container), "mp3")
                self.assertEqual(
                    result.args,
                    (
                        *FIRST_AUDIO, *TAGS, *BRAND_RESET,
                        "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3",
                    ),
                )

    def test_non_mp4_containers_keep_their_brand_tags_alone(self) -> None:
        for container in ("mp3", "ogg", "flac", "matroska,webm", "wav"):
            with self.subTest(container=container):
                result = plan(make_info("aac", container=container), "m4a")
                self.assertNotIn("major_brand=", result.args)
                self.assertNotIn("-metadata", result.args)

    def test_a_video_source_maps_only_its_audio(self) -> None:
        info = make_info("aac", bits=None, container=MP4_FAMILY, video=picture())
        result = plan(info, "mp3")
        self.assertEqual(
            result.args,
            (
                *FIRST_AUDIO, *TAGS, *BRAND_RESET,
                "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3",
            ),
        )
        self.assertNotIn("0:v", result.args)
        self.assertEqual(result.note, "audio encoded to mp3")

    def test_a_source_without_audio_is_an_error(self) -> None:
        for info in (make_info(audio=()), make_info(audio=(), video=picture(), container=MP4_FAMILY)):
            with self.subTest(video=info.video is not None):
                with self.assertRaises(MediaError) as caught:
                    plan(info, "mp3")
                self.assertEqual(str(caught.exception), "no audio track")

    def test_the_target_may_be_written_with_a_dot_or_capitals(self) -> None:
        expected = plan(make_info("flac", bits=16), "mp3")
        for spelling in (".MP3", "mp3", ".mp3", "MP3"):
            with self.subTest(spelling=spelling):
                self.assertEqual(plan(make_info("flac", bits=16), spelling), expected)


class Mp3TargetTests(unittest.TestCase):
    def test_an_mp3_source_is_copied(self) -> None:
        result = plan(make_info("mp3", bits=None, container="mp3"), "mp3")
        self.assertEqual(
            result.args, (*FIRST_AUDIO, *TAGS, "-c:a", "copy", "-id3v2_version", "3")
        )
        self.assertEqual(result.note, "audio copied")

    def test_other_codecs_are_encoded_at_a_quality_level(self) -> None:
        for codec in ("pcm_s16le", "flac", "aac", "opus", "vorbis"):
            with self.subTest(codec=codec):
                result = plan(make_info(codec), "mp3")
                self.assertEqual(
                    result.args,
                    (*FIRST_AUDIO, *TAGS, "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3"),
                )
                self.assertEqual(result.note, "audio encoded to mp3")

    def test_a_bitrate_switches_to_a_fixed_rate_even_for_mp3_sources(self) -> None:
        for codec, container in (("flac", "flac"), ("mp3", "mp3")):
            with self.subTest(codec=codec):
                result = plan(make_info(codec, container=container), "mp3", "128k")
                self.assertEqual(
                    result.args,
                    (*FIRST_AUDIO, *TAGS, "-c:a", "libmp3lame", "-b:a", "128k", "-id3v2_version", "3"),
                )
                self.assertEqual(result.note, "audio encoded to mp3")

    def test_the_id3_option_is_last_with_cover_art_and_brand_tags(self) -> None:
        info = make_info("aac", bits=None, container=MP4_FAMILY, cover=True)
        self.assertEqual(
            plan(info, "mp3").args,
            (
                *FIRST_AUDIO, *COVER, *TAGS, *BRAND_RESET,
                "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3",
            ),
        )


class M4aTargetTests(unittest.TestCase):
    def test_aac_and_alac_sources_are_copied(self) -> None:
        for codec in ("aac", "alac"):
            with self.subTest(codec=codec):
                result = plan(make_info(codec, bits=None, container="mp3"), "m4a")
                self.assertEqual(result.args, (*FIRST_AUDIO, *TAGS, "-c:a", "copy"))
                self.assertEqual(result.note, "audio copied")

    def test_m4a_to_m4a_copies_and_clears_the_brand_tags(self) -> None:
        result = plan(make_info("aac", bits=None, container=MP4_FAMILY), "m4a")
        self.assertEqual(result.args, (*FIRST_AUDIO, *TAGS, *BRAND_RESET, "-c:a", "copy"))

    def test_other_codecs_are_encoded_to_aac_at_192k(self) -> None:
        for codec in ("mp3", "flac", "pcm_s16le", "opus"):
            with self.subTest(codec=codec):
                result = plan(make_info(codec), "m4a")
                self.assertEqual(
                    result.args, (*FIRST_AUDIO, *TAGS, "-c:a", "aac", "-b:a", "192k")
                )
                self.assertEqual(result.note, "audio encoded to aac")

    def test_a_given_rate_replaces_192k_and_forces_encoding(self) -> None:
        for codec in ("flac", "aac"):
            with self.subTest(codec=codec):
                result = plan(make_info(codec), "m4a", "96k")
                self.assertEqual(
                    result.args, (*FIRST_AUDIO, *TAGS, "-c:a", "aac", "-b:a", "96k")
                )
                self.assertEqual(result.note, "audio encoded to aac")


class OpusTargetTests(unittest.TestCase):
    def test_an_opus_source_is_copied(self) -> None:
        result = plan(make_info("opus", bits=None, container="ogg"), "opus")
        self.assertEqual(
            result.args, (*FIRST_AUDIO, "-map_metadata", "0:s:a:0", "-c:a", "copy")
        )
        self.assertEqual(result.note, "audio copied")

    def test_other_codecs_are_encoded_at_128k(self) -> None:
        result = plan(make_info("flac"), "opus")
        self.assertEqual(result.args, (*FIRST_AUDIO, *TAGS, "-c:a", "libopus", "-b:a", "128k"))
        self.assertEqual(result.note, "audio encoded to opus")

    def test_a_given_rate_forces_encoding_even_from_opus(self) -> None:
        result = plan(make_info("opus", container="ogg"), "opus", "64k")
        self.assertEqual(
            result.args,
            (*FIRST_AUDIO, "-map_metadata", "0:s:a:0", "-c:a", "libopus", "-b:a", "64k"),
        )
        self.assertEqual(result.note, "audio encoded to opus")


class OggTargetTests(unittest.TestCase):
    def test_a_vorbis_source_is_copied(self) -> None:
        result = plan(make_info("vorbis", bits=None, container="ogg"), "ogg")
        self.assertEqual(
            result.args, (*FIRST_AUDIO, "-map_metadata", "0:s:a:0", "-c:a", "copy")
        )
        self.assertEqual(result.note, "audio copied")

    def test_other_codecs_are_encoded_at_quality_5(self) -> None:
        result = plan(make_info("mp3", bits=None, container="mp3"), "ogg")
        self.assertEqual(result.args, (*FIRST_AUDIO, *TAGS, "-c:a", "libvorbis", "-q:a", "5"))
        self.assertEqual(result.note, "audio encoded to vorbis")

    def test_a_given_rate_is_a_fixed_rate(self) -> None:
        for codec, container in (("flac", "flac"), ("vorbis", "ogg")):
            with self.subTest(codec=codec):
                result = plan(make_info(codec, container=container), "ogg", "160k")
                self.assertEqual(result.args[-4:], ("-c:a", "libvorbis", "-b:a", "160k"))
                self.assertEqual(result.note, "audio encoded to vorbis")


class FlacTargetTests(unittest.TestCase):
    def test_a_flac_source_is_copied_with_or_without_a_bitrate(self) -> None:
        for bitrate in (None, "128k"):
            with self.subTest(bitrate=bitrate):
                result = plan(make_info("flac", bits=24, container="flac"), "flac", bitrate)
                self.assertEqual(result.args, (*FIRST_AUDIO, *TAGS, "-c:a", "copy"))
                self.assertEqual(result.note, "audio copied")

    def test_sixteen_bit_and_unknown_depth_sources_are_encoded_as_s16(self) -> None:
        for codec, bits in (("pcm_s16le", 16), ("mp3", None), ("aac", None), ("pcm_u8", 8)):
            with self.subTest(codec=codec, bits=bits):
                result = plan(make_info(codec, bits=bits), "flac")
                self.assertEqual(
                    result.args,
                    (
                        *FIRST_AUDIO, *TAGS,
                        "-c:a", "flac", "-sample_fmt", "s16", "-compression_level", "5",
                    ),
                )
                self.assertEqual(result.note, "audio encoded to flac")

    def test_deeper_sources_are_encoded_as_s32(self) -> None:
        for bits in (24, 32):
            with self.subTest(bits=bits):
                result = plan(make_info("pcm_s24le", bits=bits), "flac")
                self.assertEqual(
                    result.args,
                    (
                        *FIRST_AUDIO, *TAGS,
                        "-c:a", "flac", "-sample_fmt", "s32", "-compression_level", "5",
                    ),
                )

    def test_a_bitrate_does_not_change_an_encode_to_flac(self) -> None:
        with_rate = plan(make_info("mp3", bits=None), "flac", "128k")
        self.assertEqual(with_rate, plan(make_info("mp3", bits=None), "flac"))


class WavTargetTests(unittest.TestCase):
    def test_pcm_sources_are_copied(self) -> None:
        for codec in ("pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le"):
            with self.subTest(codec=codec):
                result = plan(make_info(codec, bits=24), "wav")
                self.assertEqual(result.args, (*FIRST_AUDIO, *TAGS, "-c:a", "copy"))
                self.assertEqual(result.note, "audio copied")

    def test_a_bitrate_is_ignored_for_a_wav_copy(self) -> None:
        result = plan(make_info("pcm_s16le"), "wav", "128k")
        self.assertEqual(result.args, (*FIRST_AUDIO, *TAGS, "-c:a", "copy"))

    def test_big_endian_pcm_is_not_copied(self) -> None:
        result = plan(make_info("pcm_s16be", bits=16), "wav")
        self.assertEqual(result.args[-2:], ("-c:a", "pcm_s16le"))

    def test_sixteen_bit_and_unknown_depth_sources_decode_to_s16(self) -> None:
        for codec, bits in (("flac", 16), ("mp3", None), ("aac", None)):
            with self.subTest(codec=codec, bits=bits):
                result = plan(make_info(codec, bits=bits), "wav")
                self.assertEqual(result.args, (*FIRST_AUDIO, *TAGS, "-c:a", "pcm_s16le"))
                self.assertEqual(result.note, "audio encoded to pcm_s16le")

    def test_deeper_sources_decode_to_s24(self) -> None:
        for bits in (24, 32):
            with self.subTest(bits=bits):
                result = plan(make_info("flac", bits=bits), "wav")
                self.assertEqual(result.args, (*FIRST_AUDIO, *TAGS, "-c:a", "pcm_s24le"))
                self.assertEqual(result.note, "audio encoded to pcm_s24le")


class CoverArtTests(unittest.TestCase):
    def test_mp3_m4a_and_flac_keep_the_cover_picture(self) -> None:
        expected = {
            "mp3": ("-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3"),
            "m4a": ("-c:a", "aac", "-b:a", "192k"),
            "flac": ("-c:a", "flac", "-sample_fmt", "s16", "-compression_level", "5"),
        }
        for target, codec_args in expected.items():
            with self.subTest(target=target):
                result = plan(make_info("pcm_s16le", cover=True), target)
                self.assertEqual(result.args, (*FIRST_AUDIO, *COVER, *TAGS, *codec_args))
                self.assertNotIn("dropped", result.note)

    def test_a_copy_still_keeps_the_cover_picture(self) -> None:
        result = plan(make_info("mp3", bits=None, container="mp3", cover=True), "mp3")
        self.assertEqual(
            result.args,
            (*FIRST_AUDIO, *COVER, *TAGS, "-c:a", "copy", "-id3v2_version", "3"),
        )
        self.assertEqual(result.note, "audio copied")

    def test_opus_ogg_and_wav_drop_the_cover_and_say_so(self) -> None:
        expected = {
            "opus": (
                ("-c:a", "libopus", "-b:a", "128k"),
                "audio encoded to opus; cover art dropped: .opus cannot hold it",
            ),
            "ogg": (
                ("-c:a", "libvorbis", "-q:a", "5"),
                "audio encoded to vorbis; cover art dropped: .ogg cannot hold it",
            ),
            "wav": (
                ("-c:a", "pcm_s16le"),
                "audio encoded to pcm_s16le; cover art dropped: .wav cannot hold it",
            ),
        }
        for target, (codec_args, note) in expected.items():
            with self.subTest(target=target):
                result = plan(make_info("mp3", bits=None, container="mp3", cover=True), target)
                self.assertEqual(result.args, (*FIRST_AUDIO, *TAGS, *codec_args))
                self.assertNotIn("0:v", result.args)
                self.assertEqual(result.note, note)

    def test_the_drop_note_follows_a_copy_note_too(self) -> None:
        result = plan(make_info("pcm_s16le", cover=True), "wav")
        self.assertEqual(result.note, "audio copied; cover art dropped: .wav cannot hold it")

    def test_a_source_without_cover_art_has_no_drop_note(self) -> None:
        for target in ("opus", "ogg", "wav"):
            with self.subTest(target=target):
                self.assertNotIn("dropped", plan(make_info("flac"), target).note)

    def test_a_real_video_stream_is_never_mapped_as_cover_art(self) -> None:
        info = make_info("aac", bits=None, container=MP4_FAMILY, cover=True, video=picture())
        for target in ("mp3", "m4a", "flac", "opus"):
            with self.subTest(target=target):
                result = plan(info, target)
                self.assertNotIn("0:v", result.args)
                self.assertNotIn("attached_pic", result.args)
                self.assertNotIn("dropped", result.note)


if __name__ == "__main__":
    unittest.main()
