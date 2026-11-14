"""Reading and editing audio tags with `jzr_tools.core.tags`, on real generated files.

Files are made with ffmpeg when the class starts. Tests that need no audio
file (assignment parsing, value checks, picture detection) run without ffmpeg.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from jzr_tools.core import tags
from jzr_tools.core.tags import Cover, TagEdit, TagError

from media_fixtures import HAVE_FFMPEG, ffmpeg_run
from tags_fixtures import (
    CJK,
    CYRILLIC,
    EMOJI,
    FORMATS,
    SAVE_TARGETS,
    AudioSet,
    audio_md5,
    damage_then_fail,
    make_jpeg,
    make_png,
    raw_tag_count,
)

ALL_VALUES = {
    "title": "Song",
    "artist": "Band",
    "album": "Record",
    "albumartist": "Various",
    "year": "2020",
    "track": "3/12",
    "disc": "1/2",
    "genre": "Rock",
    "comment": "Nice one",
    "composer": "Someone",
}
LYRICS = "First line\nВторая строка\n\n三行目 \U0001F3B5"


class KeysTests(unittest.TestCase):
    def test_keys_are_the_ten_editable_tags_in_display_order(self) -> None:
        self.assertEqual(
            tags.KEYS,
            (
                "title", "artist", "album", "albumartist", "year",
                "track", "disc", "genre", "comment", "composer",
            ),
        )

    def test_lyrics_can_be_unset_but_is_not_a_plain_key(self) -> None:
        self.assertEqual(tags.UNSET_KEYS, tags.KEYS + ("lyrics",))
        self.assertNotIn("lyrics", tags.KEYS)


class ParseAssignmentTests(unittest.TestCase):
    def test_key_is_lower_cased_and_value_kept(self) -> None:
        self.assertEqual(tags.parse_assignment("Title=Song"), ("title", "Song"))

    def test_value_keeps_further_equals_signs(self) -> None:
        self.assertEqual(tags.parse_assignment("comment=a=b"), ("comment", "a=b"))

    def test_surrounding_whitespace_of_the_value_is_stripped(self) -> None:
        self.assertEqual(tags.parse_assignment("artist=  Band \t"), ("artist", "Band"))

    def test_non_ascii_values_pass_through(self) -> None:
        self.assertEqual(tags.parse_assignment(f"title={CYRILLIC}"), ("title", CYRILLIC))
        self.assertEqual(tags.parse_assignment(f"album={EMOJI}"), ("album", EMOJI))

    def test_year_and_track_are_checked(self) -> None:
        self.assertEqual(tags.parse_assignment("year=2020-05"), ("year", "2020-05"))
        self.assertEqual(tags.parse_assignment("track=3/12"), ("track", "3/12"))

    def test_bad_assignments_raise_value_error_with_a_message(self) -> None:
        for text in (
            "title",
            "",
            "=value",
            "nope=x",
            "lyrics=words",
            "title=",
            "title=   ",
            "year=abc",
            "track=0",
            "track=x",
            "disc=1/0",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as caught:
                    tags.parse_assignment(text)
                self.assertTrue(str(caught.exception).strip())


class CheckValueTests(unittest.TestCase):
    def test_years_accept_year_month_and_date(self) -> None:
        for value in ("2020", "2020-05", "2020-05-17", "1999"):
            with self.subTest(value=value):
                self.assertEqual(tags.check_value("year", value), value)

    def test_bad_years_are_refused(self) -> None:
        for value in ("20", "20200", "2020-5", "2020/05", "May 2020", "2020-05-17-01", "twenty"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    tags.check_value("year", value)

    def test_track_and_disc_accept_a_number_or_a_number_of_total(self) -> None:
        for key in ("track", "disc"):
            for value in ("1", "3/12", "65535", "65535/65535"):
                with self.subTest(key=key, value=value):
                    self.assertEqual(tags.check_value(key, value), value)

    def test_track_and_disc_outside_one_to_65535_are_refused(self) -> None:
        for key in ("track", "disc"):
            for value in ("0", "65536", "-1", "3/0", "3/65536", "3/", "/12", "a/b", "1/2/3", "3 of 12"):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        tags.check_value(key, value)

    def test_text_keys_take_any_non_empty_text_and_strip_it(self) -> None:
        for key in ("title", "artist", "album", "albumartist", "genre", "comment", "composer"):
            with self.subTest(key=key):
                self.assertEqual(tags.check_value(key, "  Zażółć 123 / x=y  "), "Zażółć 123 / x=y")
                with self.assertRaises(ValueError):
                    tags.check_value(key, "")
                with self.assertRaises(ValueError):
                    tags.check_value(key, "  \t ")

    def test_year_value_is_stripped_before_it_is_checked(self) -> None:
        self.assertEqual(tags.check_value("year", " 2020 "), "2020")


class LoadCoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-cover-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_jpeg_is_detected_with_its_exact_bytes(self) -> None:
        data = make_jpeg()
        cover = tags.load_cover(self.write("front.jpg", data))
        self.assertEqual(cover, Cover("image/jpeg", data))

    def test_png_is_detected_with_its_exact_bytes(self) -> None:
        data = make_png()
        cover = tags.load_cover(self.write("front.png", data))
        self.assertEqual(cover, Cover("image/png", data))

    def test_the_kind_comes_from_the_bytes_not_the_name(self) -> None:
        self.assertEqual(tags.load_cover(self.write("a.png", make_jpeg())).mime, "image/jpeg")
        self.assertEqual(tags.load_cover(self.write("b.jpeg", make_png())).mime, "image/png")
        self.assertEqual(tags.load_cover(self.write("c", make_png())).mime, "image/png")

    def test_anything_else_is_refused(self) -> None:
        cases = {
            "words.jpg": b"just some text\n",
            "empty.png": b"",
            "anim.gif": b"GIF89a\x01\x00\x01\x00\x00\x00\x00;",
            "tiny.jpg": b"\xff",
            "song.mp3": b"ID3\x03\x00\x00\x00\x00\x00\x00",
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    tags.load_cover(self.write(name, data))


class CoverValueTests(unittest.TestCase):
    def test_cover_and_tag_set_are_immutable_values(self) -> None:
        cover = Cover("image/png", b"x")
        with self.assertRaises(Exception):
            cover.mime = "image/jpeg"  # type: ignore[misc]
        tag_set = tags.TagSet("mp3", {}, None, None)
        with self.assertRaises(Exception):
            tag_set.format = "flac"  # type: ignore[misc]

    def test_an_edit_with_nothing_set_changes_nothing_by_default(self) -> None:
        edit = TagEdit()
        self.assertEqual(dict(edit.values), {})
        self.assertEqual(edit.unset, frozenset())
        self.assertFalse(edit.clear)
        self.assertIsNone(edit.cover)
        self.assertFalse(edit.remove_cover)
        self.assertIsNone(edit.lyrics)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not on PATH")
class RealTagsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.audio = AudioSet()
        cls.addClassCleanup(cls.audio.close)
        if cls.audio.error:
            raise unittest.SkipTest(cls.audio.error)
        cls.jpeg = cls.audio.read("cover.jpg")
        cls.png = cls.audio.read("cover.png")
        cls.other_png = cls.audio.read("other.png")
        cls.big_png = cls.audio.read("big.png")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-tags-work-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name).resolve()

    def stage(self, fmt: str, name: str | None = None, source: str | None = None) -> Path:
        target = self.work / (name or f"song.{fmt}")
        shutil.copy(self.audio.path(source or f"fresh.{fmt}"), target)
        return target

    def edit(self, path: Path, **fields: object) -> None:
        tags.apply_edit(path, TagEdit(**fields))

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.work.glob("jzr-tmp-*"))

    # --- reading -------------------------------------------------------

    def test_format_is_named_for_each_kind_of_file(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                self.assertEqual(tags.read_tags(self.stage(fmt)).format, fmt)

    def test_a_fresh_file_has_no_cover_and_no_lyrics(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                found = tags.read_tags(self.stage(fmt))
                self.assertIsNone(found.cover)
                self.assertIsNone(found.lyrics)
                self.assertTrue(set(found.values) <= set(tags.KEYS))

    def test_format_comes_from_the_content_not_the_name(self) -> None:
        cases = [
            ("mp3", "wrong.ogg"),
            ("ogg", "wrong.mp3"),
            ("opus", "wrong.ogg"),
            ("flac", "wrong.m4a"),
            ("m4a", "wrong.flac"),
            ("mp3", "noextension"),
        ]
        for fmt, name in cases:
            with self.subTest(fmt=fmt, name=name):
                path = self.stage(fmt, name)
                self.assertEqual(tags.read_tags(path).format, fmt)

    def test_a_renamed_file_can_be_edited_as_what_it_really_is(self) -> None:
        path = self.stage("mp3", "wrong.ogg")
        self.edit(path, values={"title": "Real MP3"})
        found = tags.read_tags(path)
        self.assertEqual(found.format, "mp3")
        self.assertEqual(found.values["title"], "Real MP3")

    def test_files_that_are_not_supported_audio_raise_tag_error(self) -> None:
        text = self.work / "words.mp3"
        text.write_text("not audio at all\n", encoding="utf-8")
        empty = self.work / "empty.flac"
        empty.write_bytes(b"")
        wav = self.work / "tone.wav"
        ffmpeg_run("-f", "lavfi", "-i", "sine=duration=1", str(wav))
        for path in (text, empty, wav):
            with self.subTest(name=path.name):
                with self.assertRaises(TagError) as caught:
                    tags.read_tags(path)
                self.assertTrue(str(caught.exception).strip())
                self.assertIn("not a supported audio file", str(caught.exception))

    def test_every_key_reads_back_after_it_is_set(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values=ALL_VALUES)
                found = tags.read_tags(path)
                self.assertEqual(found.values, ALL_VALUES)
                self.assertEqual(list(found.values), list(tags.KEYS))

    def test_values_that_were_never_set_are_absent(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values={"title": "Only", "genre": "Jazz"})
                self.assertEqual(
                    tags.read_tags(path).values, {"title": "Only", "genre": "Jazz"}
                )

    def test_non_ascii_values_round_trip_in_every_format(self) -> None:
        values = {
            "title": CYRILLIC,
            "artist": CJK,
            "album": EMOJI,
            "comment": f"{CYRILLIC} / {CJK} / {EMOJI}",
        }
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values=values)
                self.assertEqual(tags.read_tags(path).values, values)

    def test_track_and_disc_with_and_without_a_total_round_trip(self) -> None:
        for fmt in FORMATS:
            for track, disc in (("3/12", "1/2"), ("7", "4")):
                with self.subTest(fmt=fmt, track=track, disc=disc):
                    path = self.stage(fmt, f"song-{track.replace('/', '-')}.{fmt}")
                    self.edit(path, values={"track": track, "disc": disc})
                    found = tags.read_tags(path).values
                    self.assertEqual(found["track"], track)
                    self.assertEqual(found["disc"], disc)

    def test_year_round_trips_as_a_year_and_as_a_full_date(self) -> None:
        for fmt in FORMATS:
            for year in ("2020", "2020-05-17"):
                with self.subTest(fmt=fmt, year=year):
                    path = self.stage(fmt, f"song-{year}.{fmt}")
                    self.edit(path, values={"year": year})
                    self.assertEqual(tags.read_tags(path).values["year"], year)

    def test_several_values_of_one_tag_are_joined_with_semicolons(self) -> None:
        import mutagen
        from mutagen.id3 import ID3, TPE1
        from mutagen.mp4 import MP4

        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                if fmt == "mp3":
                    # Only ID3v2.4 holds several values; v2.3 joins them with "/".
                    id3 = ID3()
                    id3.add(TPE1(encoding=3, text=["One", "Two"]))
                    id3.save(str(path), v2_version=4)
                elif fmt == "m4a":
                    mp4 = MP4(str(path))
                    mp4["\xa9ART"] = ["One", "Two"]
                    mp4.save()
                else:
                    other = mutagen.File(str(path))
                    other["ARTIST"] = ["One", "Two"]
                    other.save()
                self.assertEqual(tags.read_tags(path).values["artist"], "One; Two")

    # --- editing -------------------------------------------------------

    def test_set_changes_only_the_keys_given(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values={"title": "First", "artist": "Band"})
                self.edit(path, values={"title": "Second", "album": "Record"})
                self.assertEqual(
                    tags.read_tags(path).values,
                    {"title": "Second", "artist": "Band", "album": "Record"},
                )

    def test_unset_removes_only_the_named_tags(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values=ALL_VALUES)
                self.edit(path, unset=frozenset({"comment", "year", "track"}))
                expected = {
                    k: v for k, v in ALL_VALUES.items() if k not in ("comment", "year", "track")
                }
                self.assertEqual(tags.read_tags(path).values, expected)

    def test_unset_of_a_tag_that_is_not_there_is_harmless(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values={"title": "Song"})
                self.edit(path, unset=frozenset({"genre", "lyrics"}))
                self.assertEqual(tags.read_tags(path).values, {"title": "Song"})

    def test_clear_removes_every_tag_cover_and_lyrics(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(
                    path,
                    values=ALL_VALUES,
                    cover=Cover("image/jpeg", self.jpeg),
                    lyrics=LYRICS,
                )
                self.edit(path, clear=True)
                found = tags.read_tags(path)
                self.assertEqual(found.values, {})
                self.assertIsNone(found.cover)
                self.assertIsNone(found.lyrics)
                self.assertEqual(raw_tag_count(path), 0)

    def test_clear_also_removes_tags_outside_the_ten_keys(self) -> None:
        import mutagen
        from mutagen.id3 import ID3, TXXX
        from mutagen.mp4 import MP4, MP4FreeForm

        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                if fmt == "mp3":
                    id3 = ID3()
                    id3.add(TXXX(encoding=3, desc="MOOD", text=["calm"]))
                    id3.save(str(path), v2_version=3)
                elif fmt == "m4a":
                    mp4 = MP4(str(path))
                    mp4["----:com.example:MOOD"] = [MP4FreeForm(b"calm")]
                    mp4.save()
                else:
                    other = mutagen.File(str(path))
                    other["MOOD"] = ["calm"]
                    other.save()
                self.assertGreater(raw_tag_count(path), 0)
                self.edit(path, clear=True)
                self.assertEqual(raw_tag_count(path), 0)

    def test_clear_then_values_leaves_only_the_new_values(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values=ALL_VALUES, lyrics=LYRICS)
                self.edit(path, clear=True, values={"title": "Fresh"})
                found = tags.read_tags(path)
                self.assertEqual(found.values, {"title": "Fresh"})
                self.assertIsNone(found.lyrics)

    def test_a_value_set_in_the_same_edit_as_its_unset_wins(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values={"title": "Old", "album": "Keep"})
                self.edit(path, unset=frozenset({"title"}), values={"title": "New"})
                self.assertEqual(
                    tags.read_tags(path).values, {"title": "New", "album": "Keep"}
                )

    # --- covers --------------------------------------------------------

    def test_jpeg_and_png_covers_are_embedded_with_exact_bytes(self) -> None:
        for fmt in FORMATS:
            for mime, data in (("image/jpeg", self.jpeg), ("image/png", self.png)):
                with self.subTest(fmt=fmt, mime=mime):
                    path = self.stage(fmt, f"song-{mime[-3:]}.{fmt}")
                    self.edit(path, cover=Cover(mime, data))
                    cover = tags.read_tags(path).cover
                    self.assertEqual(cover, Cover(mime, data))

    def test_embedding_a_cover_leaves_the_text_tags_alone(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values=ALL_VALUES, lyrics=LYRICS)
                self.edit(path, cover=Cover("image/png", self.png))
                found = tags.read_tags(path)
                self.assertEqual(found.values, ALL_VALUES)
                self.assertEqual(found.lyrics, LYRICS)

    def test_a_new_cover_replaces_the_old_one(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, cover=Cover("image/jpeg", self.jpeg))
                self.edit(path, cover=Cover("image/png", self.other_png))
                self.assertEqual(
                    tags.read_tags(path).cover, Cover("image/png", self.other_png)
                )
                self.assertEqual(self.picture_count(path), 1)

    def test_a_new_cover_replaces_every_picture_already_embedded(self) -> None:
        path = self.stage("flac")
        import mutagen.flac

        flac = mutagen.flac.FLAC(str(path))
        for kind, data in ((3, self.jpeg), (4, self.png)):
            picture = mutagen.flac.Picture()
            picture.type = kind
            picture.mime = "image/jpeg" if data is self.jpeg else "image/png"
            picture.data = data
            flac.add_picture(picture)
        flac.save()
        self.assertEqual(self.picture_count(path), 2)
        self.edit(path, cover=Cover("image/png", self.other_png))
        self.assertEqual(self.picture_count(path), 1)
        self.assertEqual(tags.read_tags(path).cover, Cover("image/png", self.other_png))

    def test_no_cover_removes_the_picture_and_keeps_the_tags(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values={"title": "Song"}, cover=Cover("image/jpeg", self.jpeg))
                self.edit(path, remove_cover=True)
                found = tags.read_tags(path)
                self.assertIsNone(found.cover)
                self.assertEqual(found.values, {"title": "Song"})
                self.assertEqual(self.picture_count(path), 0)

    def picture_count(self, path: Path) -> int:
        import mutagen
        from mutagen.flac import FLAC

        found = mutagen.File(str(path))
        if isinstance(found, FLAC):
            return len(found.pictures)
        if found.tags is None:
            return 0
        if hasattr(found.tags, "getall"):
            return len(found.tags.getall("APIC"))
        if "covr" in found.tags:
            return len(found.tags["covr"])
        return len(found.tags.get("metadata_block_picture", []))

    # --- lyrics --------------------------------------------------------

    def test_multi_line_non_ascii_lyrics_round_trip(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, lyrics=LYRICS)
                self.assertEqual(tags.read_tags(path).lyrics, LYRICS)

    def test_new_lyrics_replace_the_old(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, lyrics="old words")
                self.edit(path, lyrics="new words\nsecond")
                self.assertEqual(tags.read_tags(path).lyrics, "new words\nsecond")

    def test_unset_lyrics_removes_them_and_keeps_the_rest(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values={"title": "Song"}, lyrics=LYRICS)
                self.edit(path, unset=frozenset({"lyrics"}))
                found = tags.read_tags(path)
                self.assertIsNone(found.lyrics)
                self.assertEqual(found.values, {"title": "Song"})

    def test_lyrics_do_not_show_up_among_the_values(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, lyrics=LYRICS)
                self.assertEqual(tags.read_tags(path).values, {})

    # --- exporting the cover ------------------------------------------

    def test_export_writes_the_embedded_bytes_and_returns_the_cover(self) -> None:
        for fmt in FORMATS:
            for mime, data, extension in (
                ("image/jpeg", self.jpeg, "jpg"),
                ("image/jpeg", self.jpeg, "jpeg"),
                ("image/png", self.png, "png"),
            ):
                with self.subTest(fmt=fmt, mime=mime, extension=extension):
                    path = self.stage(fmt, f"song-{extension}.{fmt}")
                    self.edit(path, cover=Cover(mime, data))
                    target = self.work / f"out-{fmt}.{extension}"
                    returned = tags.export_cover(path, target)
                    self.assertEqual(returned, Cover(mime, data))
                    self.assertEqual(target.read_bytes(), data)
        self.assertEqual(self.leftovers(), [])

    def test_export_does_not_change_the_audio_file(self) -> None:
        path = self.stage("mp3")
        self.edit(path, cover=Cover("image/jpeg", self.jpeg))
        before = path.read_bytes()
        tags.export_cover(path, self.work / "out.jpg")
        self.assertEqual(path.read_bytes(), before)

    def test_export_refuses_a_target_that_exists_and_leaves_it_alone(self) -> None:
        path = self.stage("flac")
        self.edit(path, cover=Cover("image/jpeg", self.jpeg))
        target = self.work / "taken.jpg"
        target.write_bytes(b"precious")
        with self.assertRaises(TagError):
            tags.export_cover(path, target)
        self.assertEqual(target.read_bytes(), b"precious")
        self.assertEqual(self.leftovers(), [])

    def test_export_refuses_an_extension_that_does_not_fit_the_picture(self) -> None:
        jpeg_file = self.stage("mp3", "jpeg-inside.mp3")
        self.edit(jpeg_file, cover=Cover("image/jpeg", self.jpeg))
        png_file = self.stage("mp3", "png-inside.mp3")
        self.edit(png_file, cover=Cover("image/png", self.png))
        cases = [
            (jpeg_file, "out.png"),
            (jpeg_file, "out.gif"),
            (jpeg_file, "out"),
            (png_file, "out.jpg"),
            (png_file, "out.jpeg"),
            (png_file, "out.bmp"),
        ]
        for source, name in cases:
            with self.subTest(source=source.name, target=name):
                target = self.work / name
                with self.assertRaises(TagError):
                    tags.export_cover(source, target)
                self.assertFalse(target.exists())
        self.assertEqual(self.leftovers(), [])

    def test_export_of_a_file_with_no_cover_is_refused(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                target = self.work / f"none-{fmt}.jpg"
                with self.assertRaises(TagError) as caught:
                    tags.export_cover(path, target)
                self.assertIn("no cover art", str(caught.exception))
                self.assertFalse(target.exists())

    # --- the audio itself ---------------------------------------------

    def test_edits_leave_the_audio_data_identical(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                audio_before = audio_md5(path)
                self.edit(path, values=ALL_VALUES, lyrics=LYRICS)
                self.assertEqual(audio_md5(path), audio_before)
                self.edit(path, cover=Cover("image/png", self.png))
                self.assertEqual(audio_md5(path), audio_before)
                self.edit(path, unset=frozenset({"title"}), remove_cover=True)
                self.assertEqual(audio_md5(path), audio_before)
                self.edit(path, clear=True)
                self.assertEqual(audio_md5(path), audio_before)

    def test_a_much_larger_tag_block_still_leaves_the_audio_identical(self) -> None:
        self.assertGreater(len(self.big_png), 200_000)
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                audio_before = audio_md5(path)
                size_before = path.stat().st_size
                self.edit(path, cover=Cover("image/png", self.big_png), values={"title": "Big"})
                self.assertGreater(path.stat().st_size, size_before + 200_000)
                self.assertEqual(audio_md5(path), audio_before)
                self.assertEqual(tags.read_tags(path).cover, Cover("image/png", self.big_png))
                self.edit(path, remove_cover=True)
                self.assertEqual(audio_md5(path), audio_before)

    def test_the_edited_file_is_still_playable(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values=ALL_VALUES, cover=Cover("image/jpeg", self.jpeg), lyrics=LYRICS)
                completed = subprocess.run(
                    [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_no_temporary_file_is_left_after_a_good_edit(self) -> None:
        for fmt in FORMATS:
            path = self.stage(fmt)
            self.edit(path, values={"title": "Song"})
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(
            sorted(p.name for p in self.work.iterdir()), sorted(f"song.{f}" for f in FORMATS)
        )

    # --- a failed write ------------------------------------------------

    def test_a_failed_write_leaves_the_file_as_it_was(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                self.edit(path, values={"title": "Before"})
                original = path.read_bytes()
                before = sorted(p.name for p in self.work.iterdir())
                with mock.patch(SAVE_TARGETS[fmt], damage_then_fail):
                    with self.assertRaises(TagError) as caught:
                        self.edit(path, values={"title": "After"}, cover=Cover("image/png", self.png))
                self.assertTrue(str(caught.exception).strip())
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(sorted(p.name for p in self.work.iterdir()), before)
                self.assertEqual(self.leftovers(), [])
                self.assertEqual(tags.read_tags(path).values, {"title": "Before"})

    def test_a_failed_write_to_a_file_with_no_tags_leaves_it_as_it_was(self) -> None:
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                path = self.stage(fmt)
                original = path.read_bytes()
                with mock.patch(SAVE_TARGETS[fmt], damage_then_fail):
                    with self.assertRaises(TagError):
                        self.edit(path, clear=True)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(self.leftovers(), [])

    def test_editing_a_file_that_is_not_audio_fails_and_leaves_it(self) -> None:
        path = self.work / "words.mp3"
        path.write_text("plain words\n", encoding="utf-8")
        with self.assertRaises(TagError):
            self.edit(path, values={"title": "x"})
        self.assertEqual(path.read_text(encoding="utf-8"), "plain words\n")
        self.assertEqual(self.leftovers(), [])

    def test_editing_a_file_that_is_missing_fails_with_tag_error(self) -> None:
        with self.assertRaises(TagError):
            self.edit(self.work / "gone.mp3", values={"title": "x"})
        self.assertEqual(list(self.work.iterdir()), [])


class TagSetDictTests(unittest.TestCase):
    def test_document_has_file_format_tags_cover_and_lyrics(self) -> None:
        path = Path("some") / "song.mp3"
        tag_set = tags.TagSet(
            "mp3", {"title": "Song"}, Cover("image/png", b"12345"), "la\nla"
        )
        self.assertEqual(
            tags.tagset_as_dict(tag_set, path),
            {
                "file": str(path),
                "format": "mp3",
                "tags": {"title": "Song"},
                "cover": {"mime": "image/png", "bytes": 5},
                "lyrics": "la\nla",
            },
        )

    def test_missing_cover_and_lyrics_are_null(self) -> None:
        document = tags.tagset_as_dict(tags.TagSet("flac", {}, None, None), Path("a.flac"))
        self.assertEqual(
            document,
            {"file": "a.flac", "format": "flac", "tags": {}, "cover": None, "lyrics": None},
        )


if __name__ == "__main__":
    unittest.main()
