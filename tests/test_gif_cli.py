"""CLI behaviour of `jzr video gif` and `jzr video convert --to gif` against stand-in tools.

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
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app

from media_fixtures import recorded_calls
from test_audio_convert_cli import FLAGS
from test_media_cli import NOT_FOUND, describe, err, run_without_tools
from test_trim_cli import audio_probe, before_input, make_tool, plan_of, value_after

runner = CliRunner()

MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
PALETTE = (
    "split[a][b];[a]palettegen=stats_mode=diff[p];"
    "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle[g]"
)
LONG_TAIL = "a shorter part, a smaller --width or a lower --fps makes it smaller"


def graph(*filters: str) -> str:
    """The filter graph one ffmpeg run uses: optional filters, then the palette steps."""
    chain = ",".join(filters)
    return f"[0:0]{chain + ',' if chain else ''}{PALETTE}"


def gif_probe(
    width: int = 64,
    height: int = 48,
    fps: int = 20,
    duration: float = 10.0,
    *,
    audio: bool = True,
) -> str:
    streams: list[dict[str, object]] = [
        {
            "index": 0, "codec_type": "video", "codec_name": "h264",
            "width": width, "height": height, "avg_frame_rate": f"{fps}/1", "pix_fmt": "yuv420p",
        }
    ]
    if audio:
        streams.append(
            {
                "index": 1, "codec_type": "audio", "codec_name": "aac",
                "channels": 2, "channel_layout": "stereo", "sample_rate": "44100",
            }
        )
    return json.dumps(
        {
            "streams": streams,
            "format": {"format_name": MP4_FAMILY, "duration": str(duration), "size": "1000"},
        }
    )


class GifCase(unittest.TestCase):
    PROBE = gif_probe()

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

    def invoke_command(self, *args: object):  # noqa: ANN201
        environ = {**os.environ, "JZR_FFMPEG": self.ffmpeg, "JZR_FFPROBE": self.ffprobe}
        with mock.patch.dict(os.environ, environ, clear=True):
            return runner.invoke(app, ["video", *map(str, args)])

    def gif(self, *args: object):  # noqa: ANN201
        return self.invoke_command("gif", *args)

    def convert(self, *args: object):  # noqa: ANN201
        return self.invoke_command("convert", *args)

    def ffmpeg_calls(self) -> list[list[str]]:
        return [c for c in recorded_calls(self.ffmpeg) if "-i" in c]

    def ffprobe_calls(self) -> list[list[str]]:
        return [c for c in recorded_calls(self.ffprobe) if "-i" in c]

    def names(self) -> set[str]:
        return {p.name for p in self.dir.iterdir()}

    def made_from(self) -> set[str]:
        return {Path(c[c.index("-i") + 1]).name for c in self.ffmpeg_calls()}

    def result_line(self, result: object, source: Path) -> str:
        (line,) = [l for l in result.stdout.splitlines() if l.startswith(f"{source} -> ")]  # type: ignore[attr-defined]
        return line


class UsageErrorTests(GifCase):
    def assert_usage_error(self, result: object, *needles: str) -> None:
        self.assertEqual(result.exit_code, 2, describe(result))
        self.assertNotIn("Traceback", describe(result))
        self.assertNotIn("No such command", err(result))
        for needle in needles:
            self.assertIn(needle, err(result), describe(result))
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertEqual(self.ffprobe_calls(), [])

    def test_end_and_duration_cannot_be_combined(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.assert_usage_error(
            self.gif(clip, "--end", "5", "--duration", "2"),
            "--end and --duration cannot be used together",
        )

    def test_an_end_not_after_the_start_is_refused_with_both_times(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.assert_usage_error(
            self.gif(clip, "--start", "5", "--end", "3"),
            "--end 0:03.000 is not after --start 0:05.000",
        )
        self.assert_usage_error(
            self.gif(clip, "--start", "0:05", "--end", "5"),
            "--end 0:05.000 is not after --start 0:05.000",
        )

    def test_an_end_of_zero_is_not_after_the_default_start(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.assert_usage_error(
            self.gif(clip, "--end", "0"), "--end 0:00.000 is not after --start 0:00.000"
        )

    def test_a_duration_of_zero_is_refused(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.assert_usage_error(
            self.gif(clip, "--start", "1", "--duration", "0"), "--duration must be more than 0"
        )

    def test_a_bad_time_is_refused_naming_its_option(self) -> None:
        (clip,) = self.touch("clip.mp4")
        for option in ("--start", "--end", "--duration"):
            with self.subTest(option=option):
                other = ["--start", "0"] if option != "--start" else []
                self.assert_usage_error(self.gif(clip, *other, option, "1:75"), option)
        self.assert_usage_error(self.gif(clip, "--start", "soon"), "--start")

    def test_a_width_below_two_or_not_a_whole_number_is_refused(self) -> None:
        (clip,) = self.touch("clip.mp4")
        for value in ("1", "0", "-5", "abc", "3.5"):
            with self.subTest(value=value):
                self.assert_usage_error(self.gif(clip, "--width", value), "--width")

    def test_a_frame_rate_that_is_not_above_zero_or_not_a_number_is_refused(self) -> None:
        (clip,) = self.touch("clip.mp4")
        for value in ("0", "-1", "fast"):
            with self.subTest(value=value):
                self.assert_usage_error(self.gif(clip, "--fps", value), "--fps")

    def test_output_must_end_in_gif(self) -> None:
        (clip,) = self.touch("clip.mp4")
        target = self.dir / "x.mp4"
        self.assert_usage_error(
            self.gif(clip, "-o", target), f"--output {target} does not end in .gif."
        )
        self.assertNotIn("x.mp4", self.names())

    def test_output_names_one_file_for_one_source(self) -> None:
        a, b = self.touch("a.mp4", "b.mp4")
        self.assert_usage_error(self.gif(a, b, "-o", self.dir / "x.gif"), "--output")

    def test_output_cannot_be_combined_with_outdir(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.assert_usage_error(
            self.gif(clip, "-o", self.dir / "x.gif", "--outdir", self.dir / "o"),
            "--output and --outdir cannot be used together",
        )

    def test_dir_and_recursive_do_not_go_with_listed_sources(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.assert_usage_error(self.gif(clip, "--dir", self.dir), "--dir")
        self.assert_usage_error(self.gif(clip, "--recursive"), "--recursive")

    def test_a_listed_file_that_does_not_exist_is_a_usage_error(self) -> None:
        missing = self.dir / "gone.mp4"
        self.assert_usage_error(self.gif(missing), f"File not found: {missing}")

    def test_usage_errors_come_before_the_tools_are_looked_for(self) -> None:
        (clip,) = self.touch("clip.mp4")
        cases = [
            ["--end", "5", "--duration", "2"],
            ["--start", "5", "--end", "3"],
            ["--duration", "0"],
            ["--start", "1:75"],
            ["--width", "1"],
            ["--fps", "0"],
            ["-o", str(self.dir / "x.mp4")],
        ]
        for args in cases:
            with self.subTest(args=args):
                result = run_without_tools(["video", "gif", str(clip), *args])
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertNotIn("was not found", err(result))
                self.assertNotIn("No such command", err(result))


class HelpTests(GifCase):
    def test_the_video_group_lists_gif(self) -> None:
        result = runner.invoke(app, ["video", "--help"])
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertRegex(result.stdout, r"(?m)^  gif\s+\S")

    def test_gif_help_lists_the_options_in_order(self) -> None:
        result = runner.invoke(app, ["video", "gif", "--help"])
        self.assertEqual(result.exit_code, 0, describe(result))
        options = (
            "--dir", "--pattern", "--recursive", "--output", "--outdir", "--start", "--end",
            "--duration", "--width", "--fps", "--overwrite", "--dry-run",
        )
        listing = result.stdout[result.stdout.index("Options:"):]
        positions = [listing.find(option) for option in options]
        self.assertNotIn(-1, positions, result.stdout)
        self.assertEqual(positions, sorted(positions), result.stdout)

    def test_the_time_and_size_options_have_no_short_forms(self) -> None:
        result = runner.invoke(app, ["video", "gif", "--help"])
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertNotRegex(result.stdout, r"(?m)^\s+-[a-zA-Z], --(start|end|duration|width|fps)\b")


class CommandTests(GifCase):
    def test_the_whole_file_is_used_when_no_range_is_given(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip)
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertNotIn("-ss", call)
        self.assertNotIn("-t", call)

    def test_the_command_for_one_file_is_exactly_the_gif_command(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip)
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(
            call[:-1],
            [
                *FLAGS, "-i", str(clip),
                "-filter_complex", graph("fps=15"), "-map", "[g]", "-loop", "0",
            ],
        )
        output = Path(call[-1])
        self.assertTrue(output.name.startswith("jzr-tmp-"), output)
        self.assertEqual(output.suffix, ".gif")
        self.assertEqual(output.parent, self.dir)

    def test_the_picture_is_scaled_down_to_the_default_width_of_480(self) -> None:
        self.install(gif_probe(1920, 1080, 30))
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip)
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(
            value_after(plan_of(call), "-filter_complex"),
            graph("fps=15", "scale=480:270:flags=lanczos"),
        )
        # The stand-in ffmpeg writes the seven bytes of "trimmed", so every result line ends in "7 B".
        self.assertEqual(
            self.result_line(result, clip), f"{clip} -> {self.dir / 'clip.gif'} (480x270, 15 fps, 7 B)"
        )

    def test_a_narrower_picture_is_never_scaled_up(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--width", "640")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertNotIn("flags=lanczos", value_after(plan_of(call), "-filter_complex"))
        self.assertIn("(64x48, 15 fps, 7 B)", result.stdout)

    def test_width_reaches_the_filter_graph(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--width", "32")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(
            value_after(plan_of(call), "-filter_complex"),
            graph("fps=15", "scale=32:24:flags=lanczos"),
        )
        self.assertIn("(32x24, 15 fps, 7 B)", result.stdout)

    def test_fps_below_the_source_rate_reaches_the_filter_graph(self) -> None:
        (clip,) = self.touch("clip.mp4")
        for value, written in (("5", "5"), ("12.5", "12.5")):
            with self.subTest(fps=value):
                result = self.gif(clip, "--fps", value, "--overwrite")
                self.assertEqual(result.exit_code, 0, describe(result))
                call = self.ffmpeg_calls()[-1]
                self.assertEqual(
                    value_after(plan_of(call), "-filter_complex"), graph(f"fps={written}")
                )
                self.assertIn(f"{written} fps, 7 B)", result.stdout)

    def test_a_frame_rate_above_the_source_rate_keeps_the_source_rate(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--fps", "25")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(value_after(plan_of(call), "-filter_complex"), graph())
        self.assertIn("(64x48, 20 fps, 7 B)", result.stdout)

    def test_width_and_fps_work_together(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--width", "32", "--fps", "5")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(
            value_after(plan_of(call), "-filter_complex"),
            graph("fps=5", "scale=32:24:flags=lanczos"),
        )

    def test_start_and_end_reach_ffmpeg_as_seek_before_the_input_and_a_length_after(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--start", "0:02.5", "--end", "0:06")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(value_after(before_input(call), "-ss"), "2.5")
        self.assertNotIn("-ss", plan_of(call))
        self.assertEqual(value_after(plan_of(call), "-t"), "3.5")
        self.assertNotIn("-t", before_input(call))

    def test_duration_is_taken_from_the_start(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--start", "2", "--duration", "3")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(value_after(before_input(call), "-ss"), "2")
        self.assertEqual(value_after(plan_of(call), "-t"), "3")

    def test_a_range_from_the_beginning_has_no_seek(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--duration", "2")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertNotIn("-ss", call)
        self.assertEqual(value_after(plan_of(call), "-t"), "2")

    def test_a_start_alone_runs_to_the_end_of_the_file(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--start", "4")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(value_after(before_input(call), "-ss"), "4")
        self.assertNotIn("-t", plan_of(call))

    def test_an_end_past_the_end_of_the_file_runs_to_the_end_and_says_so(self) -> None:
        self.install(gif_probe(duration=8.0))
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--start", "2", "--end", "0:20")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertNotIn("-t", plan_of(call))
        self.assertIn("the file ends at 0:08.00", result.stdout)

    def test_no_audio_is_mapped(self) -> None:
        (clip,) = self.touch("clip.mp4")
        self.gif(clip)
        (call,) = self.ffmpeg_calls()
        self.assertEqual(plan_of(call).count("-map"), 1)
        self.assertNotIn("-c:a", call)

    def test_success_prints_source_target_and_the_note_in_brackets(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip)
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        self.assertIn(f"{clip} -> {self.dir / 'clip.gif'} (64x48, 15 fps, 7 B)", lines)
        self.assertIn("ffmpeg: 1 converted", lines)
        self.assertEqual(err(result), "")
        self.assertEqual(self.names(), {"clip.mp4", "clip.gif"})

    def test_a_clip_over_30_seconds_gets_a_size_note(self) -> None:
        self.install(gif_probe(1920, 1080, 30, duration=60.0))
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(
            f"{clip} -> {self.dir / 'clip.gif'} (480x270, 15 fps; "
            f"long GIF (1:00): expect tens to hundreds of MB; {LONG_TAIL}, 7 B)",
            result.stdout,
        )

    def test_a_range_of_30_seconds_or_less_gets_no_size_note(self) -> None:
        self.install(gif_probe(1920, 1080, 30, duration=60.0))
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--start", "10", "--duration", "30")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertNotIn("long GIF", result.stdout)

    def test_a_range_cut_from_a_long_file_is_judged_by_its_own_length(self) -> None:
        self.install(gif_probe(1920, 1080, 30, duration=600.0))
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--start", "10", "--end", "70")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn("long GIF (1:00): expect tens to hundreds of MB", result.stdout)

    def test_a_dry_run_prints_the_command_and_the_note_and_writes_nothing(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--start", "0:02", "--duration", "3", "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        (line,) = [l for l in result.stdout.splitlines() if " -i " in l]
        words = shlex.split(line)
        self.assertEqual(words[0], self.ffmpeg)
        self.assertEqual(
            words[words.index("-i"):],
            [
                "-i", str(clip), "-filter_complex", graph("fps=15"), "-map", "[g]",
                "-t", "3", "-loop", "0", str(self.dir / "clip.gif"),
            ],
        )
        self.assertEqual(words[words.index("-ss") + 1], "2")
        self.assertLess(words.index("-ss"), words.index("-i"))
        self.assertIn("note: 64x48, 15 fps", err(result).splitlines())
        self.assertEqual(self.names(), {"clip.mp4"})
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertEqual(len(self.ffprobe_calls()), 1)

    def test_a_dry_run_with_output_creates_no_folder(self) -> None:
        (clip,) = self.touch("clip.mp4")
        target = self.dir / "made" / "x.gif"
        result = self.gif(clip, "-o", target, "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(shlex.quote(str(target)), result.stdout)
        self.assertFalse(target.parent.exists())

    def test_a_dry_run_of_a_long_clip_shows_the_size_note(self) -> None:
        self.install(gif_probe(1920, 1080, 30, duration=60.0))
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip, "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(
            f"note: 480x270, 15 fps; long GIF (1:00): expect tens to hundreds of MB; {LONG_TAIL}",
            err(result).splitlines(),
        )

    def test_no_temporary_file_is_left_behind(self) -> None:
        self.touch("a.mp4", "b.mp4")
        self.gif("--dir", self.dir)
        self.assertTrue({"a.gif", "b.gif"} <= self.names())
        self.assertEqual({n for n in self.names() if n.startswith("jzr-tmp-")}, set())


class TargetTests(GifCase):
    def test_the_result_goes_beside_the_source_as_stem_gif(self) -> None:
        (clip,) = self.touch("clip.mkv")
        result = self.gif(clip)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual((self.dir / "clip.gif").read_bytes(), b"trimmed")
        self.assertEqual(self.names(), {"clip.mkv", "clip.gif"})

    def test_an_upper_case_extension_is_replaced_by_gif(self) -> None:
        (clip,) = self.touch("CLIP.MP4")
        result = self.gif(clip)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn("CLIP.gif", self.names())

    def test_outdir_collects_the_results(self) -> None:
        (clip,) = self.touch("clip.mp4")
        out = self.dir / "out"
        result = self.gif(clip, "--outdir", out)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue((out / "clip.gif").is_file())
        self.assertNotIn("clip.gif", self.names())

    def test_output_names_the_target(self) -> None:
        (clip,) = self.touch("clip.mp4")
        target = self.dir / "made" / "piece.gif"
        result = self.gif(clip, "-o", target)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue(target.is_file())
        (call,) = self.ffmpeg_calls()
        self.assertEqual(Path(call[-1]).parent, target.parent)

    def test_a_directory_scan_with_outdir_mirrors_subfolders(self) -> None:
        self.touch("a.mp4", "sub/b.mp4")
        out = self.dir / "out"
        result = self.gif("--dir", self.dir, "--recursive", "--outdir", out)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue((out / "a.gif").is_file())
        self.assertTrue((out / "sub" / "b.gif").is_file())
        self.assertNotIn("a.gif", self.names())

    def test_a_name_with_spaces_a_percent_sign_or_a_leading_dash_works(self) -> None:
        names = ["with space.mp4", "100% real.mp4", "-leading dash.mp4", "видео.mp4"]
        self.touch(*names)
        result = self.gif("--dir", self.dir)
        self.assertEqual(result.exit_code, 0, describe(result))
        for name in names:
            self.assertIn(Path(name).with_suffix(".gif").name, self.names())
        self.assertEqual(self.made_from(), set(names))


class ScanTests(GifCase):
    def test_a_scan_takes_every_video_extension_and_leaves_other_files_alone(self) -> None:
        videos = [
            "a.mp4", "b.mkv", "c.webm", "d.mov", "e.avi", "f.m4v", "g.mpg", "h.3gp", "i.ts", "j.ogv",
        ]
        self.touch(*videos, "song.wav", "song.mp3", "notes.txt", "old.gif")
        result = self.gif("--dir", self.dir)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.made_from(), set(videos))
        self.assertIn("ffmpeg: 10 converted", result.stdout.splitlines())

    def test_pattern_and_recursive_narrow_the_scan(self) -> None:
        self.touch("take1.mp4", "other.mp4", "sub/take2.mp4")
        result = self.gif("--dir", self.dir, "--recursive", "--pattern", "take")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.made_from(), {"take1.mp4", "take2.mp4"})

    def test_a_scan_without_recursive_stays_in_the_folder(self) -> None:
        self.touch("a.mp4", "sub/b.mp4")
        result = self.gif("--dir", self.dir)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.made_from(), {"a.mp4"})

    def test_nothing_found_says_so_and_exits_1(self) -> None:
        self.touch("song.wav", "notes.txt")
        result = self.gif("--dir", self.dir)
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("No matching files.", err(result))
        self.assertEqual(self.ffmpeg_calls(), [])


class FailureTests(GifCase):
    def test_a_file_without_a_video_track_fails_alone(self) -> None:
        self.install(by_name={"song": audio_probe()})
        song, clip = self.touch("song.mp3", "clip.mp4")
        result = self.gif(song, clip)
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {song}: no video track", err(result))
        self.assertEqual(err(result).count("fail "), 1, describe(result))
        self.assertTrue((self.dir / "clip.gif").is_file())
        self.assertFalse((self.dir / "song.gif").exists())
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())

    def test_one_unreadable_file_fails_alone(self) -> None:
        self.ffprobe = make_tool(
            self.bin, "ffprobe", stdout="", stderr="not a media file\n", code=1,
            by_name={"good": self.PROBE},
        )
        bad, good = self.touch("bad.mp4", "good.mp4")
        result = self.gif(bad, good)
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {bad}: not a readable media file", err(result))
        self.assertEqual(err(result).count("fail "), 1, describe(result))
        self.assertTrue((self.dir / "good.gif").is_file())
        self.assertFalse((self.dir / "bad.gif").exists())
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())

    def test_a_start_past_the_end_fails_that_file_only(self) -> None:
        self.install(by_name={"short": gif_probe(duration=2.0), "long": gif_probe(duration=60.0)})
        short, long = self.touch("short.mp4", "long.mp4")
        result = self.gif(short, long, "--start", "30", "--duration", "2")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(
            f"fail {short}: --start 0:30.000 is past the end of the file (0:02.00)", err(result)
        )
        self.assertEqual(self.made_from(), {"long.mp4"})
        self.assertFalse((self.dir / "short.gif").exists())
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())

    def test_an_ffmpeg_failure_is_reported_and_leaves_nothing(self) -> None:
        self.ffmpeg = make_tool(self.bin, "ffmpeg", code=1, stderr="Error while encoding: disk is full\n")
        (clip,) = self.touch("clip.mp4")
        result = self.gif(clip)
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {clip}: Error while encoding: disk is full", err(result))
        self.assertEqual(self.names(), {"clip.mp4"})


class SkipTests(GifCase):
    def test_an_existing_target_is_skipped_without_probing(self) -> None:
        clip, existing = self.touch("clip.mp4", "clip.gif")
        result = self.gif(clip)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {clip}: target exists (use --overwrite)", err(result))
        self.assertEqual(existing.read_bytes(), b"source")
        self.assertEqual(self.ffprobe_calls(), [])
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertIn("ffmpeg: 1 skipped", result.stdout)

    def test_overwrite_replaces_the_existing_target(self) -> None:
        clip, existing = self.touch("clip.mp4", "clip.gif")
        result = self.gif(clip, "--overwrite")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(existing.read_bytes(), b"trimmed")
        self.assertIn("ffmpeg: 1 converted", result.stdout)

    def test_a_skipped_file_does_not_stop_the_others(self) -> None:
        a, _existing, b = self.touch("a.mp4", "a.gif", "b.mp4")
        result = self.gif(a, b)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.made_from(), {"b.mp4"})
        self.assertIn("ffmpeg: 1 converted, 1 skipped", result.stdout)


class ToolLookupTests(GifCase):
    def test_without_ffmpeg_and_without_a_terminal_the_install_command_is_named(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = run_without_tools(["video", "gif", str(clip)])
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(err(result).splitlines()[0], NOT_FOUND.format("ffmpeg"), describe(result))
        self.assertEqual(err(result).count("was not found"), 1, describe(result))
        self.assertNotIn("Traceback", describe(result))
        self.assertEqual(self.names(), {"clip.mp4"})


class ConvertToGifTests(GifCase):
    def test_gif_is_an_accepted_target(self) -> None:
        (clip,) = self.touch("clip.mov")
        for value in ("gif", "GIF", ".gif"):
            with self.subTest(value=value):
                result = self.convert(clip, "--to", value, "--overwrite")
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertNotIn("Unsupported", err(result))
                self.assertTrue((self.dir / "clip.gif").is_file())

    def test_the_result_line_names_the_size_and_rate(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.convert(clip, "--to", "gif")
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        self.assertIn(f"{clip} -> {self.dir / 'clip.gif'} (64x48, 15 fps, 7 B)", lines)
        self.assertIn("ffmpeg: 1 converted", lines)

    def test_a_large_source_is_scaled_to_480_wide(self) -> None:
        self.install(gif_probe(1920, 1080, 30))
        (clip,) = self.touch("clip.mp4")
        result = self.convert(clip, "--to", "gif")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"{clip} -> {self.dir / 'clip.gif'} (480x270, 15 fps, 7 B)", result.stdout)

    def test_the_target_is_taken_from_the_output_extension(self) -> None:
        (clip,) = self.touch("clip.mp4")
        target = self.dir / "made" / "piece.gif"
        result = self.convert(clip, "-o", target)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue(target.is_file())
        (call,) = self.ffmpeg_calls()
        self.assertEqual(Path(call[-1]).suffix, ".gif")
        self.assertIn("-filter_complex", call)

    def test_a_pair_command_converts_that_extension_only(self) -> None:
        self.touch("a.mp4", "b.mov", "c.mkv")
        result = self.invoke_command("mp42gif", "--dir", self.dir)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.made_from(), {"a.mp4"})
        self.assertTrue((self.dir / "a.gif").is_file())
        result = self.invoke_command("mov2gif", self.dir / "b.mov")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue((self.dir / "b.gif").is_file())

    def test_a_pair_command_has_the_other_convert_options_but_no_to(self) -> None:
        result = self.invoke_command("mp42gif", "--help")
        self.assertEqual(result.exit_code, 0, describe(result))
        options = result.stdout.split("Options:", 1)[1]
        self.assertNotIn("--to", options)
        self.assertIn("--dry-run", options)

    def test_a_scan_for_gif_takes_every_video_file(self) -> None:
        self.touch("a.mp4", "b.mkv", "c.webm", "song.mp3", "notes.txt")
        result = self.convert("--dir", self.dir, "--to", "gif")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.made_from(), {"a.mp4", "b.mkv", "c.webm"})

    def test_the_command_is_the_one_video_gif_builds_with_no_options(self) -> None:
        self.install(gif_probe(1280, 720, 30))
        (clip,) = self.touch("clip.mp4")
        first = self.gif(clip, "--outdir", self.dir / "from-gif")
        second = self.convert(clip, "--to", "gif", "--outdir", self.dir / "from-convert")
        self.assertEqual(first.exit_code, 0, describe(first))
        self.assertEqual(second.exit_code, 0, describe(second))
        gif_call, convert_call = self.ffmpeg_calls()
        self.assertEqual(convert_call[:-1], gif_call[:-1])
        self.assertEqual(
            value_after(plan_of(convert_call), "-filter_complex"),
            graph("fps=15", "scale=480:270:flags=lanczos"),
        )
        self.assertEqual(plan_of(convert_call)[-2:], ["-loop", "0"])
        self.assertNotIn("-ss", convert_call)
        self.assertNotIn("-t", convert_call)

    def test_a_dry_run_prints_the_same_command(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.convert(clip, "--to", "gif", "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        (line,) = [l for l in result.stdout.splitlines() if " -i " in l]
        words = shlex.split(line)
        self.assertEqual(
            words[words.index("-i"):],
            [
                "-i", str(clip), "-filter_complex", graph("fps=15"), "-map", "[g]",
                "-loop", "0", str(self.dir / "clip.gif"),
            ],
        )
        self.assertIn("note: 64x48, 15 fps", err(result).splitlines())
        self.assertEqual(self.names(), {"clip.mp4"})

    def test_video_and_audio_options_are_refused_with_gif(self) -> None:
        (clip,) = self.touch("clip.mov")
        cases = [
            (["--codec", "h264"], "--codec"),
            (["--quality", "high"], "--quality"),
            (["--crf", "20"], "--crf"),
            (["--resize", "100"], "--resize"),
            (["--fps", "10"], "--fps"),
            (["--no-audio"], "--no-audio"),
            (["--bitrate", "128k"], "--bitrate"),
        ]
        for args, name in cases:
            with self.subTest(args=args):
                result = self.convert(clip, "--to", "gif", *args)
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertIn(
                    f"{name} does not apply to .gif; 'jzr video gif' has the GIF options",
                    err(result),
                )
                self.assertNotIn("Traceback", describe(result))
                self.assertEqual(self.ffmpeg_calls(), [])
                self.assertEqual(self.ffprobe_calls(), [])
        self.assertEqual(self.names(), {"clip.mov"})

    def test_a_pair_command_refuses_the_same_options(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke_command("mp42gif", clip, "--fps", "10")
        self.assertEqual(result.exit_code, 2, describe(result))
        self.assertIn(
            "--fps does not apply to .gif; 'jzr video gif' has the GIF options", err(result)
        )
        self.assertEqual(self.ffmpeg_calls(), [])

    def test_the_options_are_usage_errors_even_when_ffmpeg_is_missing(self) -> None:
        (clip,) = self.touch("clip.mov")
        for name, value in (("--codec", "h264"), ("--crf", "20"), ("--fps", "10"), ("--bitrate", "128k")):
            with self.subTest(option=name):
                result = run_without_tools(["video", "convert", str(clip), "--to", "gif", name, value])
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertIn("does not apply to .gif", err(result))
                self.assertNotIn("was not found", err(result))
        result = run_without_tools(["video", "convert", str(clip), "--to", "gif", "--no-audio"])
        self.assertEqual(result.exit_code, 2, describe(result))

    def test_an_existing_gif_is_skipped_and_overwrite_replaces_it(self) -> None:
        clip, existing = self.touch("clip.mp4", "clip.gif")
        result = self.convert(clip, "--to", "gif")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {clip}: target exists (use --overwrite)", err(result))
        self.assertEqual(existing.read_bytes(), b"source")
        result = self.convert(clip, "--to", "gif", "--overwrite")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(existing.read_bytes(), b"trimmed")


if __name__ == "__main__":
    unittest.main()
