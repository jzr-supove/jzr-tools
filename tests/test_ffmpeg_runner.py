"""Tests for finding ffmpeg and running jobs, without needing a real ffmpeg.

Process behaviour is checked against small stand-in programs (see
media_fixtures.make_fake_tool) that exit with a chosen code and print chosen
text, and against a fake process object for the Ctrl+C path.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from jzr_tools.core import ffmpeg
from jzr_tools.core.ffmpeg import OUTPUT, PASSLOG, Job, Tool, Tools
from jzr_tools.core.media import MediaError, MissingToolError

from media_fixtures import make_fake_tool, recorded_calls, tool_not_found_error

WHICH = "jzr_tools.core.ffmpeg.shutil.which"
POPEN = "jzr_tools.core.ffmpeg.subprocess.Popen"
RUN = "jzr_tools.core.ffmpeg.subprocess.run"

COMMON_FLAGS = ["-hide_banner", "-nostdin", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y"]


def fake_which(found: dict[str, str]):  # noqa: ANN201
    def which(name: str, *args: object, **kwargs: object) -> str | None:
        return found.get(Path(name).stem.lower())

    return which


class LocateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        # An empty jzr data folder, so a copy installed on this machine never leaks in.
        self.data = self.folder / "data"
        self.data.mkdir()
        self.base = {"JZR_DATA_DIR": str(self.data)}
        self.ffmpeg_file = self.folder / "my-ffmpeg.exe"
        self.ffprobe_file = self.folder / "my-ffprobe.exe"
        self.ffmpeg_file.write_bytes(b"x")
        self.ffprobe_file.write_bytes(b"x")

    def jzr_copy(self, name: str) -> Path:
        """Put a file where jzr's own copy of ``name`` lives and return its path."""
        from jzr_tools.core import ffmpeg_install

        path = ffmpeg_install.tool_path(name, self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    def test_environment_wins_over_path(self) -> None:
        environ = {**self.base, "JZR_FFMPEG": str(self.ffmpeg_file), "JZR_FFPROBE": str(self.ffprobe_file)}
        with mock.patch(WHICH, side_effect=fake_which({"ffmpeg": "P/ffmpeg", "ffprobe": "P/ffprobe"})):
            tools = ffmpeg.locate(environ)
        self.assertEqual(tools.ffmpeg, Tool(str(self.ffmpeg_file), "environment"))
        self.assertEqual(tools.ffprobe, Tool(str(self.ffprobe_file), "environment"))

    def test_path_is_used_when_the_environment_is_silent(self) -> None:
        found = {"ffmpeg": "C:/bin/ffmpeg.exe", "ffprobe": "C:/bin/ffprobe.exe"}
        with mock.patch(WHICH, side_effect=fake_which(found)):
            tools = ffmpeg.locate(self.base)
        self.assertEqual(tools.ffmpeg, Tool("C:/bin/ffmpeg.exe", "PATH"))
        self.assertEqual(tools.ffprobe, Tool("C:/bin/ffprobe.exe", "PATH"))

    def test_each_tool_is_resolved_on_its_own(self) -> None:
        environ = {**self.base, "JZR_FFMPEG": str(self.ffmpeg_file)}
        with mock.patch(WHICH, side_effect=fake_which({"ffprobe": "C:/bin/ffprobe.exe"})):
            tools = ffmpeg.locate(environ)
        self.assertEqual(tools.ffmpeg.origin, "environment")
        self.assertEqual(tools.ffprobe.origin, "PATH")

    def test_environment_pointing_at_no_file_is_an_error_not_a_fallback(self) -> None:
        missing = str(self.folder / "nope.exe")
        with mock.patch(WHICH, side_effect=fake_which({"ffmpeg": "P/ffmpeg", "ffprobe": "P/ffprobe"})):
            with self.assertRaises(MissingToolError) as caught:
                ffmpeg.locate({**self.base, "JZR_FFMPEG": missing})
        self.assertEqual(str(caught.exception), f"JZR_FFMPEG is set to {missing}, which is not a file")
        self.assertNotIsInstance(caught.exception, tool_not_found_error())

    def test_environment_pointing_at_a_folder_is_an_error(self) -> None:
        with mock.patch(WHICH, side_effect=fake_which({})):
            with self.assertRaises(MissingToolError) as caught:
                ffmpeg.locate_tool("ffprobe", {**self.base, "JZR_FFPROBE": str(self.folder)})
        self.assertEqual(
            str(caught.exception), f"JZR_FFPROBE is set to {self.folder}, which is not a file"
        )

    def test_missing_ffmpeg_message(self) -> None:
        with mock.patch(WHICH, side_effect=fake_which({"ffprobe": "P/ffprobe"})):
            with self.assertRaises(MissingToolError) as caught:
                ffmpeg.locate(self.base)
        self.assertEqual(str(caught.exception), "ffmpeg was not found. Run: jzr ffmpeg install")
        self.assertIsInstance(caught.exception, tool_not_found_error())

    def test_missing_ffprobe_message(self) -> None:
        with mock.patch(WHICH, side_effect=fake_which({"ffmpeg": "P/ffmpeg"})):
            with self.assertRaises(MissingToolError) as caught:
                ffmpeg.locate(self.base)
        self.assertEqual(str(caught.exception), "ffprobe was not found. Run: jzr ffmpeg install")
        self.assertIsInstance(caught.exception, tool_not_found_error())

    def test_locate_tool_finds_one_tool_without_needing_the_other(self) -> None:
        with mock.patch(WHICH, side_effect=fake_which({"ffprobe": "P/ffprobe"})):
            tool = ffmpeg.locate_tool("ffprobe", self.base)
            self.assertEqual(tool, Tool("P/ffprobe", "PATH"))
            with self.assertRaises(MissingToolError) as caught:
                ffmpeg.locate_tool("ffmpeg", self.base)
        self.assertEqual(str(caught.exception), "ffmpeg was not found. Run: jzr ffmpeg install")

    def test_missing_tool_error_is_a_media_error(self) -> None:
        with mock.patch(WHICH, side_effect=fake_which({})):
            with self.assertRaises(MediaError):
                ffmpeg.locate(self.base)

    def test_jzr_copy_is_used_before_path(self) -> None:
        copies = {name: self.jzr_copy(name) for name in ("ffmpeg", "ffprobe")}
        with mock.patch(WHICH, side_effect=fake_which({"ffmpeg": "P/ffmpeg", "ffprobe": "P/ffprobe"})):
            tools = ffmpeg.locate(self.base)
        self.assertEqual(tools.ffmpeg, Tool(str(copies["ffmpeg"]), "jzr"))
        self.assertEqual(tools.ffprobe, Tool(str(copies["ffprobe"]), "jzr"))

    def test_jzr_copy_is_found_when_nothing_is_on_path(self) -> None:
        copy = self.jzr_copy("ffprobe")
        with mock.patch(WHICH, side_effect=fake_which({})):
            self.assertEqual(ffmpeg.locate_tool("ffprobe", self.base), Tool(str(copy), "jzr"))

    def test_environment_wins_over_the_jzr_copy(self) -> None:
        self.jzr_copy("ffmpeg")
        environ = {**self.base, "JZR_FFMPEG": str(self.ffmpeg_file)}
        with mock.patch(WHICH, side_effect=fake_which({})):
            tool = ffmpeg.locate_tool("ffmpeg", environ)
        self.assertEqual(tool, Tool(str(self.ffmpeg_file), "environment"))

    def test_jzr_copy_that_is_not_a_file_is_skipped(self) -> None:
        from jzr_tools.core import ffmpeg_install

        # A folder where the program should be, so it is not a usable copy.
        ffmpeg_install.tool_path("ffmpeg", self.base).mkdir(parents=True)
        with mock.patch(WHICH, side_effect=fake_which({"ffmpeg": "P/ffmpeg", "ffprobe": "P/ffprobe"})):
            tools = ffmpeg.locate(self.base)
        self.assertEqual(tools.ffmpeg, Tool("P/ffmpeg", "PATH"))
        self.assertEqual(tools.ffprobe, Tool("P/ffprobe", "PATH"))


def completed(stdout: str, code: int = 0) -> subprocess.CompletedProcess:
    class Text(str):
        """Text that also answers ``decode`` so it passes for process output."""

        def decode(self, *args: object, **kwargs: object) -> str:
            return str(self)

    return subprocess.CompletedProcess(["x"], code, Text(stdout), Text(""))


VERSION_TEXT = (
    "ffmpeg version 7.1.1-full_build-www.gyan.dev Copyright (c) 2000-2025 the FFmpeg developers\n"
    "built with gcc 14.2.0\n"
)
ENCODERS_TEXT = """Encoders:
 V..... = Video
 A..... = Audio
 S..... = Subtitle
 .F.... = Frame-level multithreading
 ..S... = Slice-level multithreading
 ...X.. = Codec is experimental
 ....B. = Supports draw_horiz_band
 .....D = Supports direct rendering method 1
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V....D libvpx-vp9           libvpx VP9 (codec vp9)
 A....D aac                  AAC (Advanced Audio Coding)
 A....D libmp3lame           libmp3lame MP3 (MPEG audio layer 3) (codec mp3)
 S..... srt                  SubRip subtitle (codec subrip)
"""


class VersionAndEncodersTests(unittest.TestCase):
    TOOL = Tool("ffmpeg", "PATH")

    def test_version_is_the_word_after_version_on_the_first_line(self) -> None:
        with mock.patch(RUN, return_value=completed(VERSION_TEXT)):
            self.assertEqual(ffmpeg.version(self.TOOL), "7.1.1-full_build-www.gyan.dev")

    def test_version_is_unknown_when_it_cannot_be_read(self) -> None:
        for outcome in (
            completed("", 1),
            completed("something else entirely\n"),
            OSError("cannot start"),
        ):
            with self.subTest(outcome=outcome):
                if isinstance(outcome, Exception):
                    patcher = mock.patch(RUN, side_effect=outcome)
                else:
                    patcher = mock.patch(RUN, return_value=outcome)
                with patcher:
                    self.assertEqual(ffmpeg.version(self.TOOL), "unknown")

    def test_encoders_are_the_names_after_the_separator(self) -> None:
        with mock.patch(RUN, return_value=completed(ENCODERS_TEXT)):
            names = ffmpeg.encoders(self.TOOL)
        self.assertIsInstance(names, frozenset)
        self.assertEqual(names, frozenset({"libx264", "libvpx-vp9", "aac", "libmp3lame", "srt"}))

    def test_encoders_asks_ffmpeg_once(self) -> None:
        with mock.patch(RUN, return_value=completed(ENCODERS_TEXT)) as run:
            ffmpeg.encoders(self.TOOL)
        self.assertEqual(run.call_count, 1)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "ffmpeg")
        self.assertIn("-encoders", argv)


class Fixture(unittest.TestCase):
    """A target folder and a place for fake tools, both under one temp dir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        self.bin = root / "bin"
        self.out = root / "out"
        self.bin.mkdir()
        self.out.mkdir()

    def tools(self, **spec: object) -> Tools:
        path = make_fake_tool(self.bin, "ffmpeg", **spec)
        return Tools(Tool(path, "environment"), Tool("unused-ffprobe", "environment"))

    def job(self, *steps: tuple[str, ...], name: str = "result.mp4", duration: float | None = None) -> Job:
        if not steps:
            steps = (("-i", "input.mp4", "-c", "copy", OUTPUT),)
        return Job(steps=tuple(steps), target=self.out / name, duration=duration)

    def listing(self) -> list[str]:
        return sorted(p.name for p in self.out.iterdir())


class CommandLinesTests(Fixture):
    def test_one_line_per_step_with_the_real_target(self) -> None:
        tools = Tools(Tool("/x/ffmpeg", "PATH"), Tool("/x/ffprobe", "PATH"))
        job = Job(
            steps=(
                ("-i", "in.mov", "-pass", "1", "-passlogfile", PASSLOG, "-f", "null", "-"),
                ("-i", "in.mov", "-pass", "2", "-passlogfile", PASSLOG, OUTPUT),
            ),
            target=self.out / "result.mp4",
        )
        lines = ffmpeg.command_lines(job, tools)
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertEqual(line[0], "/x/ffmpeg")
            self.assertIn("in.mov", line)
            self.assertNotIn(OUTPUT, line)
        self.assertEqual(lines[1][-1], str(self.out / "result.mp4"))

    def test_nothing_is_started_or_written(self) -> None:
        tools = Tools(Tool("/x/ffmpeg", "PATH"), Tool("/x/ffprobe", "PATH"))
        with mock.patch(POPEN) as popen, mock.patch(RUN) as run:
            ffmpeg.command_lines(self.job(), tools)
        popen.assert_not_called()
        run.assert_not_called()
        self.assertEqual(self.listing(), [])


class RunSuccessTests(Fixture):
    def test_writes_the_target_through_a_temporary_name_beside_it(self) -> None:
        tools = self.tools(output_bytes="finished")
        ffmpeg.run(self.job(), tools)

        self.assertEqual(self.listing(), ["result.mp4"])
        self.assertEqual((self.out / "result.mp4").read_bytes(), b"finished")

        (call,) = recorded_calls(tools.ffmpeg.path)
        self.assertEqual(call[: len(COMMON_FLAGS)], COMMON_FLAGS)
        self.assertEqual(call[len(COMMON_FLAGS):][:4], ["-i", "input.mp4", "-c", "copy"])
        temp = Path(call[-1])
        self.assertTrue(temp.is_absolute(), call[-1])
        self.assertEqual(temp.parent, self.out)
        self.assertRegex(temp.name, r"^jzr-tmp-[0-9a-f]{16}\.mp4$")
        self.assertFalse(temp.exists())

    def test_an_existing_target_is_replaced_by_the_new_result(self) -> None:
        (self.out / "result.mp4").write_bytes(b"old")
        ffmpeg.run(self.job(), self.tools(output_bytes="new"))
        self.assertEqual((self.out / "result.mp4").read_bytes(), b"new")
        self.assertEqual(self.listing(), ["result.mp4"])

    def test_temporary_name_keeps_the_target_suffix(self) -> None:
        tools = self.tools()
        ffmpeg.run(self.job(name="song.m4a"), tools)
        temp = Path(recorded_calls(tools.ffmpeg.path)[0][-1])
        self.assertEqual(temp.suffix, ".m4a")

    def test_two_steps_share_one_output_and_the_pass_log_is_cleaned_up(self) -> None:
        tools = self.tools(output_bytes="two-pass")
        job = self.job(
            ("-i", "in.mp4", "-pass", "1", "-passlogfile", PASSLOG, "-f", "null", "-"),
            ("-i", "in.mp4", "-pass", "2", "-passlogfile", PASSLOG, OUTPUT),
        )
        ffmpeg.run(job, tools)

        first, second = recorded_calls(tools.ffmpeg.path)
        passlog = first[first.index("-passlogfile") + 1]
        self.assertEqual(second[second.index("-passlogfile") + 1], passlog)
        self.assertTrue(Path(passlog).is_absolute())
        self.assertNotEqual(Path(passlog).parent, self.out)
        self.assertFalse(os.path.exists(passlog + "-0.log"))
        self.assertEqual(self.listing(), ["result.mp4"])

    def test_pass_log_is_cleaned_up_after_a_failure_too(self) -> None:
        tools = self.tools(code=1, stderr="Something broke\n")
        job = self.job(("-i", "in.mp4", "-passlogfile", PASSLOG, OUTPUT))
        with self.assertRaises(MediaError):
            ffmpeg.run(job, tools)
        passlog = recorded_calls(tools.ffmpeg.path)[0]
        passlog = passlog[passlog.index("-passlogfile") + 1]
        self.assertFalse(os.path.exists(passlog + "-0.log"))
        self.assertEqual(self.listing(), [])


class ProgressTests(Fixture):
    BLOCKS = (
        "frame=0\nout_time_us=N/A\nout_time_ms=N/A\nout_time=N/A\nspeed=N/A\nprogress=continue\n"
        "frame=5\nout_time_us=500000\nout_time_ms=500000\nout_time=00:00:00.500000\nspeed=1.5x\nprogress=continue\n"
        "frame=10\nout_time_us=1000000\nout_time_ms=1000000\nout_time=00:00:01.000000\nspeed=2.0x\nprogress=end\n"
    )

    def collect(self, tools: Tools, job: Job) -> list:
        updates: list = []
        ffmpeg.run(job, tools, on_progress=updates.append)
        return updates

    def test_fraction_is_time_over_duration_and_reaches_one(self) -> None:
        updates = self.collect(self.tools(stdout=self.BLOCKS), self.job(duration=1.0))
        known = [u for u in updates if u.seconds]
        self.assertTrue(known)
        self.assertEqual([round(u.seconds, 3) for u in known][:2], [0.5, 1.0])
        self.assertAlmostEqual(known[0].fraction, 0.5, places=3)
        self.assertAlmostEqual(known[-1].fraction, 1.0, places=2)
        fractions = [u.fraction for u in known]
        self.assertEqual(fractions, sorted(fractions))
        self.assertEqual(known[0].speed, 1.5)
        self.assertEqual(known[1].speed, 2.0)
        for update in updates:
            self.assertEqual(update.steps, 1)

    def test_without_a_duration_the_fraction_is_none(self) -> None:
        updates = self.collect(self.tools(stdout=self.BLOCKS), self.job())
        self.assertTrue(updates)
        self.assertTrue(all(u.fraction is None for u in updates))
        self.assertTrue(any(u.seconds == 0.5 for u in updates))

    def test_steps_counts_the_steps_of_the_job(self) -> None:
        job = self.job(("-i", "a", "-f", "null", "-"), ("-i", "a", OUTPUT), duration=1.0)
        updates = self.collect(self.tools(stdout=self.BLOCKS), job)
        self.assertEqual({u.steps for u in updates}, {2})
        self.assertEqual(len({u.step for u in updates}), 2)

    def test_no_callback_is_fine(self) -> None:
        ffmpeg.run(self.job(), self.tools(stdout=self.BLOCKS))
        self.assertEqual(self.listing(), ["result.mp4"])


class RunFailureTests(Fixture):
    def failure(self, stderr: str, code: int = 1, **job_args: object) -> str:
        (self.out / "result.mp4").write_bytes(b"keep me")
        with self.assertRaises(MediaError) as caught:
            ffmpeg.run(self.job(**job_args), self.tools(code=code, stderr=stderr, output_bytes="partial"))
        self.assertEqual(self.listing(), ["result.mp4"], "a temporary file was left behind")
        self.assertEqual((self.out / "result.mp4").read_bytes(), b"keep me")
        return str(caught.exception)

    def test_unknown_encoder_is_named_plainly(self) -> None:
        stderr = (
            "[vost#0:0 @ 0000022013959480] Unknown encoder 'libnothere'\n"
            "[vost#0:0 @ 0000022013959480] Error selecting an encoder\n"
            "Error opening output file out1.mp4.\n"
            "Error opening output files: Encoder not found\n"
        )
        self.assertEqual(self.failure(stderr, 8), "this ffmpeg build has no libnothere encoder")

    def test_generic_follow_up_lines_are_skipped_and_the_prefix_removed(self) -> None:
        stderr = (
            "[mp3 @ 000001F2A3B4C5D6] Invalid audio stream. Exactly one MP3 audio stream is required.\n"
            "[out#0/mp3 @ 000001F2A3B4C5E0] Could not write header (incorrect codec parameters ?): Invalid argument\n"
            "Conversion failed!\n"
        )
        self.assertEqual(
            self.failure(stderr), "Invalid audio stream. Exactly one MP3 audio stream is required."
        )

    def test_unreadable_input_reports_the_reason(self) -> None:
        stderr = (
            "[in#0 @ 0000024A1B2C3D4E] Error opening input: Invalid data found when processing input\n"
            "Error opening input file notes.txt.\n"
            "Error opening input files: Invalid data found when processing input\n"
        )
        self.assertIn("Invalid data found when processing input", self.failure(stderr))

    def test_the_last_meaningful_line_wins(self) -> None:
        stderr = "first problem\nsecond problem\n\n"
        self.assertEqual(self.failure(stderr), "second problem")

    def test_empty_stderr_reports_the_exit_code(self) -> None:
        self.assertEqual(self.failure("", 3), "ffmpeg exited with code 3")
        self.assertEqual(self.failure("\n  \n", 4), "ffmpeg exited with code 4")

    def test_a_failing_first_step_stops_the_job(self) -> None:
        tools = self.tools(code=1, stderr="boom\n")
        job = self.job(("-i", "a", "-f", "null", "-"), ("-i", "a", OUTPUT))
        with self.assertRaises(MediaError):
            ffmpeg.run(job, tools)
        self.assertEqual(len(recorded_calls(tools.ffmpeg.path)), 1)
        self.assertEqual(self.listing(), [])


class InterruptTests(Fixture):
    class Stream:
        def __init__(self, interrupt: bool) -> None:
            self.interrupt = interrupt

        def __iter__(self):  # noqa: ANN204
            if self.interrupt:
                raise KeyboardInterrupt
            return iter(())

        def __next__(self) -> str:
            if self.interrupt:
                raise KeyboardInterrupt
            raise StopIteration

        def readline(self, *args: object) -> str:
            if self.interrupt:
                raise KeyboardInterrupt
            return ""

        def read(self, *args: object) -> str:
            if self.interrupt:
                raise KeyboardInterrupt
            return ""

        def close(self) -> None:
            pass

    class Process:
        def __init__(self, outer: type) -> None:
            self.stdout = outer.Stream(True)
            self.stderr = outer.Stream(False)
            self.stdin = None
            self.pid = 4242
            self.returncode: int | None = None
            self.stopped = False

        def poll(self) -> int | None:
            return self.returncode

        def _stop(self) -> None:
            self.stopped = True
            self.returncode = -15

        def terminate(self) -> None:
            self._stop()

        def kill(self) -> None:
            self._stop()

        def send_signal(self, *args: object) -> None:
            self._stop()

        def wait(self, timeout: float | None = None) -> int:
            if not self.stopped:
                raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)
            return self.returncode or 0

        def communicate(self, *args: object, **kwargs: object) -> tuple[str, str]:
            return "", ""

        def __enter__(self) -> "InterruptTests.Process":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def test_ctrl_c_stops_ffmpeg_removes_the_temp_file_and_reraises(self) -> None:
        (self.out / "result.mp4").write_bytes(b"keep me")
        created: list[Path] = []
        processes: list[InterruptTests.Process] = []

        def start(argv: list[str], *args: object, **kwargs: object) -> "InterruptTests.Process":
            temp = next(Path(a) for a in argv if Path(a).name.startswith("jzr-tmp-"))
            temp.write_bytes(b"partial")
            created.append(temp)
            process = InterruptTests.Process(InterruptTests)
            processes.append(process)
            return process

        tools = Tools(Tool("ffmpeg", "PATH"), Tool("ffprobe", "PATH"))
        with mock.patch(POPEN, side_effect=start):
            with self.assertRaises(KeyboardInterrupt):
                ffmpeg.run(self.job(), tools)

        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists(), "the temporary file survived Ctrl+C")
        self.assertEqual(self.listing(), ["result.mp4"])
        self.assertEqual((self.out / "result.mp4").read_bytes(), b"keep me")
        self.assertTrue(processes[0].stopped, "ffmpeg was left running")


if __name__ == "__main__":
    unittest.main()
