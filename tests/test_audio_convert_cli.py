"""CLI behaviour of `jzr audio convert` and `jzr audio <from>2<to>` against stand-in tools.

The stand-in ffprobe answers every call with the same JSON, and the stand-in
ffmpeg writes whatever output path it is given, so each test describes the
source it needs and checks the commands that were started.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app

from media_fixtures import recorded_calls
from size_fixtures import with_size
from test_media_cli import describe, err, run_without_tools

runner = CliRunner()

_SCRIPT = r'''
import json, os, sys

spec_path = sys.argv[1]
args = sys.argv[2:]
with open(spec_path, encoding="utf-8") as handle:
    spec = json.load(handle)
with open(spec_path + ".argv", "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")

marker = spec.get("fail_marker")
if marker and any(marker in os.path.basename(arg) for arg in args):
    sys.stderr.write(spec.get("fail_stderr", ""))
    sys.exit(spec.get("fail_code", 1))
for arg in args:
    if os.path.basename(arg).startswith("jzr-tmp-"):
        with open(arg, "wb") as handle:
            handle.write(spec.get("output_bytes", "converted").encode("ascii"))
sys.stdout.write(spec.get("stdout", ""))
sys.stderr.write(spec.get("stderr", ""))
sys.exit(spec.get("code", 0))
'''

FLAGS = ["-hide_banner", "-nostdin", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y"]
MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"


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


def probe_json(
    codec: str = "pcm_s16le",
    container: str = "wav",
    *,
    bits: int = 16,
    video: bool = False,
    audio: bool = True,
) -> str:
    streams: list[dict[str, object]] = []
    if video:
        streams.append(
            {
                "index": 0, "codec_type": "video", "codec_name": "h264",
                "width": 64, "height": 48, "avg_frame_rate": "10/1", "pix_fmt": "yuv420p",
            }
        )
    if audio:
        streams.append(
            {
                "index": len(streams), "codec_type": "audio", "codec_name": codec,
                "channels": 1, "channel_layout": "mono", "sample_rate": "44100",
                "bits_per_sample": bits,
            }
        )
    return json.dumps(
        {"streams": streams, "format": {"format_name": container, "duration": "1.0", "size": "1000"}}
    )


def plan_of(call: list[str]) -> list[str]:
    """The ffmpeg arguments between the source and the output path."""
    return call[call.index("-i") + 2 : -1]


def source_of(call: list[str]) -> str:
    return call[call.index("-i") + 1]


class FakeToolsCase(unittest.TestCase):
    PROBE = probe_json()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        self.dir = root / "music"
        self.dir.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        self.install()

    def install(self, probe: str | None = None, ffprobe: dict | None = None, ffmpeg: dict | None = None) -> None:
        self.ffprobe = make_tool(
            self.bin, "ffprobe", stdout=self.PROBE if probe is None else probe, **(ffprobe or {})
        )
        self.ffmpeg = make_tool(self.bin, "ffmpeg", **(ffmpeg or {}))

    def touch(self, *names: str, root: Path | None = None) -> list[Path]:
        paths = []
        for name in names:
            path = (root or self.dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"source")
            paths.append(path)
        return paths

    def invoke(self, *args: object):  # noqa: ANN201
        environ = {**os.environ, "JZR_FFMPEG": self.ffmpeg, "JZR_FFPROBE": self.ffprobe}
        with mock.patch.dict(os.environ, environ, clear=True):
            return runner.invoke(app, ["audio", *map(str, args)])

    def ffmpeg_calls(self) -> list[list[str]]:
        return [c for c in recorded_calls(self.ffmpeg) if "-i" in c]

    def ffprobe_calls(self) -> list[list[str]]:
        return [c for c in recorded_calls(self.ffprobe) if "-i" in c]

    def names(self, folder: Path | None = None) -> set[str]:
        return {p.name for p in (folder or self.dir).iterdir()}


class UsageErrorTests(FakeToolsCase):
    def assert_usage_error(self, result: object, *needles: str) -> None:
        self.assertEqual(result.exit_code, 2, describe(result))
        self.assertNotIn("Traceback", describe(result))
        for needle in needles:
            self.assertIn(needle, err(result), describe(result))
        self.assertEqual(self.ffmpeg_calls(), [])

    def test_a_target_is_required(self) -> None:
        (song,) = self.touch("song.wav")
        self.assert_usage_error(self.invoke("convert", song), "--to")

    def test_an_unsupported_target_is_refused_naming_it(self) -> None:
        (song,) = self.touch("song.wav")
        for value in ("xyz", "aac", "mp4", "aiff"):
            with self.subTest(value=value):
                self.assert_usage_error(self.invoke("convert", song, "--to", value), value)

    def test_the_target_ignores_case_and_a_leading_dot(self) -> None:
        (song,) = self.touch("song.wav")
        for value in ("MP3", ".mp3", ".Mp3"):
            with self.subTest(value=value):
                result = self.invoke("convert", song, "--to", value, "--overwrite")
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertIn(f"{song} -> {song.with_suffix('.mp3')}", result.stdout)

    def test_an_unreadable_source_format_is_refused_naming_it(self) -> None:
        (song,) = self.touch("song.wav")
        for value in ("mp4", "xyz"):
            with self.subTest(value=value):
                self.assert_usage_error(
                    self.invoke("convert", song, "--to", "mp3", "--from", value), value
                )

    def test_a_bad_bitrate_is_refused_naming_it(self) -> None:
        (song,) = self.touch("song.wav")
        for value in ("fast", "4", "600k"):
            with self.subTest(value=value):
                self.assert_usage_error(
                    self.invoke("convert", song, "--to", "mp3", "--bitrate", value), value
                )

    def test_a_missing_file_is_a_usage_error(self) -> None:
        missing = self.dir / "gone.wav"
        self.assert_usage_error(
            self.invoke("convert", missing, "--to", "mp3"), f"File not found: {missing}"
        )

    def test_dir_and_recursive_conflict_with_listed_sources(self) -> None:
        (song,) = self.touch("song.wav")
        for extra in (["--dir", str(self.dir)], ["--recursive"]):
            with self.subTest(extra=extra):
                self.assert_usage_error(self.invoke("convert", song, "--to", "mp3", *extra))

    def test_from_lets_the_extension_be_left_off(self) -> None:
        self.touch("song.wav")
        result = self.invoke("convert", self.dir / "song", "--from", "wav", "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"{self.dir / 'song.wav'} -> {self.dir / 'song.mp3'}", result.stdout)

    def test_from_refuses_a_listed_file_of_another_extension(self) -> None:
        (song,) = self.touch("song.flac")
        self.assert_usage_error(self.invoke("convert", song, "--from", "wav", "--to", "mp3"))

    def test_without_ffmpeg_a_bad_option_is_still_a_usage_error(self) -> None:
        result = run_without_tools(["audio", "convert", "--to", "xyz"])
        self.assertEqual(result.exit_code, 2, describe(result))


class OutputFileTests(FakeToolsCase):
    def test_output_names_the_file_and_creates_its_folder(self) -> None:
        (song,) = self.touch("song.wav")
        target = self.dir / "out" / "x.mp3"
        result = self.invoke("convert", song, "-o", target)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue(target.is_file())
        self.assertIn(with_size(f"{song} -> {target} (audio encoded to mp3)", target), result.stdout)

    def test_the_target_format_is_taken_from_the_output_extension(self) -> None:
        (song,) = self.touch("song.wav")
        result = self.invoke("convert", song, "--output", self.dir / "song2.m4a")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertIn("aac", plan_of(call))
        self.assertTrue((self.dir / "song2.m4a").is_file())

    def test_output_without_a_known_extension_and_without_to_is_a_usage_error(self) -> None:
        (song,) = self.touch("song.wav")
        for name in ("noext", "x.xyz"):
            with self.subTest(name=name):
                result = self.invoke("convert", song, "-o", self.dir / name)
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertEqual(self.ffmpeg_calls(), [])


class ScanTests(FakeToolsCase):
    def converted_sources(self) -> set[str]:
        return {Path(source_of(c)).name for c in self.ffmpeg_calls()}

    def test_only_audio_files_that_are_not_the_target_are_picked(self) -> None:
        self.touch(
            "a.wav", "b.flac", "c.mp3", "d.opus", "e.aiff", "f.mka", "g.oga", "h.wma",
            "clip.mp4", "notes.txt", "cover.jpg",
        )
        result = self.invoke("convert", "--dir", self.dir, "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(
            self.converted_sources(),
            {"a.wav", "b.flac", "d.opus", "e.aiff", "f.mka", "g.oga", "h.wma"},
        )

    def test_a_wav_target_leaves_wav_files_alone(self) -> None:
        self.touch("a.wav", "b.flac")
        result = self.invoke("convert", "--dir", self.dir, "--to", "wav")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.converted_sources(), {"b.flac"})

    def test_from_limits_the_scan_to_one_extension(self) -> None:
        self.touch("a.wav", "b.flac", "c.opus")
        result = self.invoke("convert", "--dir", self.dir, "--from", "flac", "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.converted_sources(), {"b.flac"})

    def test_pattern_filters_by_file_name(self) -> None:
        self.touch("take1.wav", "take2.wav", "other.wav")
        result = self.invoke("convert", "--dir", self.dir, "--to", "mp3", "--pattern", r"^take")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.converted_sources(), {"take1.wav", "take2.wav"})

    def test_subfolders_are_scanned_only_with_recursive(self) -> None:
        self.touch("top.wav", "sub/deep.wav")
        self.invoke("convert", "--dir", self.dir, "--to", "mp3")
        self.assertEqual(self.converted_sources(), {"top.wav"})
        self.invoke("convert", "--dir", self.dir, "--to", "mp3", "--recursive", "--overwrite")
        self.assertIn("deep.wav", self.converted_sources())

    def test_nothing_to_convert_says_so_and_exits_1(self) -> None:
        self.touch("clip.mp4", "notes.txt", "only.mp3")
        result = self.invoke("convert", "--dir", self.dir, "--to", "mp3")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("No matching files.", err(result))
        self.assertEqual(self.ffmpeg_calls(), [])

    def test_the_default_folder_is_the_current_one(self) -> None:
        self.touch("here.wav")
        before = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, before)
        result = self.invoke("convert", "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.converted_sources(), {"here.wav"})


class OutputPlacementTests(FakeToolsCase):
    def test_the_output_sits_beside_the_source_with_the_new_extension(self) -> None:
        self.touch("song.wav")
        result = self.invoke("convert", "--dir", self.dir, "--to", "flac")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.names(), {"song.wav", "song.flac"})

    def test_outdir_collects_the_outputs_and_mirrors_subfolders(self) -> None:
        self.touch("a.wav", "sub/b.wav")
        out = self.dir / "out"
        result = self.invoke(
            "convert", "--dir", self.dir, "--recursive", "--to", "mp3", "--outdir", out
        )
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue((out / "a.mp3").is_file())
        self.assertTrue((out / "sub" / "b.mp3").is_file())
        self.assertFalse((self.dir / "a.mp3").exists())

    def test_files_that_would_share_a_target_get_a_numbered_name(self) -> None:
        self.touch("same.wav", "same.flac")
        result = self.invoke("convert", "--dir", self.dir, "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.names() & {"same.mp3", "same-2.mp3"}, {"same.mp3", "same-2.mp3"})

    def test_no_temporary_file_is_left_behind(self) -> None:
        self.touch("a.wav", "b.wav")
        self.invoke("convert", "--dir", self.dir, "--to", "mp3")
        self.assertEqual({n for n in self.names() if n.startswith("jzr-tmp-")}, set())


class ResultLineTests(FakeToolsCase):
    def test_success_prints_source_target_and_note(self) -> None:
        (song,) = self.touch("song.wav")
        result = self.invoke("convert", song, "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        # The stand-in ffmpeg writes the nine bytes of "converted".
        self.assertIn(f"{song} -> {self.dir / 'song.mp3'} (audio encoded to mp3, 9 B)", lines)
        self.assertIn("ffmpeg: 1 converted", lines)
        self.assertEqual((self.dir / "song.mp3").read_bytes(), b"converted")
        self.assertEqual(err(result), "")

    def test_a_copy_is_reported_as_a_copy(self) -> None:
        self.install(probe_json("mp3", "mp3", bits=0))
        (song,) = self.touch("song.mp3")
        out = self.dir / "out"
        result = self.invoke("convert", song, "--to", "mp3", "--outdir", out)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(with_size(f"{song} -> {out / 'song.mp3'} (audio copied)", out / "song.mp3"), result.stdout)
        (call,) = self.ffmpeg_calls()
        self.assertEqual(plan_of(call)[-4:], ["-c:a", "copy", "-id3v2_version", "3"])

    def test_the_ffmpeg_command_has_the_planned_arguments(self) -> None:
        (song,) = self.touch("song.wav")
        result = self.invoke("convert", song, "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(call[: len(FLAGS)], FLAGS)
        self.assertEqual(source_of(call), str(song))
        self.assertEqual(
            plan_of(call),
            [
                "-map", "0:a:0", "-map_metadata", "0",
                "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3",
            ],
        )
        self.assertTrue(Path(call[-1]).name.startswith("jzr-tmp-"))
        self.assertEqual(Path(call[-1]).suffix, ".mp3")

    def test_bitrate_sets_a_fixed_rate(self) -> None:
        (song,) = self.touch("song.wav")
        result = self.invoke("convert", song, "--to", "mp3", "--bitrate", "128")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        plan = plan_of(call)
        self.assertEqual(plan[plan.index("-c:a") :][:4], ["-c:a", "libmp3lame", "-b:a", "128k"])
        self.assertNotIn("-q:a", plan)

    def test_no_progress_text_appears_without_a_terminal(self) -> None:
        self.install(ffmpeg={"stdout": "out_time_us=500000\nprogress=end\n"})
        (song,) = self.touch("song.wav")
        result = self.invoke("convert", song, "--to", "mp3")
        self.assertEqual(err(result), "")
        self.assertNotIn("%", result.stdout)


class SkipAndFailTests(FakeToolsCase):
    def test_an_existing_target_is_skipped_without_probing(self) -> None:
        (song,) = self.touch("song.wav")
        (existing,) = self.touch("song.mp3")
        result = self.invoke("convert", song, "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {song}: target exists (use --overwrite)", err(result))
        self.assertEqual(existing.read_bytes(), b"source")
        self.assertEqual(self.ffprobe_calls(), [])
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertIn("ffmpeg: 1 skipped", result.stdout)

    def test_overwrite_replaces_the_existing_target(self) -> None:
        (song,) = self.touch("song.wav")
        (existing,) = self.touch("song.mp3")
        result = self.invoke("convert", song, "--to", "mp3", "--overwrite")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(existing.read_bytes(), b"converted")
        self.assertNotIn("skip", err(result))

    def test_a_source_that_is_its_own_target_is_skipped_without_probing(self) -> None:
        (song,) = self.touch("song.mp3")
        result = self.invoke("convert", song, "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {song}: source is the target", err(result))
        self.assertEqual(self.ffprobe_calls(), [])
        self.assertEqual(song.read_bytes(), b"source")

    def test_an_unreadable_file_fails(self) -> None:
        self.install(ffprobe={"code": 1, "stderr": "Invalid data found\n"}, probe="")
        (song,) = self.touch("song.wav")
        result = self.invoke("convert", song, "--to", "mp3")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {song}: not a readable media file", err(result))
        self.assertIn("ffmpeg: 1 failed", result.stdout)
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertNotIn("Traceback", describe(result))

    def test_a_file_without_audio_fails(self) -> None:
        self.install(probe_json(video=True, audio=False, container=MP4_FAMILY))
        (clip,) = self.touch("silent.mp4")
        result = self.invoke("convert", clip, "--to", "mp3")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {clip}: no audio track", err(result))
        self.assertEqual(self.ffmpeg_calls(), [])

    def test_an_ffmpeg_failure_reports_its_reason_and_leaves_nothing(self) -> None:
        self.install(ffmpeg={"code": 1, "stderr": "Error while encoding: disk is full\n"})
        (song,) = self.touch("song.wav")
        result = self.invoke("convert", song, "--to", "mp3")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {song}: Error while encoding: disk is full", err(result))
        self.assertEqual(self.names(), {"song.wav"})

    def test_one_bad_file_fails_alone_and_the_rest_convert(self) -> None:
        self.install(ffprobe={"fail_marker": "broken", "fail_code": 1})
        good1, bad, good2 = self.touch("a.wav", "broken.wav", "c.wav")
        result = self.invoke("convert", good1, bad, good2, "--to", "mp3")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(err(result).count("fail "), 1, describe(result))
        self.assertIn(f"fail {bad}: not a readable media file", err(result))
        self.assertIn(f"{good1} -> {self.dir / 'a.mp3'}", result.stdout)
        self.assertIn(f"{good2} -> {self.dir / 'c.mp3'}", result.stdout)
        self.assertIn("ffmpeg: 2 converted, 1 failed", result.stdout.splitlines())
        self.assertEqual(self.names(), {"a.wav", "a.mp3", "broken.wav", "c.wav", "c.mp3"})

    def test_an_ffmpeg_failure_on_one_file_does_not_stop_the_others(self) -> None:
        self.install(ffmpeg={"fail_marker": "broken", "fail_stderr": "Conversion exploded\n"})
        good, bad = self.touch("a.wav", "broken.wav")
        result = self.invoke("convert", bad, good, "--to", "mp3")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {bad}: Conversion exploded", err(result))
        self.assertTrue((self.dir / "a.mp3").is_file())
        self.assertFalse((self.dir / "broken.mp3").exists())


class ListedVideoTests(FakeToolsCase):
    PROBE = probe_json("aac", MP4_FAMILY, bits=0, video=True)

    def test_a_listed_mp4_is_converted_using_only_its_audio(self) -> None:
        (clip,) = self.touch("clip.mp4")
        result = self.invoke("convert", clip, "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(
            with_size(f"{clip} -> {self.dir / 'clip.mp3'} (audio encoded to mp3)", self.dir / "clip.mp3"),
            result.stdout,
        )
        (call,) = self.ffmpeg_calls()
        self.assertEqual(
            plan_of(call),
            [
                "-map", "0:a:0", "-map_metadata", "0",
                "-metadata", "major_brand=",
                "-metadata", "minor_version=",
                "-metadata", "compatible_brands=",
                "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3",
            ],
        )
        self.assertNotIn("0:v", plan_of(call))

    def test_scanning_never_picks_up_video_files(self) -> None:
        self.touch("clip.mp4", "movie.mkv", "song.wav")
        result = self.invoke("convert", "--dir", self.dir, "--to", "mp3")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual([Path(source_of(c)).name for c in self.ffmpeg_calls()], ["song.wav"])


class DryRunTests(FakeToolsCase):
    def test_dry_run_prints_the_command_and_the_note_and_writes_nothing(self) -> None:
        (song,) = self.touch("song.wav")
        result = self.invoke("convert", song, "--to", "mp3", "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        expected = shlex.join(
            [
                self.ffmpeg, *FLAGS, "-i", str(song),
                "-map", "0:a:0", "-map_metadata", "0",
                "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3",
                str(self.dir / "song.mp3"),
            ]
        )
        self.assertIn(expected, result.stdout.splitlines())
        self.assertIn("note: audio encoded to mp3", err(result).splitlines())
        self.assertEqual(self.names(), {"song.wav"})
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertEqual(len(self.ffprobe_calls()), 1)

    def test_dry_run_with_output_names_that_path(self) -> None:
        (song,) = self.touch("song.wav")
        target = self.dir / "made" / "x.mp3"
        result = self.invoke("convert", song, "-o", target, "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(shlex.quote(str(target)), result.stdout)
        self.assertFalse(target.parent.exists())
        self.assertEqual(self.ffmpeg_calls(), [])

    def test_dry_run_still_reports_a_file_without_audio(self) -> None:
        self.install(probe_json(video=True, audio=False, container=MP4_FAMILY))
        (clip,) = self.touch("silent.mp4")
        result = self.invoke("convert", clip, "--to", "mp3", "--dry-run")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {clip}: no audio track", err(result))


class BitrateCaveatTests(FakeToolsCase):
    def test_a_lossless_target_says_once_that_the_bitrate_is_ignored(self) -> None:
        self.touch("a.mp3", "b.mp3")
        self.install(probe_json("mp3", "mp3", bits=0))
        result = self.invoke("convert", "--dir", self.dir, "--to", "flac", "--bitrate", "128k")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(
            err(result).count("note: .flac is lossless; --bitrate is ignored"), 1, describe(result)
        )
        self.assertTrue((self.dir / "a.flac").is_file())
        self.assertTrue((self.dir / "b.flac").is_file())
        self.assertNotIn("-b:a", " ".join(" ".join(c) for c in self.ffmpeg_calls()))

    def test_wav_says_so_too(self) -> None:
        (song,) = self.touch("song.mp3")
        self.install(probe_json("mp3", "mp3", bits=0))
        result = self.invoke("convert", song, "--to", "wav", "-b", "96k")
        self.assertIn("note: .wav is lossless; --bitrate is ignored", err(result))
        self.assertTrue((self.dir / "song.wav").is_file())

    def test_a_lossy_target_or_no_bitrate_says_nothing(self) -> None:
        (song,) = self.touch("song.wav")
        self.assertNotIn("lossless", err(self.invoke("convert", song, "--to", "mp3", "--bitrate", "96")))
        self.assertNotIn("lossless", err(self.invoke("convert", song, "--to", "flac")))


class ToolLookupTests(FakeToolsCase):
    def test_missing_tools_are_reported_once_before_any_file(self) -> None:
        (song,) = self.touch("song.wav")
        result = run_without_tools(["audio", "convert", str(song), "--to", "mp3"])
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(err(result).count("was not found"), 1, describe(result))
        self.assertNotIn("Traceback", describe(result))
        self.assertEqual(self.names(), {"song.wav"})

    def test_a_tool_variable_naming_no_file_is_reported(self) -> None:
        (song,) = self.touch("song.wav")
        environ = {**os.environ, "JZR_FFMPEG": str(self.bin / "nope"), "JZR_FFPROBE": self.ffprobe}
        with mock.patch.dict(os.environ, environ, clear=True):
            result = runner.invoke(app, ["audio", "convert", str(song), "--to", "mp3"])
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("JZR_FFMPEG", err(result))


class PairCommandTests(FakeToolsCase):
    def test_wav2mp3_converts_wav_files_only(self) -> None:
        self.touch("a.wav", "b.flac")
        result = self.invoke("wav2mp3", "--dir", self.dir)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual([Path(source_of(c)).name for c in self.ffmpeg_calls()], ["a.wav"])
        self.assertTrue((self.dir / "a.mp3").is_file())

    def test_a_listed_source_may_leave_its_extension_off(self) -> None:
        self.touch("song.wav")
        result = self.invoke("wav2mp3", self.dir / "song")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"{self.dir / 'song.wav'} -> {self.dir / 'song.mp3'}", result.stdout)

    def test_pairs_support_bitrate_output_and_dry_run(self) -> None:
        (song,) = self.touch("song.flac")
        self.install(probe_json("flac", "flac", bits=16))
        result = self.invoke(
            "flac2m4a", song, "-b", "96k", "-o", self.dir / "o" / "x.m4a", "--dry-run"
        )
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn("aac -b:a 96k", result.stdout.replace("'", ""))

    def test_the_documented_pairs_resolve(self) -> None:
        for pair in ("wav2mp3", "flac2m4a", "m4a2mp3", "aiff2flac", "mp32opus", "ogg2wav", "WAV2MP3"):
            with self.subTest(pair=pair):
                result = self.invoke(pair, "--help")
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertIn("Usage:", result.stdout)

    def test_unknown_pairs_are_no_such_command(self) -> None:
        for pair in ("mp42mp3", "wav2xyz", "foo2mp3", "mp32aac", "wav2"):
            with self.subTest(pair=pair):
                result = self.invoke(pair, "--help")
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertIn("No such command", err(result))

    def test_a_pair_has_no_to_or_from_option(self) -> None:
        result = self.invoke("wav2mp3", "--help")
        options = result.stdout.split("Options:", 1)[1]
        self.assertNotIn("--to", options)
        self.assertNotIn("--from", options)
        self.assertIn("--bitrate", options)


class HelpTests(FakeToolsCase):
    def test_convert_and_pairs_show_output_and_outdir_without_a_short_outdir(self) -> None:
        for args in (["convert", "--help"], ["wav2mp3", "--help"]):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertIn("-o, --output FILE", result.stdout)
                self.assertIn("--outdir DIRECTORY", result.stdout)
                self.assertNotIn("-o, --outdir", result.stdout)

    def test_options_are_listed_in_the_agreed_order(self) -> None:
        result = self.invoke("convert", "--help")
        self.assertEqual(result.exit_code, 0, describe(result))
        text = result.stdout.split("Options:", 1)[1]
        order = ["--dir", "--pattern", "--recursive", "--output", "--outdir", "--bitrate", "--overwrite", "--dry-run"]
        # The long name that starts each option row, not mentions in help text.
        listed = re.findall(r"^  (?:-\w, )?(--[\w-]+)", text, flags=re.MULTILINE)
        self.assertEqual([name for name in listed if name in order], order)

    def test_the_audio_group_lists_convert(self) -> None:
        result = self.invoke("--help")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertRegex(result.stdout, r"(?m)^  convert\s+\S")


if __name__ == "__main__":
    unittest.main()
