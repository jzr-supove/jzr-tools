"""`jzr video gif` and `jzr video convert --to gif` against the real ffmpeg.

Inputs are generated with ffmpeg's lavfi sources when the class starts and are
kept tiny so the whole file runs in a few seconds.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from click.testing import CliRunner

from jzr_tools.cli import app

from media_fixtures import HAVE_FFMPEG, ffmpeg_run
from size_fixtures import with_size
from test_media_cli import describe, err

runner = CliRunner()

LOOP_FOREVER = b"\x03\x01\x00\x00"
H264 = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]


def video_stream(path: Path) -> dict:
    completed = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe", "-v", "error", "-of", "json",
            "-show_streams", "-select_streams", "v:0", "-i", str(path),
        ],
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout.decode("utf-8"))["streams"][0]


def size_of(path: Path) -> tuple[int, int]:
    stream = video_stream(path)
    return stream["width"], stream["height"]


def frames_in(path: Path) -> int:
    completed = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe", "-v", "error", "-count_frames",
            "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
            "-of", "csv=p=0", "-i", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(completed.stdout.strip().rstrip(","))


def loop_bytes(path: Path) -> bytes:
    """The four bytes that follow the NETSCAPE2.0 application id, where the loop count lives."""
    data = path.read_bytes()
    at = data.find(b"NETSCAPE2.0")
    if at < 0:
        raise AssertionError(f"{path} has no NETSCAPE2.0 block, so it does not loop")
    return data[at + 11 : at + 15]


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not on PATH")
class RealGifTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._clips = tempfile.TemporaryDirectory(prefix="jzr-gif-clips-")
        cls.addClassCleanup(cls._clips.cleanup)
        cls.clips = Path(cls._clips.name).resolve()
        d = cls.clips
        try:
            ffmpeg_run(
                "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30:duration=2", *H264,
                str(d / "square.mp4"),
            )
            ffmpeg_run(
                "-f", "lavfi", "-i", "testsrc2=size=200x100:rate=30:duration=1", *H264,
                str(d / "wide.mp4"),
            )
            ffmpeg_run(
                "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=10:duration=1", *H264,
                str(d / "landscape.mp4"),
            )
            ffmpeg_run(
                "-display_rotation", "90", "-i", str(d / "landscape.mp4"), "-c", "copy",
                str(d / "rotated.mp4"),
            )
            ffmpeg_run(
                "-f", "lavfi", "-i", "testsrc2=size=16x16:rate=1:duration=31", *H264,
                str(d / "long.mp4"),
            )
        except (subprocess.CalledProcessError, OSError) as error:
            detail = getattr(error, "stderr", b"") or b""
            raise unittest.SkipTest(
                f"could not generate test clips: {error} {detail.decode(errors='replace')}"
            ) from error

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-gif-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name).resolve()
        self.addCleanup(self.assert_no_temporary_files)

    def assert_no_temporary_files(self) -> None:
        self.assertEqual([p.name for p in self.work.rglob("jzr-tmp-*")], [])

    def stage(self, name: str, as_name: str | None = None) -> Path:
        target = self.work / (as_name or name)
        shutil.copy(self.clips / name, target)
        return target

    def gif(self, *args: object):  # noqa: ANN201
        return runner.invoke(app, ["video", "gif", *map(str, args)])

    def ok_gif(self, *args: object):  # noqa: ANN201
        result = self.gif(*args)
        self.assertEqual(result.exit_code, 0, describe(result))
        return result

    def test_the_output_is_a_gif_with_the_source_picture_size(self) -> None:
        source = self.stage("square.mp4")
        result = self.ok_gif(source)
        out = self.work / "square.gif"
        self.assertEqual(video_stream(out)["codec_name"], "gif")
        self.assertEqual(size_of(out), (64, 64))
        self.assertIn(with_size(f"{source} -> {out} (64x64, 15 fps)", out), result.stdout)

    def test_the_gif_loops_forever(self) -> None:
        source = self.stage("square.mp4")
        self.ok_gif(source)
        self.assertEqual(loop_bytes(self.work / "square.gif"), LOOP_FOREVER)

    def test_two_seconds_at_15_fps_make_30_frames(self) -> None:
        source = self.stage("square.mp4")
        self.ok_gif(source)
        self.assertAlmostEqual(frames_in(self.work / "square.gif"), 30, delta=1)

    def test_a_range_makes_a_gif_of_that_length(self) -> None:
        source = self.stage("square.mp4")
        self.ok_gif(source, "--start", "0.5", "--duration", "1")
        out = self.work / "square.gif"
        self.assertAlmostEqual(frames_in(out), 15, delta=1)
        self.ok_gif(source, "--end", "0.4", "--outdir", self.work / "end")
        self.assertAlmostEqual(frames_in(self.work / "end" / "square.gif"), 6, delta=1)

    def test_fps_sets_the_frame_rate(self) -> None:
        source = self.stage("square.mp4")
        self.ok_gif(source, "--fps", "5")
        self.assertAlmostEqual(frames_in(self.work / "square.gif"), 10, delta=1)

    def test_a_picture_narrower_than_width_is_never_scaled_up(self) -> None:
        source = self.stage("square.mp4")
        self.ok_gif(source, "--width", "480")
        self.assertEqual(size_of(self.work / "square.gif"), (64, 64))

    def test_a_wider_picture_is_cut_to_width_keeping_the_proportions(self) -> None:
        source = self.stage("wide.mp4")
        result = self.ok_gif(source, "--width", "100")
        out = self.work / "wide.gif"
        self.assertEqual(size_of(out), (100, 50))
        self.assertIn(with_size(f"{source} -> {out} (100x50, 15 fps)", out), result.stdout)

    def test_a_clip_with_a_rotation_comes_out_upright(self) -> None:
        source = self.stage("rotated.mp4")
        result = self.ok_gif(source)
        out = self.work / "rotated.gif"
        self.assertEqual(size_of(out), (64, 96))
        self.assertIn(with_size(f"{source} -> {out} (64x96, 10 fps)", out), result.stdout)

    def test_a_rotated_clip_is_scaled_by_its_upright_width(self) -> None:
        source = self.stage("rotated.mp4")
        self.ok_gif(source, "--width", "32")
        self.assertEqual(size_of(self.work / "rotated.gif"), (32, 48))

    def test_a_clip_over_30_seconds_gets_the_size_note(self) -> None:
        source = self.stage("long.mp4")
        result = self.ok_gif(source)
        self.assertIn("long GIF (0:31): expect under 1 MB", result.stdout)
        self.assertEqual(video_stream(self.work / "long.gif")["codec_name"], "gif")
        short = self.ok_gif(source, "--duration", "10", "--outdir", self.work / "short")
        self.assertNotIn("long GIF", short.stdout)

    def test_a_file_name_with_cyrillic_letters_spaces_and_a_percent_sign_works(self) -> None:
        source = self.stage("square.mp4", "мой клип 100% готов.mp4")
        self.ok_gif(source)
        out = self.work / "мой клип 100% готов.gif"
        self.assertTrue(out.is_file())
        self.assertAlmostEqual(frames_in(out), 30, delta=1)

    def test_a_junk_file_fails_alone_while_a_good_one_is_converted(self) -> None:
        junk = self.work / "broken.mp4"
        junk.write_bytes(bytes(range(256)) * 64)
        good = self.stage("square.mp4")
        result = self.gif(junk, good)
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {junk}:", err(result))
        self.assertEqual(err(result).count("fail "), 1, describe(result))
        self.assertTrue((self.work / "square.gif").is_file())
        self.assertFalse((self.work / "broken.gif").exists())
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())

    def test_a_start_past_the_end_fails_and_writes_nothing(self) -> None:
        source = self.stage("square.mp4")
        result = self.gif(source, "--start", "30")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("is past the end of the file (0:02.00)", err(result))
        self.assertFalse((self.work / "square.gif").exists())

    def test_a_dry_run_writes_nothing(self) -> None:
        source = self.stage("square.mp4")
        result = self.ok_gif(source, "--dry-run")
        self.assertIn("-filter_complex", result.stdout)
        self.assertEqual({p.name for p in self.work.iterdir()}, {"square.mp4"})

    def test_convert_to_gif_makes_the_same_gif_as_video_gif(self) -> None:
        source = self.stage("wide.mp4")
        self.ok_gif(source, "--outdir", self.work / "direct")
        result = runner.invoke(
            app, ["video", "convert", str(source), "--to", "gif", "--outdir", str(self.work / "via")]
        )
        self.assertEqual(result.exit_code, 0, describe(result))
        direct = self.work / "direct" / "wide.gif"
        via = self.work / "via" / "wide.gif"
        self.assertEqual(video_stream(via)["codec_name"], "gif")
        self.assertEqual(size_of(via), size_of(direct))
        self.assertEqual(frames_in(via), frames_in(direct))
        self.assertEqual(loop_bytes(via), LOOP_FOREVER)

    def test_a_pair_command_makes_a_gif(self) -> None:
        source = self.stage("square.mp4")
        result = runner.invoke(app, ["video", "mp42gif", str(source)])
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(video_stream(self.work / "square.gif")["codec_name"], "gif")

    def test_an_existing_gif_is_kept_until_overwrite_is_given(self) -> None:
        source = self.stage("square.mp4")
        self.ok_gif(source)
        out = self.work / "square.gif"
        first = out.read_bytes()
        result = self.ok_gif(source, "--width", "32")
        self.assertIn("target exists", err(result))
        self.assertEqual(out.read_bytes(), first)
        self.ok_gif(source, "--width", "32", "--overwrite")
        self.assertEqual(size_of(out), (32, 32))


if __name__ == "__main__":
    unittest.main()
