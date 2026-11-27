"""Tests for the heic task: three 4032x3024 HEIC photos to WebP, longest side 1600, originals kept.

Fixtures (HEIC encoding is the slow part) are made once for the whole module with the task's own
prepare_fixtures. The correct result is made the way a careful user would, with the runner venv's
pillow-heif and Pillow; the wrong ones differ from it in exactly one way.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import pillow_heif  # noqa: E402
from jzr_bench.tasks import get_task  # noqa: E402
from PIL import Image, ImageChops, ImageStat  # noqa: E402
from task_check_support import CheckCase  # noqa: E402

pillow_heif.register_heif_opener()

PROMPT = "Convert the HEIC photos in this folder to WebP, at most 1600 pixels on the longest side. Keep the originals."
STEMS = ("IMG_0001", "IMG_0002", "IMG_0003")
SOURCES = tuple(f"{stem}.HEIC" for stem in STEMS)

TMP: tempfile.TemporaryDirectory[str] | None = None
FIXTURES: Path
PHOTOS: dict[str, Image.Image] = {}  # stem -> the decoded source, RGB, full size
GOOD: dict[str, Image.Image] = {}  # stem -> the source scaled to 1600x1200, RGB
GOOD_DIR: Path
VARIANT_DIR: Path


def save_webp(image: Image.Image, dest: Path, quality: int = 80) -> Path:
    image.save(dest, format="WEBP", quality=quality)
    return dest


def thumb(image: Image.Image) -> Image.Image:
    return image.convert("RGB").resize((32, 24), Image.Resampling.BOX)


def mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(thumb(a), thumb(b))).mean) / 3


def setUpModule() -> None:
    global TMP, FIXTURES, GOOD_DIR, VARIANT_DIR
    TMP = tempfile.TemporaryDirectory()
    base = Path(TMP.name)
    FIXTURES = base / "fixtures"
    FIXTURES.mkdir()
    get_task("heic").prepare_fixtures(FIXTURES)

    GOOD_DIR = base / "good"
    VARIANT_DIR = base / "variants"
    GOOD_DIR.mkdir()
    VARIANT_DIR.mkdir()
    for stem, name in zip(STEMS, SOURCES, strict=True):
        with Image.open(FIXTURES / name) as im:
            PHOTOS[stem] = im.convert("RGB")
        GOOD[stem] = PHOTOS[stem].resize((1600, 1200), Image.Resampling.LANCZOS)
        save_webp(GOOD[stem], GOOD_DIR / f"{stem}.webp")

    first = GOOD[STEMS[0]]
    v = VARIANT_DIR
    # one wrong thing each, all for IMG_0001
    save_webp(first.resize((800, 600), Image.Resampling.LANCZOS), v / "small.webp")  # longest side 800
    save_webp(PHOTOS[STEMS[0]], v / "fullsize.webp")  # not resized: 4032x3024
    save_webp(first.resize((1600, 1000), Image.Resampling.LANCZOS), v / "short.webp")  # aspect squashed
    save_webp(first.resize((1600, 1600), Image.Resampling.LANCZOS), v / "square.webp")
    save_webp(first.resize((1600, 1202), Image.Resampling.LANCZOS), v / "h1202.webp")  # within +-2
    save_webp(first.resize((1600, 1198), Image.Resampling.LANCZOS), v / "h1198.webp")
    save_webp(first.resize((1600, 1203), Image.Resampling.LANCZOS), v / "h1203.webp")  # outside +-2
    save_webp(first.resize((1600, 1197), Image.Resampling.LANCZOS), v / "h1197.webp")
    save_webp(first.transpose(Image.Transpose.ROTATE_90), v / "rotated.webp")  # 1200x1600: right sizes, wrong picture
    first.save(v / "png-named.webp", format="PNG")  # a PNG, right size and picture
    save_webp(Image.new("RGB", (1600, 1200), (128, 128, 128)), v / "flat.webp")  # right size, no picture


def tearDownModule() -> None:
    if TMP is not None:
        TMP.cleanup()


class HeicFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.task = get_task("heic")

    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(self.task.prompt, PROMPT)

    def test_prepare_fixtures_writes_the_three_heic_files(self) -> None:
        self.assertEqual(sorted(p.name for p in FIXTURES.iterdir()), sorted(SOURCES))
        for name in SOURCES:
            self.assertGreater((FIXTURES / name).stat().st_size, 10_000, name)

    def test_the_files_are_real_heic_4032_by_3024(self) -> None:
        for name in SOURCES:
            with self.subTest(file=name), Image.open(FIXTURES / name) as im:
                self.assertEqual(im.format, "HEIF")
                self.assertEqual(im.size, (4032, 3024))
        # the bytes themselves say HEIC: an ISO base media file with an heic/heix/mif1 brand
        for name in SOURCES:
            head = (FIXTURES / name).read_bytes()[:32]
            self.assertEqual(head[4:8], b"ftyp", name)
            self.assertTrue(
                any(brand in head for brand in (b"heic", b"heix", b"mif1")),
                (name, head),
            )

    def test_photos_are_not_flat_colour(self) -> None:
        for stem in STEMS:
            with self.subTest(photo=stem):
                luminance = thumb(PHOTOS[stem]).convert("L")
                self.assertGreater(ImageStat.Stat(luminance).stddev[0], 15)
                self.assertGreater(len(thumb(PHOTOS[stem]).getcolors(32 * 24) or []), 100)

    def test_photos_differ_from_each_other_at_a_downscaled_size(self) -> None:
        for a, b in (
            ("IMG_0001", "IMG_0002"),
            ("IMG_0001", "IMG_0003"),
            ("IMG_0002", "IMG_0003"),
        ):
            with self.subTest(pair=(a, b)):
                self.assertGreater(mean_abs_diff(PHOTOS[a], PHOTOS[b]), 12)

    def test_setup_copies_the_photos_into_the_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.task.setup(work, FIXTURES)
            for name in SOURCES:
                self.assertEqual((work / name).read_bytes(), (FIXTURES / name).read_bytes())
            self.assertEqual(sorted(p.name for p in work.iterdir()), sorted(SOURCES))


class HeicCheckTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.task = get_task("heic")
        cls.fixtures = FIXTURES

    def install(self, **overrides: str | None) -> None:
        """The correct three WebPs, with some replaced by a variant file (or removed with None)."""
        for stem in STEMS:
            shutil.copy(GOOD_DIR / f"{stem}.webp", self.work / f"{stem}.webp")
        for name, variant in overrides.items():
            target = self.work / f"{name}.webp"
            target.unlink(missing_ok=True)
            if variant is not None:
                shutil.copy(VARIANT_DIR / variant, target)

    def test_the_correct_result_is_what_the_spec_describes(self) -> None:
        # Guards the helpers, so a pass or a failure below means something.
        for stem in STEMS:
            with Image.open(GOOD_DIR / f"{stem}.webp") as im:
                self.assertEqual((im.format, im.size), ("WEBP", (1600, 1200)))
        for name, size, fmt in (
            ("small.webp", (800, 600), "WEBP"),
            ("fullsize.webp", (4032, 3024), "WEBP"),
            ("short.webp", (1600, 1000), "WEBP"),
            ("square.webp", (1600, 1600), "WEBP"),
            ("h1202.webp", (1600, 1202), "WEBP"),
            ("h1203.webp", (1600, 1203), "WEBP"),
            ("rotated.webp", (1200, 1600), "WEBP"),
            ("png-named.webp", (1600, 1200), "PNG"),
            ("flat.webp", (1600, 1200), "WEBP"),
        ):
            with Image.open(VARIANT_DIR / name) as im:
                self.assertEqual((im.format, im.size), (fmt, size), name)
        # the three photos stay told apart after the downscale the check works with
        self.assertGreater(mean_abs_diff(GOOD["IMG_0001"], GOOD["IMG_0002"]), 12)

    def test_correct_result_passes(self) -> None:
        self.install()
        self.assert_passes()

    def test_final_text_does_not_matter(self) -> None:
        self.install()
        self.assertTrue(self.check("").passed)
        self.assertTrue(self.check("something unrelated").passed)

    def test_uppercase_extension_is_accepted(self) -> None:
        self.install()
        (self.work / "IMG_0002.webp").rename(self.work / "IMG_0002.WEBP")
        self.assert_passes()

    def test_mixed_case_extension_is_accepted(self) -> None:
        self.install()
        (self.work / "IMG_0003.webp").rename(self.work / "IMG_0003.WebP")
        self.assert_passes()

    def test_other_files_left_in_the_folder_do_not_matter(self) -> None:
        self.install()
        GOOD["IMG_0001"].save(self.work / "IMG_0001.jpg")
        (self.work / "notes.txt").write_text("scratch\n")
        self.assert_passes()

    def test_second_side_within_two_pixels_passes(self) -> None:
        for variant in ("h1202.webp", "h1198.webp"):
            with self.subTest(variant=variant):
                self.install(IMG_0001=variant)
                self.assert_passes()

    def test_second_side_more_than_two_pixels_off_fails(self) -> None:
        for variant in ("h1203.webp", "h1197.webp"):
            with self.subTest(variant=variant):
                self.install(IMG_0001=variant)
                self.assert_fails_with(
                    self.check(),
                    "1200",
                    "aspect",
                    "height",
                    "other side",
                    "size",
                    "dimension",
                )

    def test_no_output_at_all_fails(self) -> None:
        self.install(IMG_0001=None, IMG_0002=None, IMG_0003=None)
        result = self.check()
        self.assert_fails_with(result, "webp", "missing", "exist", "not found")
        joined = " ".join(result.details).lower()
        for stem in STEMS:
            self.assertIn(stem.lower(), joined)

    def test_one_missing_output_fails_and_names_it(self) -> None:
        self.install(IMG_0002=None)
        result = self.check()
        self.assert_fails_with(result, "img_0002", reasons=1)
        self.assert_fails_with(result, "missing", "exist", "not found", "webp")

    def test_output_with_another_stem_does_not_count(self) -> None:
        self.install(IMG_0003=None)
        shutil.copy(GOOD_DIR / "IMG_0003.webp", self.work / "photo3.webp")
        self.assert_fails_with(self.check(), "img_0003", reasons=1)

    def test_longest_side_not_1600_fails(self) -> None:
        for variant in ("small.webp", "fullsize.webp"):
            with self.subTest(variant=variant):
                self.install(IMG_0001=variant)
                self.assert_fails_with(self.check(), "1600", "longest", "size", "dimension", reasons=1)

    def test_wrong_aspect_fails(self) -> None:
        for variant in ("short.webp", "square.webp"):
            with self.subTest(variant=variant):
                self.install(IMG_0001=variant)
                self.assert_fails_with(
                    self.check(),
                    "1200",
                    "aspect",
                    "height",
                    "other side",
                    "size",
                    "dimension",
                )

    def test_rotated_picture_fails_even_though_its_sides_are_1600_and_1200(
        self,
    ) -> None:
        self.install(IMG_0001="rotated.webp")
        self.assert_fails_with(
            self.check(),
            "content",
            "match",
            "source",
            "closer",
            "differ",
            "similar",
            "rotat",
            "side",
        )

    def test_png_named_webp_fails(self) -> None:
        self.install(IMG_0001="png-named.webp")
        self.assert_fails_with(self.check(), "webp", "format", "png", reasons=1)

    def test_text_file_named_webp_fails(self) -> None:
        self.install()
        (self.work / "IMG_0001.webp").write_text("this is not an image\n")
        self.assert_fails_with(self.check(), "webp", "image", "valid", "open", "read", "format")

    def test_flat_picture_of_the_right_size_fails(self) -> None:
        self.install(IMG_0001="flat.webp")
        self.assert_fails_with(
            self.check(),
            "content",
            "match",
            "source",
            "closer",
            "differ",
            "similar",
            reasons=1,
        )

    def test_outputs_swapped_between_two_photos_fail(self) -> None:
        self.install()
        shutil.copy(GOOD_DIR / "IMG_0002.webp", self.work / "IMG_0001.webp")
        shutil.copy(GOOD_DIR / "IMG_0001.webp", self.work / "IMG_0002.webp")
        result = self.check()
        self.assert_fails_with(result, "content", "match", "source", "closer", "differ", "similar", "swap")
        joined = " ".join(result.details).lower()
        self.assertIn("img_0001", joined)
        self.assertIn("img_0002", joined)
        self.assertNotIn("img_0003", joined)

    def test_outputs_rotated_between_all_three_photos_fail(self) -> None:
        self.install()
        for stem, other in zip(STEMS, STEMS[1:] + STEMS[:1], strict=True):
            shutil.copy(GOOD_DIR / f"{other}.webp", self.work / f"{stem}.webp")
        result = self.check()
        self.assert_fails_with(result, "content", "match", "source", "closer", "differ", "similar")
        joined = " ".join(result.details).lower()
        for stem in STEMS:
            self.assertIn(stem.lower(), joined)

    def test_modified_source_fails_even_with_correct_outputs(self) -> None:
        self.install()
        with (self.work / "IMG_0002.HEIC").open("ab") as handle:
            handle.write(b"tampered")
        result = self.check()
        self.assert_fails_with(result, "img_0002.heic", reasons=1)
        self.assert_fails_with(result, "unchanged", "sha", "hash", "modified", "changed")

    def test_deleted_source_fails_even_with_correct_outputs(self) -> None:
        self.install()
        (self.work / "IMG_0003.HEIC").unlink()
        result = self.check()
        self.assert_fails_with(result, "img_0003.heic", reasons=1)
        self.assert_fails_with(
            result,
            "unchanged",
            "sha",
            "hash",
            "missing",
            "changed",
            "exist",
            "kept",
            "original",
        )

    def test_converted_and_moved_sources_fail(self) -> None:
        # the originals must be kept: renaming them away leaves the .HEIC names missing
        self.install()
        (self.work / "originals").mkdir()
        for name in SOURCES:
            shutil.move(self.work / name, self.work / "originals" / name)
        result = self.check()
        self.assert_fails_with(result, "heic", "unchanged", "missing", "original", "kept")

    def test_check_does_not_modify_the_work_folder(self) -> None:
        self.install()
        self.assert_check_leaves_work_folder_alone()

    def test_check_does_not_modify_the_work_folder_when_it_fails(self) -> None:
        self.install(IMG_0001="png-named.webp", IMG_0002=None)
        self.assert_check_leaves_work_folder_alone()


if __name__ == "__main__":
    unittest.main()
