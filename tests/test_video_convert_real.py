"""`jzr video convert` against the real ffmpeg: files are made, converted and probed.

Inputs are generated one second long, 64x48, with ffmpeg's lavfi sources when
the class starts.
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

from media_fixtures import HAVE_FFMPEG, Clips, ffmpeg_run, mp4_tool_names
from size_fixtures import with_size
from test_media_cli import describe, err

runner = CliRunner()


def ffprobe(path: Path) -> dict:
    completed = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe", "-v", "error", "-of", "json",
            "-show_format", "-show_streams", "-i", str(path),
        ],
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout.decode("utf-8"))


def streams(path: Path, kind: str) -> list[dict]:
    return [s for s in ffprobe(path)["streams"] if s["codec_type"] == kind]


def video_stream(path: Path) -> dict:
    return streams(path, "video")[0]


def audio_stream(path: Path) -> dict:
    return streams(path, "audio")[0]


def tags(path: Path) -> dict[str, str]:
    data = ffprobe(path)
    found = {k.lower(): v for k, v in data["format"].get("tags", {}).items()}
    for stream in data["streams"]:
        for key, value in stream.get("tags", {}).items():
            found.setdefault(key.lower(), value)
    return found


def encoders() -> str:
    completed = subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout


def needs_encoders(*names: str):  # noqa: ANN201
    missing = [name for name in names if not HAVE_FFMPEG or name not in encoders()]
    return unittest.skipIf(bool(missing), f"ffmpeg lacks the encoder {', '.join(missing)}")


def box_offset(path: Path, name: bytes) -> int:
    return path.read_bytes().find(name)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not on PATH")
class RealVideoConvertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.clips = Clips()
        cls.addClassCleanup(cls.clips.close)
        if cls.clips.error:
            raise unittest.SkipTest(cls.clips.error)
        d = cls.clips.dir
        picture = ["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10:duration=1"]
        sine = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
        try:
            ffmpeg_run(
                *picture, *sine, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(d / "clip.mov"),
            )
            ffmpeg_run(
                *picture, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "silent.mp4")
            )
            (d / "subs.srt").write_text(
                "1\n00:00:00,000 --> 00:00:00,900\nhello\n", encoding="utf-8"
            )
            ffmpeg_run(
                *picture, *sine, *sine, "-i", str(d / "subs.srt"),
                "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "srt",
                "-ac:a:0", "1", "-ac:a:1", "2", "-shortest", str(d / "multi.mkv"),
            )
        except (subprocess.CalledProcessError, OSError) as error:
            raise unittest.SkipTest(f"could not generate test clips: {error}") from error
        channels = [s["channels"] for s in streams(d / "multi.mkv", "audio")]
        if channels != [1, 2]:
            raise unittest.SkipTest(f"could not build a mono and stereo clip: {channels}")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-video-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name).resolve()

    def stage(self, *names: str) -> list[Path]:
        paths = []
        for name in names:
            target = self.work / name
            shutil.copy(self.clips.path(name), target)
            paths.append(target)
        return paths

    def convert(self, *args: object):  # noqa: ANN201
        return runner.invoke(app, ["video", "convert", *map(str, args)])

    def ok(self, *args: object):  # noqa: ANN201
        result = self.convert(*args)
        self.assertEqual(result.exit_code, 0, describe(result))
        return result

    def leftovers(self) -> list[str]:
        return [p.name for p in self.work.rglob("jzr-tmp-*")]

    def rotation_in_info(self, path: Path) -> int:
        result = runner.invoke(app, ["video", "info", "--json", str(path)])
        self.assertEqual(result.exit_code, 0, describe(result))
        return json.loads(result.stdout)[0]["video"]["rotation"]

    def test_mov_to_mp4_is_a_copy_with_the_index_at_the_front(self) -> None:
        (mov,) = self.stage("clip.mov")
        result = self.ok(mov, "--to", "mp4")
        out = self.work / "clip.mp4"
        self.assertIn(with_size(f"{mov} -> {out} (video copied, audio copied)", out), result.stdout)
        self.assertEqual(video_stream(out)["codec_name"], "h264")
        self.assertEqual(audio_stream(out)["codec_name"], "aac")
        moov, mdat = box_offset(out, b"moov"), box_offset(out, b"mdat")
        self.assertGreater(moov, 0)
        self.assertGreater(mdat, 0)
        self.assertLess(moov, mdat)
        self.assertEqual(self.leftovers(), [])

    @needs_encoders("libvpx-vp9", "libopus")
    def test_mov_to_webm_is_vp9_with_opus(self) -> None:
        (mov,) = self.stage("clip.mov")
        result = self.ok(mov, "--to", "webm")
        out = self.work / "clip.webm"
        self.assertIn(
            with_size(f"{mov} -> {out} (video encoded to vp9, audio encoded to opus)", out), result.stdout
        )
        self.assertEqual(video_stream(out)["codec_name"], "vp9")
        self.assertEqual(audio_stream(out)["codec_name"], "opus")
        self.assertIn("webm", ffprobe(out)["format"]["format_name"])

    @needs_encoders("libx265")
    def test_hevc_in_mp4_carries_the_hvc1_tag(self) -> None:
        (mov,) = self.stage("clip.mov")
        self.ok(mov, "--to", "mp4", "--codec", "hevc")
        stream = video_stream(self.work / "clip.mp4")
        self.assertEqual(stream["codec_name"], "hevc")
        self.assertEqual(stream["codec_tag_string"], "hvc1")

    def test_a_forced_codec_re_encodes_even_the_sources_own_codec(self) -> None:
        (mov,) = self.stage("clip.mov")
        result = self.ok(mov, "--to", "mp4", "--codec", "h264")
        self.assertIn("video encoded to h264", result.stdout)
        self.assertEqual(video_stream(self.work / "clip.mp4")["codec_name"], "h264")

    def test_mkv_made_from_mp4_carries_no_mp4_brand_tags(self) -> None:
        (mp4,) = self.stage("clip.mp4")
        self.assertIn("major_brand", tags(mp4))
        self.ok(mp4, "--to", "mkv")
        out = self.work / "clip.mkv"
        self.assertEqual(video_stream(out)["codec_name"], "h264")
        self.assertNotIn("major_brand", tags(out))
        self.assertNotIn("compatible_brands", tags(out))

    def test_mp42mp3_leaves_one_mp3_stream_and_nothing_else(self) -> None:
        (mp4,) = self.stage("clip.mp4")
        result = runner.invoke(app, ["video", "mp42mp3", str(mp4)])
        self.assertEqual(result.exit_code, 0, describe(result))
        out = self.work / "clip.mp3"
        found = ffprobe(out)["streams"]
        self.assertEqual([(s["codec_type"], s["codec_name"]) for s in found], [("audio", "mp3")])
        self.assertNotIn("major_brand", tags(out))

    def test_a_video_without_audio_fails_alone_for_an_audio_target(self) -> None:
        silent, mov = self.stage("silent.mp4", "clip.mov")
        result = self.convert(silent, mov, "--to", "mp3")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {silent}: no audio track", err(result))
        self.assertTrue((self.work / "clip.mp3").is_file())
        self.assertFalse((self.work / "silent.mp3").exists())
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())
        self.assertEqual(self.leftovers(), [])

    def test_a_rotated_clip_keeps_its_rotation_when_copied(self) -> None:
        (rotated,) = self.stage("rotated.mp4")
        self.assertEqual(self.rotation_in_info(rotated), 270)
        self.ok(rotated, "--to", "mkv", "--outdir", self.work / "mkv")
        self.ok(rotated, "--to", "mov", "--outdir", self.work / "mov")
        self.assertEqual(self.rotation_in_info(self.work / "mkv" / "rotated.mkv"), 270)
        self.assertEqual(self.rotation_in_info(self.work / "mov" / "rotated.mov"), 270)
        stream = video_stream(self.work / "mov" / "rotated.mov")
        self.assertEqual((stream["width"], stream["height"]), (64, 48))

    def test_a_rotated_clip_is_upright_without_rotation_when_re_encoded(self) -> None:
        (rotated,) = self.stage("rotated.mp4")
        out = self.work / "out" / "rotated.mp4"
        self.ok(rotated, "--to", "mp4", "--codec", "h264", "--outdir", out.parent)
        stream = video_stream(out)
        self.assertEqual((stream["width"], stream["height"]), (48, 64))
        self.assertEqual(self.rotation_in_info(out), 0)

    def test_resize_sets_the_short_edge(self) -> None:
        (mov,) = self.stage("clip.mov")
        self.ok(mov, "--to", "mp4", "--resize", "32")
        stream = video_stream(self.work / "clip.mp4")
        self.assertEqual((stream["width"], stream["height"]), (42, 32))

    def test_a_resize_larger_than_the_picture_changes_nothing(self) -> None:
        (mov,) = self.stage("clip.mov")
        result = self.ok(mov, "--to", "mp4", "--resize", "480")
        stream = video_stream(self.work / "clip.mp4")
        self.assertEqual((stream["width"], stream["height"]), (64, 48))
        self.assertIn("video copied", result.stdout)
        self.assertIn("not resized", result.stdout)

    def test_fps_sets_the_frame_rate(self) -> None:
        (mov,) = self.stage("clip.mov")
        self.ok(mov, "--to", "mp4", "--fps", "5")
        stream = video_stream(self.work / "clip.mp4")
        self.assertEqual(stream["avg_frame_rate"], "5/1")

    def test_no_audio_leaves_no_audio_stream(self) -> None:
        (mov,) = self.stage("clip.mov")
        result = self.ok(mov, "--to", "mp4", "--no-audio")
        out = self.work / "clip.mp4"
        self.assertEqual(streams(out, "audio"), [])
        self.assertEqual(video_stream(out)["codec_name"], "h264")
        self.assertIn("audio dropped", result.stdout)

    def test_the_stereo_track_is_kept_and_the_rest_is_named_as_dropped(self) -> None:
        (multi,) = self.stage("multi.mkv")
        result = self.ok(multi, "--to", "mp4")
        out = self.work / "multi.mp4"
        kept = streams(out, "audio")
        self.assertEqual([s["channels"] for s in kept], [2])
        self.assertEqual(streams(out, "subtitle"), [])
        note = result.stdout
        self.assertIn("dropped:", note)
        self.assertIn("audio stream 1", note)
        self.assertNotIn("audio stream 2", note)
        self.assertIn("subtitle", note.lower())

    def test_awkward_file_names_convert(self) -> None:
        for name in mp4_tool_names():
            with self.subTest(name=name):
                source = self.work / name
                shutil.copy(self.clips.path("clip.mp4"), source)
                result = self.ok(source, "--to", "mkv")
                target = self.work / f"{Path(name).stem}.mkv"
                self.assertTrue(target.is_file(), result.stdout)
                self.assertEqual(video_stream(target)["codec_name"], "h264")
        self.assertEqual(self.leftovers(), [])

    def test_a_text_file_named_mov_fails_alone(self) -> None:
        fake = self.work / "fake.mov"
        shutil.copy(self.clips.path("notes.txt"), fake)
        (mp4,) = self.stage("clip.mp4")
        result = self.convert(fake, mp4, "--to", "mkv")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {fake}: not a readable media file", err(result))
        self.assertTrue((self.work / "clip.mkv").is_file())
        self.assertFalse((self.work / "fake.mkv").exists())
        self.assertEqual(self.leftovers(), [])

    def test_scanning_converts_video_and_leaves_audio_and_text_alone(self) -> None:
        self.stage("clip.mov", "clip.mp4", "notes.txt")
        result = self.ok("--dir", self.work, "--to", "mp4")
        self.assertEqual(
            {p.name for p in self.work.iterdir()}, {"clip.mov", "clip.mp4", "notes.txt"}, result.stdout
        )
        self.assertIn("skip", err(result))

    def test_dry_run_writes_nothing(self) -> None:
        (mov,) = self.stage("clip.mov")
        result = self.ok(mov, "--to", "mp4", "--dry-run")
        self.assertIn("faststart", result.stdout)
        self.assertEqual({p.name for p in self.work.iterdir()}, {"clip.mov"})


if __name__ == "__main__":
    unittest.main()
