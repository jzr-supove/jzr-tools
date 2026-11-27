"""Tests for the rename task (held out): six photos to trip-001.jpg .. trip-006.jpg, oldest first by modification time.

The fixture is made once per class with the task's own prepare_fixtures; Task.setup must give the
copies the fixtures' modification times. Correct answers are made by renaming the work folder's files
with os.rename (which keeps their times); each wrong one differs from a correct one in exactly one way.
"""

from __future__ import annotations

import itertools
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench.tasks import HELD_OUT, TASKS, get_task  # noqa: E402
from jzr_support import HAVE_JZR, NEEDS_JZR, run_jzr  # noqa: E402
from PIL import Image, ImageChops, ImageStat  # noqa: E402
from task_check_support import CheckCase  # noqa: E402

PROMPT = "Rename the six photos in this folder to trip-001.jpg up to trip-006.jpg, oldest first by modification time."
LISTED = ["IMG 2024 (1).JPG", "DSC_0042.jpeg", "photo-b.jpg", "Scan 3.JPEG", "holiday.jpg", "z_last.Jpg"]
TRIPS = [f"trip-{n:03}.jpg" for n in range(1, 7)]


def by_mtime(folder: Path, names: list[str]) -> list[str]:
    """The names, oldest first by modification time."""
    return sorted(names, key=lambda name: (folder / name).stat().st_mtime_ns)


def thumb(image: Image.Image) -> Image.Image:
    return image.convert("RGB").resize((32, 24), Image.Resampling.BOX)


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(thumb(a), thumb(b))).mean) / 3


class RenameTaskTest(unittest.TestCase):
    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(get_task("rename").prompt, PROMPT)

    def test_it_is_named_rename_and_registered_as_held_out(self) -> None:
        task = get_task("rename")
        self.assertEqual(task.name, "rename")
        self.assertIs(TASKS["rename"], task)
        self.assertIn("rename", HELD_OUT)


class RenameFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("rename")
        cls.task.prepare_fixtures(cls.fixtures)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_prepare_fixtures_writes_exactly_the_six_photos(self) -> None:
        self.assertEqual(sorted(p.name for p in self.fixtures.iterdir()), sorted(LISTED))

    def test_each_is_a_320x240_jpeg_whatever_its_extension(self) -> None:
        for name in LISTED:
            with self.subTest(photo=name), Image.open(self.fixtures / name) as image:
                self.assertEqual((image.format, image.size), ("JPEG", (320, 240)))

    def test_the_six_pictures_are_all_different(self) -> None:
        self.assertEqual(len({(self.fixtures / name).read_bytes() for name in LISTED}), 6)
        thumbs = {}
        for name in LISTED:
            with Image.open(self.fixtures / name) as image:
                thumbs[name] = image.convert("RGB")
        for a, b in itertools.combinations(LISTED, 2):
            with self.subTest(pair=(a, b)):
                self.assertGreater(difference(thumbs[a], thumbs[b]), 8)

    def test_the_photos_are_an_hour_apart(self) -> None:
        times = sorted((self.fixtures / name).stat().st_mtime for name in LISTED)
        for earlier, later in itertools.pairwise(times):
            self.assertAlmostEqual(later - earlier, 3600, delta=1)

    def test_oldest_first_is_neither_the_listed_nor_the_alphabetical_order(self) -> None:
        order = by_mtime(self.fixtures, LISTED)
        self.assertEqual(sorted(order), sorted(LISTED))
        self.assertNotEqual(order, LISTED)
        self.assertNotEqual(order, sorted(LISTED))
        self.assertNotEqual(order, sorted(LISTED, key=str.lower))

    def test_setup_copies_the_files_with_their_modification_times(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.task.setup(work, self.fixtures)
            self.assertEqual(sorted(p.name for p in work.iterdir()), sorted(LISTED))
            for name in LISTED:
                with self.subTest(photo=name):
                    self.assertEqual((work / name).read_bytes(), (self.fixtures / name).read_bytes())
                    self.assertAlmostEqual(
                        (work / name).stat().st_mtime, (self.fixtures / name).stat().st_mtime, delta=0.01
                    )
            self.assertEqual(by_mtime(work, LISTED), by_mtime(self.fixtures, LISTED))

    def test_setup_prepares_an_empty_fixture_dir_and_keeps_the_times(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixtures, work = Path(tmp) / "f", Path(tmp) / "w"
            self.task.setup(work, fixtures)
            self.assertEqual(sorted(p.name for p in work.iterdir()), sorted(LISTED))
            self.assertEqual(by_mtime(work, LISTED), by_mtime(fixtures, LISTED))
            self.assertEqual(by_mtime(work, LISTED), by_mtime(self.fixtures, LISTED))

    def test_the_order_of_the_times_is_the_same_every_time_it_is_prepared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.task.prepare_fixtures(Path(tmp))
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), sorted(LISTED))
            self.assertEqual(by_mtime(Path(tmp), LISTED), by_mtime(self.fixtures, LISTED))


class RenameCheckTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("rename")
        cls.task.prepare_fixtures(cls.fixtures)
        cls.order = by_mtime(cls.fixtures, LISTED)  # oldest first

    def rename_in_order(self, order: list[str], trips: list[str] | None = None) -> None:
        """Rename the work folder's files so that order[i] becomes trips[i]."""
        # (through temporary names, so that nothing is overwritten on the way)
        trips = trips or TRIPS
        for number, name in enumerate(order):
            os.rename(self.work / name, self.work / f".tmp{number}")
        for number, trip in enumerate(trips):
            os.rename(self.work / f".tmp{number}", self.work / trip)

    def folder_images(self) -> list[str]:
        return sorted(p.name for p in self.work.iterdir() if p.is_file())

    def test_the_inputs_of_these_tests_are_what_they_are_meant_to_be(self) -> None:
        # Guards the inputs of this test, so a pass or a failure below means something.
        self.assertEqual(by_mtime(self.work, LISTED), self.order)
        for wrong in (LISTED, sorted(LISTED), sorted(LISTED, key=str.lower), self.order[::-1]):
            self.assertNotEqual(wrong, self.order)

    # right answers

    def test_oldest_first_by_modification_time_passes(self) -> None:
        self.rename_in_order(self.order)
        self.assertEqual(self.folder_images(), TRIPS)
        self.assert_passes()

    def test_final_text_does_not_matter(self) -> None:
        self.rename_in_order(self.order)
        self.assertTrue(self.check("").passed)
        self.assertTrue(self.check("something unrelated").passed)

    def test_other_files_in_the_folder_do_not_count(self) -> None:
        # "as its image files": a log or a notes file left beside the photos is not an image
        self.rename_in_order(self.order)
        (self.work / "rename.log").write_text("renamed 6 files\n")
        self.assert_passes()

    def test_the_times_of_the_renamed_files_do_not_matter(self) -> None:
        # an answer made by copying the files (new times) and deleting the old ones is as good: only names and bytes count
        for number, name in enumerate(self.order):
            (self.work / TRIPS[number]).write_bytes((self.work / name).read_bytes())
            (self.work / name).unlink()
        self.assert_passes()

    # wrong order

    def test_alphabetical_order_fails(self) -> None:
        for label, order in {"case-sensitive": sorted(LISTED), "ignoring case": sorted(LISTED, key=str.lower)}.items():
            with self.subTest(sorted=label):
                self.rename_in_order(order)
                self.assert_fails_with(self.check(), "order", "trip-001.jpg", "mtime", "modification", "wrong")
                self.rename_in_order(TRIPS, trips=order)

    def test_the_listed_order_fails(self) -> None:
        self.rename_in_order(LISTED)
        self.assert_fails_with(self.check(), "order", "trip-001.jpg", "mtime", "modification", "wrong")

    def test_newest_first_fails(self) -> None:
        self.rename_in_order(self.order[::-1])
        self.assert_fails_with(self.check(), "order", "trip-001.jpg", "mtime", "modification", "wrong")

    def test_two_neighbours_swapped_fails_and_names_both_places(self) -> None:
        swapped = list(self.order)
        swapped[2], swapped[3] = swapped[3], swapped[2]
        self.rename_in_order(swapped)
        result = self.check()
        self.assert_fails_with(result, "trip-003.jpg", "trip-004.jpg")
        joined = " ".join(result.details)
        self.assertIn("trip-003.jpg", joined)
        self.assertIn("trip-004.jpg", joined)

    # old names, missing and changed files

    def test_nothing_done_fails(self) -> None:
        self.assert_fails_with(self.check(), "trip-001.jpg", "missing", "exist")

    def test_a_leftover_old_name_fails(self) -> None:
        self.rename_in_order(self.order)
        (self.work / "holiday.jpg").write_bytes((self.work / "trip-006.jpg").read_bytes())
        self.assert_fails_with(self.check(), "holiday.jpg", "leftover", "unexpected", "extra", "old")

    def test_copies_instead_of_renames_fail(self) -> None:
        for number, name in enumerate(self.order):
            (self.work / TRIPS[number]).write_bytes((self.work / name).read_bytes())
        self.assertEqual(len(self.folder_images()), 12)
        result = self.check()
        self.assert_fails_with(result, "leftover", "unexpected", "extra", "old", "12")
        self.assertIn("holiday.jpg", " ".join(result.details))

    def test_one_file_left_with_its_old_name_fails_naming_it_and_the_missing_one(self) -> None:
        self.rename_in_order(self.order[:5], TRIPS[:5])
        left = self.order[5]
        result = self.check()
        self.assert_fails_with(result, left, "leftover", "unexpected", "old")
        self.assertIn("trip-006.jpg", " ".join(result.details))

    def test_a_missing_file_fails(self) -> None:
        self.rename_in_order(self.order)
        (self.work / "trip-006.jpg").unlink()
        self.assert_fails_with(self.check(), "trip-006.jpg", "missing", "exist")

    def test_a_seventh_trip_file_fails(self) -> None:
        self.rename_in_order(self.order)
        (self.work / "trip-007.jpg").write_bytes((self.work / "trip-001.jpg").read_bytes())
        self.assert_fails_with(self.check(), "trip-007.jpg", "unexpected", "extra", "seven", "7")

    def test_a_different_numbering_or_name_fails(self) -> None:
        for label, trips in {
            "from 000": [f"trip-{n:03}.jpg" for n in range(0, 6)],
            "unpadded": [f"trip-{n}.jpg" for n in range(1, 7)],
            "jpeg extension": [f"trip-{n:03}.jpeg" for n in range(1, 7)],
            "other prefix": [f"trip_{n:03}.jpg" for n in range(1, 7)],
        }.items():
            with self.subTest(names=label):
                self.rename_in_order(self.order, trips=trips)
                self.assert_fails_with(self.check(), "trip-", "missing", "unexpected", "name")
                self.rename_in_order(trips, trips=self.order)

    def test_the_photos_moved_into_a_subfolder_fail(self) -> None:
        (self.work / "trips").mkdir()
        self.rename_in_order(self.order)
        for trip in TRIPS:
            os.rename(self.work / trip, self.work / "trips" / trip)
        self.assert_fails_with(self.check(), "trip-001.jpg", "missing", "exist")

    def test_a_re_encoded_file_fails_and_names_its_place(self) -> None:
        self.rename_in_order(self.order)
        target = self.work / "trip-003.jpg"
        with Image.open(target) as image:
            image.convert("RGB").save(target, format="JPEG", quality=70)  # the same picture, other bytes
        result = self.check()
        self.assert_fails_with(result, "trip-003.jpg", "bytes", "content", "changed", "differ", "wrong", reasons=1)

    def test_the_bytes_of_another_photo_in_a_place_fail(self) -> None:
        self.rename_in_order(self.order)
        (self.work / "trip-002.jpg").write_bytes((self.work / "trip-005.jpg").read_bytes())
        result = self.check()
        self.assert_fails_with(result, "trip-002.jpg", "bytes", "content", "changed", "differ", "wrong")

    def test_an_empty_file_in_a_place_fails(self) -> None:
        self.rename_in_order(self.order)
        (self.work / "trip-004.jpg").write_bytes(b"")
        self.assert_fails_with(self.check(), "trip-004.jpg")

    def test_check_does_not_modify_the_work_folder(self) -> None:
        self.rename_in_order(self.order)
        self.assert_check_leaves_work_folder_alone()

    def test_check_does_not_modify_the_work_folder_when_it_fails(self) -> None:
        self.rename_in_order(LISTED)
        self.assert_check_leaves_work_folder_alone()


@unittest.skipUnless(HAVE_JZR, NEEDS_JZR)
class RenameJzrTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("rename")
        cls.task.prepare_fixtures(cls.fixtures)

    def test_jzr_file_rename_sorted_by_mtime_passes_the_check(self) -> None:
        run_jzr(["file", "rename", "--template", "trip-{n:03}", "--sort", "mtime", "--ext", "jpg", "-y"], self.work)
        self.assertEqual(sorted(p.name for p in self.work.iterdir()), TRIPS)
        self.assert_passes()


if __name__ == "__main__":
    unittest.main()
