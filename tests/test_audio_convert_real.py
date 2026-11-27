"""`jzr audio convert` against the real ffmpeg: files are made, converted and probed.

Inputs are generated one second long with ffmpeg's lavfi sources when the
class starts.
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

from media_fixtures import HAVE_FFMPEG, Clips, ffmpeg_run
from size_fixtures import with_size
from test_media_cli import describe, err
from test_video_convert_real import encoders

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


def audio_stream(path: Path) -> dict:
    return next(s for s in ffprobe(path)["streams"] if s["codec_type"] == "audio")


def tags(path: Path) -> dict[str, str]:
    data = ffprobe(path)
    found = {k.lower(): v for k, v in data["format"].get("tags", {}).items()}
    for stream in data["streams"]:
        for key, value in stream.get("tags", {}).items():
            found.setdefault(key.lower(), value)
    return found


def has_picture(path: Path) -> bool:
    return any(
        s.get("disposition", {}).get("attached_pic") == 1 for s in ffprobe(path)["streams"]
    )


def has_video(path: Path) -> bool:
    return any(
        s["codec_type"] == "video" and s.get("disposition", {}).get("attached_pic") != 1
        for s in ffprobe(path)["streams"]
    )


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not on PATH")
class RealAudioConvertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.clips = Clips()
        cls.addClassCleanup(cls.clips.close)
        if cls.clips.error:
            raise unittest.SkipTest(cls.clips.error)
        d = cls.clips.dir
        sine = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
        try:
            ffmpeg_run(*sine, "-c:a", "pcm_s16le", str(d / "tone16.wav"))
            ffmpeg_run(*sine, "-c:a", "pcm_s24le", str(d / "tone24.wav"))
            ffmpeg_run(*sine, "-c:a", "aac", str(d / "song.m4a"))
            ffmpeg_run(
                "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "silent.mp4"),
            )
        except (subprocess.CalledProcessError, OSError) as error:
            raise unittest.SkipTest(f"could not generate test clips: {error}") from error

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-audio-")
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
        return runner.invoke(app, ["audio", "convert", *map(str, args)])

    def ok(self, *args: object):  # noqa: ANN201
        result = self.convert(*args)
        self.assertEqual(result.exit_code, 0, describe(result))
        return result

    def leftovers(self) -> list[str]:
        return [p.name for p in self.work.rglob("jzr-tmp-*")]

    def test_wav_to_mp3_and_back_to_wav(self) -> None:
        (wav,) = self.stage("tone16.wav")
        self.ok(wav, "--to", "mp3")
        mp3 = self.work / "tone16.mp3"
        self.assertEqual(audio_stream(mp3)["codec_name"], "mp3")
        self.ok(mp3, "--to", "wav", "--outdir", self.work / "back")
        back = self.work / "back" / "tone16.wav"
        self.assertEqual(audio_stream(back)["codec_name"], "pcm_s16le")
        self.assertAlmostEqual(float(ffprobe(back)["format"]["duration"]), 1.0, delta=0.25)
        self.assertEqual(self.leftovers(), [])

    def test_wav_to_flac_and_back_keeps_24_bits(self) -> None:
        (wav,) = self.stage("tone24.wav")
        self.ok(wav, "--to", "flac")
        flac = self.work / "tone24.flac"
        self.assertEqual(audio_stream(flac)["codec_name"], "flac")
        self.ok(flac, "--to", "wav", "--outdir", self.work / "back")
        self.assertEqual(
            audio_stream(self.work / "back" / "tone24.wav")["codec_name"], "pcm_s24le"
        )

    def test_a_sixteen_bit_wav_to_flac_stays_sixteen_bits(self) -> None:
        (wav,) = self.stage("tone16.wav")
        self.ok(wav, "--to", "flac")
        self.ok(self.work / "tone16.flac", "--to", "wav", "--outdir", self.work / "back")
        self.assertEqual(
            audio_stream(self.work / "back" / "tone16.wav")["codec_name"], "pcm_s16le"
        )

    def test_mp3_to_m4a_and_m4a_to_mp3(self) -> None:
        (mp3,) = self.stage("tagged.mp3")
        self.ok(mp3, "--to", "m4a")
        self.assertEqual(audio_stream(self.work / "tagged.m4a")["codec_name"], "aac")
        out = self.work / "back"
        self.ok(self.work / "tagged.m4a", "--to", "mp3", "--outdir", out)
        converted = out / "tagged.mp3"
        self.assertEqual(audio_stream(converted)["codec_name"], "mp3")
        self.assertNotIn("major_brand", tags(converted))
        self.assertNotIn("compatible_brands", tags(converted))
        self.assertEqual(tags(converted).get("title"), "Song")

    def test_an_m4a_made_for_mp3_carries_no_mp4_brand_tags(self) -> None:
        (m4a,) = self.stage("song.m4a")
        self.assertIn("major_brand", tags(m4a))
        self.ok(m4a, "--to", "mp3")
        self.assertNotIn("major_brand", tags(self.work / "song.mp3"))

    def test_m4a_to_m4a_in_another_folder_is_a_copy(self) -> None:
        (m4a,) = self.stage("song.m4a")
        out = self.work / "out"
        result = self.ok(m4a, "--to", "m4a", "--outdir", out)
        self.assertIn(with_size(f"{m4a} -> {out / 'song.m4a'} (audio copied)", out / "song.m4a"), result.stdout)
        self.assertEqual(audio_stream(out / "song.m4a")["codec_name"], "aac")

    def test_opus_to_mp3_keeps_title_and_artist(self) -> None:
        (opus,) = self.stage("tagged.opus")
        self.ok(opus, "--to", "mp3")
        found = tags(self.work / "tagged.mp3")
        self.assertEqual(found.get("title"), "Opus Song")
        self.assertEqual(found.get("artist"), "Opus Band")

    def test_tags_survive_a_trip_through_flac(self) -> None:
        (mp3,) = self.stage("tagged.mp3")
        self.ok(mp3, "--to", "flac")
        found = tags(self.work / "tagged.flac")
        self.assertEqual(found.get("title"), "Song")
        self.assertEqual(found.get("album"), "Disc")

    def test_cover_art_is_kept_for_flac_and_m4a_and_mp3(self) -> None:
        (mp3,) = self.stage("tagged.mp3")
        self.assertTrue(has_picture(mp3))
        for target in ("flac", "m4a"):
            with self.subTest(target=target):
                result = self.ok(mp3, "--to", target)
                self.assertTrue(has_picture(self.work / f"tagged.{target}"))
                self.assertFalse(has_video(self.work / f"tagged.{target}"))
                self.assertNotIn("cover art dropped", result.stdout)
        self.ok(self.work / "tagged.flac", "--to", "mp3", "--outdir", self.work / "again")
        self.assertTrue(has_picture(self.work / "again" / "tagged.mp3"))

    def test_cover_art_is_dropped_for_opus_with_a_note(self) -> None:
        (mp3,) = self.stage("tagged.mp3")
        result = self.ok(mp3, "--to", "opus")
        out = self.work / "tagged.opus"
        self.assertEqual(audio_stream(out)["codec_name"], "opus")
        self.assertFalse(has_picture(out))
        self.assertIn("cover art dropped: .opus cannot hold it", result.stdout)
        self.assertEqual(tags(out).get("title"), "Song")

    def test_cover_art_is_dropped_for_ogg_and_wav_with_a_note(self) -> None:
        (mp3,) = self.stage("tagged.mp3")
        for target in ("ogg", "wav"):
            with self.subTest(target=target):
                if target == "ogg" and "libvorbis" not in encoders():
                    self.skipTest("ffmpeg lacks the encoder libvorbis")
                result = self.ok(mp3, "--to", target)
                self.assertFalse(has_picture(self.work / f"tagged.{target}"))
                self.assertIn(f"cover art dropped: .{target} cannot hold it", result.stdout)

    def test_a_listed_mp4_converts_to_mp3_with_its_audio_only(self) -> None:
        (clip,) = self.stage("clip.mp4")
        self.ok(clip, "--to", "mp3")
        out = self.work / "clip.mp3"
        self.assertEqual(audio_stream(out)["codec_name"], "mp3")
        self.assertFalse(has_video(out))
        self.assertNotIn("major_brand", tags(out))

    def test_an_mp4_without_audio_fails_alone(self) -> None:
        silent, wav = self.stage("silent.mp4", "tone16.wav")
        result = self.convert(silent, wav, "--to", "mp3")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {silent}: no audio track", err(result))
        self.assertTrue((self.work / "tone16.mp3").is_file())
        self.assertFalse((self.work / "silent.mp3").exists())
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())
        self.assertEqual(self.leftovers(), [])

    def test_a_text_file_fails_alone(self) -> None:
        text, wav = self.stage("notes.txt", "tone16.wav")
        result = self.convert(text, wav, "--to", "mp3")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {text}: not a readable media file", err(result))
        self.assertTrue((self.work / "tone16.mp3").is_file())
        self.assertFalse((self.work / "notes.mp3").exists())
        self.assertEqual(self.leftovers(), [])

    def test_awkward_file_names_convert(self) -> None:
        names = ["with space", "100% real", "-leading dash", "песня"]
        for name in names:
            with self.subTest(name=name):
                source = self.work / f"{name}.wav"
                shutil.copy(self.clips.path("tone16.wav"), source)
                result = self.ok(source, "--to", "mp3")
                target = self.work / f"{name}.mp3"
                self.assertTrue(target.is_file(), result.stdout)
                self.assertEqual(audio_stream(target)["codec_name"], "mp3")
        self.assertEqual(self.leftovers(), [])

    def test_an_existing_target_is_skipped_until_overwrite_is_given(self) -> None:
        (wav,) = self.stage("tone16.wav")
        target = self.work / "tone16.mp3"
        target.write_bytes(b"old")
        result = self.convert(wav, "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {wav}: target exists (use --overwrite)", err(result))
        self.assertEqual(target.read_bytes(), b"old")
        self.ok(wav, "--to", "mp3", "--overwrite")
        self.assertEqual(audio_stream(target)["codec_name"], "mp3")
        self.assertEqual(self.leftovers(), [])

    def test_scanning_converts_audio_and_leaves_video_and_text_alone(self) -> None:
        self.stage("tone16.wav", "plain.flac", "clip.mp4", "notes.txt")
        result = self.ok("--dir", self.work, "--to", "mp3")
        self.assertEqual(
            {p.name for p in self.work.iterdir()},
            {"tone16.wav", "plain.flac", "clip.mp4", "notes.txt", "tone16.mp3", "plain.mp3"},
            result.stdout,
        )

    def test_output_writes_exactly_that_file(self) -> None:
        (wav,) = self.stage("tone16.wav")
        target = self.work / "deep" / "x.mp3"
        self.ok(wav, "-o", target)
        self.assertEqual(audio_stream(target)["codec_name"], "mp3")

    def test_the_target_format_can_come_from_the_output_extension(self) -> None:
        (wav,) = self.stage("tone16.wav")
        self.ok(wav, "--output", self.work / "z.flac")
        self.assertEqual(audio_stream(self.work / "z.flac")["codec_name"], "flac")

    def test_a_bitrate_changes_the_size_of_the_mp3(self) -> None:
        (wav,) = self.stage("tone16.wav")
        self.ok(wav, "--to", "mp3", "--bitrate", "32k", "--outdir", self.work / "low")
        self.ok(wav, "--to", "mp3", "--bitrate", "256k", "--outdir", self.work / "high")
        low = (self.work / "low" / "tone16.mp3").stat().st_size
        high = (self.work / "high" / "tone16.mp3").stat().st_size
        self.assertLess(low, high)

    def test_a_pair_command_converts_like_convert(self) -> None:
        (wav,) = self.stage("tone16.wav")
        result = runner.invoke(app, ["audio", "wav2flac", str(wav)])
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(audio_stream(self.work / "tone16.flac")["codec_name"], "flac")

    def test_dry_run_writes_nothing(self) -> None:
        (wav,) = self.stage("tone16.wav")
        result = self.ok(wav, "--to", "mp3", "--dry-run")
        self.assertIn("libmp3lame", result.stdout)
        self.assertEqual({p.name for p in self.work.iterdir()}, {"tone16.wav"})


if __name__ == "__main__":
    unittest.main()
