"""CLI behaviour of `jzr audio tags`, run against real generated audio files."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core import tags
from jzr_tools.core.tags import Cover, TagEdit

from media_fixtures import HAVE_FFMPEG
from tags_fixtures import (
    CYRILLIC,
    FORMATS,
    SAVE_TARGETS,
    AudioSet,
    damage_then_fail,
)
from test_media_cli import describe, err

runner = CliRunner()

LYRICS = "line one\nстрока два\nline three"


def rows_of(block: list[str]) -> dict[str, str]:
    """Label -> value for the indented rows under a file's path line."""
    found = {}
    for line in block:
        match = re.match(r"^  (\S+)\s+(.*)$", line)
        assert match, f"not a row: {line!r}"
        found[match.group(1)] = match.group(2)
    return found


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not on PATH")
class AudioTagsCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.audio = AudioSet()
        cls.addClassCleanup(cls.audio.close)
        if cls.audio.error:
            raise unittest.SkipTest(cls.audio.error)
        cls.jpeg = cls.audio.read("cover.jpg")
        cls.png = cls.audio.read("cover.png")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-tags-cli-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name).resolve()

    def stage(self, fmt: str, name: str | None = None) -> Path:
        target = self.work / (name or f"song.{fmt}")
        shutil.copy(self.audio.path(f"fresh.{fmt}"), target)
        return target

    def extra(self, name: str) -> Path:
        target = self.work / name
        shutil.copy(self.audio.path(name), target)
        return target

    def tags(self, *args: object):  # noqa: ANN201
        return runner.invoke(app, ["audio", "tags", *map(str, args)])

    def ok(self, *args: object):  # noqa: ANN201
        result = self.tags(*args)
        self.assertEqual(result.exit_code, 0, describe(result))
        return result

    def snapshot(self) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in sorted(self.work.iterdir()) if p.is_file()}

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.work.glob("jzr-tmp-*"))

    # --- printing ------------------------------------------------------

    def test_group_help_lists_tags(self) -> None:
        result = runner.invoke(app, ["audio", "-h"])
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertRegex(result.stdout, r"(?m)^  tags\s+\S")

    def test_tags_help_shows_every_option(self) -> None:
        result = runner.invoke(app, ["audio", "tags", "-h"])
        self.assertEqual(result.exit_code, 0, describe(result))
        for option in (
            "--json", "--set", "-s", "--unset", "-u", "--clear", "--cover",
            "--no-cover", "--lyrics", "--export-cover",
        ):
            self.assertIn(option, result.stdout)

    def test_a_tagged_file_prints_its_path_then_aligned_rows_in_key_order(self) -> None:
        path = self.stage("mp3")
        tags.apply_edit(
            path,
            TagEdit(
                values={
                    "composer": "Someone", "title": CYRILLIC, "year": "2020",
                    "track": "3/12", "artist": "Band",
                },
                cover=Cover("image/jpeg", self.jpeg),
                lyrics=LYRICS,
            ),
        )
        result = self.ok(path)
        lines = result.stdout.rstrip("\n").split("\n")
        self.assertEqual(lines[0], str(path))
        body = lines[1:]
        for line in body:
            self.assertTrue(line.startswith("  ") and not line.startswith("   "), repr(line))
        rows = rows_of(body)
        self.assertEqual(
            list(rows), ["format", "title", "artist", "year", "track", "composer", "cover", "lyrics"]
        )
        self.assertEqual(rows["format"], "mp3")
        self.assertEqual(rows["title"], CYRILLIC)
        self.assertEqual(rows["track"], "3/12")
        self.assertRegex(rows["cover"], r"^image/jpeg, \d+(\.\d)? (B|kB)$")
        self.assertEqual(rows["lyrics"], "3 lines")
        starts = {re.match(r"^  \S+\s+", line).end() for line in body}
        self.assertEqual(len(starts), 1, f"values are not in one column: {body!r}")
        longest_label = max(len(label) for label in rows)
        self.assertGreaterEqual(starts.pop(), 2 + longest_label + 1)

    def test_a_file_without_cover_or_lyrics_says_none(self) -> None:
        path = self.stage("flac")
        tags.apply_edit(path, TagEdit(values={"title": "Plain"}))
        rows = rows_of(self.ok(path).stdout.rstrip("\n").split("\n")[1:])
        self.assertEqual(rows["cover"], "none")
        self.assertEqual(rows["lyrics"], "none")
        self.assertEqual(rows["format"], "flac")
        self.assertEqual(rows["title"], "Plain")
        self.assertNotIn("artist", rows)

    def test_every_format_is_printed_with_its_name(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                rows = rows_of(self.ok(self.stage(fmt)).stdout.rstrip("\n").split("\n")[1:])
                self.assertEqual(rows["format"], fmt)

    def test_several_files_are_separated_by_one_blank_line_in_the_order_given(self) -> None:
        first = self.stage("mp3", "b.mp3")
        second = self.stage("opus", "a.opus")
        lines = self.ok(first, second).stdout.rstrip("\n").split("\n")
        self.assertEqual(lines[0], str(first))
        blank = lines.index("")
        self.assertEqual(lines[blank + 1], str(second))
        self.assertNotIn("", lines[blank + 1 :])
        self.assertNotIn("", lines[:blank])
        self.assertEqual(rows_of(lines[1:blank])["format"], "mp3")
        self.assertEqual(rows_of(lines[blank + 2 :])["format"], "opus")

    def test_reading_does_not_modify_any_file(self) -> None:
        paths = [self.stage(fmt) for fmt in FORMATS]
        before = self.snapshot()
        self.ok(*paths)
        self.ok("--json", *paths)
        self.assertEqual(self.snapshot(), before)

    def test_a_renamed_file_is_reported_as_what_it_really_is(self) -> None:
        path = self.stage("mp3", "really-mp3.ogg")
        rows = rows_of(self.ok(path).stdout.rstrip("\n").split("\n")[1:])
        self.assertEqual(rows["format"], "mp3")

    # --- json ----------------------------------------------------------

    def test_json_is_one_array_with_a_document_per_file(self) -> None:
        first = self.stage("m4a", "one.m4a")
        second = self.stage("ogg", "two.ogg")
        tags.apply_edit(
            first,
            TagEdit(values={"title": CYRILLIC, "track": "2/9"}, cover=Cover("image/png", self.png), lyrics=LYRICS),
        )
        result = self.ok("--json", first, second)
        documents = json.loads(result.stdout)
        self.assertEqual(
            documents,
            [
                {
                    "file": str(first),
                    "format": "m4a",
                    "tags": {"title": CYRILLIC, "track": "2/9"},
                    "cover": {"mime": "image/png", "bytes": len(self.png)},
                    "lyrics": LYRICS,
                },
                {
                    "file": str(second),
                    "format": "ogg",
                    "tags": {},
                    "cover": None,
                    "lyrics": None,
                },
            ],
        )

    def test_json_lists_only_the_files_that_worked(self) -> None:
        good = self.stage("flac", "good.flac")
        bad = self.work / "bad.mp3"
        bad.write_text("words", encoding="utf-8")
        result = self.tags("--json", bad, good)
        self.assertEqual(result.exit_code, 1, describe(result))
        documents = json.loads(result.stdout)
        self.assertEqual([d["file"] for d in documents], [str(good)])
        self.assertIn(f"fail {bad}:", err(result))

    # --- editing -------------------------------------------------------

    def test_set_prints_updated_and_the_new_values_can_be_read(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                result = self.ok(
                    path, "--set", f"Title={CYRILLIC}", "-s", "track=3/12", "--set", "year=2020"
                )
                self.assertEqual(result.stdout.strip().split("\n"), [f"updated {path}"])
                self.assertEqual(
                    tags.read_tags(path).values,
                    {"title": CYRILLIC, "year": "2020", "track": "3/12"},
                )
        self.assertEqual(self.leftovers(), [])

    def test_one_edit_applies_to_every_listed_file(self) -> None:
        paths = [self.stage(fmt) for fmt in FORMATS]
        result = self.ok(*paths, "--set", "artist=Band")
        self.assertEqual(
            result.stdout.strip().split("\n"), [f"updated {p}" for p in paths]
        )
        for path in paths:
            self.assertEqual(tags.read_tags(path).values, {"artist": "Band"})

    def test_unset_and_clear(self) -> None:
        path = self.stage("mp3")
        self.ok(path, "--set", "title=A", "--set", "album=B", "--set", "genre=C")
        result = self.ok(path, "-u", "title", "--unset", "GENRE")
        self.assertEqual(result.stdout.strip(), f"updated {path}")
        self.assertEqual(tags.read_tags(path).values, {"album": "B"})
        self.ok(path, "--clear")
        self.assertEqual(tags.read_tags(path).values, {})

    def test_clear_with_set_leaves_only_what_was_set(self) -> None:
        path = self.stage("opus")
        self.ok(path, "--set", "title=Old", "--set", "album=Gone")
        self.ok(path, "--clear", "--set", "artist=New")
        self.assertEqual(tags.read_tags(path).values, {"artist": "New"})

    def test_cover_lyrics_and_removal(self) -> None:
        path = self.stage("m4a")
        jpeg = self.audio.path("cover.jpg")
        png = self.audio.path("cover.png")
        lyrics = self.work / "words.txt"
        lyrics.write_text(LYRICS, encoding="utf-8")
        self.ok(path, "--cover", jpeg, "--lyrics", lyrics)
        found = tags.read_tags(path)
        self.assertEqual(found.cover, Cover("image/jpeg", self.jpeg))
        self.assertEqual(found.lyrics, LYRICS)
        self.ok(path, "--cover", png)
        self.assertEqual(tags.read_tags(path).cover, Cover("image/png", self.png))
        self.ok(path, "--no-cover")
        self.assertIsNone(tags.read_tags(path).cover)
        self.assertEqual(tags.read_tags(path).lyrics, LYRICS)
        self.ok(path, "--unset", "lyrics")
        self.assertIsNone(tags.read_tags(path).lyrics)

    def test_a_cover_is_recognised_by_content_not_by_file_name(self) -> None:
        path = self.stage("flac")
        disguised = self.work / "picture.jpg"
        disguised.write_bytes(self.png)
        self.ok(path, "--cover", disguised)
        self.assertEqual(tags.read_tags(path).cover, Cover("image/png", self.png))

    def test_lyrics_file_with_windows_line_endings_still_embeds_the_words(self) -> None:
        path = self.stage("mp3")
        lyrics = self.work / "crlf.txt"
        lyrics.write_bytes("alpha\r\nbeta\r\n".encode("utf-8"))
        self.ok(path, "--lyrics", lyrics)
        found = tags.read_tags(path).lyrics
        self.assertIsNotNone(found)
        self.assertEqual(found.split(), ["alpha", "beta"])

    def test_export_cover_writes_the_picture_and_prints_the_arrow(self) -> None:
        path = self.stage("opus")
        tags.apply_edit(path, TagEdit(cover=Cover("image/png", self.png)))
        target = self.work / "front.png"
        result = self.ok(path, "--export-cover", target)
        self.assertEqual(result.stdout.strip().split("\n"), [f"{path} -> {target}"])
        self.assertEqual(target.read_bytes(), self.png)

    def test_export_cover_failures_are_reported_per_file_and_write_nothing(self) -> None:
        with_cover = self.stage("mp3", "has.mp3")
        tags.apply_edit(with_cover, TagEdit(cover=Cover("image/jpeg", self.jpeg)))
        without = self.stage("mp3", "bare.mp3")
        taken = self.work / "taken.jpg"
        taken.write_bytes(b"mine")
        cases = [
            (with_cover, self.work / "out.png", "wrong extension"),
            (with_cover, taken, "target exists"),
            (without, self.work / "none.jpg", "no cover"),
        ]
        for source, target, label in cases:
            with self.subTest(label=label):
                before = self.snapshot()
                result = self.tags(source, "--export-cover", target)
                self.assertEqual(result.exit_code, 1, describe(result))
                self.assertIn(f"fail {source}:", err(result))
                self.assertEqual(result.stdout.strip(), "")
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(taken.read_bytes(), b"mine")

    # --- usage errors --------------------------------------------------

    def test_usage_errors_exit_2_and_touch_no_file(self) -> None:
        path = self.stage("mp3", "a.mp3")
        other = self.stage("flac", "b.flac")
        words = self.work / "words.txt"
        words.write_text("some words", encoding="utf-8")
        empty = self.work / "empty.txt"
        empty.write_bytes(b"")
        binary = self.work / "binary.txt"
        binary.write_bytes(b"\xff\xfe\x00\x80 not utf-8 \xc3\x28")
        not_image = self.work / "notimage.png"
        not_image.write_text("not a picture", encoding="utf-8")
        gif = self.work / "pic.gif"
        gif.write_bytes(b"GIF89a\x01\x00\x01\x00\x00\x00\x00;")
        jpeg = self.audio.path("cover.jpg")
        target = self.work / "export.jpg"
        cases = {
            "unknown set key": ["--set", "bogus=1"],
            "lyrics via set": ["--set", "lyrics=words"],
            "unknown unset key": ["--unset", "bogus"],
            "set without equals": ["--set", "title"],
            "set with empty value": ["--set", "title="],
            "set with blank value": ["--set", "title=   "],
            "bad year": ["--set", "year=abc"],
            "bad track": ["--set", "track=0"],
            "bad disc": ["--set", "disc=3/70000"],
            "same key twice": ["--set", "title=A", "--set", "title=B"],
            "same key twice, other case": ["--set", "title=A", "--set", "TITLE=B"],
            "set and unset": ["--set", "title=A", "--unset", "title"],
            "cover and no-cover": ["--cover", str(jpeg), "--no-cover"],
            "cover that is text": ["--cover", str(not_image)],
            "cover that is a gif": ["--cover", str(gif)],
            "cover that is missing": ["--cover", str(self.work / "nope.jpg")],
            "lyrics not utf-8": ["--lyrics", str(binary)],
            "lyrics empty": ["--lyrics", str(empty)],
            "lyrics missing": ["--lyrics", str(self.work / "nope.txt")],
            "json with set": ["--json", "--set", "title=A"],
            "json with unset": ["--json", "--unset", "title"],
            "json with clear": ["--json", "--clear"],
            "json with cover": ["--json", "--cover", str(jpeg)],
            "json with no-cover": ["--json", "--no-cover"],
            "json with lyrics": ["--json", "--lyrics", str(words)],
            "json with export": ["--json", "--export-cover", str(target)],
            "export with set": ["--export-cover", str(target), "--set", "title=A"],
            "export with unset": ["--export-cover", str(target), "--unset", "title"],
            "export with clear": ["--export-cover", str(target), "--clear"],
            "export with cover": ["--export-cover", str(target), "--cover", str(jpeg)],
            "export with no-cover": ["--export-cover", str(target), "--no-cover"],
            "export with lyrics": ["--export-cover", str(target), "--lyrics", str(words)],
        }
        for label, options in cases.items():
            with self.subTest(label=label):
                before = self.snapshot()
                result = self.tags(path, *options)
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.leftovers(), [])
                self.assertFalse(target.exists())
                self.assertNotIn("Traceback", err(result))
        for label in ("same key twice", "unknown set key", "cover that is text"):
            with self.subTest(label=label, files="two"):
                before = self.snapshot()
                result = self.tags(path, other, *cases[label])
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertEqual(self.snapshot(), before)

    def test_export_cover_with_more_than_one_file_is_a_usage_error(self) -> None:
        first = self.stage("mp3", "a.mp3")
        second = self.stage("mp3", "b.mp3")
        for path in (first, second):
            tags.apply_edit(path, TagEdit(cover=Cover("image/jpeg", self.jpeg)))
        target = self.work / "out.jpg"
        before = self.snapshot()
        result = self.tags(first, second, "--export-cover", target)
        self.assertEqual(result.exit_code, 2, describe(result))
        self.assertFalse(target.exists())
        self.assertEqual(self.snapshot(), before)

    def test_no_file_at_all_is_a_usage_error(self) -> None:
        result = self.tags()
        self.assertEqual(result.exit_code, 2, describe(result))

    def test_a_listed_file_that_is_missing_is_a_usage_error_and_nothing_is_changed(self) -> None:
        good = self.stage("mp3")
        before = self.snapshot()
        for options in ([], ["--set", "title=A"], ["--json"]):
            with self.subTest(options=options):
                result = self.tags(good, self.work / "gone.mp3", *options)
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertEqual(self.snapshot(), before)

    # --- failures that stay with one file ------------------------------

    def test_a_bad_file_fails_alone_while_the_others_are_updated(self) -> None:
        first = self.stage("mp3", "first.mp3")
        fake = self.work / "fake.mp3"
        fake.write_text("this is text, not audio", encoding="utf-8")
        notes = self.work / "notes.txt"
        notes.write_text("notes", encoding="utf-8")
        last = self.stage("flac", "last.flac")
        result = self.tags(first, fake, notes, last, "--set", "title=Hello")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(result.stdout.strip().split("\n"), [f"updated {first}", f"updated {last}"])
        error_lines = err(result).strip().split("\n")
        self.assertEqual(len(error_lines), 2, error_lines)
        self.assertTrue(error_lines[0].startswith(f"fail {fake}: "), error_lines[0])
        self.assertTrue(error_lines[1].startswith(f"fail {notes}: "), error_lines[1])
        self.assertIn("not a supported audio file", error_lines[1])
        self.assertEqual(tags.read_tags(first).values, {"title": "Hello"})
        self.assertEqual(tags.read_tags(last).values, {"title": "Hello"})
        self.assertEqual(fake.read_text(encoding="utf-8"), "this is text, not audio")
        self.assertEqual(notes.read_text(encoding="utf-8"), "notes")
        self.assertEqual(self.leftovers(), [])

    def test_a_bad_file_fails_alone_when_only_reading(self) -> None:
        good = self.stage("ogg", "good.ogg")
        notes = self.work / "notes.txt"
        notes.write_text("notes", encoding="utf-8")
        result = self.tags(notes, good)
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertTrue(result.stdout.startswith(str(good)), result.stdout)
        self.assertNotIn(str(notes), result.stdout)
        self.assertTrue(err(result).startswith(f"fail {notes}: "), err(result))

    def test_a_failed_write_leaves_the_file_as_it_was(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.ok(path, "--set", "title=Before")
                before = self.snapshot()
                with mock.patch(SAVE_TARGETS[fmt], damage_then_fail):
                    result = self.tags(path, "--set", "title=After", "--cover", self.audio.path("cover.png"))
                self.assertEqual(result.exit_code, 1, describe(result))
                self.assertIn(f"fail {path}:", err(result))
                self.assertNotIn("updated", result.stdout)
                self.assertNotIn("Traceback", err(result))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.leftovers(), [])

    def test_a_failed_write_to_one_file_does_not_stop_the_next(self) -> None:
        broken = self.stage("mp3", "broken.mp3")
        fine = self.stage("flac", "fine.flac")
        original = broken.read_bytes()
        with mock.patch(SAVE_TARGETS["mp3"], damage_then_fail):
            result = self.tags(broken, fine, "--set", "title=Hi")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(result.stdout.strip(), f"updated {fine}")
        self.assertIn(f"fail {broken}:", err(result))
        self.assertEqual(broken.read_bytes(), original)
        self.assertEqual(tags.read_tags(fine).values, {"title": "Hi"})


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not on PATH")
class AudioTagsNeedNoFfmpegTests(unittest.TestCase):
    """Reading, editing and exporting never look for ffmpeg or ffprobe."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.audio = AudioSet()
        cls.addClassCleanup(cls.audio.close)
        if cls.audio.error:
            raise unittest.SkipTest(cls.audio.error)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-tags-noff-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name).resolve()
        self.path = self.work / "song.mp3"
        shutil.copy(self.audio.path("fresh.mp3"), self.path)
        self.cover = self.audio.path("cover.jpg")
        self.empty_data = self.work / "empty-data"
        self.empty_data.mkdir()

    def invoke(self, *args: object):  # noqa: ANN201
        """Run `audio tags` where no ffmpeg can be found and any lookup is an error."""

        def forbidden(*a: object, **k: object) -> None:
            raise AssertionError("ffmpeg or ffprobe was looked for")

        environ = {
            "JZR_FFMPEG": str(self.work / "missing" / "ffmpeg"),
            "JZR_FFPROBE": str(self.work / "missing" / "ffprobe"),
            "JZR_DATA_DIR": str(self.empty_data),
            "PATH": str(self.empty_data),
        }
        with (
            mock.patch.dict(os.environ, environ),
            mock.patch("jzr_tools.core.ffmpeg.locate_tool", side_effect=forbidden) as locate,
            mock.patch("jzr_tools.commands._batch.require_tools", side_effect=forbidden) as require,
            mock.patch("jzr_tools.core.ffmpeg.shutil.which", return_value=None),
        ):
            result = runner.invoke(app, ["audio", "tags", *map(str, args)])
        locate.assert_not_called()
        require.assert_not_called()
        return result

    def test_reading_works_without_ffmpeg(self) -> None:
        result = self.invoke(self.path)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue(result.stdout.startswith(str(self.path)))
        result = self.invoke("--json", self.path)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(json.loads(result.stdout)[0]["format"], "mp3")

    def test_editing_works_without_ffmpeg(self) -> None:
        result = self.invoke(self.path, "--set", "title=Song", "--cover", self.cover)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(result.stdout.strip(), f"updated {self.path}")
        found = tags.read_tags(self.path)
        self.assertEqual(found.values, {"title": "Song"})
        self.assertEqual(found.cover.mime, "image/jpeg")

    def test_exporting_works_without_ffmpeg(self) -> None:
        self.invoke(self.path, "--cover", self.cover)
        target = self.work / "front.jpg"
        result = self.invoke(self.path, "--export-cover", target)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(target.read_bytes(), self.cover.read_bytes())

    def test_a_failure_does_not_ask_for_ffmpeg_either(self) -> None:
        words = self.work / "words.mp3"
        words.write_text("not audio", encoding="utf-8")
        result = self.invoke(words)
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {words}:", err(result))
        self.assertNotIn("jzr ffmpeg install", err(result))


if __name__ == "__main__":
    unittest.main()
