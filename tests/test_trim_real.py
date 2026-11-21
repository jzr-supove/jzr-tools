"""`jzr video trim` and `jzr audio trim` against the real ffmpeg.

Inputs are generated with ffmpeg's lavfi sources when the class starts. The
video is 64x48, 10 s at 20 frames per second with a keyframe every 2.5 s. The
audio is 8 s of silence with a 1 kHz tone from 3.0 s to 4.0 s, so the place a
cut landed can be found by measuring where the tone starts in the result.
"""

from __future__ import annotations

import array
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

from click.testing import CliRunner

from jzr_tools.cli import app

from media_fixtures import HAVE_FFMPEG, ffmpeg_run
from test_media_cli import describe, err

runner = CliRunner()

FPS = 20
FRAME = 1 / FPS
TONE_AT = 3.0
SAMPLE_RATE = 44100
TONE = (
    f"anullsrc=r={SAMPLE_RATE}:cl=mono:d=3[a];"
    f"sine=frequency=1000:duration=1:sample_rate={SAMPLE_RATE}[b];"
    f"anullsrc=r={SAMPLE_RATE}:cl=mono:d=4[c];"
    "[a][b][c]concat=n=3:v=0:a=1[out]"
)
TAGS = ["-metadata", "title=Song", "-metadata", "artist=Band"]


def ffprobe(path: Path, *extra: str) -> dict:
    completed = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe", "-v", "error", "-of", "json",
            "-show_format", "-show_streams", *extra, "-i", str(path),
        ],
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout.decode("utf-8"))


def stream_of(path: Path, kind: str) -> dict:
    return next(s for s in ffprobe(path)["streams"] if s["codec_type"] == kind)


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


def video_frames(path: Path) -> int:
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


def tone_start(path: Path) -> float:
    """Where the tone begins, in seconds, counted in decoded samples.

    The file is decoded to 16-bit mono WAV and the first sample louder than
    about -30 dBFS is found, so container timestamps and start offsets do not
    matter.
    """
    with tempfile.TemporaryDirectory(prefix="jzr-tone-") as scratch:
        decoded = Path(scratch) / "decoded.wav"
        subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-nostats", "-v", "error",
                "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE),
                "-c:a", "pcm_s16le", str(decoded),
            ],
            capture_output=True,
            check=True,
        )
        with wave.open(str(decoded), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()
    for index, value in enumerate(samples):
        if abs(value) > 1000:
            return index / SAMPLE_RATE
    raise AssertionError(f"no tone found in {path}")


def encoders() -> str:
    completed = subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout


def nearest_source_time(cut: Path, source: Path, candidates: tuple[float, ...]) -> float:
    """Which of the source times ``candidates`` the first picture of ``cut`` looks most like."""
    best, best_score = candidates[0], -1.0
    for at in candidates:
        completed = subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-nostats", "-i", str(cut),
                "-ss", str(at), "-i", str(source), "-frames:v", "1",
                "-lavfi", "[0:v][1:v]psnr", "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        found = re.search(r"average:(\S+)", completed.stderr)
        if found is None:
            raise AssertionError(completed.stderr)
        score = float("inf") if found.group(1) == "inf" else float(found.group(1))
        if score > best_score:
            best, best_score = at, score
    return best


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not on PATH")
class RealTrimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._clips = tempfile.TemporaryDirectory(prefix="jzr-trim-clips-")
        cls.addClassCleanup(cls._clips.cleanup)
        cls.clips = Path(cls._clips.name).resolve()
        cls.built: set[str] = set()
        d = cls.clips
        try:
            ffmpeg_run(
                "-f", "lavfi", "-i", f"testsrc2=size=64x48:rate={FPS}:duration=10",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", str(FPS * 5 // 2),
                "-keyint_min", str(FPS * 5 // 2), "-sc_threshold", "0",
                "-c:a", "aac", "-shortest", str(d / "video.mp4"),
            )
            ffmpeg_run(
                "-f", "lavfi", "-i", "testsrc2=size=64x64", "-frames:v", "1", str(d / "cover.jpg")
            )
            ffmpeg_run("-filter_complex", TONE, "-map", "[out]", "-c:a", "pcm_s16le", str(d / "tone16.wav"))
        except (subprocess.CalledProcessError, OSError) as error:
            detail = getattr(error, "stderr", b"") or b""
            raise unittest.SkipTest(
                f"could not generate test clips: {error} {detail.decode(errors='replace')}"
            ) from error
        base = ["-i", str(d / "tone16.wav")]
        cover = ["-i", str(d / "cover.jpg")]
        available = encoders()
        recipes = {
            "tone24.wav": (base, ["-c:a", "pcm_s24le"], ""),
            "tone.flac": (base, ["-c:a", "flac", *TAGS], ""),
            "tone24.flac": (base, ["-c:a", "flac", "-sample_fmt", "s32"], ""),
            "song.mp3": (
                base, ["-c:a", "libmp3lame", "-b:a", "128k", "-id3v2_version", "3", *TAGS], "libmp3lame"
            ),
            "song.m4a": (base, ["-c:a", "aac", "-b:a", "96k", *TAGS], ""),
            "song.ogg": (base, ["-c:a", "libvorbis", *TAGS], "libvorbis"),
            "song.opus": (base, ["-c:a", "libopus", *TAGS], "libopus"),
            "covered.mp3": (
                base + cover,
                [
                    "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame", "-b:a", "128k", "-c:v", "copy",
                    "-id3v2_version", "3", "-disposition:v", "attached_pic", *TAGS,
                ],
                "libmp3lame",
            ),
            "covered.m4a": (
                base + cover,
                [
                    "-map", "0:a", "-map", "1:v", "-c:a", "aac", "-b:a", "96k", "-c:v", "copy",
                    "-disposition:v", "attached_pic", *TAGS,
                ],
                "",
            ),
        }
        for name, (inputs, options, needs) in recipes.items():
            if needs and needs not in available:
                continue
            try:
                ffmpeg_run(*inputs, *options, str(d / name))
            except (subprocess.CalledProcessError, OSError):
                continue
            cls.built.add(name)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-trim-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name).resolve()
        self.addCleanup(self.assert_no_temporary_files)

    def assert_no_temporary_files(self) -> None:
        self.assertEqual([p.name for p in self.work.rglob("jzr-tmp-*")], [])

    def stage(self, name: str, as_name: str | None = None) -> Path:
        if name not in {"video.mp4", "cover.jpg", "tone16.wav"} and name not in self.built:
            self.skipTest(f"{name} could not be generated with this ffmpeg")
        target = self.work / (as_name or name)
        shutil.copy(self.clips / name, target)
        return target

    def trim(self, group: str, *args: object):  # noqa: ANN201
        return runner.invoke(app, [group, "trim", *map(str, args)])

    def ok(self, group: str, *args: object):  # noqa: ANN201
        result = self.trim(group, *args)
        self.assertEqual(result.exit_code, 0, describe(result))
        return result

    def tag_cover_line(self, path: Path) -> str:
        result = runner.invoke(app, ["audio", "tags", str(path)])
        self.assertEqual(result.exit_code, 0, describe(result))
        (line,) = [l for l in result.stdout.splitlines() if l.strip().startswith("cover")]
        return line

    # --- video ---------------------------------------------------------------

    def test_an_exact_video_cut_lasts_the_asked_time_and_starts_at_the_asked_picture(self) -> None:
        source = self.stage("video.mp4")
        result = self.ok("video", source, "--start", "3.5", "--duration", "3")
        out = self.work / "video_trim.mp4"
        self.assertIn("video encoded to h264, audio copied", result.stdout)
        video = stream_of(out, "video")
        self.assertEqual(video["codec_name"], "h264")
        self.assertAlmostEqual(float(video["duration"]), 3.0, delta=FRAME)
        self.assertAlmostEqual(video_frames(out), 3 * FPS, delta=1)
        self.assertEqual(nearest_source_time(out, source, (2.5, 3.5)), 3.5)
        self.assertEqual(stream_of(out, "audio")["codec_name"], "aac")

    def test_an_exact_cut_to_the_end_of_the_file_runs_to_the_end(self) -> None:
        source = self.stage("video.mp4")
        self.ok("video", source, "--start", "8")
        self.assertAlmostEqual(video_frames(self.work / "video_trim.mp4"), 2 * FPS, delta=1)

    def test_a_cut_from_the_beginning_keeps_the_first_picture(self) -> None:
        source = self.stage("video.mp4")
        self.ok("video", source, "--end", "2")
        out = self.work / "video_trim.mp4"
        self.assertAlmostEqual(video_frames(out), 2 * FPS, delta=1)
        self.assertEqual(nearest_source_time(out, source, (0.0, 1.0)), 0.0)

    def test_a_fast_cut_starts_at_the_keyframe_before_the_start_and_says_so(self) -> None:
        source = self.stage("video.mp4")
        result = self.ok("video", source, "--start", "3.5", "--duration", "3", "--fast")
        out = self.work / "video_trim.mp4"
        self.assertIn("video copied, audio copied", result.stdout)
        self.assertRegex(result.stdout, r"starts at 0:02\.[45]\d\d, the keyframe before 0:03\.500")
        video = stream_of(out, "video")
        self.assertEqual(video["codec_name"], "h264")
        # The keyframe is 1 s before the asked start, and the cut still ends at the asked end.
        self.assertAlmostEqual(float(video["duration"]), 4.0, delta=0.15)
        self.assertAlmostEqual(video_frames(out), 4 * FPS, delta=3)
        self.assertEqual(nearest_source_time(out, source, (2.5, 3.5)), 2.5)
        self.assertLess(float(ffprobe(out)["format"]["start_time"]), 0.1)

    def test_a_fast_cut_starting_on_a_keyframe_is_as_long_as_asked(self) -> None:
        source = self.stage("video.mp4")
        result = self.ok("video", source, "--start", "5", "--duration", "2.5", "--fast")
        out = self.work / "video_trim.mp4"
        self.assertNotIn("starts at", result.stdout)
        self.assertAlmostEqual(float(stream_of(out, "video")["duration"]), 2.5, delta=0.15)
        self.assertAlmostEqual(video_frames(out), int(2.5 * FPS), delta=2)

    def test_exact_and_fast_cuts_of_one_range_differ_by_the_pre_roll(self) -> None:
        source = self.stage("video.mp4")
        self.ok("video", source, "--start", "3.5", "--duration", "3", "--outdir", self.work / "exact")
        self.ok(
            "video", source, "--start", "3.5", "--duration", "3", "--fast",
            "--outdir", self.work / "fast",
        )
        exact = self.work / "exact" / "video_trim.mp4"
        fast = self.work / "fast" / "video_trim.mp4"
        self.assertAlmostEqual(video_frames(fast) - video_frames(exact), FPS, delta=3)
        self.assertGreater(
            float(stream_of(fast, "video")["duration"]),
            float(stream_of(exact, "video")["duration"]) + 0.5,
        )
        self.assertEqual(nearest_source_time(exact, source, (2.5, 3.5)), 3.5)
        self.assertEqual(nearest_source_time(fast, source, (2.5, 3.5)), 2.5)

    def test_a_dry_run_writes_nothing(self) -> None:
        source = self.stage("video.mp4")
        result = self.ok("video", source, "--start", "3", "--end", "5", "--dry-run")
        self.assertIn("-ss", result.stdout)
        self.assertEqual({p.name for p in self.work.iterdir()}, {"video.mp4"})

    def test_a_start_past_the_end_names_the_files_duration_and_writes_nothing(self) -> None:
        source = self.stage("video.mp4")
        other = self.stage("video.mp4", "other.mp4")
        result = self.trim("video", source, other, "--start", "12")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("is past the end of the file (0:10.00)", err(result))
        self.assertFalse(any(p.name.endswith("_trim.mp4") for p in self.work.iterdir()))

    def test_a_start_past_the_end_fails_one_file_while_another_is_trimmed(self) -> None:
        short = self.work / "short.mp4"
        ffmpeg_run(
            "-i", str(self.clips / "video.mp4"), "-t", "2", "-c", "copy", str(short)
        )
        long = self.stage("video.mp4", "long.mp4")
        result = self.trim("video", short, long, "--start", "5", "--end", "7")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {short}: --start 0:05.000 is past the end of the file", err(result))
        self.assertTrue((self.work / "long_trim.mp4").is_file())
        self.assertFalse((self.work / "short_trim.mp4").exists())
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())

    def test_a_file_name_with_cyrillic_letters_spaces_and_a_percent_sign_works(self) -> None:
        video = self.stage("video.mp4", "мой клип 100% готов.mp4")
        song = self.stage("song.mp3" if "song.mp3" in self.built else "tone16.wav", None)
        song = song.rename(self.work / f"моя песня 5% тест{song.suffix}")
        self.ok("video", video, "--start", "1", "--duration", "2")
        out = self.work / "мой клип 100% готов_trim.mp4"
        self.assertTrue(out.is_file())
        self.assertAlmostEqual(video_frames(out), 2 * FPS, delta=1)
        self.ok("audio", song, "--start", "1", "--duration", "2")
        audio_out = self.work / f"моя песня 5% тест_trim{song.suffix}"
        self.assertTrue(audio_out.is_file())
        self.assertAlmostEqual(float(ffprobe(audio_out)["format"]["duration"]), 2.0, delta=0.1)

    def test_a_broken_file_fails_alone_while_a_good_one_is_trimmed(self) -> None:
        broken = self.work / "broken.mp4"
        broken.write_bytes(bytes(range(256)) * 64)
        good = self.stage("video.mp4")
        result = self.trim("video", broken, good, "--start", "1", "--duration", "2")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {broken}:", err(result))
        self.assertEqual(err(result).count("fail "), 1, describe(result))
        self.assertTrue((self.work / "video_trim.mp4").is_file())
        self.assertFalse((self.work / "broken_trim.mp4").exists())
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())

    def test_an_audio_file_is_refused_by_video_trim_and_a_video_by_audio_trim(self) -> None:
        song = self.stage("tone16.wav")
        video = self.stage("video.mp4")
        result = self.trim("video", song, "--start", "1")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {song}: no video track; use 'jzr audio trim' for it", err(result))
        result = self.trim("audio", video, "--start", "1")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {video}: a video file; use 'jzr video trim' for it", err(result))
        self.assertEqual(
            {p.name for p in self.work.iterdir()}, {"tone16.wav", "video.mp4"}
        )

    def test_a_scan_leaves_earlier_results_alone(self) -> None:
        self.stage("video.mp4")
        self.ok("video", "--dir", self.work, "--start", "1", "--duration", "1")
        self.assertEqual({p.name for p in self.work.iterdir()}, {"video.mp4", "video_trim.mp4"})
        result = self.ok("video", "--dir", self.work, "--start", "2", "--duration", "1")
        self.assertIn("skip", err(result))
        self.assertEqual({p.name for p in self.work.iterdir()}, {"video.mp4", "video_trim.mp4"})

    # --- audio: lossless -----------------------------------------------------

    def assert_tone_at(self, path: Path, expected: float, tolerance: float) -> None:
        self.assertAlmostEqual(tone_start(path), expected, delta=tolerance, msg=str(path))

    def test_a_wav_cut_is_sample_exact(self) -> None:
        source = self.stage("tone16.wav")
        result = self.ok("audio", source, "--start", "2.5", "--duration", "1.25")
        out = self.work / "tone16_trim.wav"
        self.assertIn("cut to the sample", result.stdout)
        audio = stream_of(out, "audio")
        self.assertEqual(audio["codec_name"], "pcm_s16le")
        self.assertEqual(int(audio["duration_ts"]), int(1.25 * SAMPLE_RATE))
        self.assert_tone_at(out, TONE_AT - 2.5, 0.002)

    def test_a_wav_cut_from_the_beginning_is_sample_exact(self) -> None:
        source = self.stage("tone16.wav")
        self.ok("audio", source, "--end", "3.5")
        out = self.work / "tone16_trim.wav"
        self.assertEqual(int(stream_of(out, "audio")["duration_ts"]), int(3.5 * SAMPLE_RATE))
        self.assert_tone_at(out, TONE_AT, 0.002)

    def test_a_24_bit_wav_keeps_its_codec_and_depth(self) -> None:
        source = self.stage("tone24.wav")
        self.ok("audio", source, "--start", "2", "--end", "6")
        out = self.work / "tone24_trim.wav"
        audio = stream_of(out, "audio")
        self.assertEqual(audio["codec_name"], "pcm_s24le")
        self.assertEqual(audio["bits_per_raw_sample"], "24")
        self.assertEqual(int(audio["duration_ts"]), 4 * SAMPLE_RATE)
        self.assert_tone_at(out, TONE_AT - 2, 0.002)

    def test_a_flac_cut_is_sample_exact_and_keeps_its_tags(self) -> None:
        source = self.stage("tone.flac")
        self.ok("audio", source, "--start", "2.5", "--duration", "1.25")
        out = self.work / "tone_trim.flac"
        audio = stream_of(out, "audio")
        self.assertEqual(audio["codec_name"], "flac")
        self.assertEqual(int(audio["duration_ts"]), int(1.25 * SAMPLE_RATE))
        self.assert_tone_at(out, TONE_AT - 2.5, 0.002)
        self.assertEqual(tags(out).get("title"), "Song")
        self.assertEqual(tags(out).get("artist"), "Band")

    def test_a_24_bit_flac_keeps_its_depth(self) -> None:
        source = self.stage("tone24.flac")
        self.ok("audio", source, "--start", "2", "--end", "6")
        audio = stream_of(self.work / "tone24_trim.flac", "audio")
        self.assertEqual(audio["codec_name"], "flac")
        self.assertEqual(audio["bits_per_raw_sample"], "24")
        self.assertEqual(int(audio["duration_ts"]), 4 * SAMPLE_RATE)

    def test_exact_on_a_wav_changes_nothing_and_says_so(self) -> None:
        source = self.stage("tone16.wav")
        result = self.ok("audio", source, "--start", "2", "--end", "6", "--exact")
        self.assertIn("--exact changes nothing: .wav is always cut to the sample", result.stdout)
        out = self.work / "tone16_trim.wav"
        self.assertEqual(int(stream_of(out, "audio")["duration_ts"]), 4 * SAMPLE_RATE)

    # --- audio: lossy copies -------------------------------------------------

    def test_lossy_copies_land_within_30_ms_and_keep_title_and_artist(self) -> None:
        for name, codec in (
            ("song.mp3", "mp3"), ("song.m4a", "aac"), ("song.ogg", "vorbis"), ("song.opus", "opus"),
        ):
            source = self.stage(name)
            source_tone = tone_start(source)
            for start in (2.0, 2.02, 2.04, 2.07):
                with self.subTest(name=name, start=start):
                    out_dir = self.work / f"out-{source.suffix.lstrip('.')}-{start}"
                    result = self.ok(
                        "audio", source, "--start", start, "--end", start + 4, "--outdir", out_dir
                    )
                    out = out_dir / f"{source.stem}_trim{source.suffix}"
                    self.assertIn("(audio copied)", result.stdout)
                    self.assertEqual(stream_of(out, "audio")["codec_name"], codec)
                    self.assertAlmostEqual(tone_start(out), source_tone - start, delta=0.03)
                    self.assertAlmostEqual(
                        float(ffprobe(out)["format"]["duration"]), 4.0, delta=0.1
                    )
                    self.assertEqual(tags(out).get("title"), "Song")
                    self.assertEqual(tags(out).get("artist"), "Band")

    def test_an_mp3_and_an_m4a_with_cover_art_keep_it(self) -> None:
        for name in ("covered.mp3", "covered.m4a"):
            with self.subTest(name=name):
                source = self.stage(name)
                self.assertTrue(has_picture(source))
                out_dir = self.work / f"out-{source.suffix.lstrip('.')}"
                self.ok("audio", source, "--start", "2", "--end", "6", "--outdir", out_dir)
                out = out_dir / f"covered_trim{source.suffix}"
                self.assertTrue(has_picture(out), ffprobe(out)["streams"])
                self.assertEqual(tags(out).get("title"), "Song")
                self.assertAlmostEqual(tone_start(out), tone_start(source) - 2, delta=0.03)

    def test_opus_ogg_and_flac_files_keep_a_cover_written_with_audio_tags(self) -> None:
        for name in ("song.opus", "song.ogg", "tone.flac"):
            with self.subTest(name=name):
                source = self.stage(name, f"cover-{name}")
                tagged = runner.invoke(
                    app, ["audio", "tags", str(source), "--cover", str(self.clips / "cover.jpg")]
                )
                self.assertEqual(tagged.exit_code, 0, describe(tagged))
                self.assertRegex(self.tag_cover_line(source), r"image/jpeg")
                out_dir = self.work / f"out-{source.suffix.lstrip('.')}"
                self.ok("audio", source, "--start", "2", "--end", "6", "--outdir", out_dir)
                out = out_dir / f"{source.stem}_trim{source.suffix}"
                self.assertRegex(self.tag_cover_line(out), r"image/jpeg")
                self.assertEqual(tags(out).get("title"), "Song")

    def test_exact_on_an_mp3_gives_an_mp3_at_about_the_source_bitrate(self) -> None:
        source = self.stage("song.mp3")
        result = self.ok("audio", source, "--start", "2", "--end", "6", "--exact")
        out = self.work / "song_trim.mp3"
        audio = stream_of(out, "audio")
        self.assertEqual(audio["codec_name"], "mp3")
        wanted = int(stream_of(source, "audio")["bit_rate"])
        self.assertAlmostEqual(int(audio["bit_rate"]), wanted, delta=wanted * 0.1)
        self.assertRegex(result.stdout, r"audio encoded to mp3 at \d+ kb/s")
        self.assertAlmostEqual(float(ffprobe(out)["format"]["duration"]), 4.0, delta=0.1)
        self.assertAlmostEqual(tone_start(out), tone_start(source) - 2, delta=0.03)
        self.assertEqual(tags(out).get("title"), "Song")


if __name__ == "__main__":
    unittest.main()
