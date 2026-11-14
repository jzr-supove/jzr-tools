"""Pure tests for time and size handling and for turning ffprobe JSON into facts."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from jzr_tools.core import batch, images, media
from jzr_tools.core.media import (
    AudioStream,
    MediaError,
    MediaInfo,
    MissingToolError,
    VideoStream,
    format_size,
    format_time,
    info_as_dict,
    info_rows,
    parse_probe,
    parse_size,
    parse_time,
)

DEGREE = "\N{DEGREE SIGN}"


class ParseTimeTests(unittest.TestCase):
    def test_accepts_seconds_and_clock_forms(self) -> None:
        cases = {
            "90": 90.0,
            "7.5": 7.5,
            "0": 0.0,
            "1:30": 90.0,
            "0:01:30.250": 90.25,
            "1:5": 65.0,
            "1:02:03": 3723.0,
            "100:00": 6000.0,
            "00:00:07.5": 7.5,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                value = parse_time(text)
                self.assertIsInstance(value, float)
                self.assertAlmostEqual(value, expected, places=6)

    def test_rejects_malformed_times_and_names_the_input(self) -> None:
        bad = [
            "", " ", "abc", "-5", "+5", "inf", "nan", "-inf", ":30", "1:", "1::5",
            "1:60", "1:2:60", "1:2:3:4", "1:-5", "0:00:61", "1,5",
        ]
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as caught:
                    parse_time(text)
                if text.strip():
                    self.assertIn(text, str(caught.exception))


class FormatTimeTests(unittest.TestCase):
    def test_minutes_under_an_hour_and_hours_from_one_hour(self) -> None:
        cases = {
            0: "0:00",
            5: "0:05",
            90: "1:30",
            3599: "59:59",
            3600: "1:00:00",
            3725: "1:02:05",
            36000: "10:00:00",
        }
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_time(seconds), expected)

    def test_precision_adds_decimals_to_the_seconds(self) -> None:
        self.assertEqual(format_time(7.5, 2), "0:07.50")
        self.assertEqual(format_time(90.25, 3), "1:30.250")
        self.assertEqual(format_time(3723.5, 1), "1:02:03.5")

    def test_rounding_carries_into_the_next_unit(self) -> None:
        self.assertEqual(format_time(59.999, 2), "1:00.00")
        self.assertEqual(format_time(59.6), "1:00")
        self.assertEqual(format_time(3599.9), "1:00:00")

    def test_negative_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            format_time(-1)

    def test_round_trips_through_parse_time(self) -> None:
        for seconds in (0.0, 12.5, 90.25, 3725.125):
            with self.subTest(seconds=seconds):
                self.assertAlmostEqual(parse_time(format_time(seconds, 3)), seconds, places=3)


class ParseSizeTests(unittest.TestCase):
    def test_decimal_units(self) -> None:
        cases = {
            "25MB": 25_000_000,
            "8M": 8_000_000,
            "500KB": 500_000,
            "500K": 500_000,
            "2GB": 2_000_000_000,
            "1G": 1_000_000_000,
            "1.5GB": 1_500_000_000,
            "0.5MB": 500_000,
            "1024": 1024,
            "10B": 10,
            "1": 1,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                value = parse_size(text)
                self.assertIsInstance(value, int)
                self.assertEqual(value, expected)

    def test_binary_units(self) -> None:
        self.assertEqual(parse_size("1KiB"), 1024)
        self.assertEqual(parse_size("2MiB"), 2 * 1024**2)
        self.assertEqual(parse_size("1GiB"), 1024**3)
        self.assertEqual(parse_size("1.5KiB"), 1536)

    def test_case_and_one_space_are_ignored(self) -> None:
        self.assertEqual(parse_size("25mb"), 25_000_000)
        self.assertEqual(parse_size("25 MB"), 25_000_000)
        self.assertEqual(parse_size("1 kib"), 1024)

    def test_rejections(self) -> None:
        bad = ["", "MB", "abc", "-5MB", "0", "0MB", "0.5", "0.5B", "5XB", "5 TB?", "1.2.3MB", "inf", "nan"]
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_size(text)


class FormatSizeTests(unittest.TestCase):
    def test_bytes_and_one_decimal_above(self) -> None:
        cases = {
            0: "0 B",
            999: "999 B",
            1_234: "1.2 kB",
            1_500: "1.5 kB",
            25_000_000: "25.0 MB",
            81_300_000: "81.3 MB",
            1_500_000_000: "1.5 GB",
        }
        for number, expected in cases.items():
            with self.subTest(number=number):
                self.assertEqual(format_size(number), expected)


class TableTests(unittest.TestCase):
    def test_target_tables(self) -> None:
        self.assertEqual(media.VIDEO_CONTAINERS, ("mp4", "mov", "mkv", "webm"))
        self.assertEqual(media.AUDIO_FORMATS, ("mp3", "m4a", "opus", "ogg", "flac", "wav"))
        self.assertEqual(
            media.VIDEO_TARGETS, media.VIDEO_CONTAINERS + media.AUDIO_FORMATS + ("gif",)
        )

    def test_encoder_tables_name_the_ffmpeg_encoders(self) -> None:
        self.assertEqual(media.VIDEO_ENCODERS["h264"], "libx264")
        self.assertEqual(media.VIDEO_ENCODERS["hevc"], "libx265")
        self.assertEqual(media.VIDEO_ENCODERS["av1"], "libsvtav1")
        self.assertEqual(media.VIDEO_ENCODERS["vp9"], "libvpx-vp9")
        self.assertEqual(media.VIDEO_ENCODERS["gif"], "gif")
        self.assertEqual(media.AUDIO_ENCODERS["aac"], "aac")
        self.assertEqual(media.AUDIO_ENCODERS["mp3"], "libmp3lame")
        self.assertEqual(media.AUDIO_ENCODERS["opus"], "libopus")
        self.assertEqual(media.AUDIO_ENCODERS["vorbis"], "libvorbis")
        self.assertEqual(media.AUDIO_ENCODERS["flac"], "flac")
        self.assertIn("pcm_s16le", media.AUDIO_ENCODERS)
        self.assertIn("pcm_s24le", media.AUDIO_ENCODERS)

    def test_missing_tool_error_is_a_media_error(self) -> None:
        self.assertTrue(issubclass(MissingToolError, MediaError))
        self.assertTrue(issubclass(MediaError, Exception))


def video_stream(index: int = 0, **extra: object) -> dict:
    stream = {
        "index": index,
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "pix_fmt": "yuv420p",
        "avg_frame_rate": "30000/1001",
        "r_frame_rate": "30000/1001",
        "bit_rate": "700000",
        "disposition": {"attached_pic": 0},
    }
    stream.update(extra)
    return stream


def audio_stream(index: int = 1, **extra: object) -> dict:
    stream = {
        "index": index,
        "codec_type": "audio",
        "codec_name": "aac",
        "sample_rate": "48000",
        "channels": 2,
        "channel_layout": "stereo",
        "bit_rate": "128000",
        "disposition": {"attached_pic": 0},
    }
    stream.update(extra)
    return stream


def probe_json(streams: list[dict], **format_fields: object) -> dict:
    fmt = {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV",
        "duration": "90.500000",
        "size": "1500000",
        "bit_rate": "800000",
    }
    fmt.update(format_fields)
    return {"streams": streams, "format": fmt}


PATH = Path("clip.mp4")


class ParseProbeTests(unittest.TestCase):
    def test_a_video_with_audio(self) -> None:
        info = parse_probe(probe_json([video_stream(), audio_stream()]), PATH)
        self.assertIsInstance(info, MediaInfo)
        self.assertEqual(info.path, PATH)
        self.assertEqual(info.container, "mov,mp4,m4a,3gp,3g2,mj2")
        self.assertEqual(info.container_name, "QuickTime / MOV")
        self.assertAlmostEqual(info.duration, 90.5)
        self.assertEqual(info.size, 1_500_000)
        self.assertEqual(info.bitrate, 800_000)
        self.assertIsInstance(info.video, VideoStream)
        self.assertEqual(info.video.index, 0)
        self.assertEqual(info.video.codec, "h264")
        self.assertEqual((info.video.width, info.video.height), (1920, 1080))
        self.assertEqual(info.video.pixel_format, "yuv420p")
        self.assertEqual(info.video.bitrate, 700_000)
        self.assertAlmostEqual(info.video.fps, 29.97, places=2)
        self.assertEqual(info.video.rotation, 0)
        self.assertFalse(info.video.hdr)
        self.assertEqual(len(info.audio), 1)
        audio = info.audio[0]
        self.assertIsInstance(audio, AudioStream)
        self.assertEqual(audio.index, 1)
        self.assertEqual(audio.codec, "aac")
        self.assertEqual(audio.channels, 2)
        self.assertEqual(audio.channel_layout, "stereo")
        self.assertEqual(audio.sample_rate, 48000)
        self.assertEqual(audio.bitrate, 128_000)
        self.assertEqual(list(info.extra), [])
        self.assertFalse(info.cover_art)

    def test_audio_is_a_tuple_of_every_audio_stream(self) -> None:
        data = probe_json(
            [video_stream(), audio_stream(1, tags={"language": "eng"}), audio_stream(2)]
        )
        info = parse_probe(data, PATH)
        self.assertIsInstance(info.audio, tuple)
        self.assertEqual([a.index for a in info.audio], [1, 2])
        self.assertEqual(info.audio[0].language, "eng")
        self.assertIsNone(info.audio[1].language)

    def test_numbers_are_numbers_and_absent_ones_are_none(self) -> None:
        data = {
            "streams": [audio_stream(0, bit_rate=None)],
            "format": {"format_name": "wav", "format_long_name": "WAV / WAVE"},
        }
        data["streams"][0].pop("bit_rate")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a.wav"
            path.write_bytes(b"x" * 321)
            info = parse_probe(data, path)
        self.assertIsNone(info.duration)
        self.assertIsNone(info.bitrate)
        self.assertIsNone(info.audio[0].bitrate)
        self.assertIsNone(info.video)

    def test_size_falls_back_to_the_size_on_disk(self) -> None:
        data = probe_json([audio_stream(0)])
        del data["format"]["size"]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a.m4a"
            path.write_bytes(b"x" * 4321)
            info = parse_probe(data, path)
        self.assertEqual(info.size, 4321)

    def test_format_size_wins_over_the_disk_size(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a.m4a"
            path.write_bytes(b"x" * 10)
            info = parse_probe(probe_json([audio_stream(0)], size="999"), path)
        self.assertEqual(info.size, 999)

    def test_no_streams_is_not_a_readable_media_file(self) -> None:
        for data in ({"streams": [], "format": {"format_name": "x"}}, {"format": {}}, {}):
            with self.subTest(data=data):
                with self.assertRaises(MediaError) as caught:
                    parse_probe(data, PATH)
                self.assertEqual(str(caught.exception), "not a readable media file")


class FrameRateTests(unittest.TestCase):
    def fps(self, **fields: object) -> float | None:
        info = parse_probe(probe_json([video_stream(**fields)]), PATH)
        return info.video.fps

    def test_uses_the_average_rate(self) -> None:
        self.assertAlmostEqual(self.fps(avg_frame_rate="25/1", r_frame_rate="50/1"), 25.0)

    def test_falls_back_to_the_base_rate_when_average_is_zero_over_zero(self) -> None:
        self.assertAlmostEqual(self.fps(avg_frame_rate="0/0", r_frame_rate="24000/1001"), 23.976, places=2)

    def test_no_rate_at_all_is_none(self) -> None:
        self.assertIsNone(self.fps(avg_frame_rate="0/0", r_frame_rate="0/0"))

    def test_missing_fields_are_none(self) -> None:
        stream = video_stream()
        del stream["avg_frame_rate"], stream["r_frame_rate"]
        self.assertIsNone(parse_probe(probe_json([stream]), PATH).video.fps)


class RotationTests(unittest.TestCase):
    def rotation(self, side_data: list[dict] | None) -> int:
        fields = {} if side_data is None else {"side_data_list": side_data}
        info = parse_probe(probe_json([video_stream(**fields)]), PATH)
        return info.video.rotation

    def test_ffprobe_anticlockwise_rotation_becomes_clockwise_degrees(self) -> None:
        self.assertEqual(self.rotation([{"rotation": 90}]), 270)
        self.assertEqual(self.rotation([{"rotation": -90}]), 90)
        self.assertEqual(self.rotation([{"rotation": 180}]), 180)
        self.assertEqual(self.rotation([{"rotation": -180}]), 180)
        self.assertEqual(self.rotation([{"rotation": 270}]), 90)

    def test_no_rotation_is_zero(self) -> None:
        self.assertEqual(self.rotation(None), 0)
        self.assertEqual(self.rotation([]), 0)
        self.assertEqual(self.rotation([{"rotation": 0}]), 0)

    def test_other_side_data_is_ignored(self) -> None:
        self.assertEqual(
            self.rotation([{"side_data_type": "Mastering display metadata"}, {"rotation": -90}]),
            90,
        )

    def test_rotation_is_always_in_zero_to_359(self) -> None:
        for raw in (-450, -360, 360, 450, 719):
            with self.subTest(raw=raw):
                self.assertIn(self.rotation([{"rotation": raw}]), range(360))


class HdrTests(unittest.TestCase):
    def stream(self, transfer: str | None) -> VideoStream:
        fields = {} if transfer is None else {"color_transfer": transfer}
        return parse_probe(probe_json([video_stream(**fields)]), PATH).video

    def test_pq_and_hlg_are_hdr(self) -> None:
        for transfer in ("smpte2084", "arib-std-b67"):
            with self.subTest(transfer=transfer):
                stream = self.stream(transfer)
                self.assertEqual(stream.transfer, transfer)
                self.assertTrue(stream.hdr)

    def test_other_transfers_are_not(self) -> None:
        for transfer in ("bt709", "iec61966-2-1", None):
            with self.subTest(transfer=transfer):
                self.assertFalse(self.stream(transfer).hdr)


class CoverArtTests(unittest.TestCase):
    def cover(self) -> dict:
        return video_stream(
            1, codec_name="mjpeg", width=500, height=500, avg_frame_rate="0/0",
            r_frame_rate="90000/1", disposition={"attached_pic": 1},
        )

    def test_attached_picture_is_cover_art_not_the_video_stream(self) -> None:
        data = probe_json([audio_stream(0, codec_name="mp3"), self.cover()], format_name="mp3")
        info = parse_probe(data, Path("song.mp3"))
        self.assertIsNone(info.video)
        self.assertTrue(info.cover_art)
        self.assertEqual([a.codec for a in info.audio], ["mp3"])

    def test_the_real_video_is_chosen_over_a_cover_listed_first(self) -> None:
        cover = self.cover()
        cover["index"] = 0
        data = probe_json([cover, video_stream(1), audio_stream(2)])
        info = parse_probe(data, PATH)
        self.assertEqual(info.video.index, 1)
        self.assertEqual(info.video.codec, "h264")
        self.assertTrue(info.cover_art)

    def test_no_attached_picture_means_no_cover_art(self) -> None:
        self.assertFalse(parse_probe(probe_json([video_stream(), audio_stream()]), PATH).cover_art)


class TagTests(unittest.TestCase):
    def test_format_tags_are_lower_cased(self) -> None:
        data = probe_json(
            [audio_stream(0)], tags={"TITLE": "Song", "Artist": "Band", "album": "Disc"}
        )
        info = parse_probe(data, PATH)
        self.assertEqual(info.tags["title"], "Song")
        self.assertEqual(info.tags["artist"], "Band")
        self.assertEqual(info.tags["album"], "Disc")

    def test_stream_tags_fill_in_what_the_format_lacks(self) -> None:
        data = probe_json(
            [audio_stream(0, tags={"TITLE": "Opus Song", "ARTIST": "Opus Band"})],
            format_name="ogg",
        )
        info = parse_probe(data, PATH)
        self.assertEqual(info.tags["title"], "Opus Song")
        self.assertEqual(info.tags["artist"], "Opus Band")

    def test_format_tags_win_over_stream_tags(self) -> None:
        data = probe_json(
            [audio_stream(0, tags={"title": "from stream", "album": "stream album"})],
            tags={"title": "from format"},
        )
        info = parse_probe(data, PATH)
        self.assertEqual(info.tags["title"], "from format")
        self.assertEqual(info.tags["album"], "stream album")

    def test_only_the_first_audio_stream_is_consulted(self) -> None:
        data = probe_json(
            [audio_stream(0), audio_stream(1, tags={"title": "second track"})]
        )
        self.assertNotIn("title", parse_probe(data, PATH).tags)

    def test_no_tags_is_an_empty_mapping(self) -> None:
        self.assertEqual(dict(parse_probe(probe_json([audio_stream(0)]), PATH).tags), {})


class ExtraStreamTests(unittest.TestCase):
    def test_other_streams_get_labels(self) -> None:
        data = probe_json(
            [
                video_stream(0),
                audio_stream(1),
                {"index": 2, "codec_type": "subtitle", "codec_name": "mov_text",
                 "tags": {"language": "eng"}},
                video_stream(3, codec_name="h264"),
                {"index": 4, "codec_type": "data"},
            ]
        )
        info = parse_probe(data, PATH)
        self.assertEqual(info.video.index, 0)
        self.assertEqual(
            list(info.extra),
            ["subtitle stream 2 (mov_text, eng)", "video stream 3 (h264)", "data stream 4"],
        )

    def test_subtitle_without_language_has_only_the_codec(self) -> None:
        data = probe_json(
            [video_stream(0), {"index": 1, "codec_type": "subtitle", "codec_name": "subrip"}]
        )
        self.assertEqual(list(parse_probe(data, PATH).extra), ["subtitle stream 1 (subrip)"])


def sample_info(**changes: object) -> MediaInfo:
    fields = dict(
        path=PATH,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        container_name="QuickTime / MOV",
        duration=90.5,
        size=1_500_000,
        bitrate=800_000,
        video=VideoStream(0, "h264", 1920, 1080, 29.97, "yuv420p", 0, None, 700_000),
        audio=(AudioStream(1, "aac", 2, "stereo", 48000, 128_000, "eng"),),
        extra=(),
        tags={},
        cover_art=False,
    )
    fields.update(changes)
    return MediaInfo(**fields)


class InfoAsDictTests(unittest.TestCase):
    def test_keys_and_json_types(self) -> None:
        document = info_as_dict(sample_info(tags={"title": "T"}, extra=("data stream 2",)))
        self.assertEqual(
            set(document),
            {
                "file", "container", "container_name", "duration", "size", "bitrate",
                "video", "audio", "extra", "tags", "cover_art",
            },
        )
        self.assertEqual(document["file"], str(PATH))
        self.assertEqual(document["container"], "mov,mp4,m4a,3gp,3g2,mj2")
        self.assertEqual(document["container_name"], "QuickTime / MOV")
        self.assertEqual(document["duration"], 90.5)
        self.assertIs(type(document["size"]), int)
        self.assertEqual(document["size"], 1_500_000)
        self.assertEqual(document["bitrate"], 800_000)
        self.assertEqual(document["extra"], ["data stream 2"])
        self.assertEqual(document["tags"], {"title": "T"})
        self.assertIs(document["cover_art"], False)

        video = document["video"]
        self.assertEqual(
            set(video),
            {"index", "codec", "width", "height", "fps", "pixel_format", "rotation",
             "transfer", "bitrate", "hdr"},
        )
        self.assertEqual((video["width"], video["height"]), (1920, 1080))
        self.assertEqual(video["fps"], 29.97)
        self.assertIs(video["hdr"], False)
        self.assertIsNone(video["transfer"])

        self.assertIsInstance(document["audio"], list)
        audio = document["audio"][0]
        self.assertEqual(
            set(audio),
            {"index", "codec", "channels", "channel_layout", "sample_rate", "bitrate", "language"},
        )
        self.assertIs(type(audio["channels"]), int)
        self.assertEqual(audio["sample_rate"], 48000)
        self.assertEqual(audio["language"], "eng")

    def test_is_serialisable_and_numbers_stay_numbers(self) -> None:
        decoded = json.loads(json.dumps(info_as_dict(sample_info())))
        self.assertIsInstance(decoded["duration"], float)
        self.assertIsInstance(decoded["video"]["width"], int)
        self.assertIsInstance(decoded["audio"][0]["sample_rate"], int)

    def test_audio_only_file_has_null_video(self) -> None:
        document = info_as_dict(sample_info(video=None))
        self.assertIsNone(document["video"])

    def test_hdr_flag_follows_the_transfer(self) -> None:
        hdr = VideoStream(0, "hevc", 3840, 2160, 24.0, "yuv420p10le", 0, "smpte2084", None)
        document = info_as_dict(sample_info(video=hdr))
        self.assertIs(document["video"]["hdr"], True)
        self.assertEqual(document["video"]["transfer"], "smpte2084")


class InfoRowsTests(unittest.TestCase):
    def test_rows_of_a_plain_video(self) -> None:
        self.assertEqual(
            info_rows(sample_info(), tags=False),
            [
                ("container", "QuickTime / MOV"),
                ("duration", "1:30.50"),
                ("size", "1.5 MB"),
                ("bitrate", "800 kb/s"),
                ("video", "h264, 1920x1080, 29.97 fps, yuv420p"),
                ("audio", "aac, 2 ch (stereo), 48000 Hz, 128 kb/s"),
            ],
        )

    def test_rotation_row_only_when_not_zero(self) -> None:
        rotated = VideoStream(0, "h264", 1920, 1080, 29.97, "yuv420p", 90, None, None)
        rows = info_rows(sample_info(video=rotated), tags=False)
        self.assertIn(("rotation", f"90{DEGREE} clockwise"), rows)
        labels = [label for label, _ in info_rows(sample_info(), tags=False)]
        self.assertNotIn("rotation", labels)
        self.assertLess(
            [label for label, _ in rows].index("video"),
            [label for label, _ in rows].index("rotation"),
        )

    def test_hdr_row_names_the_transfer(self) -> None:
        for transfer, text in (("smpte2084", "PQ (smpte2084)"), ("arib-std-b67", "HLG (arib-std-b67)")):
            with self.subTest(transfer=transfer):
                stream = VideoStream(0, "hevc", 3840, 2160, 24.0, "yuv420p10le", 0, transfer, None)
                self.assertIn(("hdr", text), info_rows(sample_info(video=stream), tags=False))
        labels = [label for label, _ in info_rows(sample_info(), tags=False)]
        self.assertNotIn("hdr", labels)

    def test_several_audio_streams_are_numbered(self) -> None:
        audio = (
            AudioStream(1, "aac", 2, "stereo", 48000, 128_000, "eng"),
            AudioStream(2, "ac3", 6, "5.1", 48000, 384_000, "fra"),
        )
        rows = info_rows(sample_info(audio=audio), tags=False)
        self.assertIn(("audio 1", "aac, 2 ch (stereo), 48000 Hz, 128 kb/s"), rows)
        self.assertIn(("audio 2", "ac3, 6 ch (5.1), 48000 Hz, 384 kb/s"), rows)
        self.assertNotIn("audio", [label for label, _ in rows])

    def test_extra_streams_get_extra_rows(self) -> None:
        rows = info_rows(sample_info(extra=("subtitle stream 2 (mov_text, eng)", "data stream 3")), tags=False)
        self.assertEqual(
            [value for label, value in rows if label == "extra"],
            ["subtitle stream 2 (mov_text, eng)", "data stream 3"],
        )

    def test_audio_only_file_has_no_video_row(self) -> None:
        labels = [label for label, _ in info_rows(sample_info(video=None), tags=False)]
        self.assertNotIn("video", labels)
        self.assertIn("audio", labels)

    def test_tag_rows_only_with_tags(self) -> None:
        info = sample_info(
            video=None,
            tags={"title": "Song", "artist": "Band", "album": "Disc", "date": "2020-05-04", "track": "3"},
            cover_art=True,
        )
        without = dict(info_rows(info, tags=False))
        self.assertNotIn("title", without)
        self.assertNotIn("cover art", without)

        rows = info_rows(info, tags=True)
        self.assertEqual(
            [label for label, _ in rows][-6:],
            ["title", "artist", "album", "year", "track", "cover art"],
        )
        found = dict(rows)
        self.assertEqual(found["title"], "Song")
        self.assertEqual(found["artist"], "Band")
        self.assertEqual(found["album"], "Disc")
        self.assertEqual(found["year"], "2020")
        self.assertEqual(found["track"], "3")
        self.assertEqual(found["cover art"], "yes")

    def test_year_comes_from_the_year_tag_when_there_is_no_date(self) -> None:
        found = dict(info_rows(sample_info(video=None, tags={"year": "1999"}), tags=True))
        self.assertEqual(found["year"], "1999")

    def test_absent_tags_are_left_out_and_cover_art_says_no(self) -> None:
        found = dict(info_rows(sample_info(video=None, tags={"title": "Only"}), tags=True))
        self.assertEqual(found["title"], "Only")
        for label in ("artist", "album", "year", "track"):
            self.assertNotIn(label, found)
        self.assertEqual(found["cover art"], "no")


class BatchModuleTests(unittest.TestCase):
    NAMES = ("normalize_extension", "target_for", "plan_targets", "find_sources", "ConversionResult")

    def test_the_moved_names_live_in_batch(self) -> None:
        for name in self.NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(batch, name))

    def test_images_exposes_the_very_same_objects(self) -> None:
        for name in self.NAMES:
            with self.subTest(name=name):
                self.assertIs(getattr(images, name), getattr(batch, name))

    def test_equivalent_extensions_stays_with_the_image_aliases(self) -> None:
        self.assertTrue(hasattr(images, "equivalent_extensions"))
        self.assertFalse(hasattr(batch, "equivalent_extensions"))

    def test_batch_does_not_depend_on_image_code(self) -> None:
        source = Path(batch.__file__).read_text(encoding="utf-8")
        self.assertNotIn("pillow", source.lower())
        self.assertNotIn("import click", source)


if __name__ == "__main__":
    unittest.main()
