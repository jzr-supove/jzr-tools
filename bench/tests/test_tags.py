"""Tests for the tags task (held out): tag three MP3s and embed cover.jpg in each.

The fixtures are made once per class with the task's own prepare_fixtures (3 s tones, so ffmpeg is
needed and the tests are skipped without it). Correct answers are written with mutagen; each wrong one
differs from a correct one in exactly one way.
"""

from __future__ import annotations

import array
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench.tasks import HELD_OUT, TASKS, get_task  # noqa: E402
from jzr_bench.tasks._pictures import photo_like  # noqa: E402
from jzr_support import HAVE_JZR, NEEDS_JZR, run_jzr  # noqa: E402
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TRCK, ID3NoHeaderError  # noqa: E402
from mutagen.mp3 import MP3  # noqa: E402
from PIL import Image, ImageChops, ImageStat  # noqa: E402
from task_check_support import CheckCase  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
NEEDS_FFMPEG = "ffmpeg is required for the tags task tests (the fixtures are tones encoded to MP3)"

PROMPT = (
    'Tag the three MP3s in this folder: artist "The Testers", album "Bench Songs", the title from each file name '
    '(for "01 - First Light.mp3" it is "First Light") and the track number from its leading number. '
    "Embed cover.jpg as the cover art in each."
)
NAMES = ("01 - First Light.mp3", "02 - Second Wind.mp3", "03 - Third Rail.mp3")
TITLES = ("First Light", "Second Wind", "Third Rail")
ARTIST, ALBUM = "The Testers", "Bench Songs"


def run(*args: str) -> bytes:
    return subprocess.run(args, check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=120).stdout


def length(path: Path) -> float:
    return MP3(path).info.length


def tone_hz(path: Path) -> float:
    raw = run(FFMPEG or "ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-")
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    crossings = sum(1 for a, b in zip(samples, samples[1:], strict=False) if (a < 0) != (b < 0))
    return crossings / 2 / (len(samples) / 8000)


def jpeg_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def thumb(image: Image.Image) -> Image.Image:
    return image.convert("RGB").resize((32, 32), Image.Resampling.BOX)


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(thumb(a), thumb(b))).mean) / 3


def write_tags(
    path: Path,
    *,
    title: str | None,
    track: str | None,
    artist: str | None = ARTIST,
    album: str | None = ALBUM,
    covers: tuple[tuple[str, bytes], ...] = (),
    version: int = 4,
) -> None:
    """Replace the file's ID3 tags with exactly these (what a careful user does with mutagen)."""
    tags = ID3()
    if artist is not None:
        tags.add(TPE1(encoding=3, text=[artist]))
    if album is not None:
        tags.add(TALB(encoding=3, text=[album]))
    if title is not None:
        tags.add(TIT2(encoding=3, text=[title]))
    if track is not None:
        tags.add(TRCK(encoding=3, text=[track]))
    for number, (mime, data) in enumerate(covers):
        tags.add(APIC(encoding=3, mime=mime, type=3, desc=f"Cover {number}", data=data))
    tags.save(path, v2_version=version)


class TagsTaskTest(unittest.TestCase):
    """What needs no ffmpeg: the prompt, the name and the registry."""

    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(get_task("tags").prompt, PROMPT)

    def test_it_is_named_tags_and_registered_as_held_out(self) -> None:
        task = get_task("tags")
        self.assertEqual(task.name, "tags")
        self.assertIs(TASKS["tags"], task)
        self.assertIn("tags", HELD_OUT)


@unittest.skipUnless(FFMPEG, NEEDS_FFMPEG)
class TagsFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("tags")
        cls.task.prepare_fixtures(cls.fixtures)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_prepare_fixtures_writes_the_three_mp3s_and_the_cover(self) -> None:
        self.assertEqual(sorted(p.name for p in self.fixtures.iterdir()), sorted([*NAMES, "cover.jpg"]))

    def test_each_mp3_is_a_three_second_mp3(self) -> None:
        for name in NAMES:
            with self.subTest(file=name):
                self.assertAlmostEqual(length(self.fixtures / name), 3.0, delta=0.15)

    def test_the_mp3s_are_tones_of_different_pitch(self) -> None:
        pitches = [tone_hz(self.fixtures / name) for name in NAMES]
        for index, pitch in enumerate(pitches):
            self.assertGreater(pitch, 100, pitches)  # a tone, not silence
            for other in pitches[index + 1 :]:
                self.assertGreater(abs(pitch - other), 50, pitches)
        self.assertEqual(len({(self.fixtures / n).read_bytes() for n in NAMES}), 3)

    def test_the_mp3s_have_no_tags(self) -> None:
        for name in NAMES:
            with self.subTest(file=name):
                try:
                    tags = ID3(self.fixtures / name)
                except ID3NoHeaderError:
                    continue
                for frame in ("TPE1", "TALB", "TIT2", "TRCK", "TPE2", "TCON", "TDRC", "TYER"):
                    self.assertNotIn(frame, tags)
                self.assertEqual(tags.getall("APIC"), [])

    def test_the_cover_is_a_600x600_jpeg_with_photo_like_detail(self) -> None:
        with Image.open(self.fixtures / "cover.jpg") as cover:
            self.assertEqual(cover.format, "JPEG")
            self.assertEqual(cover.size, (600, 600))
            stats = ImageStat.Stat(cover.convert("RGB"))
        self.assertGreater(sum(stats.stddev) / 3, 20)  # not a flat colour

    def test_setup_copies_the_four_files_into_the_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.task.setup(work, self.fixtures)
            self.assertEqual(sorted(p.name for p in work.iterdir()), sorted([*NAMES, "cover.jpg"]))
            for name in (*NAMES, "cover.jpg"):
                self.assertEqual((work / name).read_bytes(), (self.fixtures / name).read_bytes())


@unittest.skipUnless(FFMPEG, NEEDS_FFMPEG)
class TagsCheckTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        base = Path(cls._tmp.name)
        cls.fixtures = base / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("tags")
        cls.task.prepare_fixtures(cls.fixtures)
        cls.cover_bytes = (cls.fixtures / "cover.jpg").read_bytes()
        cls.cover = Image.open(cls.fixtures / "cover.jpg")
        cls.cover.load()
        cls.addClassCleanup(cls.cover.close)

        # audio for the "re-encoded" answers: the first file at its own length, cut short and made longer
        audio = base / "audio"
        audio.mkdir()
        cls.audio = audio
        source = cls.fixtures / NAMES[0]
        ffmpeg = FFMPEG or "ffmpeg"
        run(
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-map_metadata",
            "-1",
            "-b:a",
            "128k",
            str(audio / "same.mp3"),
        )
        run(ffmpeg, "-v", "error", "-y", "-i", str(source), "-map_metadata", "-1", "-t", "2", str(audio / "cut.mp3"))
        run(
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-map_metadata",
            "-1",
            "-af",
            "apad",
            "-t",
            "4.5",
            str(audio / "longer.mp3"),
        )
        # another picture of the same size, and a flat one
        cls.other_cover = photo_like((600, 600), seed=99)
        cls.flat_cover = Image.new("RGB", (600, 600), (128, 128, 128))

    # building blocks

    def path(self, index: int) -> Path:
        return self.work / NAMES[index]

    def tag_all(self, **overrides) -> None:
        """Tag all three files correctly, with `overrides` applied to every file's arguments."""
        for index in range(3):
            self.tag_one(index, **overrides)

    def tag_one(self, index: int, **overrides) -> None:
        arguments: dict[str, Any] = {
            "title": TITLES[index],
            "track": str(index + 1),
            "covers": (("image/jpeg", self.cover_bytes),),
        }
        arguments.update(overrides)
        write_tags(self.path(index), **arguments)

    def assert_names(self, result, *needles: str) -> None:
        joined = " ".join(result.details).lower()
        for needle in needles:
            self.assertIn(needle.lower(), joined, result.details)

    def test_the_inputs_of_these_tests_are_what_they_are_meant_to_be(self) -> None:
        # Guards the inputs of this test, so a pass or a failure below means something.
        self.assertAlmostEqual(length(self.audio / "same.mp3"), 3.0, delta=0.1)
        self.assertAlmostEqual(length(self.audio / "cut.mp3"), 2.0, delta=0.1)
        self.assertAlmostEqual(length(self.audio / "longer.mp3"), 4.5, delta=0.1)
        self.assertGreater(difference(self.cover, self.other_cover), 20)
        self.assertGreater(difference(self.cover, self.flat_cover), 10)
        self.assertLess(difference(self.cover, self.cover.resize((300, 300))), 2)

    # right answers

    def test_a_correct_answer_passes(self) -> None:
        self.tag_all()
        self.assert_passes()

    def test_final_text_does_not_matter(self) -> None:
        self.tag_all()
        self.assertTrue(self.check("").passed)
        self.assertTrue(self.check("something unrelated").passed)

    def test_id3v2_3_passes_too(self) -> None:
        self.tag_all(version=3)
        self.assert_passes()

    def test_the_track_may_be_written_as_1_01_or_1_of_3(self) -> None:
        for style in ("{n}", "{n:02}", "{n}/3", "{n:02}/03"):
            with self.subTest(style=style):
                for index in range(3):
                    self.tag_one(index, track=style.format(n=index + 1))
                self.assert_passes()

    def test_a_cover_scaled_down_and_stored_as_png_passes(self) -> None:
        self.tag_all(covers=(("image/png", png_bytes(self.cover.resize((300, 300), Image.Resampling.LANCZOS))),))
        self.assert_passes()

    def test_one_good_picture_among_other_pictures_is_enough(self) -> None:
        # "at least one embedded picture that decodes and resembles cover.jpg"
        self.tag_all(covers=(("image/jpeg", b"not an image"), ("image/jpeg", self.cover_bytes)))
        self.assert_passes()

    def test_files_re_encoded_to_the_same_length_pass(self) -> None:
        for index in range(3):
            shutil.copy(self.audio / "same.mp3", self.path(index))
        self.tag_all()
        self.assert_passes()

    def test_a_length_off_by_less_than_a_fifth_of_a_second_passes(self) -> None:
        # the check allows 0.2 s: a file about a tenth of a second shorter is fine
        with tempfile.TemporaryDirectory() as tmp:
            nearly = Path(tmp) / "nearly.mp3"
            run(
                FFMPEG or "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(self.path(0)),
                "-map_metadata",
                "-1",
                "-t",
                "2.9",
                str(nearly),
            )
            self.assertAlmostEqual(length(nearly), 2.9, delta=0.1)
            shutil.copy(nearly, self.path(0))
        self.tag_all()
        self.assert_passes()

    # missing or wrong tags

    def test_untouched_files_fail_and_every_file_and_tag_is_named(self) -> None:
        result = self.check()
        self.assert_fails_with(result, "artist")
        for name in NAMES:
            self.assert_names(result, name)
        self.assert_names(result, "album", "title", "track", "cover")

    def test_a_missing_artist_album_title_or_track_fails(self) -> None:
        for field in ("artist", "album", "title", "track"):
            with self.subTest(missing=field):
                self.tag_all()
                self.tag_one(1, **{field: None})
                result = self.check()
                self.assert_fails_with(result, field)
                self.assert_names(result, NAMES[1])

    def test_a_wrong_artist_fails(self) -> None:
        for artist in ("the testers", "The Tester", "Testers", "Various"):
            with self.subTest(artist=artist):
                self.tag_all()
                self.tag_one(2, artist=artist)
                result = self.check()
                self.assert_fails_with(result, "artist", reasons=1)
                self.assert_names(result, NAMES[2])

    def test_a_wrong_album_fails(self) -> None:
        for album in ("bench songs", "Bench", "Bench Songs 2"):
            with self.subTest(album=album):
                self.tag_all()
                self.tag_one(0, album=album)
                result = self.check()
                self.assert_fails_with(result, "album", reasons=1)
                self.assert_names(result, NAMES[0])

    def test_a_title_that_is_not_the_one_in_the_file_name_fails(self) -> None:
        for title in ("first light", "01 - First Light", "01 - First Light.mp3", "Second Wind", "First Light (demo)"):
            with self.subTest(title=title):
                self.tag_all()
                self.tag_one(0, title=title)
                result = self.check()
                self.assert_fails_with(result, "title", reasons=1)
                self.assert_names(result, NAMES[0])

    def test_a_wrong_track_number_fails(self) -> None:
        for track in ("2", "0", "4", "10", "02/3"):
            with self.subTest(track=track):
                self.tag_all()
                self.tag_one(0, track=track)
                result = self.check()
                self.assert_fails_with(result, "track", reasons=1)
                self.assert_names(result, NAMES[0])

    def test_all_tracks_numbered_1_fail(self) -> None:
        self.tag_all(track="1")
        result = self.check()
        self.assert_fails_with(result, "track")
        self.assert_names(result, NAMES[1], NAMES[2])

    def test_a_file_left_untagged_fails_and_is_named(self) -> None:
        self.tag_one(0)
        self.tag_one(1)
        result = self.check()
        self.assert_fails_with(result, "artist", "title", "tag")
        self.assert_names(result, NAMES[2])
        self.assertNotIn(NAMES[0].lower(), " ".join(result.details).lower())

    # the cover

    def test_a_missing_cover_fails(self) -> None:
        self.tag_all()
        self.tag_one(1, covers=())
        result = self.check()
        self.assert_fails_with(result, "cover", "picture", "image", "art", reasons=1)
        self.assert_names(result, NAMES[1])

    def test_a_cover_missing_in_every_file_fails(self) -> None:
        self.tag_all(covers=())
        result = self.check()
        self.assert_fails_with(result, "cover", "picture", "image", "art")
        for name in NAMES:
            self.assert_names(result, name)

    def test_another_picture_as_the_cover_fails(self) -> None:
        for label, picture in (("other photo", self.other_cover), ("flat grey", self.flat_cover)):
            with self.subTest(cover=label):
                self.tag_all()
                self.tag_one(2, covers=(("image/jpeg", jpeg_bytes(picture)),))
                result = self.check()
                self.assert_fails_with(result, "cover", "picture", "image", "resembl", reasons=1)
                self.assert_names(result, NAMES[2])

    def test_a_cover_that_does_not_decode_fails(self) -> None:
        for label, data in (("text", b"this is not an image"), ("truncated", self.cover_bytes[:200]), ("empty", b"")):
            with self.subTest(cover=label):
                self.tag_all()
                self.tag_one(0, covers=(("image/jpeg", data),))
                result = self.check()
                self.assert_fails_with(result, "cover", "picture", "image", "decode", reasons=1)
                self.assert_names(result, NAMES[0])

    # the audio and the files

    def test_a_renamed_file_fails(self) -> None:
        self.tag_all()
        self.path(0).rename(self.work / "First Light.mp3")
        result = self.check()
        self.assert_fails_with(result, NAMES[0], "missing", "exist")

    def test_a_missing_file_fails(self) -> None:
        self.tag_all()
        self.path(2).unlink()
        result = self.check()
        self.assert_fails_with(result, NAMES[2], "missing", "exist")

    def test_a_file_re_encoded_to_a_different_length_fails(self) -> None:
        for label in ("cut.mp3", "longer.mp3"):
            with self.subTest(audio=label):
                shutil.copy(self.audio / label, self.path(0))
                self.tag_all()
                result = self.check()
                self.assert_fails_with(result, "length", "duration", "second", "audio", reasons=1)
                self.assert_names(result, NAMES[0])

    def test_something_that_is_not_an_mp3_fails(self) -> None:
        pictures = {"png.mp3": png_bytes(self.cover), "text.mp3": b"First Light, a lovely song\n", "empty.mp3": b""}
        for label, data in pictures.items():
            with self.subTest(content=label):
                self.tag_all()
                self.path(1).write_bytes(data)
                result = self.check()
                self.assert_fails_with(result, "mp3", "audio", "valid", "read", "open")
                self.assert_names(result, NAMES[1])

    def test_a_changed_cover_jpg_fails_even_with_correct_tags(self) -> None:
        self.tag_all()
        (self.work / "cover.jpg").write_bytes(jpeg_bytes(self.other_cover))
        result = self.check()
        self.assert_fails_with(result, "cover.jpg", reasons=1)
        self.assert_fails_with(result, "unchanged", "sha", "hash", "modified", "changed")

    def test_a_deleted_cover_jpg_fails_even_with_correct_tags(self) -> None:
        self.tag_all()
        (self.work / "cover.jpg").unlink()
        self.assert_fails_with(self.check(), "cover.jpg", "unchanged", "missing", "changed")

    def test_check_does_not_modify_the_work_folder(self) -> None:
        self.tag_all()
        self.assert_check_leaves_work_folder_alone()

    def test_check_does_not_modify_the_work_folder_when_it_fails(self) -> None:
        self.tag_all()
        self.tag_one(0, covers=())
        self.assert_check_leaves_work_folder_alone()


@unittest.skipUnless(FFMPEG, NEEDS_FFMPEG)
@unittest.skipUnless(HAVE_JZR, NEEDS_JZR)
class TagsJzrTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("tags")
        cls.task.prepare_fixtures(cls.fixtures)

    def test_jzr_audio_tags_on_each_file_passes_the_check(self) -> None:
        for number, (name, title) in enumerate(zip(NAMES, TITLES, strict=True), 1):
            run_jzr(
                [
                    "audio",
                    "tags",
                    name,
                    "--set",
                    f"artist={ARTIST}",
                    "--set",
                    f"album={ALBUM}",
                    "--set",
                    f"title={title}",
                    "--set",
                    f"track={number}",
                    "--cover",
                    "cover.jpg",
                ],
                self.work,
            )
        self.assert_passes()


if __name__ == "__main__":
    unittest.main()
