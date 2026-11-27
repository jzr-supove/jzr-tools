"""Tests for the compress task (held out): a 4000x3000 photo.jpg to photo_small.jpg under 500 kB.

The fixture is made once per class with the task's own prepare_fixtures. The correct result is a
quality-30 JPEG of it made with Pillow (what the spec says the similarity threshold must allow); the
wrong ones differ from it in one way: too large, resized, not a JPEG, a different picture.
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

from jzr_bench.tasks import get_task  # noqa: E402
from PIL import Image, ImageChops, ImageOps, ImageStat  # noqa: E402
from task_check_support import CheckCase  # noqa: E402

PROMPT = "Make a copy of photo.jpg that is under 500 kB, same dimensions, still a JPEG. Save it as photo_small.jpg."
LIMIT = 500_000


def thumb(image: Image.Image) -> Image.Image:
    return image.convert("RGB").resize((80, 60), Image.Resampling.BOX)


def mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute difference (0..255) of two pictures at 80x60."""
    return sum(ImageStat.Stat(ImageChops.difference(thumb(a), thumb(b))).mean) / 3


def pad_to(path: Path, size: int) -> None:
    """Append zero bytes after the end of the JPEG until the file is exactly size bytes (decoders ignore them)."""
    current = path.stat().st_size
    assert current <= size, (current, size)
    with path.open("ab") as handle:
        handle.write(b"\0" * (size - current))


class CompressFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("compress")
        cls.task.prepare_fixtures(cls.fixtures)
        cls.photo = cls.fixtures / "photo.jpg"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(self.task.prompt, PROMPT)

    def test_prepare_fixtures_writes_exactly_photo_jpg(self) -> None:
        self.assertEqual([p.name for p in self.fixtures.iterdir()], ["photo.jpg"])

    def test_photo_is_a_4000_by_3000_jpeg(self) -> None:
        with Image.open(self.photo) as im:
            self.assertEqual((im.format, im.size), ("JPEG", (4000, 3000)))

    def test_photo_is_over_3_mb(self) -> None:
        self.assertGreater(self.photo.stat().st_size, 3_000_000)

    def test_photo_is_not_flat_colour(self) -> None:
        with Image.open(self.photo) as im:
            self.assertGreater(ImageStat.Stat(thumb(im).convert("L")).stddev[0], 10)

    def test_a_quality_30_jpeg_of_it_fits_under_500_kb(self) -> None:
        # the task must be doable by the ordinary route: re-encode at a moderate quality
        with Image.open(self.photo) as im:
            dest = Path(self._tmp.name) / "q30.jpg"
            im.convert("RGB").save(dest, format="JPEG", quality=30)
        self.assertLess(dest.stat().st_size, LIMIT)

    def test_setup_copies_the_photo_into_the_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.task.setup(work, self.fixtures)
            self.assertEqual((work / "photo.jpg").read_bytes(), self.photo.read_bytes())
            self.assertFalse((work / "photo_small.jpg").exists())


class CompressCheckTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.fixtures = base / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("compress")
        cls.task.prepare_fixtures(cls.fixtures)

        out = base / "results"
        out.mkdir()
        cls.results = out
        with Image.open(cls.fixtures / "photo.jpg") as opened:
            src = opened.convert("RGB")
        cls.src_thumb = thumb(src)
        # correct: the same picture, same size, quality 30
        src.save(out / "good.jpg", format="JPEG", quality=30)
        # exactly at and just under the limit (padding after the JPEG end marker)
        shutil.copy(out / "good.jpg", out / "at-limit.jpg")
        pad_to(out / "at-limit.jpg", LIMIT)
        shutil.copy(out / "good.jpg", out / "just-under.jpg")
        pad_to(out / "just-under.jpg", LIMIT - 1)
        # too large: the same picture at quality 90, and the untouched copy
        src.save(out / "q90.jpg", format="JPEG", quality=90)
        shutil.copy(cls.fixtures / "photo.jpg", out / "copy.jpg")
        # resized (and small enough): half size, and one row short
        src.resize((2000, 1500), Image.Resampling.LANCZOS).save(out / "half.jpg", format="JPEG", quality=30)
        src.crop((0, 0, 4000, 2999)).save(out / "one-row-short.jpg", format="JPEG", quality=30)
        src.resize((1920, 1440), Image.Resampling.LANCZOS).save(out / "web.jpg", format="JPEG", quality=85)
        # not a JPEG: the right picture as PNG and as WebP (4000x3000, WebP small), a text file, an empty file
        src.save(out / "png-named.jpg", format="PNG", compress_level=1)
        src.save(out / "webp-named.jpg", format="WEBP", quality=30)
        (out / "text.jpg").write_text("this is not an image\n")
        (out / "empty.jpg").write_bytes(b"")
        # destroyed or different pictures that are small enough and 4000x3000
        Image.new("RGB", (4000, 3000), (128, 128, 128)).save(out / "flat.jpg", format="JPEG", quality=90)
        Image.new("RGB", (4000, 3000), (0, 0, 0)).save(out / "black.jpg", format="JPEG", quality=90)
        ImageOps.invert(src).save(out / "inverted.jpg", format="JPEG", quality=30)
        src.rotate(180).save(out / "upside-down.jpg", format="JPEG", quality=30)
        ImageOps.mirror(src).save(out / "mirrored.jpg", format="JPEG", quality=30)
        cls.thumbs: dict[str, Image.Image] = {}
        for name in ("good", "flat", "black", "inverted", "upside-down", "mirrored"):
            with Image.open(out / f"{name}.jpg") as im:
                cls.thumbs[name] = thumb(im)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def install(self, name: str | None) -> None:
        if name is not None:
            shutil.copy(self.results / name, self.work / "photo_small.jpg")

    def test_the_variants_are_what_they_are_meant_to_be(self) -> None:
        # Guards the inputs of this test, so a pass or a failure below means something.
        def info(name: str) -> tuple[str | None, tuple[int, int], int]:
            path = self.results / name
            with Image.open(path) as im:
                return im.format, im.size, path.stat().st_size

        fmt, size, nbytes = info("good.jpg")
        self.assertEqual((fmt, size), ("JPEG", (4000, 3000)))
        self.assertLess(nbytes, LIMIT - 1)
        self.assertEqual(info("at-limit.jpg"), ("JPEG", (4000, 3000), LIMIT))
        self.assertEqual(info("just-under.jpg"), ("JPEG", (4000, 3000), LIMIT - 1))
        for name in ("q90.jpg", "copy.jpg"):
            fmt, size, nbytes = info(name)
            self.assertEqual((fmt, size), ("JPEG", (4000, 3000)), name)
            self.assertGreater(nbytes, LIMIT, name)
        for name, expected in (
            ("half.jpg", (2000, 1500)),
            ("one-row-short.jpg", (4000, 2999)),
            ("web.jpg", (1920, 1440)),
        ):
            fmt, size, nbytes = info(name)
            self.assertEqual((fmt, size), ("JPEG", expected), name)
            self.assertLess(nbytes, LIMIT, name)
        self.assertEqual(info("png-named.jpg")[:2], ("PNG", (4000, 3000)))
        fmt, size, nbytes = info("webp-named.jpg")
        self.assertEqual((fmt, size), ("WEBP", (4000, 3000)))
        self.assertLess(nbytes, LIMIT)
        for name in (
            "flat.jpg",
            "black.jpg",
            "inverted.jpg",
            "upside-down.jpg",
            "mirrored.jpg",
        ):
            fmt, size, nbytes = info(name)
            self.assertEqual((fmt, size), ("JPEG", (4000, 3000)), name)
            self.assertLess(nbytes, LIMIT, name)

    def test_the_good_picture_is_close_and_the_wrong_ones_are_far_from_the_source(
        self,
    ) -> None:
        # The check's similarity rule can only be exercised if these inputs really are near or far.
        self.assertLess(mean_abs_diff(self.thumbs["good"], self.src_thumb), 6)
        for name in ("flat", "black", "inverted", "upside-down", "mirrored"):
            with self.subTest(picture=name):
                self.assertGreater(mean_abs_diff(self.thumbs[name], self.src_thumb), 15)

    def test_correct_photo_small_passes(self) -> None:
        self.install("good.jpg")
        self.assert_passes()

    def test_final_text_does_not_matter(self) -> None:
        self.install("good.jpg")
        self.assertTrue(self.check("").passed)
        self.assertTrue(self.check("something unrelated").passed)

    def test_499_999_bytes_passes(self) -> None:
        self.install("just-under.jpg")
        self.assert_passes()

    def test_exactly_500_000_bytes_fails_because_it_must_be_under(self) -> None:
        self.install("at-limit.jpg")
        self.assert_fails_with(self.check(), "size", "bytes", "large", "kb", "500", "under", reasons=1)

    def test_missing_photo_small_fails(self) -> None:
        self.install(None)
        self.assert_fails_with(self.check(), "photo_small.jpg", "exist", "missing", "not found")

    def test_too_large_fails_with_one_reason(self) -> None:
        for name in ("q90.jpg", "copy.jpg"):
            with self.subTest(variant=name):
                self.install(name)
                self.assert_fails_with(
                    self.check(),
                    "size",
                    "bytes",
                    "large",
                    "kb",
                    "500",
                    "under",
                    reasons=1,
                )

    def test_resized_fails(self) -> None:
        for name in ("half.jpg", "web.jpg"):
            with self.subTest(variant=name):
                self.install(name)
                self.assert_fails_with(
                    self.check(),
                    "dimension",
                    "size",
                    "4000",
                    "3000",
                    "width",
                    "height",
                    "resiz",
                )

    def test_resized_fails_with_one_reason_when_only_the_dimensions_are_wrong(
        self,
    ) -> None:
        self.install("half.jpg")
        self.assert_fails_with(
            self.check(),
            "dimension",
            "size",
            "4000",
            "3000",
            "width",
            "height",
            "resiz",
            reasons=1,
        )

    def test_one_pixel_row_short_fails(self) -> None:
        self.install("one-row-short.jpg")
        self.assert_fails_with(self.check(), "dimension", "size", "4000", "3000", "height", "2999")

    def test_png_content_fails(self) -> None:
        self.install("png-named.jpg")
        self.assert_fails_with(self.check(), "jpeg", "format", "png")

    def test_webp_content_under_the_limit_fails_with_one_reason(self) -> None:
        self.install("webp-named.jpg")
        self.assert_fails_with(self.check(), "jpeg", "format", "webp", reasons=1)

    def test_text_file_named_photo_small_fails(self) -> None:
        self.install("text.jpg")
        self.assert_fails_with(self.check(), "jpeg", "image", "valid", "open", "read", "format")

    def test_empty_file_named_photo_small_fails(self) -> None:
        self.install("empty.jpg")
        self.assert_fails_with(self.check(), "jpeg", "image", "valid", "open", "read", "format", "empty")

    def test_flat_or_black_image_under_500_kb_fails_on_content(self) -> None:
        for name in ("flat.jpg", "black.jpg"):
            with self.subTest(variant=name):
                self.install(name)
                self.assert_fails_with(
                    self.check(),
                    "content",
                    "differ",
                    "similar",
                    "source",
                    "look",
                    "match",
                    "difference",
                    reasons=1,
                )

    def test_a_different_picture_of_the_same_size_fails_on_content(self) -> None:
        for name in ("inverted.jpg", "upside-down.jpg", "mirrored.jpg"):
            with self.subTest(variant=name):
                self.install(name)
                self.assert_fails_with(
                    self.check(),
                    "content",
                    "differ",
                    "similar",
                    "source",
                    "look",
                    "match",
                    "difference",
                    reasons=1,
                )

    def test_modified_source_fails_even_with_a_correct_copy(self) -> None:
        self.install("good.jpg")
        with (self.work / "photo.jpg").open("ab") as handle:
            handle.write(b"tampered")
        result = self.check()
        self.assert_fails_with(result, "photo.jpg", reasons=1)
        self.assert_fails_with(result, "unchanged", "sha", "hash", "modified", "changed")

    def test_source_overwritten_with_the_small_copy_fails(self) -> None:
        # compressing in place and copying the result: photo.jpg is no longer the original
        self.install("good.jpg")
        shutil.copy(self.results / "good.jpg", self.work / "photo.jpg")
        self.assert_fails_with(self.check(), "photo.jpg", "unchanged", "sha", "hash", "modified", "changed")

    def test_deleted_source_fails_even_with_a_correct_copy(self) -> None:
        self.install("good.jpg")
        (self.work / "photo.jpg").unlink()
        self.assert_fails_with(self.check(), "photo.jpg", "unchanged", "sha", "hash", "missing", "changed")

    def test_check_does_not_modify_the_work_folder(self) -> None:
        self.install("good.jpg")
        self.assert_check_leaves_work_folder_alone()

    def test_check_does_not_modify_the_work_folder_when_it_fails(self) -> None:
        self.install("png-named.jpg")
        self.assert_check_leaves_work_folder_alone()


if __name__ == "__main__":
    unittest.main()
