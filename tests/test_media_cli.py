"""CLI behaviour of `jzr video info|formats` and `jzr audio info|formats`."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core import ffmpeg, media
from jzr_tools.core.media import info_rows

from media_fixtures import HAVE_FFMPEG, Clips, make_fake_tool, recorded_calls

WHICH = "jzr_tools.core.ffmpeg.shutil.which"
runner = CliRunner()

ENV_WITHOUT_TOOLS = {"JZR_FFMPEG", "JZR_FFPROBE", "JZR_DATA_DIR"}
NOT_FOUND = "{} was not found. Run: jzr ffmpeg install"

# An empty folder standing in for jzr's own data folder, so a copy installed on
# the machine running the tests is never found.
_EMPTY_DATA = tempfile.TemporaryDirectory(prefix="jzr-empty-data-")


def err(result: object) -> str:
    try:
        return result.stderr
    except (AttributeError, ValueError):
        return ""


def describe(result: object) -> str:
    return f"exit={result.exit_code}\nstdout={result.stdout!r}\nstderr={err(result)!r}"


def without_tools(on_path: tuple[str, ...] = ()):  # noqa: ANN201
    """Environment where the named tools (none by default) are only on PATH."""
    environ = {k: v for k, v in os.environ.items() if k not in ENV_WITHOUT_TOOLS}
    environ["JZR_DATA_DIR"] = _EMPTY_DATA.name
    found = {name: f"C:/bin/{name}" for name in on_path}
    return (
        mock.patch.dict(os.environ, environ, clear=True),
        mock.patch(WHICH, side_effect=lambda name, *a, **k: found.get(Path(name).stem)),
    )


def run_without_tools(args: list[str]):  # noqa: ANN201
    env, which = without_tools()
    with env, which:
        return runner.invoke(app, args)


class MissingToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.file = Path(self._tmp.name) / "clip.mp4"
        self.file.write_bytes(b"not really media")

    def test_info_without_ffprobe_says_so_once_and_exits_1(self) -> None:
        for group in ("video", "audio"):
            with self.subTest(group=group):
                env, which = without_tools(on_path=("ffmpeg",))
                with env, which:
                    result = runner.invoke(app, [group, "info", str(self.file)])
                self.assertEqual(result.exit_code, 1, describe(result))
                self.assertEqual(err(result).strip(), NOT_FOUND.format("ffprobe"), describe(result))
                self.assertNotIn("Traceback", describe(result))
                self.assertEqual(result.stdout.strip(), "")

    def test_info_without_any_ffmpeg_says_so_once(self) -> None:
        result = run_without_tools(["video", "info", str(self.file)])
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(err(result).strip(), NOT_FOUND.format("ffprobe"), describe(result))
        self.assertNotIn("Traceback", describe(result))

    def test_formats_without_ffmpeg_still_succeeds(self) -> None:
        for group, targets in (("video", "mp4"), ("audio", "mp3")):
            with self.subTest(group=group):
                result = run_without_tools([group, "formats"])
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertIn("targets:", result.stdout)
                self.assertIn(targets, result.stdout)
                self.assertIn("ffmpeg: not found", result.stdout)
                self.assertEqual(err(result).splitlines()[0], NOT_FOUND.format("ffmpeg"), describe(result))
                self.assertNotIn("libmp3lame", result.stdout)
                self.assertNotIn("libx264", result.stdout)
                self.assertNotIn("Traceback", describe(result))


class FormatsWithFakeToolsTests(unittest.TestCase):
    """`formats` against stand-in tools, so versions and encoder lists are known."""

    ENCODERS = (
        "Encoders:\n V..... = Video\n ------\n"
        " V....D libx264              libx264 H.264\n"
        " V....D libx265              libx265 H.265\n"
        " A....D aac                  AAC\n"
        " A....D libmp3lame           MP3\n"
        " A....D flac                 FLAC\n"
        " A....D pcm_s16le            PCM\n"
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        folder = Path(self._tmp.name)
        self.ffmpeg = make_fake_tool(
            folder, "ffmpeg",
            version_text="ffmpeg version 9.9.9-test Copyright (c) the FFmpeg developers\n",
            encoders_text=self.ENCODERS,
        )
        self.ffprobe = make_fake_tool(
            folder, "ffprobe",
            version_text="ffprobe version 8.8.8-probe Copyright (c) the FFmpeg developers\n",
        )

    def invoke(self, group: str):  # noqa: ANN201
        environ = {**os.environ, "JZR_FFMPEG": self.ffmpeg, "JZR_FFPROBE": self.ffprobe}
        with mock.patch.dict(os.environ, environ, clear=True):
            return runner.invoke(app, [group, "formats"])

    def rows(self, result: object) -> dict[str, list[str]]:
        """Encoder rows as {codec: [codec, encoder, status]}."""
        lines = result.stdout.splitlines()
        start = lines.index("encoders:") + 1
        found = {}
        for line in lines[start:]:
            parts = line.split()
            if parts:
                found[parts[0]] = parts
        return found

    def test_video_formats_lists_targets_tools_and_encoder_status(self) -> None:
        result = self.invoke("video")
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        targets = next(l for l in lines if l.startswith("targets:")).split()[1:]
        for container in media.VIDEO_CONTAINERS:
            self.assertIn(container, targets)
        self.assertIn(f"ffmpeg: {self.ffmpeg} (9.9.9-test, from environment)", lines)
        self.assertIn(f"ffprobe: {self.ffprobe} (8.8.8-probe, from environment)", lines)

        rows = self.rows(result)
        for codec, encoder in {**media.VIDEO_ENCODERS, **media.AUDIO_ENCODERS}.items():
            with self.subTest(codec=codec):
                self.assertIn(codec, rows)
                present = encoder in {"libx264", "libx265", "aac", "libmp3lame", "flac", "pcm_s16le"}
                self.assertEqual(rows[codec][1:], [encoder, "yes" if present else "missing"])

    def test_audio_formats_lists_only_audio(self) -> None:
        result = self.invoke("audio")
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        self.assertIn("targets: " + " ".join(media.AUDIO_FORMATS), lines)
        self.assertIn(f"ffmpeg: {self.ffmpeg} (9.9.9-test, from environment)", lines)
        rows = self.rows(result)
        for codec in media.AUDIO_ENCODERS:
            self.assertIn(codec, rows)
        for codec in media.VIDEO_ENCODERS:
            self.assertNotIn(codec, rows)

    def test_formats_only_asks_ffprobe_for_its_version(self) -> None:
        self.invoke("video")
        for call in recorded_calls(self.ffprobe):
            self.assertIn("-version", call)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not on PATH")
class RealToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.clips = Clips()
        cls.addClassCleanup(cls.clips.close)
        if cls.clips.error:
            raise unittest.SkipTest(cls.clips.error)
        cls.tools = ffmpeg.locate()

    def p(self, name: str) -> str:
        return str(self.clips.path(name))

    def test_video_info_text(self) -> None:
        result = runner.invoke(app, ["video", "info", self.p("clip.mp4")])
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], self.p("clip.mp4"))
        body = lines[1:]
        self.assertTrue(body and all(l.startswith("  ") for l in body), result.stdout)
        self.assertIn("h264, 64x48, 10", result.stdout)
        self.assertIn("yuv420p", result.stdout)
        self.assertRegex(result.stdout, r"(?m)^  audio +aac, 1 ch \(mono\), 44100 Hz")
        self.assertNotIn("title", result.stdout)

        expected = info_rows(ffmpeg.probe(Path(self.p("clip.mp4")), self.tools.ffprobe), tags=False)
        starts = set()
        for label, value in expected:
            pattern = rf"  {re.escape(label)}( +){re.escape(value)}"
            match = next((m for m in map(lambda l: re.fullmatch(pattern, l), body) if m), None)
            self.assertIsNotNone(match, f"no row for {label}={value!r} in {body}")
            starts.add(2 + len(label) + len(match.group(1)))
        self.assertEqual(len(starts), 1, "values do not start in one column")

    def test_several_files_are_separated_by_a_blank_line(self) -> None:
        result = runner.invoke(app, ["video", "info", self.p("clip.mp4"), self.p("rotated.mp4")])
        self.assertEqual(result.exit_code, 0, describe(result))
        blocks = result.stdout.strip().split("\n\n")
        self.assertEqual(len(blocks), 2, result.stdout)
        self.assertEqual(blocks[0].splitlines()[0], self.p("clip.mp4"))
        self.assertEqual(blocks[1].splitlines()[0], self.p("rotated.mp4"))
        self.assertNotIn("rotation", blocks[0])
        self.assertRegex(blocks[1], "rotation +270\N{DEGREE SIGN} clockwise")

    def test_video_info_json_keeps_numbers_as_numbers(self) -> None:
        result = runner.invoke(app, ["video", "info", "--json", self.p("clip.mp4")])
        self.assertEqual(result.exit_code, 0, describe(result))
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        entry = data[0]
        self.assertEqual(entry["file"], self.p("clip.mp4"))
        self.assertIsInstance(entry["duration"], float)
        self.assertIsInstance(entry["size"], int)
        self.assertEqual(entry["size"], os.path.getsize(self.p("clip.mp4")))
        self.assertEqual(entry["video"]["codec"], "h264")
        self.assertEqual((entry["video"]["width"], entry["video"]["height"]), (64, 48))
        self.assertIsInstance(entry["video"]["fps"], float)
        self.assertIs(entry["video"]["hdr"], False)
        self.assertEqual(entry["video"]["rotation"], 0)
        self.assertEqual(entry["audio"][0]["codec"], "aac")
        self.assertEqual(entry["audio"][0]["sample_rate"], 44100)
        self.assertIsInstance(entry["audio"][0]["channels"], int)
        self.assertIs(entry["cover_art"], False)

    def test_audio_info_lists_tags_and_cover_art(self) -> None:
        result = runner.invoke(app, ["audio", "info", self.p("tagged.mp3")])
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertRegex(result.stdout, r"(?m)^  title +Song$")
        self.assertRegex(result.stdout, r"(?m)^  artist +Band$")
        self.assertRegex(result.stdout, r"(?m)^  album +Disc$")
        self.assertRegex(result.stdout, r"(?m)^  year +2020$")
        self.assertRegex(result.stdout, r"(?m)^  cover art +yes$")
        self.assertNotRegex(result.stdout, r"(?m)^  video ")

    def test_audio_info_json_has_null_video_and_the_tags(self) -> None:
        result = runner.invoke(app, ["audio", "info", "--json", self.p("tagged.opus")])
        self.assertEqual(result.exit_code, 0, describe(result))
        (entry,) = json.loads(result.stdout)
        self.assertIsNone(entry["video"])
        self.assertEqual(entry["tags"]["title"], "Opus Song")
        self.assertEqual(entry["audio"][0]["codec"], "opus")

    def test_video_info_does_not_print_tag_rows(self) -> None:
        result = runner.invoke(app, ["video", "info", self.p("tagged.mp3")])
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertNotRegex(result.stdout, r"(?m)^  (title|artist|album|cover art) ")

    def test_one_bad_file_fails_alone(self) -> None:
        good, bad = self.p("clip.mp4"), self.p("notes.txt")
        result = runner.invoke(app, ["video", "info", good, bad, self.p("rotated.mp4")])
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(good, result.stdout)
        self.assertIn(self.p("rotated.mp4"), result.stdout)
        self.assertNotIn(bad, result.stdout)
        self.assertIn(f"fail {bad}: not a readable media file", err(result))
        self.assertNotIn("Traceback", err(result))

    def test_json_lists_only_the_files_that_worked(self) -> None:
        result = runner.invoke(
            app, ["audio", "info", "--json", self.p("notes.txt"), self.p("plain.flac")]
        )
        self.assertEqual(result.exit_code, 1, describe(result))
        data = json.loads(result.stdout)
        self.assertEqual([d["file"] for d in data], [self.p("plain.flac")])
        self.assertIn("fail ", err(result))

    def test_a_file_that_does_not_exist_is_a_usage_error(self) -> None:
        missing = self.p("no-such-file.mp4")
        for group in ("video", "audio"):
            with self.subTest(group=group):
                result = runner.invoke(app, [group, "info", missing])
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertNotIn("Traceback", describe(result))

    def test_info_needs_at_least_one_file(self) -> None:
        result = runner.invoke(app, ["video", "info"])
        self.assertEqual(result.exit_code, 2, describe(result))

    def test_awkward_file_names(self) -> None:
        names = ["with space.mp4", "100% real.mp4", "-dash first.mp4", "\u0432\u0438\u0434\u0435\u043e.mp4"]
        for name in names:
            with self.subTest(name=name):
                path = self.clips.path(name)
                path.write_bytes(self.clips.path("clip.mp4").read_bytes())
                result = runner.invoke(app, ["video", "info", "--json", str(path)])
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertEqual(json.loads(result.stdout)[0]["video"]["codec"], "h264")

    def test_formats_with_the_real_build(self) -> None:
        for group in ("video", "audio"):
            with self.subTest(group=group):
                result = runner.invoke(app, [group, "formats"])
                self.assertEqual(result.exit_code, 0, describe(result))
                version = ffmpeg.version(self.tools.ffmpeg)
                pattern = rf"(?m)^ffmpeg: .+ \({re.escape(version)}, from (PATH|environment)\)$"
                self.assertRegex(result.stdout, pattern)
                self.assertRegex(result.stdout, r"(?m)^ffprobe: .+ \(.+, from (PATH|environment)\)$")
                names = ffmpeg.encoders(self.tools.ffmpeg)
                lines = result.stdout.splitlines()
                for codec, encoder in media.AUDIO_ENCODERS.items():
                    row = next(l.split() for l in lines if l.split()[:2] == [codec, encoder])
                    self.assertEqual(row[2], "yes" if encoder in names else "missing")


if __name__ == "__main__":
    unittest.main()
