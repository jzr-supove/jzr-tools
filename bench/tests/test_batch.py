"""Tests for the batch task (held out): seven pictures of five formats under photos/ to JPEGs in out/.

The fixture is made once per class with the task's own prepare_fixtures. Correct answers are made with
Pillow and pillow-heif (the runner venv's), flat or mirroring the tree; each wrong one differs from a
correct one in exactly one way.
"""

from __future__ import annotations

import itertools
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import pillow_heif  # noqa: E402
from jzr_bench.tasks import HELD_OUT, TASKS, get_task  # noqa: E402
from jzr_bench.tasks._pictures import photo_like  # noqa: E402
from jzr_support import HAVE_JZR, NEEDS_JZR, run_jzr  # noqa: E402
from PIL import Image, ImageChops, ImageStat  # noqa: E402
from task_check_support import CheckCase, snapshot  # noqa: E402

pillow_heif.register_heif_opener()

PROMPT = (
    "Convert every image under photos/ (subfolders too) to JPEG and put the JPEGs in out/. "
    "Two of them have the same name: keep both. Leave photos/ as it is."
)
SOURCES = ("a.png", "b.heic", "c.tiff", "d.bmp", "sub/a.png", "sub/e.png", "sub/deep/f.webp")
FORMATS = ("PNG", "HEIF", "TIFF", "BMP", "PNG", "PNG", "WEBP")
SIZE = (800, 600)

# where each source's JPEG goes, in the two layouts the spec allows
FLAT = {
    "a.png": "a.jpg",
    "b.heic": "b.jpg",
    "c.tiff": "c.jpg",
    "d.bmp": "d.jpg",
    "sub/a.png": "sub-a.jpg",
    "sub/e.png": "e.jpg",
    "sub/deep/f.webp": "f.jpg",
}
MIRRORED = {rel: str(Path(rel).with_suffix(".jpg")) for rel in SOURCES}


def thumb(image: Image.Image) -> Image.Image:
    return image.convert("RGB").resize((32, 24), Image.Resampling.BOX)


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(thumb(a), thumb(b))).mean) / 3


def load(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB")


def save_jpeg(image: Image.Image, dest: Path, *, size: tuple[int, int] | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    picture = image.resize(size, Image.Resampling.LANCZOS) if size else image
    picture.save(dest, format="JPEG", quality=90)
    return dest


class BatchTaskTest(unittest.TestCase):
    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(get_task("batch").prompt, PROMPT)

    def test_it_is_named_batch_and_registered_as_held_out(self) -> None:
        task = get_task("batch")
        self.assertEqual(task.name, "batch")
        self.assertIs(TASKS["batch"], task)
        self.assertIn("batch", HELD_OUT)


class BatchFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("batch")
        cls.task.prepare_fixtures(cls.fixtures)
        cls.photos = cls.fixtures / "photos"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_prepare_fixtures_makes_only_the_photos_folder(self) -> None:
        self.assertEqual([p.name for p in self.fixtures.iterdir()], ["photos"])

    def test_the_seven_pictures_are_where_the_spec_says(self) -> None:
        found = sorted(str(p.relative_to(self.photos)) for p in self.photos.rglob("*") if p.is_file())
        self.assertEqual(found, sorted(SOURCES))

    def test_the_folders_are_photos_sub_and_sub_deep(self) -> None:
        folders = sorted(str(p.relative_to(self.photos)) for p in self.photos.rglob("*") if p.is_dir())
        self.assertEqual(folders, ["sub", "sub/deep"])

    def test_each_is_a_picture_in_its_own_format_of_800x600(self) -> None:
        for rel, expected in zip(SOURCES, FORMATS, strict=True):
            with self.subTest(source=rel), Image.open(self.photos / rel) as image:
                self.assertEqual(image.format, expected)
                self.assertEqual(image.size, SIZE)

    def test_two_sources_share_the_name_a_png(self) -> None:
        self.assertEqual([Path(rel).name for rel in SOURCES].count("a.png"), 2)

    def test_the_seven_pictures_are_clearly_different_from_each_other(self) -> None:
        # the check matches each source to its JPEG by a thumbnail difference under 8: the sources must be much further apart
        thumbs = {rel: load(self.photos / rel) for rel in SOURCES}
        for a, b in itertools.combinations(SOURCES, 2):
            with self.subTest(pair=(a, b)):
                self.assertGreater(difference(thumbs[a], thumbs[b]), 16)

    def test_the_pictures_are_photo_like_not_flat(self) -> None:
        for rel in SOURCES:
            with self.subTest(source=rel):
                stats = ImageStat.Stat(load(self.photos / rel))
                self.assertGreater(sum(stats.stddev) / 3, 20)

    def test_setup_copies_the_whole_tree_into_the_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.task.setup(work, self.fixtures)
            self.assertEqual([p.name for p in work.iterdir()], ["photos"])
            self.assertEqual(snapshot(work / "photos"), snapshot(self.photos))
            self.assertEqual(sorted(snapshot(work / "photos")), sorted(SOURCES))
            self.assertFalse((work / "out").exists())

    def test_setup_prepares_an_empty_fixture_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixtures, work = Path(tmp) / "f", Path(tmp) / "w"
            self.task.setup(work, fixtures)
            self.assertEqual(sorted(snapshot(work / "photos")), sorted(SOURCES))
            self.assertEqual(sorted(snapshot(fixtures / "photos")), sorted(SOURCES))


class BatchCheckTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("batch")
        cls.task.prepare_fixtures(cls.fixtures)
        cls.pictures = {rel: load(cls.fixtures / "photos" / rel) for rel in SOURCES}
        cls.stranger = photo_like(SIZE, seed=123)  # a picture that is none of the seven

    def convert(self, layout: dict[str, str], *, skip: tuple[str, ...] = (), folder: str = "out") -> None:
        """Write each source's JPEG where `layout` says (under `folder`), as a careful user would."""
        for rel, target in layout.items():
            if rel not in skip:
                save_jpeg(self.pictures[rel], self.work / folder / target)

    def jpegs(self) -> list[Path]:
        return sorted(p for p in (self.work / "out").rglob("*") if p.is_file())

    def assert_names(self, result, *needles: str) -> None:
        joined = " ".join(result.details).lower()
        for needle in needles:
            self.assertIn(needle.lower(), joined, result.details)

    def test_the_inputs_of_these_tests_are_what_they_are_meant_to_be(self) -> None:
        # Guards the inputs of this test, so a pass or a failure below means something.
        self.convert(FLAT)
        self.assertEqual(len(self.jpegs()), 7)
        for path in self.jpegs():
            with Image.open(path) as image:
                self.assertEqual((image.format, image.size), ("JPEG", SIZE))
        for rel, target in FLAT.items():
            self.assertLess(difference(load(self.work / "out" / target), self.pictures[rel]), 4, rel)
        self.assertGreater(min(difference(self.stranger, p) for p in self.pictures.values()), 16)

    # right answers

    def test_a_flat_out_folder_with_unique_names_passes(self) -> None:
        self.convert(FLAT)
        self.assert_passes()

    def test_a_mirrored_tree_passes(self) -> None:
        self.convert(MIRRORED)
        self.assertTrue((self.work / "out" / "sub" / "a.jpg").is_file())
        self.assert_passes()

    def test_final_text_does_not_matter(self) -> None:
        self.convert(FLAT)
        self.assertTrue(self.check("").passed)
        self.assertTrue(self.check("something unrelated").passed)

    def test_any_names_pass(self) -> None:
        # names in the reverse order of the sources: a source is matched by its picture, not by its name
        layout = {rel: f"photo-{9 - number}.jpg" for number, rel in enumerate(SOURCES, 1)}
        self.convert(layout)
        self.assert_passes()

    def test_the_jpeg_extension_may_be_jpeg(self) -> None:
        self.convert({rel: str(Path(target).with_suffix(".jpeg")) for rel, target in FLAT.items()})
        self.assert_passes()

    def test_a_mix_of_flat_files_and_subfolders_passes(self) -> None:
        layout = dict(FLAT)
        layout["sub/a.png"] = "sub/a.jpg"
        layout["sub/deep/f.webp"] = "sub/deep/f.jpg"
        self.convert(layout)
        self.assert_passes()

    def test_the_second_file_of_a_name_clash_numbered_by_a_tool_passes(self) -> None:
        layout = dict(FLAT)
        layout["sub/a.png"] = "a-2.jpg"
        self.convert(layout)
        self.assert_passes()

    # wrong answers

    def test_no_out_folder_fails(self) -> None:
        self.assert_fails_with(self.check(), "out")

    def test_an_empty_out_folder_fails(self) -> None:
        (self.work / "out").mkdir()
        self.assert_fails_with(self.check(), "out", "0", "seven", "7")

    def test_a_missing_picture_fails(self) -> None:
        for rel in SOURCES:
            with self.subTest(left_out=rel):
                shutil.rmtree(self.work / "out", ignore_errors=True)
                self.convert(FLAT, skip=(rel,))
                result = self.check()
                self.assert_fails_with(result, "6", "seven", "7", rel)

    def test_the_clash_resolved_by_overwriting_fails(self) -> None:
        # sub/a.png converted over a.png's JPEG: six files, and nothing resembles a.png
        self.convert(FLAT, skip=("a.png", "sub/a.png"))
        save_jpeg(self.pictures["sub/a.png"], self.work / "out" / "a.jpg")
        result = self.check()
        self.assert_fails_with(result, "6", "seven", "7", "a.png")

    def test_a_duplicate_of_one_source_in_place_of_another_fails(self) -> None:
        # seven JPEGs, but sub/a.png's is a second copy of a.png's
        self.convert(FLAT, skip=("sub/a.png",))
        save_jpeg(self.pictures["a.png"], self.work / "out" / "sub-a.jpg")
        result = self.check()
        self.assert_fails_with(result, "sub/a.png", "resembl", "match", "duplicate")

    def test_a_picture_that_is_none_of_the_sources_fails(self) -> None:
        self.convert(FLAT, skip=("sub/deep/f.webp",))
        save_jpeg(self.stranger, self.work / "out" / "f.jpg")
        result = self.check()
        self.assert_fails_with(result, "f.webp", "resembl", "match")

    def test_an_eighth_jpeg_fails(self) -> None:
        self.convert(FLAT)
        save_jpeg(self.pictures["b.heic"], self.work / "out" / "b-again.jpg")
        self.assert_fails_with(self.check(), "8", "seven", "7", "exactly")

    def test_a_wrong_size_fails(self) -> None:
        for label, size in {"half size": (400, 300), "rotated": (600, 800), "one pixel high": (800, 601)}.items():
            with self.subTest(size=label):
                shutil.rmtree(self.work / "out", ignore_errors=True)
                self.convert(FLAT, skip=("c.tiff",))
                save_jpeg(self.pictures["c.tiff"], self.work / "out" / "c.jpg", size=size)
                result = self.check()
                self.assert_fails_with(result, "800x600", "size", "c.jpg")
                self.assert_names(result, "c.jpg")

    def test_png_bytes_under_a_jpg_name_fail(self) -> None:
        self.convert(FLAT, skip=("d.bmp",))
        self.pictures["d.bmp"].save(self.work / "out" / "d.jpg", format="PNG")
        result = self.check()
        self.assert_fails_with(result, "d.jpg", "jpeg", "png")

    def test_a_text_file_under_a_jpg_name_fails(self) -> None:
        self.convert(FLAT, skip=("d.bmp",))
        (self.work / "out" / "d.jpg").write_text("not a picture\n")
        result = self.check()
        self.assert_fails_with(result, "d.jpg", "jpeg", "valid", "image", "read")

    def test_webp_bytes_under_a_jpeg_name_fail(self) -> None:
        self.convert(FLAT, skip=("sub/deep/f.webp",))
        self.pictures["sub/deep/f.webp"].save(self.work / "out" / "f.jpeg", format="WEBP")
        result = self.check()
        self.assert_fails_with(result, "f.jpeg", "jpeg", "webp")

    def test_jpegs_left_in_the_wrong_place_fail(self) -> None:
        # converted next to the sources, none in out/
        self.convert(MIRRORED, folder="photos")
        result = self.check()
        self.assert_fails_with(result, "out", "photos", "added")

    # photos/ must stay as it is

    def test_a_file_added_to_photos_fails_even_with_correct_output(self) -> None:
        self.convert(FLAT)
        save_jpeg(self.pictures["a.png"], self.work / "photos" / "a.jpg")
        result = self.check()
        self.assert_fails_with(result, "photos", "a.jpg", "added")
        self.assert_fails_with(result, "photos/a.jpg", "a.jpg")

    def test_a_file_added_to_a_subfolder_of_photos_fails(self) -> None:
        self.convert(FLAT)
        (self.work / "photos" / "sub" / "deep" / "notes.txt").write_text("converted\n")
        self.assert_fails_with(self.check(), "photos", "notes.txt", "added")

    def test_the_whole_out_folder_made_inside_photos_fails(self) -> None:
        self.convert(FLAT, folder="photos/out")
        shutil.copytree(self.work / "photos" / "out", self.work / "out")
        self.assert_fails_with(self.check(), "photos", "added")

    def test_a_changed_source_fails_even_with_correct_output(self) -> None:
        self.convert(FLAT)
        save_jpeg(self.pictures["c.tiff"], self.work / "photos" / "c.tiff")  # JPEG bytes under the .tiff name
        result = self.check()
        self.assert_fails_with(result, "c.tiff", reasons=1)
        self.assert_fails_with(result, "unchanged", "sha", "hash", "modified", "changed")

    def test_a_source_rewritten_with_the_same_picture_fails(self) -> None:
        # same pixels, different bytes: only a content hash notices
        self.convert(FLAT)
        self.pictures["a.png"].save(self.work / "photos" / "a.png", format="PNG", compress_level=0)
        self.assertNotEqual(
            (self.work / "photos" / "a.png").read_bytes(), (self.fixtures / "photos" / "a.png").read_bytes()
        )
        self.assert_fails_with(self.check(), "a.png", "unchanged", "sha", "hash", "modified", "changed")

    def test_a_source_moved_instead_of_copied_fails(self) -> None:
        self.convert(FLAT)
        (self.work / "photos" / "b.heic").unlink()
        result = self.check()
        self.assert_fails_with(result, "b.heic", "missing", "unchanged", "changed")

    def test_a_deleted_subfolder_of_photos_fails(self) -> None:
        self.convert(FLAT)
        shutil.rmtree(self.work / "photos" / "sub" / "deep")
        result = self.check()
        self.assert_fails_with(result, "f.webp", "missing", "unchanged", "changed")

    def test_check_does_not_modify_the_work_folder(self) -> None:
        self.convert(FLAT)
        self.assert_check_leaves_work_folder_alone()

    def test_check_does_not_modify_the_work_folder_when_it_fails(self) -> None:
        self.convert(FLAT, skip=("a.png",))
        self.assert_check_leaves_work_folder_alone()


@unittest.skipUnless(HAVE_JZR, NEEDS_JZR)
class BatchJzrTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("batch")
        cls.task.prepare_fixtures(cls.fixtures)

    def test_jzr_image_convert_of_the_tree_passes_the_check(self) -> None:
        run_jzr(["image", "convert", "--dir", "photos", "-r", "--to", "jpg", "--outdir", "out"], self.work)
        names = sorted(str(p.relative_to(self.work / "out")) for p in (self.work / "out").rglob("*") if p.is_file())
        self.assertEqual(len(names), 7, names)
        self.assert_passes()


if __name__ == "__main__":
    unittest.main()
