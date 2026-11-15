"""CLI behaviour of `jzr video trim` and `jzr audio trim` against stand-in tools.

The stand-in ffprobe answers with a JSON document chosen by the file name (or a
default one), and the stand-in ffmpeg writes whatever output path it is given,
so each test describes the sources it needs and checks the commands that were
started and the lines that were printed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app

from media_fixtures import recorded_calls
from test_media_cli import NOT_FOUND, describe, err, run_without_tools

runner = CliRunner()

KEYFRAME = "jzr_tools.core.ffmpeg.keyframe_before"
MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"

_SCRIPT = r'''
import json, os, sys

spec_path = sys.argv[1]
args = sys.argv[2:]
with open(spec_path, encoding="utf-8") as handle:
    spec = json.load(handle)
with open(spec_path + ".argv", "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")

names = [os.path.basename(arg) for arg in args]
for marker, text in spec.get("by_name", {}).items():
    if any(marker in name for name in names):
        sys.stdout.write(text)
        sys.exit(0)
for arg in args:
    if os.path.basename(arg).startswith("jzr-tmp-"):
        with open(arg, "wb") as handle:
            handle.write(b"trimmed")
sys.stdout.write(spec.get("stdout", ""))
sys.stderr.write(spec.get("stderr", ""))
sys.exit(spec.get("code", 0))
'''


def make_tool(folder: Path, name: str, **spec: object) -> str:
    script = folder / f"{name}_stub.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    spec_file = folder / f"{name}.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    if os.name == "nt":
        launcher = folder / f"{name}.cmd"
        launcher.write_text(
            f'@"{sys.executable}" "{script}" "{spec_file}" %*\r\n', encoding="ascii"
        )
    else:
        launcher = folder / name
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "{spec_file}" "$@"\n',
            encoding="ascii",
        )
        launcher.chmod(0o755)
    return str(launcher)


def video_probe(duration: float = 10.0, *, container: str = MP4_FAMILY) -> str:
    streams = [
        {
            "index": 0, "codec_type": "video", "codec_name": "h264",
            "width": 64, "height": 48, "avg_frame_rate": "20/1", "pix_fmt": "yuv420p",
        },
        {
            "index": 1, "codec_type": "audio", "codec_name": "aac",
            "channels": 2, "channel_layout": "stereo", "sample_rate": "44100",
        },
    ]
    return json.dumps(
        {"streams": streams, "format": {"format_name": container, "duration": str(duration), "size": "1000"}}
    )


def audio_probe(
    codec: str = "mp3", container: str = "mp3", duration: float = 10.0, *, bits: int = 0
) -> str:
    stream: dict[str, object] = {
        "index": 0, "codec_type": "audio", "codec_name": codec,
        "channels": 2, "channel_layout": "stereo", "sample_rate": "44100",
    }
    if bits:
        stream["bits_per_sample"] = bits
    return json.dumps(
        {"streams": [stream], "format": {"format_name": container, "duration": str(duration), "size": "1000"}}
    )


def plan_of(call: list[str]) -> list[str]:
    """The ffmpeg arguments between the source and the output path."""
    return call[call.index("-i") + 2 : -1]


def before_input(call: list[str]) -> list[str]:
    """The ffmpeg arguments that come before ``-i`` (the global flags included)."""
    return call[: call.index("-i")]


def value_after(words: list[str], flag: str) -> str:
    return words[words.index(flag) + 1]


def has_pair(words: list[str], first: str, second: str) -> bool:
    return any(a == first and b == second for a, b in zip(words, words[1:]))


class TrimCase(unittest.TestCase):
    group = "video"
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

    def install(self, probe: str | None = None, by_name: dict[str, str] | None = None) -> None:
        self.ffprobe = make_tool(
            self.bin, "ffprobe",
            stdout=self.PROBE if probe is None else probe, by_name=by_name or {},
        )
        self.ffmpeg = make_tool(self.bin, "ffmpeg")

    def touch(self, *names: str) -> list[Path]:
        paths = []
        for name in names:
            path = self.dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"source")
            paths.append(path)
        return paths

    def invoke(self, *args: object, group: str | None = None):  # noqa: ANN201
        environ = {**os.environ, "JZR_FFMPEG": self.ffmpeg, "JZR_FFPROBE": self.ffprobe}
        with mock.patch.dict(os.environ, environ, clear=True):
            return runner.invoke(app, [group or self.group, "trim", *map(str, args)])

    def ffmpeg_calls(self) -> list[list[str]]:
        return [c for c in recorded_calls(self.ffmpeg) if "-i" in c]

    def ffprobe_calls(self) -> list[list[str]]:
        return [c for c in recorded_calls(self.ffprobe) if "-i" in c]

    def names(self) -> set[str]:
        return {p.name for p in self.dir.iterdir()}

    def trimmed_sources(self) -> set[str]:
        return {Path(c[c.index("-i") + 1]).name for c in self.ffmpeg_calls()}


class UsageErrorTests(TrimCase):
    def assert_usage_error(self, result: object, *needles: str) -> None:
        self.assertEqual(result.exit_code, 2, describe(result))
        self.assertNotIn("Traceback", describe(result))
        self.assertNotIn("No such command", err(result))
        for needle in needles:
            self.assertIn(needle, err(result), describe(result))
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertEqual(self.ffprobe_calls(), [])

    def check_both_groups(self, args_for, *needles: str) -> None:  # noqa: ANN001
        for group in ("video", "audio"):
            with self.subTest(group=group):
                (clip,) = self.touch(f"{group}-clip.mp4" if group == "video" else "song.mp3")
                self.assert_usage_error(self.invoke(*args_for(clip), group=group), *needles)

    def test_a_range_needs_start_end_or_duration(self) -> None:
        self.check_both_groups(lambda clip: [clip], "--start", "--end", "--duration")

    def test_end_and_duration_cannot_be_combined(self) -> None:
        self.check_both_groups(
            lambda clip: [clip, "--end", "5", "--duration", "2"],
            "--end and --duration cannot be used together",
        )

    def test_an_end_not_after_the_start_is_refused_with_both_times(self) -> None:
        self.check_both_groups(
            lambda clip: [clip, "--start", "5", "--end", "3"],
            "--end 0:03.000 is not after --start 0:05.000",
        )
        self.check_both_groups(
            lambda clip: [clip, "--start", "0:05", "--end", "5"],
            "--end 0:05.000 is not after --start 0:05.000",
        )

    def test_an_end_of_zero_is_not_after_the_default_start(self) -> None:
        self.check_both_groups(
            lambda clip: [clip, "--end", "0"], "--end 0:00.000 is not after --start 0:00.000"
        )

    def test_a_duration_of_zero_is_refused(self) -> None:
        self.check_both_groups(
            lambda clip: [clip, "--start", "1", "--duration", "0"], "--duration must be more than 0"
        )

    def test_a_bad_time_is_refused_naming_its_option(self) -> None:
        for option in ("--start", "--end", "--duration"):
            with self.subTest(option=option):
                (clip,) = self.touch("clip.mp4")
                other = ["--start", "0"] if option != "--start" else []
                self.assert_usage_error(self.invoke(clip, *other, option, "1:75"), option)

    def test_a_time_that_is_not_a_number_is_refused(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.assert_usage_error(self.invoke(clip, "--start", "soon"), "--start")

    def test_output_names_one_file_for_one_source(self) -> None:
        a, b = self.touch("a.mp4", "b.mp4")
        self.assert_usage_error(
            self.invoke(a, b, "--start", "1", "-o", self.dir / "x.mp4"), "--output"
        )

    def test_output_cannot_be_combined_with_outdir(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.assert_usage_error(
            self.invoke(clip, "--start", "1", "-o", self.dir / "x.mp4", "--outdir", self.dir / "o"),
            "--output and --outdir cannot be used together",
        )

    def test_output_must_keep_the_source_extension(self) -> None:
        (clip,) = self.touch("clip.mp4")
        target = self.dir / "x.mkv"
        self.assert_usage_error(
            self.invoke(clip, "--start", "1", "-o", target),
            f"--output {target} must keep the source's .mp4; trim does not change the container",
        )
        self.assertNotIn("x.mkv", self.names())

    def test_dir_and_recursive_do_not_go_with_listed_sources(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.assert_usage_error(self.invoke(clip, "--start", "1", "--dir", self.dir), "--dir")
        self.assert_usage_error(self.invoke(clip, "--start", "1", "--recursive"), "--recursive")

    def test_a_listed_file_that_does_not_exist_is_a_usage_error(self) -> None:
        missing = self.dir / "gone.mp4"
        self.assert_usage_error(self.invoke(missing, "--start", "1"), f"File not found: {missing}")

    def test_usage_errors_come_before_the_tools_are_looked_for(self) -> None:
        (clip,) = self.touch("clip.mp4")
        cases = [
            [],
            ["--end", "5", "--duration", "2"],
            ["--start", "5", "--end", "3"],
            ["--duration", "0"],
            ["--start", "1:75"],
        ]
        for group in ("video", "audio"):
            for args in cases:
                with self.subTest(group=group, args=args):
                    result = run_without_tools([group, "trim", str(clip), *args])
                    self.assertEqual(result.exit_code, 2, describe(result))
                    self.assertNotIn("was not found", err(result))
                    self.assertNotIn("No such command", err(result))


class HelpTests(TrimCase):
    def test_the_groups_list_trim(self) -> None:
        for group in ("video", "audio"):
            with self.subTest(group=group):
                result = runner.invoke(app, [group, "--help"])
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertRegex(result.stdout, r"(?m)^  trim\s+\S")

    def test_trim_help_lists_the_options_of_its_group(self) -> None:
        common = (
            "--dir", "--pattern", "--recursive", "--output", "--outdir", "--start", "--end",
            "--duration", "--overwrite", "--dry-run",
        )
        video = runner.invoke(app, ["video", "trim", "--help"])
        audio = runner.invoke(app, ["audio", "trim", "--help"])
        self.assertEqual(video.exit_code, 0, describe(video))
        self.assertEqual(audio.exit_code, 0, describe(audio))
        for option in common:
            self.assertIn(option, video.stdout)
            self.assertIn(option, audio.stdout)
        self.assertIn("--fast", video.stdout)
        self.assertNotIn("--exact", video.stdout)
        self.assertIn("--exact", audio.stdout)
        self.assertNotIn("--fast", audio.stdout)


class TargetTests(TrimCase):
    def test_the_result_goes_beside_the_source_as_stem_trim_ext(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--start", "2", "--end", "6")
        self.assertEqual(result.exit_code, 0, describe(result))
        target = self.dir / "clip_trim.mp4"
        self.assertEqual(target.read_bytes(), b"trimmed")
        self.assertEqual(self.names(), {"clip.mp4", "clip_trim.mp4"})
        (call,) = self.ffmpeg_calls()
        self.assertEqual(Path(call[-1]).parent, self.dir)
        self.assertTrue(Path(call[-1]).name.startswith("jzr-tmp-"))
        self.assertEqual(Path(call[-1]).suffix, ".mp4")

    def test_an_upper_case_extension_is_written_in_lower_case(self) -> None:
        (clip,) = self.touch("CLIP.MP4")
        result = self.invoke(clip, "--start", "2")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn("CLIP_trim.mp4", self.names())

    def test_outdir_collects_the_results(self) -> None:
        (clip,) = self.touch("clip.mp4")
        out = self.dir / "out"
        result = self.invoke(clip, "--start", "2", "--outdir", out)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue((out / "clip_trim.mp4").is_file())
        self.assertNotIn("clip_trim.mp4", self.names())

    def test_output_names_the_target_and_may_differ_in_case_of_extension(self) -> None:
        (clip,) = self.touch("clip.mp4")
        target = self.dir / "made" / "piece.MP4"
        result = self.invoke(clip, "--start", "2", "-o", target)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue(target.is_file())
        (call,) = self.ffmpeg_calls()
        self.assertEqual(Path(call[-1]).parent, target.parent)

    def test_a_directory_scan_with_outdir_mirrors_subfolders(self) -> None:
        self.touch("a.mp4", "sub/b.mp4")
        out = self.dir / "out"
        result = self.invoke("--dir", self.dir, "--recursive", "--start", "1", "--outdir", out)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue((out / "a_trim.mp4").is_file())
        self.assertTrue((out / "sub" / "b_trim.mp4").is_file())

    def test_two_sources_with_one_stem_get_numbered_targets(self) -> None:
        self.touch("take.mp4", "sub/take.mp4")
        out = self.dir / "out"
        result = self.invoke(
            self.dir / "take.mp4", self.dir / "sub" / "take.mp4", "--start", "1", "--outdir", out
        )
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["take_trim-2.mp4", "take_trim.mp4"])


class ScanTests(TrimCase):
    def test_a_video_scan_takes_video_files_and_leaves_earlier_results_out(self) -> None:
        self.touch(
            "a.mp4", "b.mkv", "old_trim.mp4", "older_trim-2.mkv", "song.wav", "song.mp3", "notes.txt"
        )
        result = self.invoke("--dir", self.dir, "--start", "1")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.trimmed_sources(), {"a.mp4", "b.mkv"})
        self.assertTrue({"a_trim.mp4", "b_trim.mkv"} <= self.names())
        self.assertIn("ffmpeg: 2 converted", result.stdout.splitlines())

    def test_an_audio_scan_takes_audio_files_and_leaves_earlier_results_out(self) -> None:
        self.install(audio_probe())
        self.touch(
            "a.mp3", "b.wav", "c.flac", "old_trim.mp3", "older_trim-2.wav", "clip.mp4", "notes.txt"
        )
        result = self.invoke("--dir", self.dir, "--start", "1", group="audio")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.trimmed_sources(), {"a.mp3", "b.wav", "c.flac"})

    def test_pattern_and_recursive_narrow_the_scan(self) -> None:
        self.touch("take1.mp4", "other.mp4", "sub/take2.mp4")
        result = self.invoke(
            "--dir", self.dir, "--recursive", "--pattern", r"take", "--start", "1"
        )
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.trimmed_sources(), {"take1.mp4", "take2.mp4"})

    def test_nothing_found_says_so_and_exits_1(self) -> None:
        self.touch("song.wav", "notes.txt", "only_trim.mp4")
        result = self.invoke("--dir", self.dir, "--start", "1")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("No matching files.", err(result))
        self.assertEqual(self.ffmpeg_calls(), [])

    def test_nothing_found_for_audio_says_so_and_exits_1(self) -> None:
        self.touch("clip.mp4", "notes.txt", "only_trim.mp3")
        result = self.invoke("--dir", self.dir, "--start", "1", group="audio")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("No matching files.", err(result))


class CommandTests(TrimCase):
    def test_success_prints_source_target_and_the_note_in_brackets(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--start", "2", "--end", "6")
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        self.assertIn(
            f"{clip} -> {self.dir / 'clip_trim.mp4'} (video encoded to h264, audio copied)", lines
        )
        self.assertIn("ffmpeg: 1 converted", lines)
        self.assertEqual(err(result), "")

    def test_start_and_end_reach_ffmpeg_as_seek_before_the_input_and_a_length_after(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--start", "0:02.5", "--end", "0:06")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(value_after(before_input(call), "-ss"), "2.5")
        self.assertNotIn("-ss", plan_of(call))
        self.assertEqual(value_after(plan_of(call), "-t"), "3.5")
        self.assertNotIn("-t", before_input(call))

    def test_duration_is_taken_from_the_start(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--start", "2", "--duration", "3")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(value_after(before_input(call), "-ss"), "2")
        self.assertEqual(value_after(plan_of(call), "-t"), "3")

    def test_a_cut_from_the_beginning_has_no_seek(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--end", "0:00.5")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertNotIn("-ss", call)
        self.assertEqual(value_after(plan_of(call), "-t"), "0.5")

    def test_a_cut_to_the_end_has_no_length(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--start", "4")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(value_after(before_input(call), "-ss"), "4")
        self.assertNotIn("-t", plan_of(call))

    def test_an_end_past_the_end_of_the_file_cuts_to_the_end_and_says_so(self) -> None:
        self.install(video_probe(duration=8.0))
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--start", "2", "--end", "0:20")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertNotIn("-t", plan_of(call))
        self.assertIn("the file ends at 0:08.00", result.stdout)

    def test_a_dry_run_prints_the_command_and_writes_nothing(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--start", "0:02", "--end", "0:05", "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        (line,) = [l for l in result.stdout.splitlines() if " -i " in l]
        words = shlex.split(line)
        self.assertEqual(words[0], self.ffmpeg)
        self.assertEqual(words[words.index("-i") + 1], str(clip))
        self.assertLess(words.index("-ss"), words.index("-i"))
        self.assertEqual(words[words.index("-ss") + 1], "2")
        self.assertGreater(words.index("-t"), words.index("-i"))
        self.assertEqual(words[words.index("-t") + 1], "3")
        self.assertEqual(words[-1], str(self.dir / "clip_trim.mp4"))
        self.assertIn("note: video encoded to h264", err(result))
        self.assertEqual(self.names(), {"clip.mp4"})
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertEqual(len(self.ffprobe_calls()), 1)

    def test_a_dry_run_with_output_creates_no_folder(self) -> None:
        (clip,) = self.touch("clip.mp4")
        target = self.dir / "made" / "x.mp4"
        result = self.invoke(clip, "--start", "2", "-o", target, "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(shlex.quote(str(target)), result.stdout)
        self.assertFalse(target.parent.exists())

    def test_no_temporary_file_is_left_behind(self) -> None:
        self.touch("a.mp4", "b.mp4")
        self.invoke("--dir", self.dir, "--start", "1")
        self.assertTrue({"a_trim.mp4", "b_trim.mp4"} <= self.names())
        self.assertEqual({n for n in self.names() if n.startswith("jzr-tmp-")}, set())


class FastTests(TrimCase):
    def invoke_fast(self, clip: Path, keyframe: float | None, *args: object):  # noqa: ANN201
        self.keyframe_calls: list[tuple[str, float, int]] = []

        def fake(path, tool, seconds, stream_index):  # noqa: ANN001, ANN202
            self.keyframe_calls.append((Path(str(path)).name, seconds, stream_index))
            return keyframe

        with mock.patch(KEYFRAME, side_effect=fake):
            return self.invoke(clip, "--fast", *args)

    def test_fast_copies_the_video_and_makes_the_timestamps_start_at_zero(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke_fast(clip, 4.0, "--start", "5", "--end", "8")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        plan = plan_of(call)
        self.assertTrue(has_pair(plan, "-c:v", "copy"), plan)
        self.assertTrue(has_pair(plan, "-c:a", "copy"), plan)
        self.assertTrue(has_pair(plan, "-avoid_negative_ts", "make_zero"), plan)
        self.assertEqual(value_after(before_input(call), "-ss"), "5")
        self.assertEqual(value_after(plan, "-t"), "3")
        self.assertNotIn("libx264", plan)

    def test_the_keyframe_is_looked_up_for_the_video_stream_at_the_start(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.invoke_fast(clip, 4.0, "--start", "5", "--end", "8")
        self.assertEqual(self.keyframe_calls, [("clip.mp4", 5.0, 0)])

    def test_a_start_between_keyframes_is_reported_with_the_real_start(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke_fast(clip, 4.0, "--start", "5", "--end", "8")
        self.assertEqual(result.exit_code, 0, describe(result))
        (line,) = [l for l in result.stdout.splitlines() if l.startswith(str(clip))]
        self.assertTrue(line.startswith(f"{clip} -> {self.dir / 'clip_trim.mp4'} (video copied, audio copied"))
        self.assertIn("starts at 0:04.000, the keyframe before 0:05.000", line)
        self.assertTrue(line.endswith(")"))

    def test_a_start_on_a_keyframe_adds_no_such_note(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke_fast(clip, 5.0, "--start", "5", "--end", "8")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertNotIn("starts at", result.stdout)
        self.assertIn(f"{clip} -> {self.dir / 'clip_trim.mp4'} (video copied, audio copied)", result.stdout)

    def test_an_unknown_keyframe_adds_no_such_note(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke_fast(clip, None, "--start", "5", "--end", "8")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertNotIn("starts at", result.stdout)

    def test_a_cut_from_the_beginning_looks_up_no_keyframe(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke_fast(clip, 0.0, "--end", "3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.keyframe_calls, [])
        (call,) = self.ffmpeg_calls()
        self.assertNotIn("-ss", call)
        self.assertTrue(has_pair(plan_of(call), "-c:v", "copy"))

    def test_without_fast_the_video_is_encoded_and_no_keyframe_is_looked_up(self) -> None:
        (clip,) = self.touch("clip.mp4")
        with mock.patch(KEYFRAME, return_value=4.0) as lookup:
            result = self.invoke(clip, "--start", "5", "--end", "8")
        self.assertEqual(result.exit_code, 0, describe(result))
        lookup.assert_not_called()
        (call,) = self.ffmpeg_calls()
        self.assertIn("libx264", plan_of(call))
        self.assertNotIn("-avoid_negative_ts", plan_of(call))

    def test_the_dry_run_shows_the_fast_command(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke_fast(clip, 4.0, "--start", "5", "--end", "8", "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        (line,) = [l for l in result.stdout.splitlines() if " -i " in l]
        words = shlex.split(line)
        self.assertTrue(has_pair(words, "-c:v", "copy"))
        self.assertTrue(has_pair(words, "-avoid_negative_ts", "make_zero"))
        self.assertIn("starts at 0:04.000", err(result))
        self.assertEqual(self.names(), {"clip.mp4"})


class AudioTrimTests(TrimCase):
    group = "audio"
    PROBE = audio_probe()

    def test_a_lossy_file_is_cut_by_copying(self) -> None:
        (song,) = self.touch("song.mp3")
        result = self.invoke(song, "--start", "2", "--end", "6")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        plan = plan_of(call)
        self.assertTrue(has_pair(plan, "-c:a", "copy"), plan)
        self.assertNotIn("libmp3lame", plan)
        self.assertEqual(value_after(before_input(call), "-ss"), "2")
        self.assertEqual(value_after(plan, "-t"), "4")
        self.assertIn(f"{song} -> {self.dir / 'song_trim.mp3'} (audio copied)", result.stdout)

    def test_exact_encodes_a_lossy_file_again(self) -> None:
        (song,) = self.touch("song.mp3")
        result = self.invoke(song, "--start", "2", "--end", "6", "--exact")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        plan = plan_of(call)
        self.assertIn("libmp3lame", plan)
        self.assertFalse(has_pair(plan, "-c:a", "copy"), plan)
        (line,) = [l for l in result.stdout.splitlines() if l.startswith(str(song))]
        self.assertIn("(audio encoded to mp3", line)

    def test_exact_changes_nothing_for_a_lossless_file_and_says_so(self) -> None:
        self.install(audio_probe("pcm_s16le", "wav", bits=16))
        (song,) = self.touch("tone.wav")
        result = self.invoke(song, "--start", "2", "--end", "6", "--exact")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertTrue(has_pair(plan_of(call), "-c:a", "pcm_s16le"), plan_of(call))
        self.assertIn("--exact changes nothing: .wav is always cut to the sample", result.stdout)
        self.assertIn("cut to the sample", result.stdout)

    def test_a_lossless_file_is_cut_to_the_sample_without_exact(self) -> None:
        self.install(audio_probe("flac", "flac", bits=16))
        (song,) = self.touch("tone.flac")
        result = self.invoke(song, "--start", "2", "--end", "6")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertTrue(has_pair(plan_of(call), "-c:a", "flac"), plan_of(call))
        self.assertIn("cut to the sample", result.stdout)
        self.assertNotIn("--exact changes nothing", result.stdout)

    def test_a_video_file_is_refused_for_audio_trim(self) -> None:
        self.install(by_name={"clip": video_probe()})
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--start", "1")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {clip}: a video file; use 'jzr video trim' for it", err(result))
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertEqual(self.names(), {"clip.mp4"})

    def test_a_video_file_fails_alone(self) -> None:
        self.install(by_name={"clip": video_probe()})
        clip, song = self.touch("clip.mp4", "song.mp3")
        result = self.invoke(clip, song, "--start", "1")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {clip}: a video file; use 'jzr video trim' for it", err(result))
        self.assertTrue((self.dir / "song_trim.mp3").is_file())
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())


class FailureTests(TrimCase):
    def test_an_audio_file_is_refused_for_video_trim_and_the_rest_go_on(self) -> None:
        self.install(by_name={"song": audio_probe()})
        song, clip = self.touch("song.mp3", "clip.mp4")
        result = self.invoke(song, clip, "--start", "1")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {song}: no video track; use 'jzr audio trim' for it", err(result))
        self.assertEqual(err(result).count("fail "), 1, describe(result))
        self.assertTrue((self.dir / "clip_trim.mp4").is_file())
        self.assertFalse((self.dir / "song_trim.mp3").exists())
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())

    def test_a_start_past_the_end_fails_that_file_only(self) -> None:
        self.install(by_name={"short": video_probe(duration=2.0), "long": video_probe(duration=10.0)})
        short, long = self.touch("short.mp4", "long.mp4")
        result = self.invoke(short, long, "--start", "5", "--end", "8")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(
            f"fail {short}: --start 0:05.000 is past the end of the file (0:02.00)", err(result)
        )
        self.assertEqual(self.trimmed_sources(), {"long.mp4"})
        self.assertTrue((self.dir / "long_trim.mp4").is_file())
        self.assertFalse((self.dir / "short_trim.mp4").exists())
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())

    def test_a_start_equal_to_the_duration_is_past_the_end(self) -> None:
        self.install(video_probe(duration=5.0))
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--start", "5")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("is past the end of the file (0:05.00)", err(result))
        self.assertEqual(self.ffmpeg_calls(), [])

    def test_a_source_without_an_extension_fails_alone(self) -> None:
        bare, clip = self.touch("noext", "clip.mp4")
        result = self.invoke(bare, clip, "--start", "1")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(
            f"fail {bare}: no file extension, so the container is unknown", err(result)
        )
        self.assertTrue((self.dir / "clip_trim.mp4").is_file())
        self.assertEqual(self.names(), {"noext", "clip.mp4", "clip_trim.mp4"})

    def test_one_unreadable_file_fails_alone(self) -> None:
        self.ffprobe = make_tool(
            self.bin, "ffprobe", stdout="", stderr="not a media file\n", code=1,
            by_name={"good": self.PROBE},
        )
        bad, good = self.touch("bad.mp4", "good.mp4")
        result = self.invoke(bad, good, "--start", "1")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {bad}:", err(result))
        self.assertTrue((self.dir / "good_trim.mp4").is_file())
        self.assertFalse((self.dir / "bad_trim.mp4").exists())

    def test_an_ffmpeg_failure_is_reported_and_leaves_nothing(self) -> None:
        self.ffmpeg = make_tool(self.bin, "ffmpeg", code=1, stderr="Error while encoding: disk is full\n")
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--start", "1")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {clip}: Error while encoding: disk is full", err(result))
        self.assertEqual(self.names(), {"clip.mp4"})


class SkipTests(TrimCase):
    def test_an_existing_target_is_skipped_without_probing(self) -> None:
        clip, existing = self.touch("clip.mp4", "clip_trim.mp4")
        result = self.invoke(clip, "--start", "1")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {clip}: target exists (use --overwrite)", err(result))
        self.assertEqual(existing.read_bytes(), b"source")
        self.assertEqual(self.ffprobe_calls(), [])
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertIn("ffmpeg: 1 skipped", result.stdout)

    def test_overwrite_replaces_the_existing_target(self) -> None:
        clip, existing = self.touch("clip.mp4", "clip_trim.mp4")
        result = self.invoke(clip, "--start", "1", "--overwrite")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(existing.read_bytes(), b"trimmed")
        self.assertIn("ffmpeg: 1 converted", result.stdout)

    def test_output_naming_the_source_is_skipped(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke(clip, "--start", "1", "-o", clip, "--overwrite")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {clip}: source is the target", err(result))
        self.assertEqual(clip.read_bytes(), b"source")
        self.assertEqual(self.ffmpeg_calls(), [])


class ToolLookupTests(TrimCase):
    def test_without_ffmpeg_and_without_a_terminal_the_install_command_is_named(self) -> None:
        (clip,) = self.touch("clip.mp4")
        for group in ("video", "audio"):
            with self.subTest(group=group):
                result = run_without_tools([group, "trim", str(clip), "--start", "1"])
                self.assertEqual(result.exit_code, 1, describe(result))
                self.assertEqual(err(result).splitlines()[0], NOT_FOUND.format("ffmpeg"), describe(result))
                self.assertEqual(err(result).count("was not found"), 1, describe(result))
                self.assertNotIn("Traceback", describe(result))
                self.assertEqual(self.names(), {"clip.mp4"})


if __name__ == "__main__":
    unittest.main()
