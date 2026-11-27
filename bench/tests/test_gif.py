"""Tests for the gif task against real ffmpeg output (skipped when ffmpeg is missing).

Fixtures are generated once per class: a 10 s 1280x720 clip.mp4, then a handful of GIFs cut from it
with ffmpeg directly (one correct, several wrong in exactly one way).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench.tasks import get_task  # noqa: E402
from jzr_bench.tasks.base import CheckResult  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
NEEDS_FFMPEG = "ffmpeg and ffprobe are required for the gif task tests"

PROMPT = (
    "Make an animated GIF from clip.mp4 in this folder: start at 0:02, 3 seconds long, "
    "480 pixels wide (keep the aspect ratio), under 2 MB. Save it as clip.gif."
)


def make_gif(source: Path, dest: Path, *, start: float, length: float, scale: str) -> None:
    """Cut a GIF with a palette, the way a careful user would."""
    vf = f"fps=10,scale={scale}:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse"
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-ss", str(start), "-t", str(length), "-i", str(source), "-vf", vf, str(dest)],
        check=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


@unittest.skipUnless(FFMPEG and FFPROBE, NEEDS_FFMPEG)
class GifFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("gif")
        cls.task.prepare_fixtures(cls.fixtures)
        probe = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,r_frame_rate",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(cls.fixtures / "clip.mp4"),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        cls.probe = json.loads(probe.stdout)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(self.task.prompt, PROMPT)

    def test_prepare_fixtures_writes_clip_mp4(self) -> None:
        self.assertTrue((self.fixtures / "clip.mp4").is_file())
        self.assertGreater((self.fixtures / "clip.mp4").stat().st_size, 10_000)

    def test_clip_is_ten_seconds_1280x720_at_30_fps(self) -> None:
        video = next(s for s in self.probe["streams"] if s["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (1280, 720))
        self.assertEqual(video["r_frame_rate"], "30/1")
        self.assertAlmostEqual(float(self.probe["format"]["duration"]), 10.0, delta=0.2)
        self.assertIn(video["codec_name"], ("h264", "mpeg4"))

    def test_clip_has_an_audio_track(self) -> None:
        self.assertIn("audio", {s["codec_type"] for s in self.probe["streams"]})

    def test_setup_copies_the_clip_into_the_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.task.setup(work, self.fixtures)
            self.assertEqual((work / "clip.mp4").read_bytes(), (self.fixtures / "clip.mp4").read_bytes())
            self.assertFalse((work / "clip.gif").exists())

    def test_setup_prepares_an_empty_fixture_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixtures, work = Path(tmp) / "f", Path(tmp) / "w"
            fixtures.mkdir()
            work.mkdir()
            self.task.setup(work, fixtures)
            self.assertTrue((work / "clip.mp4").is_file())
            self.assertTrue((fixtures / "clip.mp4").is_file())


@unittest.skipUnless(FFMPEG and FFPROBE, NEEDS_FFMPEG)
class GifCheckTest(unittest.TestCase):
    """gif.check on correct and deliberately wrong results."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.fixtures = base / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("gif")
        cls.task.prepare_fixtures(cls.fixtures)
        clip = cls.fixtures / "clip.mp4"

        gifs = base / "gifs"
        gifs.mkdir()
        cls.gifs = gifs
        # correct: start 2 s, 3 s long, 480 wide, 270 high
        make_gif(clip, gifs / "good.gif", start=2, length=3, scale="480:-2")
        # wrong in one way each
        make_gif(clip, gifs / "start0.gif", start=0, length=3, scale="480:-2")
        make_gif(clip, gifs / "start1.gif", start=1, length=3, scale="480:-2")
        make_gif(clip, gifs / "short.gif", start=2, length=1.5, scale="480:-2")
        make_gif(clip, gifs / "w300.gif", start=2, length=3, scale="300:270")  # height right, width wrong
        make_gif(clip, gifs / "w480h200.gif", start=2, length=3, scale="480:200")  # width right, height wrong

        # Too large: a valid GIF followed by >2 MB of padding after the trailer byte. Pillow reads
        # the frames up to the trailer and ignores the rest, so only the size rule can fail.
        cls.big = gifs / "big.gif"
        shutil.copy(gifs / "good.gif", cls.big)
        with cls.big.open("ab") as handle:
            handle.write(b"\0" * 2_100_000)

        # Not a GIF: a real PNG and a text file, both named clip.gif
        from PIL import Image

        # (an animated PNG made of two frames of the good GIF: right size, 3 s in total and the right first
        # frame, so only "is it a GIF" can reject it)
        with Image.open(gifs / "good.gif") as good:
            frames = []
            for index in (0, good.n_frames // 2):
                good.seek(index)
                frames.append(good.convert("RGB"))
        frames[0].save(gifs / "png-named.gif", format="PNG", save_all=True, append_images=frames[1:], duration=1500)
        (gifs / "text-named.gif").write_text("this is not an image\n")
        # A single-frame GIF of the right size and duration
        # (the first frame of the good GIF, kept as one 3 s frame: only the frame count can reject it)
        with Image.open(gifs / "good.gif") as good:
            good.seek(0)
            good.convert("RGB").save(gifs / "single.gif", format="GIF", duration=3000)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        self.work = Path(self._work.name)
        self.task.setup(self.work, self.fixtures)

    def install(self, name: str | None) -> None:
        if name is not None:
            shutil.copy(self.gifs / name, self.work / "clip.gif")

    def check(self, final_text: str = "Done, saved clip.gif.") -> CheckResult:
        result = self.task.check(self.work, self.fixtures, final_text)
        self.assertIsInstance(result, CheckResult)
        return result

    def assert_fails_with(self, result: CheckResult, *keywords: str) -> None:
        self.assertFalse(result.passed, result.details)
        self.assertTrue(result.details, "a failed check must say why")
        joined = " ".join(result.details).lower()
        self.assertTrue(
            any(k in joined for k in keywords),
            f"expected one of {keywords} in the reasons, got {result.details}",
        )

    def test_correct_gif_passes(self) -> None:
        self.install("good.gif")
        result = self.check()
        self.assertTrue(result.passed, result.details)

    def test_the_correct_gif_is_what_the_spec_describes(self) -> None:
        # Guards the fixture itself, so "passes" above means something.
        from PIL import Image

        with Image.open(self.gifs / "good.gif") as im:
            self.assertEqual(im.format, "GIF")
            self.assertEqual(im.size, (480, 270))
            self.assertGreater(im.n_frames, 1)
        self.assertLess((self.gifs / "good.gif").stat().st_size, 2_000_000)
        with Image.open(self.gifs / "png-named.gif") as im:
            self.assertEqual((im.format, im.size, im.n_frames), ("PNG", (480, 270), 2))
        with Image.open(self.gifs / "single.gif") as im:
            self.assertEqual((im.format, im.size, im.n_frames), ("GIF", (480, 270), 1))
            self.assertEqual(im.info["duration"], 3000)
        self.assertGreater(self.big.stat().st_size, 2_000_000)

    def test_final_text_does_not_matter(self) -> None:
        self.install("good.gif")
        self.assertTrue(self.check("").passed)
        self.assertTrue(self.check("something unrelated").passed)

    def test_missing_gif_fails(self) -> None:
        self.install(None)
        self.assert_fails_with(self.check(), "clip.gif", "exist", "missing", "not found")

    def test_wrong_width_fails(self) -> None:
        self.install("w300.gif")
        self.assert_fails_with(self.check(), "width", "300", "480", "size", "dimension")

    def test_wrong_height_fails(self) -> None:
        self.install("w480h200.gif")
        self.assert_fails_with(self.check(), "height", "200", "270", "aspect", "size", "dimension")

    def test_wrong_start_at_zero_fails(self) -> None:
        self.install("start0.gif")
        self.assert_fails_with(self.check(), "start", "frame", "closest", "0 s", "2 s", "offset")

    def test_wrong_start_at_one_second_fails(self) -> None:
        self.install("start1.gif")
        self.assert_fails_with(self.check(), "start", "frame", "closest", "1 s", "2 s", "offset")

    def test_wrong_duration_fails(self) -> None:
        self.install("short.gif")
        self.assert_fails_with(self.check(), "duration", "length", "seconds", " s")

    def test_too_large_fails(self) -> None:
        self.install(None)
        shutil.copy(self.big, self.work / "clip.gif")
        result = self.check()
        self.assert_fails_with(result, "size", "bytes", "large", "mb", "2,000,000", "2000000")
        self.assertEqual(len(result.details), 1, result.details)

    def test_modified_source_clip_fails_even_with_a_correct_gif(self) -> None:
        self.install("good.gif")
        with (self.work / "clip.mp4").open("ab") as handle:
            handle.write(b"tampered")
        result = self.check()
        self.assert_fails_with(result, "clip.mp4", "unchanged", "sha", "hash", "modified", "changed")
        self.assertEqual(len(result.details), 1, result.details)

    def test_deleted_source_clip_fails_even_with_a_correct_gif(self) -> None:
        self.install("good.gif")
        (self.work / "clip.mp4").unlink()
        self.assert_fails_with(self.check(), "clip.mp4", "unchanged", "sha", "hash", "missing", "changed")

    def test_png_named_clip_gif_fails(self) -> None:
        self.install("png-named.gif")
        self.assert_fails_with(self.check(), "gif", "format", "png", "frame")

    def test_text_file_named_clip_gif_fails(self) -> None:
        self.install("text-named.gif")
        self.assert_fails_with(self.check(), "gif", "image", "valid", "open", "read")

    def test_single_frame_gif_fails(self) -> None:
        self.install("single.gif")
        self.assert_fails_with(self.check(), "frame", "animated", "more than one")

    def test_each_wrong_variant_fails_only_its_own_rule(self) -> None:
        # one reason line per failed condition: a single-fault GIF must produce a single reason
        # (the stretched w300 / w480h200 variants are left out: distorting the picture could also
        # legitimately trip the first-frame rule)
        cases = {
            "short.gif": 1,
            "start0.gif": 1,
            "single.gif": 1,
        }
        for name, expected in cases.items():
            with self.subTest(gif=name):
                self.install(name)
                result = self.check()
                self.assertFalse(result.passed)
                self.assertEqual(len(result.details), expected, result.details)

    def test_check_does_not_modify_the_work_folder(self) -> None:
        self.install("good.gif")
        before = {p.name: p.read_bytes() for p in self.work.iterdir()}
        self.check()
        after = {p.name: p.read_bytes() for p in self.work.iterdir()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
