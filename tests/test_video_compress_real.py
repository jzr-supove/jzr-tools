"""`jzr video compress` against the real ffmpeg: files are made, compressed and probed.

The source is a noisy 1280x720 clip, eight seconds long, generated with
ffmpeg's lavfi sources when the class starts; the noise keeps x264 from
shrinking it to almost nothing.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from click.testing import CliRunner

from jzr_tools.cli import app

from media_fixtures import HAVE_FFMPEG, ffmpeg_run
from test_media_cli import describe, err
from test_video_convert_real import audio_stream, box_offset, streams, tags, video_stream

runner = CliRunner()


def short_edge(path: Path) -> int:
    stream = video_stream(path)
    return min(stream["width"], stream["height"])


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not on PATH")
class RealVideoCompressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._clips = tempfile.TemporaryDirectory(prefix="jzr-compress-clips-")
        cls.addClassCleanup(cls._clips.cleanup)
        cls.clips = Path(cls._clips.name).resolve()
        try:
            ffmpeg_run(
                "-f", "lavfi", "-i",
                "testsrc2=size=1280x720:rate=25:duration=8,noise=alls=30:allf=t+u",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", "-metadata", "title=Holiday",
                str(cls.clips / "big.mp4"),
            )
            ffmpeg_run(
                "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10:duration=1",
                "-c:v", "libx264", "-crf", "40", "-preset", "veryslow", "-pix_fmt", "yuv420p",
                str(cls.clips / "tiny.mp4"),
            )
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"could not generate test clips: {exc}") from exc

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name).resolve()

    def bring(self, source: str, name: str | None = None) -> Path:
        target = self.dir / (name or source)
        shutil.copy(self.clips / source, target)
        return target

    def compress(self, *args: object):  # noqa: ANN201
        return runner.invoke(app, ["video", "compress", *map(str, args)])

    def names(self) -> set[str]:
        return {p.name for p in self.dir.iterdir()}

    def test_a_size_gives_an_h264_aac_mp4_within_it(self) -> None:
        clip = self.bring("big.mp4")
        self.assertGreater(clip.stat().st_size, 2_000_000)
        result = self.compress(clip, "--size", "2MB")
        self.assertEqual(result.exit_code, 0, describe(result))
        out = self.dir / "big_compressed.mp4"
        self.assertLessEqual(out.stat().st_size, 2_000_000)
        self.assertEqual(video_stream(out)["codec_name"], "h264")
        self.assertEqual(audio_stream(out)["codec_name"], "aac")
        self.assertEqual(self.names(), {"big.mp4", "big_compressed.mp4"})

    def test_a_tight_size_lowers_the_short_edge(self) -> None:
        clip = self.bring("big.mp4")
        result = self.compress(clip, "--size", "1MB")
        self.assertEqual(result.exit_code, 0, describe(result))
        out = self.dir / "big_compressed.mp4"
        self.assertLessEqual(out.stat().st_size, 1_000_000)
        self.assertLess(short_edge(out), 720)

    def test_no_audio_leaves_no_audio_stream(self) -> None:
        clip = self.bring("big.mp4")
        result = self.compress(clip, "--size", "2MB", "--no-audio")
        self.assertEqual(result.exit_code, 0, describe(result))
        out = self.dir / "big_compressed.mp4"
        self.assertEqual(streams(out, "audio"), [])
        self.assertLessEqual(out.stat().st_size, 2_000_000)

    def test_quality_mode_makes_yuv420p_h264_with_the_index_up_front(self) -> None:
        clip = self.bring("big.mp4")
        result = self.compress(clip, "--resize", "360")
        self.assertEqual(result.exit_code, 0, describe(result))
        out = self.dir / "big_compressed.mp4"
        stream = video_stream(out)
        self.assertEqual(stream["codec_name"], "h264")
        self.assertEqual(stream["pix_fmt"], "yuv420p")
        self.assertEqual((stream["width"], stream["height"]), (640, 360))
        moov, mdat = box_offset(out, b"moov"), box_offset(out, b"mdat")
        self.assertGreaterEqual(moov, 0)
        self.assertGreaterEqual(mdat, 0)
        self.assertLess(moov, mdat)
        self.assertLess(out.stat().st_size, clip.stat().st_size)

    def test_a_source_that_is_already_small_is_left_alone(self) -> None:
        clip = self.bring("tiny.mp4")
        result = self.compress(clip)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {clip}: already smaller than the result", err(result))
        self.assertEqual(self.names(), {"tiny.mp4"})

    def test_an_impossible_size_fails_and_writes_nothing(self) -> None:
        clip = self.bring("big.mp4")
        result = self.compress(clip, "--size", "20KB")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("too small for a", err(result))
        self.assertEqual(self.names(), {"big.mp4"})

    def test_cyrillic_and_percent_names_work(self) -> None:
        clip = self.bring("big.mp4", "видео 100%.mp4")
        result = self.compress(clip, "--resize", "240")
        self.assertEqual(result.exit_code, 0, describe(result))
        out = self.dir / "видео 100%_compressed.mp4"
        self.assertTrue(out.is_file(), self.names())
        self.assertEqual(short_edge(out), 240)

    def test_a_broken_file_fails_alone_and_leaves_nothing_behind(self) -> None:
        good = self.bring("big.mp4")
        bad = self.dir / "broken.mp4"
        bad.write_bytes(b"this is not a video" * 50)
        result = self.compress(bad, good, "--resize", "240")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(err(result).count("fail "), 1, describe(result))
        self.assertIn(f"fail {bad}:", err(result))
        self.assertEqual(self.names(), {"big.mp4", "broken.mp4", "big_compressed.mp4"})

    def test_strip_removes_the_title_that_is_kept_by_default(self) -> None:
        clip = self.bring("big.mp4")
        self.assertEqual(tags(clip).get("title"), "Holiday")
        kept = self.dir / "kept.mp4"
        stripped = self.dir / "stripped.mp4"
        self.assertEqual(self.compress(clip, "--resize", "240", "-o", kept).exit_code, 0)
        self.assertEqual(
            self.compress(clip, "--resize", "240", "--strip", "-o", stripped).exit_code, 0
        )
        self.assertEqual(tags(kept).get("title"), "Holiday")
        self.assertNotIn("title", tags(stripped))


if __name__ == "__main__":
    unittest.main()
