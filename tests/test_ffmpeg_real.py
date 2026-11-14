"""Probing and running with the real ffmpeg (skipped when it is not on PATH)."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest

from jzr_tools.core import ffmpeg
from jzr_tools.core.ffmpeg import OUTPUT, Job
from jzr_tools.core.media import MediaError, info_rows

from media_fixtures import HAVE_FFMPEG, Clips, mp4_tool_names


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not on PATH")
class RealFfmpegCase(unittest.TestCase):
    clips: Clips

    @classmethod
    def setUpClass(cls) -> None:
        cls.tools = ffmpeg.locate()
        cls.clips = Clips()
        cls.addClassCleanup(cls.clips.close)
        if cls.clips.error:
            raise unittest.SkipTest(cls.clips.error)

    def probe(self, name_or_path: str | Path):  # noqa: ANN201
        path = self.clips.path(name_or_path) if isinstance(name_or_path, str) else name_or_path
        return ffmpeg.probe(path, self.tools.ffprobe)


class ProbeTests(RealFfmpegCase):
    def test_mp4_with_h264_and_aac(self) -> None:
        info = self.probe("clip.mp4")
        self.assertEqual(info.video.codec, "h264")
        self.assertEqual((info.video.width, info.video.height), (64, 48))
        self.assertAlmostEqual(info.video.fps, 10.0, places=2)
        self.assertEqual(info.video.pixel_format, "yuv420p")
        self.assertEqual(info.video.rotation, 0)
        self.assertEqual([a.codec for a in info.audio], ["aac"])
        self.assertEqual(info.audio[0].sample_rate, 44100)
        self.assertAlmostEqual(info.duration, 1.0, delta=0.15)
        self.assertEqual(info.size, os.path.getsize(self.clips.path("clip.mp4")))
        self.assertIn("mp4", info.container)
        self.assertFalse(info.cover_art)

    def test_mp3_with_tags_and_cover_art(self) -> None:
        info = self.probe("tagged.mp3")
        self.assertIsNone(info.video)
        self.assertTrue(info.cover_art)
        self.assertEqual([a.codec for a in info.audio], ["mp3"])
        self.assertEqual(info.tags["title"], "Song")
        self.assertEqual(info.tags["artist"], "Band")
        self.assertEqual(info.tags["album"], "Disc")
        rows = dict(info_rows(info, tags=True))
        self.assertEqual(rows["year"], "2020")
        self.assertEqual(rows["track"], "3")
        self.assertEqual(rows["cover art"], "yes")

    def test_opus_tags_that_ffprobe_puts_on_the_stream(self) -> None:
        info = self.probe("tagged.opus")
        self.assertEqual(info.audio[0].codec, "opus")
        self.assertEqual(info.tags["title"], "Opus Song")
        self.assertEqual(info.tags["artist"], "Opus Band")

    def test_flac(self) -> None:
        info = self.probe("plain.flac")
        self.assertEqual(info.audio[0].codec, "flac")
        self.assertIsNone(info.video)
        self.assertEqual(info.container, "flac")
        self.assertFalse(info.cover_art)

    def test_display_rotation_of_90_reads_as_270_clockwise(self) -> None:
        self.assertEqual(self.probe("rotated.mp4").video.rotation, 270)

    def test_text_file_is_not_a_readable_media_file(self) -> None:
        with self.assertRaises(MediaError) as caught:
            self.probe("notes.txt")
        self.assertEqual(str(caught.exception), "not a readable media file")

    def test_awkward_file_names(self) -> None:
        for name in mp4_tool_names():
            with self.subTest(name=name):
                copy = self.clips.path(name)
                shutil.copyfile(self.clips.path("clip.mp4"), copy)
                self.assertEqual(self.probe(name).video.codec, "h264")

    def test_relative_name_with_a_leading_dash(self) -> None:
        shutil.copyfile(self.clips.path("clip.mp4"), self.clips.path("-dash.mp4"))
        previous = Path.cwd()
        os.chdir(self.clips.dir)
        try:
            info = ffmpeg.probe(Path("-dash.mp4"), self.tools.ffprobe)
        finally:
            os.chdir(previous)
        self.assertEqual(info.video.codec, "h264")


class RealRunTests(RealFfmpegCase):
    def setUp(self) -> None:
        self._out = tempfile.TemporaryDirectory(prefix="jzr-out-")
        self.addCleanup(self._out.cleanup)
        self.out = Path(self._out.name).resolve()

    def listing(self) -> list[str]:
        return sorted(p.name for p in self.out.iterdir())

    def copy_job(self, target: Path, source: str = "clip.mp4", duration: float | None = 1.0) -> Job:
        return Job(
            steps=(("-i", str(self.clips.path(source)), "-c", "copy", OUTPUT),),
            target=target,
            duration=duration,
        )

    def test_success_writes_the_target_and_leaves_nothing_else(self) -> None:
        target = self.out / "copy.mp4"
        ffmpeg.run(self.copy_job(target), self.tools)
        self.assertEqual(self.listing(), ["copy.mp4"])
        info = ffmpeg.probe(target, self.tools.ffprobe)
        self.assertEqual(info.video.codec, "h264")
        self.assertEqual([a.codec for a in info.audio], ["aac"])

    def test_progress_reaches_about_one(self) -> None:
        updates = []
        ffmpeg.run(self.copy_job(self.out / "p.mp4"), self.tools, on_progress=updates.append)
        fractions = [u.fraction for u in updates if u.fraction is not None]
        self.assertTrue(fractions, "no progress was reported")
        self.assertAlmostEqual(max(fractions), 1.0, delta=0.2)

    def test_awkward_names_for_source_and_target(self) -> None:
        for index, name in enumerate(mp4_tool_names()):
            with self.subTest(name=name):
                source = self.clips.path(name)
                shutil.copyfile(self.clips.path("clip.mp4"), source)
                target = self.out / f"{index} {name}"
                job = Job(
                    steps=(("-i", str(source), "-c", "copy", OUTPUT),), target=target, duration=1.0
                )
                ffmpeg.run(job, self.tools)
                self.assertTrue(target.is_file())
                self.assertEqual(ffmpeg.probe(target, self.tools.ffprobe).video.codec, "h264")
        self.assertFalse([n for n in self.listing() if n.startswith("jzr-tmp-")])

    def test_awkward_name_that_is_the_target_alone(self) -> None:
        for name in ("-starts with dash.mp4", "50% done.mp4", "клип.mp4"):
            with self.subTest(name=name):
                target = self.out / name
                ffmpeg.run(self.copy_job(target), self.tools)
                self.assertTrue(target.is_file())

    def test_audio_that_does_not_fit_the_container_fails_cleanly(self) -> None:
        target = self.out / "result.mp3"
        target.write_bytes(b"precious")
        job = Job(
            steps=(("-i", str(self.clips.path("clip.mp4")), "-vn", "-c:a", "copy", OUTPUT),),
            target=target,
        )
        with self.assertRaises(MediaError) as caught:
            ffmpeg.run(job, self.tools)
        message = str(caught.exception)
        self.assertIn("Exactly one MP3 audio stream is required", message)
        self.assertNotIn("Could not write header", message)
        self.assertFalse(message.startswith("["), message)
        self.assertEqual(self.listing(), ["result.mp3"])
        self.assertEqual(target.read_bytes(), b"precious")

    def test_failure_without_an_existing_target_leaves_the_folder_empty(self) -> None:
        job = Job(
            steps=(("-i", str(self.clips.path("clip.mp4")), "-vn", "-c:a", "copy", OUTPUT),),
            target=self.out / "result.mp3",
        )
        with self.assertRaises(MediaError):
            ffmpeg.run(job, self.tools)
        self.assertEqual(self.listing(), [])

    def test_unknown_encoder(self) -> None:
        target = self.out / "result.mp4"
        target.write_bytes(b"precious")
        job = Job(
            steps=(("-i", str(self.clips.path("clip.mp4")), "-c:v", "libnothere", OUTPUT),),
            target=target,
        )
        with self.assertRaises(MediaError) as caught:
            ffmpeg.run(job, self.tools)
        self.assertEqual(str(caught.exception), "this ffmpeg build has no libnothere encoder")
        self.assertEqual(self.listing(), ["result.mp4"])
        self.assertEqual(target.read_bytes(), b"precious")

    def test_input_that_is_not_media(self) -> None:
        job = Job(
            steps=(("-i", str(self.clips.path("notes.txt")), "-c", "copy", OUTPUT),),
            target=self.out / "result.mp4",
        )
        with self.assertRaises(MediaError) as caught:
            ffmpeg.run(job, self.tools)
        self.assertIn("Invalid data found when processing input", str(caught.exception))
        self.assertEqual(self.listing(), [])

    def test_version_and_encoders_of_the_real_build(self) -> None:
        self.assertRegex(ffmpeg.version(self.tools.ffmpeg), r"^\d")
        names = ffmpeg.encoders(self.tools.ffmpeg)
        self.assertIn("aac", names)
        self.assertNotIn("libnothere", names)
        self.assertNotIn("Encoders:", names)


if __name__ == "__main__":
    unittest.main()
