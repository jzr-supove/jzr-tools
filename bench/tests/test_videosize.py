"""Tests for the videosize task (held out): talk.mp4, 20 s and over 20 MB, to talk_small.mp4 under 8 MB.

The fixture is slow to make (a noisy 720p clip), so it is made once for the whole module, with the
task's own prepare_fixtures, the first time a test needs it. Correct and wrong results are made with
ffmpeg from it; each wrong one differs from the correct one in exactly one way. All of it is skipped
when ffmpeg is missing.
"""

from __future__ import annotations

import array
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench.tasks import HELD_OUT, TASKS, get_task  # noqa: E402
from jzr_support import HAVE_JZR, NEEDS_JZR, run_jzr  # noqa: E402
from PIL import Image, ImageChops, ImageStat  # noqa: E402
from task_check_support import CheckCase  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
NEEDS_FFMPEG = "ffmpeg and ffprobe are required for the videosize task tests"

PROMPT = (
    "Make a copy of talk.mp4 that is under 8 MB so I can post it in a chat. "
    "Keep the whole length and the sound. Save it as talk_small.mp4."
)
LIMIT_BYTES = 8 * 1024 * 1024  # "8 MB" is 8,388,608 bytes in the spec
COMPARE_SIZE = (96, 54)

_TMP: tempfile.TemporaryDirectory[str] | None = None
_FIXTURES: Path | None = None


def fixtures() -> Path:
    """The task's fixture folder, made on first use and kept for the whole module."""
    global _TMP, _FIXTURES
    if _FIXTURES is None:
        _TMP = tempfile.TemporaryDirectory()
        folder = Path(_TMP.name) / "fixtures"
        folder.mkdir()
        get_task("videosize").prepare_fixtures(folder)
        _FIXTURES = folder
    return _FIXTURES


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()


def run(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=600)


def probe(path: Path) -> dict:
    out = run(
        FFPROBE or "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,r_frame_rate",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    )
    return json.loads(out.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def kinds(path: Path) -> set[str]:
    return {s["codec_type"] for s in probe(path)["streams"]}


def frame(path: Path, second: float) -> Image.Image:
    """The frame at `second`, scaled to 96x54, RGB (independent of the task's own code)."""
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "frame.png"
        run(
            FFMPEG or "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            str(second),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            f"scale={COMPARE_SIZE[0]}:{COMPARE_SIZE[1]}:flags=area",
            str(png),
        )
        with Image.open(png) as image:
            return image.convert("RGB").copy()


def mean_difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def frame_difference(a: Path, b: Path, second: float = 10) -> float:
    return mean_difference(frame(a, second), frame(b, second))


def tone_hz(path: Path) -> float:
    """The pitch of the audio, from the zero crossings of its first 5 seconds decoded to mono 8 kHz."""
    raw = run(
        FFMPEG or "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-t",
        "5",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "s16le",
        "-",
    ).stdout
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    crossings = sum(1 for a, b in zip(samples, samples[1:], strict=False) if (a < 0) != (b < 0))
    return crossings / 2 / (len(samples) / 8000)


def small_encode(source: Path, dest: Path, *extra: str, video: bool = True, audio: bool = True, t: str | None = None):
    """A correct-looking small copy: 640x360, about 1 Mbit/s, AAC 96k; `extra` are output options."""
    encoders = run(FFMPEG or "ffmpeg", "-hide_banner", "-encoders").stdout.decode()
    codec = ["-c:v", "libx264", "-preset", "veryfast"] if " libx264 " in encoders else ["-c:v", "mpeg4"]
    args = [FFMPEG or "ffmpeg", "-v", "error", "-y", "-i", str(source)]
    args += ["-vf", "scale=640:360", *codec, "-b:v", "1000k", "-pix_fmt", "yuv420p"] if video else ["-vn"]
    args += ["-c:a", "aac", "-b:a", "96k"] if audio else ["-an"]
    if t:
        args += ["-t", t]
    args += [*extra, "-movflags", "+faststart", str(dest)]
    run(*args)


def pad_to(path: Path, size: int) -> None:
    """Append an MP4 `free` box so that the file is exactly `size` bytes (players ignore it)."""
    extra = size - path.stat().st_size
    assert extra >= 8, (path.stat().st_size, size)
    with path.open("ab") as handle:
        handle.write(struct.pack(">I", extra) + b"free" + b"\0" * (extra - 8))


class VideosizeTaskTest(unittest.TestCase):
    """What needs no ffmpeg: the prompt, the name and the registry."""

    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(get_task("videosize").prompt, PROMPT)

    def test_it_is_named_videosize_and_registered_as_held_out(self) -> None:
        task = get_task("videosize")
        self.assertEqual(task.name, "videosize")
        self.assertIs(TASKS["videosize"], task)
        self.assertIn("videosize", HELD_OUT)


@unittest.skipUnless(FFMPEG and FFPROBE, NEEDS_FFMPEG)
class VideosizeFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = fixtures()
        cls.talk = cls.fixtures / "talk.mp4"
        cls.probe = probe(cls.talk)
        cls.task = get_task("videosize")

    def test_prepare_fixtures_writes_exactly_talk_mp4(self) -> None:
        self.assertEqual([p.name for p in self.fixtures.iterdir()], ["talk.mp4"])

    def test_the_file_is_at_least_20_mb(self) -> None:
        # (the spec's "20 MB" is read as 20,000,000 bytes: the smaller reading)
        self.assertGreaterEqual(self.talk.stat().st_size, 20_000_000)

    def test_video_is_1280x720_at_30_fps_h264_or_mpeg4(self) -> None:
        video = next(s for s in self.probe["streams"] if s["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (1280, 720))
        self.assertEqual(video["r_frame_rate"], "30/1")
        self.assertIn(video["codec_name"], ("h264", "mpeg4"))

    def test_it_lasts_20_seconds(self) -> None:
        self.assertAlmostEqual(float(self.probe["format"]["duration"]), 20.0, delta=0.3)

    def test_the_audio_is_aac_with_a_440_hz_tone(self) -> None:
        audio = [s for s in self.probe["streams"] if s["codec_type"] == "audio"]
        self.assertEqual([s["codec_name"] for s in audio], ["aac"])
        self.assertAlmostEqual(tone_hz(self.talk), 440, delta=15)

    def test_the_picture_is_busy_not_flat(self) -> None:
        # a noisy test pattern: the frame at 10 s is far from a flat grey, so that "resembles the source" means something
        flat = Image.new("RGB", COMPARE_SIZE, (128, 128, 128))
        spread = mean_difference(frame(self.talk, 10), flat)
        self.assertGreater(spread, 15)

    def test_setup_copies_the_video_into_the_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.task.setup(work, self.fixtures)
            self.assertEqual([p.name for p in work.iterdir()], ["talk.mp4"])
            self.assertEqual((work / "talk.mp4").stat().st_size, self.talk.stat().st_size)
            self.assertEqual((work / "talk.mp4").read_bytes(), self.talk.read_bytes())


@unittest.skipUnless(FFMPEG and FFPROBE, NEEDS_FFMPEG)
class VideosizeCheckTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = fixtures()
        cls.task = get_task("videosize")
        talk = cls.fixtures / "talk.mp4"
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        out = Path(cls._tmp.name)
        cls.results = out
        cls.talk = talk
        small_encode(talk, out / "good.mp4")
        small_encode(talk, out / "no-audio.mp4", audio=False)
        small_encode(talk, out / "no-video.mp4", video=False)
        small_encode(talk, out / "short.mp4", t="12")
        small_encode(talk, out / "a-bit-short.mp4", t="19.8")  # 0.2 s off: inside the 0.5 s margin
        small_encode(talk, out / "too-short.mp4", t="19.3")  # 0.7 s off
        # 22 s: the picture's last frame held and the sound padded with silence
        small_encode(
            talk,
            out / "long.mp4",
            "-af",
            "apad",
            "-t",
            "22",
            "-vf",
            "scale=640:360,tpad=stop_mode=clone:stop_duration=2",
        )
        # the same length and sound, a different picture (colours inverted): the frame at 10 s must not resemble the source's
        small_encode(talk, out / "negated.mp4", "-vf", "scale=640:360,negate")
        (out / "text.mp4").write_text("this is not a video\n")
        (out / "empty.mp4").write_bytes(b"")
        # sizes around the limit: a valid file followed by an MP4 `free` box up to the exact size
        for name, size in (
            ("just-under.mp4", LIMIT_BYTES - 1),
            ("exactly-8-mib.mp4", LIMIT_BYTES),
            ("8-2-million.mp4", 8_200_000),
        ):
            shutil.copy(out / "good.mp4", out / name)
            pad_to(out / name, size)

    def install(self, name: str | None) -> None:
        if name is not None:
            shutil.copy(self.results / name, self.work / "talk_small.mp4")

    def test_the_variants_are_what_they_are_meant_to_be(self) -> None:
        # Guards the inputs of this test, so a pass or a failure below means something.
        source_seconds = duration(self.talk)
        good = self.results / "good.mp4"
        self.assertLess(good.stat().st_size, 8_000_000)
        self.assertAlmostEqual(duration(good), source_seconds, delta=0.2)
        self.assertEqual(kinds(good), {"video", "audio"})
        self.assertLess(frame_difference(good, self.talk), 10)
        self.assertEqual(kinds(self.results / "no-audio.mp4"), {"video"})
        self.assertEqual(kinds(self.results / "no-video.mp4"), {"audio"})
        for name, seconds in (("short.mp4", 12), ("a-bit-short.mp4", 19.8), ("too-short.mp4", 19.3), ("long.mp4", 22)):
            with self.subTest(variant=name):
                self.assertAlmostEqual(duration(self.results / name), seconds, delta=0.15)
                self.assertEqual(kinds(self.results / name), {"video", "audio"})
        self.assertAlmostEqual(duration(self.results / "negated.mp4"), source_seconds, delta=0.2)
        self.assertGreater(frame_difference(self.results / "negated.mp4", self.talk), 35)
        self.assertEqual(kinds(self.results / "negated.mp4"), {"video", "audio"})
        for name, size in (
            ("just-under.mp4", LIMIT_BYTES - 1),
            ("exactly-8-mib.mp4", LIMIT_BYTES),
            ("8-2-million.mp4", 8_200_000),
        ):
            with self.subTest(variant=name):
                self.assertEqual((self.results / name).stat().st_size, size)
                self.assertEqual(kinds(self.results / name), {"video", "audio"})
                self.assertAlmostEqual(duration(self.results / name), source_seconds, delta=0.2)
        self.assertGreater(self.talk.stat().st_size, LIMIT_BYTES)

    def test_a_smaller_copy_with_the_whole_length_and_the_sound_passes(self) -> None:
        self.install("good.mp4")
        self.assert_passes()

    def test_final_text_does_not_matter(self) -> None:
        self.install("good.mp4")
        self.assertTrue(self.check("").passed)
        self.assertTrue(self.check("something unrelated").passed)

    def test_a_length_within_half_a_second_passes(self) -> None:
        self.install("a-bit-short.mp4")
        self.assert_passes()

    def test_the_limit_is_8_mib_not_8_million_bytes(self) -> None:
        for name in ("8-2-million.mp4", "just-under.mp4"):
            with self.subTest(variant=name):
                self.install(name)
                self.assert_passes()

    def test_a_file_of_exactly_8_mib_fails(self) -> None:
        self.install("exactly-8-mib.mp4")
        self.assert_fails_with(self.check(), "large", "size", "bytes", reasons=1)

    def test_missing_output_fails(self) -> None:
        self.install(None)
        self.assert_fails_with(self.check(), "talk_small.mp4", "exist", "missing", "not found")

    def test_a_copy_of_the_source_is_too_large(self) -> None:
        self.install(None)
        shutil.copy(self.talk, self.work / "talk_small.mp4")
        self.assert_fails_with(self.check(), "large", "size", "bytes", reasons=1)

    def test_a_copy_without_sound_fails(self) -> None:
        self.install("no-audio.mp4")
        self.assert_fails_with(self.check(), "audio", "sound", reasons=1)

    def test_a_copy_without_picture_fails(self) -> None:
        self.install("no-video.mp4")
        self.assert_fails_with(self.check(), "video", "picture")

    def test_a_clip_cut_short_fails(self) -> None:
        for name in ("short.mp4", "too-short.mp4"):
            with self.subTest(variant=name):
                self.install(name)
                self.assert_fails_with(self.check(), "duration", "length", "long", reasons=1)

    def test_a_clip_made_longer_fails(self) -> None:
        self.install("long.mp4")
        self.assert_fails_with(self.check(), "duration", "length", "long", reasons=1)

    def test_a_different_picture_at_the_same_length_fails(self) -> None:
        self.install("negated.mp4")
        self.assert_fails_with(self.check(), "frame", "resemble", "content", "differ", "10", reasons=1)

    def test_a_text_file_named_talk_small_mp4_fails(self) -> None:
        for name in ("text.mp4", "empty.mp4"):
            with self.subTest(variant=name):
                self.install(name)
                self.assert_fails_with(self.check(), "video", "mp4", "read", "valid", "ffprobe")

    def test_a_modified_source_fails_even_with_a_correct_copy(self) -> None:
        self.install("good.mp4")
        with (self.work / "talk.mp4").open("ab") as handle:
            handle.write(b"\0" * 16)
        result = self.check()
        self.assert_fails_with(result, "talk.mp4", reasons=1)
        self.assert_fails_with(result, "unchanged", "sha", "hash", "modified", "changed")

    def test_the_source_replaced_by_the_small_copy_fails(self) -> None:
        # the tempting shortcut: shrink the source in place and copy it
        self.install("good.mp4")
        shutil.copy(self.results / "good.mp4", self.work / "talk.mp4")
        self.assert_fails_with(self.check(), "talk.mp4", "unchanged", "sha", "hash", "modified", "changed")

    def test_a_deleted_source_fails_even_with_a_correct_copy(self) -> None:
        self.install("good.mp4")
        (self.work / "talk.mp4").unlink()
        self.assert_fails_with(self.check(), "talk.mp4", "unchanged", "sha", "hash", "missing", "changed")

    def test_check_does_not_modify_the_work_folder(self) -> None:
        self.install("good.mp4")
        self.assert_check_leaves_work_folder_alone()

    def test_check_does_not_modify_the_work_folder_when_it_fails(self) -> None:
        self.install("negated.mp4")
        self.assert_check_leaves_work_folder_alone()


@unittest.skipUnless(FFMPEG and FFPROBE, NEEDS_FFMPEG)
@unittest.skipUnless(HAVE_JZR, NEEDS_JZR)
class VideosizeJzrTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = fixtures()
        cls.task = get_task("videosize")

    def test_jzr_video_compress_to_8mb_passes_the_check(self) -> None:
        run_jzr(["video", "compress", "talk.mp4", "--size", "8MB", "-o", "talk_small.mp4"], self.work)
        self.assertTrue((self.work / "talk_small.mp4").is_file())
        self.assert_passes()


if __name__ == "__main__":
    unittest.main()
