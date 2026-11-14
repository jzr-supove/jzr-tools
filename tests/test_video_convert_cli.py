"""CLI behaviour of `jzr video convert` and `jzr video <from>2<to>` against stand-in tools.

The stand-in ffprobe answers every call with the same JSON, and the stand-in
ffmpeg writes whatever output path it is given, so each test describes the
source it needs and checks the commands that were started.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app

from media_fixtures import recorded_calls
from test_audio_convert_cli import FLAGS, make_tool, plan_of, source_of
from test_media_cli import NOT_FOUND, describe, err, run_without_tools

runner = CliRunner()

MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
TARGETS = "mp4, mov, mkv, webm, mp3, m4a, opus, ogg, flac, wav"


def video_probe(
    video: str = "h264",
    audio: str | None = "aac",
    container: str = MP4_FAMILY,
    *,
    width: int = 64,
    height: int = 48,
) -> str:
    streams: list[dict[str, object]] = [
        {
            "index": 0, "codec_type": "video", "codec_name": video,
            "width": width, "height": height, "avg_frame_rate": "10/1", "pix_fmt": "yuv420p",
        }
    ]
    if audio:
        streams.append(
            {
                "index": 1, "codec_type": "audio", "codec_name": audio,
                "channels": 2, "channel_layout": "stereo", "sample_rate": "44100",
            }
        )
    return json.dumps(
        {"streams": streams, "format": {"format_name": container, "duration": "1.0", "size": "1000"}}
    )


class FakeToolsCase(unittest.TestCase):
    PROBE = video_probe()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        self.dir = root / "clips"
        self.dir.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        self.install()

    def install(self, probe: str | None = None, ffprobe: dict | None = None, ffmpeg: dict | None = None) -> None:
        self.ffprobe = make_tool(
            self.bin, "ffprobe", stdout=self.PROBE if probe is None else probe, **(ffprobe or {})
        )
        self.ffmpeg = make_tool(self.bin, "ffmpeg", **(ffmpeg or {}))

    def touch(self, *names: str) -> list[Path]:
        paths = []
        for name in names:
            path = self.dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"source")
            paths.append(path)
        return paths

    def invoke(self, *args: object):  # noqa: ANN201
        environ = {**os.environ, "JZR_FFMPEG": self.ffmpeg, "JZR_FFPROBE": self.ffprobe}
        with mock.patch.dict(os.environ, environ, clear=True):
            return runner.invoke(app, ["video", *map(str, args)])

    def ffmpeg_calls(self) -> list[list[str]]:
        return [c for c in recorded_calls(self.ffmpeg) if "-i" in c]

    def ffprobe_calls(self) -> list[list[str]]:
        return [c for c in recorded_calls(self.ffprobe) if "-i" in c]

    def names(self) -> set[str]:
        return {p.name for p in self.dir.iterdir()}

    def converted_sources(self) -> set[str]:
        return {Path(source_of(c)).name for c in self.ffmpeg_calls()}


class UsageErrorTests(FakeToolsCase):
    def assert_usage_error(self, result: object, *needles: str) -> None:
        self.assertEqual(result.exit_code, 2, describe(result))
        self.assertNotIn("Traceback", describe(result))
        self.assertNotIn("No such command", err(result))
        for needle in needles:
            self.assertIn(needle, err(result), describe(result))
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertEqual(self.ffprobe_calls(), [])

    def test_gif_and_unknown_targets_are_refused_with_the_list_of_targets(self) -> None:
        (clip,) = self.touch("clip.mp4")
        for value in ("gif", "xyz", "aac"):
            with self.subTest(value=value):
                self.assert_usage_error(
                    self.invoke("convert", clip, "--to", value),
                    f"Unsupported --to format: {value}. Choose from {TARGETS}.",
                )

    def test_a_target_is_required_without_output(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.assert_usage_error(self.invoke("convert", clip), "--to")

    def test_the_target_ignores_case_and_a_leading_dot(self) -> None:
        (clip,) = self.touch("clip.mov")
        for value in ("MKV", ".mkv", ".Mkv"):
            with self.subTest(value=value):
                result = self.invoke("convert", clip, "--to", value, "--overwrite")
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertIn(f"{clip} -> {clip.with_suffix('.mkv')}", result.stdout)

    def test_the_target_is_taken_from_the_output_extension(self) -> None:
        (clip,) = self.touch("clip.mp4")
        target = self.dir / "made" / "clip.webm"
        result = self.invoke("convert", clip, "-o", target)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue(target.is_file())
        (call,) = self.ffmpeg_calls()
        self.assertEqual(Path(call[-1]).suffix, ".webm")
        self.assertIn("libvpx-vp9", plan_of(call))

    def test_output_with_an_unknown_extension_and_no_target_is_refused(self) -> None:
        (clip,) = self.touch("clip.mp4")
        for name in ("noext", "x.xyz", "x.gif"):
            with self.subTest(name=name):
                self.assert_usage_error(self.invoke("convert", clip, "-o", self.dir / name))

    def test_output_takes_one_source_only(self) -> None:
        a, b = self.touch("a.mov", "b.mov")
        self.assert_usage_error(self.invoke("convert", a, b, "-o", self.dir / "x.mp4"))

    def test_output_extension_must_match_the_target(self) -> None:
        (clip,) = self.touch("clip.mov")
        self.assert_usage_error(
            self.invoke("convert", clip, "--to", "mp4", "-o", self.dir / "clip.webm")
        )
        self.assertNotIn("clip.webm", self.names())

    def test_options_that_do_not_fit_the_target_are_usage_errors_with_their_message(self) -> None:
        (clip,) = self.touch("clip.mov")
        cases = [
            (["--to", "mp3", "--codec", "h264"], "--codec applies only to video targets, not .mp3"),
            (["--to", "flac", "--quality", "high"], "--quality applies only to video targets, not .flac"),
            (["--to", "mp3", "--resize", "100"], "--resize applies only to video targets, not .mp3"),
            (["--to", "wav", "--fps", "10"], "--fps applies only to video targets, not .wav"),
            (["--to", "m4a", "--no-audio"], "--no-audio applies only to video targets, not .m4a"),
            (["--to", "mp4", "--bitrate", "128k"], "--bitrate applies only to audio targets, not .mp4"),
            (["--to", "webm", "--bitrate", "128k"], "--bitrate applies only to audio targets, not .webm"),
            (["--to", "webm", "--codec", "h264"], ".webm cannot hold h264; choose from vp9, av1"),
            (["--to", "mov", "--codec", "av1"], ".mov cannot hold av1; choose from h264, hevc"),
            (["--to", "mp4", "--crf", "60"], "--crf for h264 goes from 0 to 51"),
            (["--to", "mp4", "--codec", "hevc", "--crf", "52"], "--crf for hevc goes from 0 to 51"),
            (["--to", "mp4", "--crf", "20", "--quality", "high"], "--crf and --quality cannot be used together"),
        ]
        for args, message in cases:
            with self.subTest(args=args):
                self.assert_usage_error(self.invoke("convert", clip, *args), message)

    def test_a_crf_inside_the_codecs_range_is_accepted(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = self.invoke("convert", clip, "--to", "webm", "--crf", "60")
        self.assertEqual(result.exit_code, 0, describe(result))
        result = self.invoke("convert", clip, "--to", "mp4", "--crf", "51", "--overwrite")
        self.assertEqual(result.exit_code, 0, describe(result))

    def test_numbers_out_of_range_are_refused_naming_the_option(self) -> None:
        (clip,) = self.touch("clip.mov")
        cases = [
            (["--crf", "64"], "--crf"),
            (["--crf", "-1"], "--crf"),
            (["--resize", "1"], "--resize"),
            (["--resize", "0"], "--resize"),
            (["--fps", "0"], "--fps"),
            (["--fps", "-5"], "--fps"),
            (["--fps", "fast"], "--fps"),
        ]
        for args, option in cases:
            with self.subTest(args=args):
                self.assert_usage_error(
                    self.invoke("convert", clip, "--to", "webm", *args), option
                )

    def test_an_unknown_codec_or_quality_is_refused(self) -> None:
        (clip,) = self.touch("clip.mov")
        for args in (["--codec", "mpeg4"], ["--quality", "best"]):
            with self.subTest(args=args):
                self.assert_usage_error(self.invoke("convert", clip, "--to", "mp4", *args))

    def test_from_must_be_a_video_extension(self) -> None:
        (clip,) = self.touch("clip.mov")
        for value in ("wav", "xyz"):
            with self.subTest(value=value):
                self.assert_usage_error(
                    self.invoke("convert", clip, "--from", value, "--to", "mp4"), value
                )

    def test_a_missing_file_is_a_usage_error(self) -> None:
        missing = self.dir / "gone.mov"
        self.assert_usage_error(
            self.invoke("convert", missing, "--to", "mp4"), f"File not found: {missing}"
        )

    def test_bad_options_are_usage_errors_even_when_ffmpeg_is_missing(self) -> None:
        (clip,) = self.touch("clip.mov")
        cases = [
            ["--to", "gif"],
            ["--to", "mp3", "--codec", "h264"],
            ["--to", "webm", "--codec", "h264"],
            ["--to", "mp4", "--crf", "60"],
            ["--to", "mp4", "--resize", "1"],
            ["--to", "mp4", "--fps", "0"],
            ["--to", "mp4", "--bitrate", "128k"],
        ]
        for args in cases:
            with self.subTest(args=args):
                result = run_without_tools(["video", "convert", str(clip), *args])
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertNotIn("No such command", err(result))
                self.assertNotIn("was not found", err(result))


class ScanTests(FakeToolsCase):
    def test_a_target_container_leaves_its_own_files_alone(self) -> None:
        self.touch(
            "a.mov", "b.mkv", "c.mp4", "d.m4v", "e.webm", "f.avi", "g.mpg", "h.3gp",
            "song.wav", "song.mp3", "song.m4a", "song.mka", "notes.txt", "cover.jpg",
        )
        result = self.invoke("convert", "--dir", self.dir, "--to", "mp4")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(
            self.converted_sources(), {"a.mov", "b.mkv", "e.webm", "f.avi", "g.mpg", "h.3gp"}
        )

    def test_other_targets_leave_only_their_own_extension_alone(self) -> None:
        for target, left in (("mkv", {"b.mkv"}), ("mov", {"a.mov"}), ("webm", {"e.webm"})):
            with self.subTest(target=target):
                self.setUp()
                self.touch("a.mov", "b.mkv", "c.mp4", "d.m4v", "e.webm")
                result = self.invoke("convert", "--dir", self.dir, "--to", target)
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertEqual(
                    self.converted_sources(), {"a.mov", "b.mkv", "c.mp4", "d.m4v", "e.webm"} - left
                )

    def test_scanning_for_an_audio_target_picks_video_files_only(self) -> None:
        self.touch("a.mp4", "b.mkv", "song.wav", "song.flac", "notes.txt")
        result = self.invoke("convert", "--dir", self.dir, "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.converted_sources(), {"a.mp4", "b.mkv"})

    def test_from_limits_the_scan_to_one_extension(self) -> None:
        self.touch("a.mov", "b.mkv", "c.avi")
        result = self.invoke("convert", "--dir", self.dir, "--from", "mkv", "--to", "mp4")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.converted_sources(), {"b.mkv"})

    def test_pattern_and_recursive_work_as_in_audio_convert(self) -> None:
        self.touch("take1.mov", "other.mov", "sub/take2.mov")
        self.invoke("convert", "--dir", self.dir, "--to", "mp4", "--pattern", r"^take")
        self.assertEqual(self.converted_sources(), {"take1.mov"})
        self.invoke(
            "convert", "--dir", self.dir, "--to", "mp4", "--pattern", r"take",
            "--recursive", "--overwrite",
        )
        self.assertIn("take2.mov", self.converted_sources())

    def test_nothing_to_convert_says_so_and_exits_1(self) -> None:
        self.touch("only.mp4", "song.wav", "notes.txt")
        result = self.invoke("convert", "--dir", self.dir, "--to", "mp4")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("No matching files.", err(result))
        self.assertEqual(self.ffmpeg_calls(), [])


class ConvertTests(FakeToolsCase):
    def test_success_prints_source_target_and_note(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = self.invoke("convert", clip, "--to", "mp4")
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        self.assertIn(f"{clip} -> {self.dir / 'clip.mp4'} (video copied, audio copied)", lines)
        self.assertIn("ffmpeg: 1 converted", lines)
        self.assertEqual((self.dir / "clip.mp4").read_bytes(), b"converted")
        self.assertEqual(err(result), "")

    def test_an_encode_is_reported_in_the_note(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke("convert", clip, "--to", "webm")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(
            f"{clip} -> {self.dir / 'clip.webm'} (video encoded to vp9, audio encoded to opus)",
            result.stdout,
        )

    def test_the_command_reads_the_source_and_writes_a_temporary_file_beside_the_target(self) -> None:
        (clip,) = self.touch("clip.mov")
        out = self.dir / "out"
        result = self.invoke("convert", clip, "--to", "mkv", "--outdir", out)
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(call[: len(FLAGS)], FLAGS)
        self.assertEqual(source_of(call), str(clip))
        self.assertTrue(Path(call[-1]).name.startswith("jzr-tmp-"))
        self.assertEqual(Path(call[-1]).suffix, ".mkv")
        self.assertEqual(Path(call[-1]).parent, out)
        self.assertTrue((out / "clip.mkv").is_file())

    def test_the_options_reach_the_ffmpeg_command(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = self.invoke(
            "convert", clip, "--to", "mp4", "--codec", "h264", "--crf", "30",
            "--resize", "32", "--fps", "5", "--no-audio",
        )
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        plan = plan_of(call)
        self.assertEqual(plan[plan.index("-crf") + 1], "30")
        self.assertEqual(plan[plan.index("-vf") + 1], "fps=5,scale=42:32")
        self.assertIn("libx264", plan)
        self.assertNotIn("0:1", " ".join(plan))
        self.assertNotIn("-c:a", plan)

    def test_quality_reaches_the_ffmpeg_command(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = self.invoke("convert", clip, "--to", "mp4", "--codec", "h264", "-q", "small")
        self.assertEqual(result.exit_code, 0, describe(result))
        plan = plan_of(self.ffmpeg_calls()[0])
        self.assertEqual(plan[plan.index("-crf") + 1], "28")

    def test_crf_applied_to_a_copy_is_noted(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = self.invoke("convert", clip, "--to", "mp4", "--crf", "20")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn("--crf applies only when re-encoding", result.stdout)

    def test_an_audio_target_makes_the_audio_file_next_to_the_video(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke("convert", clip, "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"{clip} -> {self.dir / 'clip.mp3'} (audio encoded to mp3)", result.stdout)
        (call,) = self.ffmpeg_calls()
        self.assertIn("libmp3lame", plan_of(call))
        self.assertNotIn("0:v", " ".join(plan_of(call)))

    def test_outdir_collects_the_outputs_and_mirrors_subfolders(self) -> None:
        self.touch("a.mov", "sub/b.mov")
        out = self.dir / "out"
        result = self.invoke(
            "convert", "--dir", self.dir, "--recursive", "--to", "mp4", "--outdir", out
        )
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue((out / "a.mp4").is_file())
        self.assertTrue((out / "sub" / "b.mp4").is_file())
        self.assertFalse((self.dir / "a.mp4").exists())

    def test_no_temporary_file_is_left_behind(self) -> None:
        self.touch("a.mov", "b.mov")
        self.invoke("convert", "--dir", self.dir, "--to", "mp4")
        self.assertTrue({"a.mp4", "b.mp4"} <= self.names())
        self.assertEqual({n for n in self.names() if n.startswith("jzr-tmp-")}, set())


class DryRunTests(FakeToolsCase):
    def test_dry_run_prints_the_command_and_the_note_and_writes_nothing(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = self.invoke("convert", clip, "--to", "mp4", "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        (line,) = [l for l in result.stdout.splitlines() if str(clip) in l and "-i" in l]
        words = shlex.split(line)
        self.assertEqual(words[0], self.ffmpeg)
        self.assertEqual(words[words.index("-i") + 1], str(clip))
        self.assertIn("copy", words)
        self.assertEqual(words[-1], str(self.dir / "clip.mp4"))
        self.assertIn("note: video copied, audio copied", err(result).splitlines())
        self.assertEqual(self.names(), {"clip.mov"})
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertEqual(len(self.ffprobe_calls()), 1)

    def test_dry_run_with_output_names_that_path_and_creates_no_folder(self) -> None:
        (clip,) = self.touch("clip.mov")
        target = self.dir / "made" / "x.mp4"
        result = self.invoke("convert", clip, "-o", target, "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(shlex.quote(str(target)), result.stdout)
        self.assertFalse(target.parent.exists())


class SkipAndFailTests(FakeToolsCase):
    def test_an_existing_target_is_skipped_without_probing(self) -> None:
        (clip,) = self.touch("clip.mov")
        (existing,) = self.touch("clip.mp4")
        result = self.invoke("convert", clip, "--to", "mp4")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {clip}: target exists (use --overwrite)", err(result))
        self.assertEqual(existing.read_bytes(), b"source")
        self.assertEqual(self.ffprobe_calls(), [])
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertIn("ffmpeg: 1 skipped", result.stdout)

    def test_overwrite_replaces_the_existing_target(self) -> None:
        (clip,) = self.touch("clip.mov")
        (existing,) = self.touch("clip.mp4")
        result = self.invoke("convert", clip, "--to", "mp4", "--overwrite")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(existing.read_bytes(), b"converted")

    def test_one_unreadable_file_fails_alone_and_the_rest_convert(self) -> None:
        self.install(ffprobe={"fail_marker": "broken", "fail_code": 1})
        good1, bad, good2 = self.touch("a.mov", "broken.mov", "c.mov")
        result = self.invoke("convert", good1, bad, good2, "--to", "mp4")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(err(result).count("fail "), 1, describe(result))
        self.assertIn(f"fail {bad}: not a readable media file", err(result))
        self.assertIn("ffmpeg: 2 converted, 1 failed", result.stdout.splitlines())
        self.assertEqual(self.names(), {"a.mov", "a.mp4", "broken.mov", "c.mov", "c.mp4"})

    def test_an_ffmpeg_failure_reports_its_reason_and_leaves_nothing(self) -> None:
        self.install(ffmpeg={"code": 1, "stderr": "Error while encoding: disk is full\n"})
        (clip,) = self.touch("clip.mov")
        result = self.invoke("convert", clip, "--to", "mp4")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {clip}: Error while encoding: disk is full", err(result))
        self.assertEqual(self.names(), {"clip.mov"})

    def test_an_ffmpeg_failure_on_one_file_does_not_stop_the_others(self) -> None:
        self.install(ffmpeg={"fail_marker": "broken", "fail_stderr": "Conversion exploded\n"})
        good, bad = self.touch("a.mov", "broken.mov")
        result = self.invoke("convert", bad, good, "--to", "mp4")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {bad}: Conversion exploded", err(result))
        self.assertTrue((self.dir / "a.mp4").is_file())
        self.assertFalse((self.dir / "broken.mp4").exists())

    def test_a_video_without_audio_fails_alone_for_an_audio_target(self) -> None:
        self.install(video_probe(audio=None))
        (clip,) = self.touch("silent.mp4")
        result = self.invoke("convert", clip, "--to", "mp3")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {clip}: no audio track", err(result))
        self.assertEqual(self.ffmpeg_calls(), [])

    def test_a_source_that_is_its_own_target_is_skipped(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke("convert", clip, "--to", "mp4")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {clip}: source is the target", err(result))
        self.assertEqual(self.ffprobe_calls(), [])


class BitrateCaveatTests(FakeToolsCase):
    def test_a_lossless_audio_target_says_the_bitrate_is_ignored(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke("convert", clip, "--to", "flac", "--bitrate", "128k")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn("note: .flac is lossless; --bitrate is ignored", err(result))
        self.assertNotIn("-b:a", " ".join(" ".join(c) for c in self.ffmpeg_calls()))

    def test_a_lossy_audio_target_says_nothing_about_lossless(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke("convert", clip, "--to", "mp3", "--bitrate", "128k")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertNotIn("lossless", err(result))


class ToolLookupTests(FakeToolsCase):
    def test_without_ffmpeg_and_without_a_terminal_the_install_command_is_named(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = run_without_tools(["video", "convert", str(clip), "--to", "mp4"])
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(err(result).splitlines()[0], NOT_FOUND.format("ffmpeg"), describe(result))
        self.assertEqual(err(result).count("was not found"), 1, describe(result))
        self.assertNotIn("Traceback", describe(result))
        self.assertEqual(self.names(), {"clip.mov"})

    def test_a_pair_command_reports_missing_tools_the_same_way(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = run_without_tools(["video", "mov2mp4", str(clip)])
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(err(result).splitlines()[0], NOT_FOUND.format("ffmpeg"), describe(result))


class PairCommandTests(FakeToolsCase):
    def test_mov2mp4_converts_mov_files_only(self) -> None:
        self.touch("a.mov", "b.mkv", "c.avi")
        result = self.invoke("mov2mp4", "--dir", self.dir)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.converted_sources(), {"a.mov"})
        self.assertTrue((self.dir / "a.mp4").is_file())

    def test_a_listed_source_may_leave_its_extension_off(self) -> None:
        self.touch("clip.mov")
        result = self.invoke("mov2mp4", self.dir / "clip")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"{self.dir / 'clip.mov'} -> {self.dir / 'clip.mp4'}", result.stdout)

    def test_mp42mp3_takes_the_audio_out_of_mp4_files(self) -> None:
        self.touch("a.mp4", "b.mov")
        result = self.invoke("mp42mp3", "--dir", self.dir)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.converted_sources(), {"a.mp4"})
        self.assertTrue((self.dir / "a.mp3").is_file())

    def test_pairs_support_video_options_output_and_dry_run(self) -> None:
        (clip,) = self.touch("clip.mkv")
        result = self.invoke(
            "mkv2webm", clip, "--crf", "40", "-o", self.dir / "o" / "x.webm", "--dry-run"
        )
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn("libvpx-vp9", result.stdout)
        self.assertFalse((self.dir / "o").exists())

    def test_the_documented_pairs_resolve(self) -> None:
        for pair in ("mov2mp4", "mkv2webm", "mp42mp3", "avi2mkv", "webm2mp4", "m4v2mov", "MOV2MP4"):
            with self.subTest(pair=pair):
                result = self.invoke(pair, "--help")
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertIn("Usage:", result.stdout)

    def test_names_that_are_not_pairs_are_no_such_command(self) -> None:
        for pair in ("mp42gif", "wav2mp3", "mov2xyz", "foo2mp4", "mp42", "mov2gif"):
            with self.subTest(pair=pair):
                result = self.invoke(pair, "--help")
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertIn("No such command", err(result))

    def test_a_pair_has_no_to_or_from_option(self) -> None:
        result = self.invoke("mov2mp4", "--help")
        options = result.stdout.split("Options:", 1)[1]
        self.assertNotIn("--to", options)
        self.assertNotIn("--from", options)
        self.assertIn("--crf", options)
        self.assertIn("--resize", options)

    def test_a_listed_file_of_another_extension_is_a_usage_error(self) -> None:
        (clip,) = self.touch("clip.mkv")
        result = self.invoke("mov2mp4", clip)
        self.assertEqual(result.exit_code, 2, describe(result))
        self.assertNotIn("No such command", err(result))
        self.assertEqual(self.ffprobe_calls(), [])
        self.assertEqual(self.ffmpeg_calls(), [])

    def test_a_pair_for_an_audio_target_refuses_video_options_that_do_not_fit(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke("mp42mp3", clip, "--codec", "h264")
        self.assertEqual(result.exit_code, 2, describe(result))
        self.assertIn("--codec applies only to video targets, not .mp3", err(result))


class HelpTests(FakeToolsCase):
    def test_convert_help_lists_the_video_options(self) -> None:
        result = self.invoke("convert", "--help")
        self.assertEqual(result.exit_code, 0, describe(result))
        for option in (
            "--to", "--from", "--dir", "--pattern", "--recursive", "--output", "--outdir",
            "--overwrite", "--dry-run", "--codec", "--quality", "--crf", "--resize", "--fps",
            "--no-audio", "--bitrate",
        ):
            self.assertIn(option, result.stdout)

    def test_the_video_group_lists_convert(self) -> None:
        result = self.invoke("--help")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertRegex(result.stdout, r"(?m)^  convert\s+\S")


if __name__ == "__main__":
    unittest.main()
