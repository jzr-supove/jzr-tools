"""Tests of the PDF compressor: size arithmetic and ``compress_file`` on PDFs built in the tests.

Most checks run a whole file through ``compress_file`` and read the result
back with pypdf and Pillow, so they hold for any way of doing the work.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from PIL import Image, JpegImagePlugin
from pypdf import PdfReader

from jzr_tools.core import pdf_compress
from jzr_tools.core.image_compress import change_text
from jzr_tools.core.media import format_size
from tests import compress_fixtures as cf
from tests import pdf_fixtures as fx

LANCZOS = Image.LANCZOS

DAMAGED = "damaged or not a PDF"
ENCRYPTED = "encrypted PDFs are not supported; use an unencrypted copy"
UNSUPPORTED = "unsupported page size, scaling or rotation"
SIGNATURE_SENTENCE = "digital signatures will not validate in the new PDF"


class Outcome:
    """What ``compress_file`` returned, with the files around it."""

    def __init__(self, result, notes, source: Path, target: Path, original: bytes) -> None:  # noqa: ANN001
        self.result = result
        self.notes = list(notes)
        self.source = source
        self.target = target
        self.original = original

    @property
    def status(self) -> str:
        return self.result.status

    @property
    def detail(self) -> str:
        return self.result.detail

    @property
    def out(self) -> bytes:
        return self.target.read_bytes()

    def expected_detail(self, tail: str = "") -> str:
        before, after = len(self.original), len(self.out)
        text = f"{format_size(before)} -> {format_size(after)}, {change_text(before, after)}"
        return f"{text}; {tail}" if tail else text


class CompressCase(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-pdf-compress-")
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)

    def put(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def names(self) -> list[str]:
        return sorted(entry.name for entry in self.root.iterdir())

    def compress(
        self,
        data: bytes,
        *,
        level: str = "recommended",
        strip: bool = False,
        overwrite: bool = False,
        dry_run: bool = False,
        name: str = "a.pdf",
        target: Path | None = None,
    ) -> Outcome:
        source = self.put(name, data)
        target = target if target is not None else self.root / f"{source.stem}_compressed.pdf"
        result, notes = pdf_compress.compress_file(
            source, target, level=level, strip=strip, overwrite=overwrite, dry_run=dry_run
        )
        return Outcome(result, notes, source, target, data)

    def written(self, data: bytes, **options: object) -> Outcome:
        outcome = self.compress(data, **options)  # type: ignore[arg-type]
        self.assertEqual(outcome.status, "compressed", outcome.detail)
        self.assertTrue(outcome.target.is_file())
        self.assertLess(len(outcome.out), len(data))
        return outcome

    def only_image(self, data: bytes) -> cf.Found:
        found = cf.images_in(data)
        self.assertEqual(len(found), 1, [(i.width, i.height, i.filters) for i in found])
        return found[0]


# ---- the arithmetic


class LevelTests(unittest.TestCase):
    def test_there_are_three_levels(self) -> None:
        self.assertEqual(set(pdf_compress.LEVELS), {"lossless", "recommended", "strong"})

    def test_the_texts_other_code_relies_on(self) -> None:
        self.assertEqual(pdf_compress.COMPRESSED_STEM, "_compressed")
        self.assertEqual(pdf_compress.ALREADY_COMPACT, "already compact (no smaller version found)")


class TargetPixelsTests(unittest.TestCase):
    def target(self, width: int, height: int, drawn, level: str = "recommended"):  # noqa: ANN001, ANN202
        return tuple(
            pdf_compress.target_pixels(width, height, drawn, pdf_compress.LEVELS[level])
        )

    def test_lossless_never_changes_the_size(self) -> None:
        self.assertEqual(self.target(6000, 4000, (100, 50), "lossless"), (6000, 4000))
        self.assertEqual(self.target(6000, 4000, None, "lossless"), (6000, 4000))

    def test_an_image_is_cut_to_150_dpi_at_the_size_it_is_drawn(self) -> None:
        # 6 x 4 inches at 150 DPI is 900 x 600 pixels.
        self.assertEqual(self.target(3000, 2000, (432, 288)), (900, 600))

    def test_strong_cuts_to_100_dpi(self) -> None:
        self.assertEqual(self.target(3000, 2000, (432, 288), "strong"), (600, 400))

    def test_the_more_demanding_side_keeps_the_dpi(self) -> None:
        # 4 x 4 inches needs 600 px; the 1000 px side only needs a 0.6 scale.
        self.assertEqual(self.target(2000, 1000, (288, 288)), (1200, 600))

    def test_the_ratio_of_the_sides_is_kept(self) -> None:
        self.assertEqual(self.target(3000, 1000, (432, 144)), (900, 300))

    def test_an_image_shown_larger_than_its_pixels_need_is_not_touched(self) -> None:
        self.assertEqual(self.target(1000, 1000, (720, 720)), (1000, 1000))

    def test_an_image_is_never_enlarged(self) -> None:
        self.assertEqual(self.target(100, 80, (720, 720)), (100, 80))
        self.assertEqual(self.target(100, 80, None), (100, 80))
        self.assertEqual(self.target(100, 80, (720, 720), "strong"), (100, 80))

    def test_without_a_drawn_size_the_longest_side_is_capped(self) -> None:
        self.assertEqual(self.target(4000, 3000, None), (2000, 1500))
        self.assertEqual(self.target(4000, 3000, None, "strong"), (1400, 1050))

    def test_without_a_drawn_size_a_small_image_is_left(self) -> None:
        self.assertEqual(self.target(1999, 1000, None), (1999, 1000))
        self.assertEqual(self.target(1400, 900, None, "strong"), (1400, 900))

    def test_the_cap_also_applies_when_the_drawn_size_asks_for_more(self) -> None:
        # 36 x 24 inches at 150 DPI would keep 5400 px, but a side stays within 2000.
        self.assertEqual(self.target(6000, 4000, (2592, 1728)), (2000, 1333))
        self.assertEqual(self.target(6000, 4000, (2592, 1728), "strong"), (1400, 933))

    def test_a_side_never_drops_below_one_pixel(self) -> None:
        self.assertEqual(self.target(10000, 1, None), (2000, 1))
        self.assertEqual(self.target(5000, 2, (72, 72), "strong"), (1400, 1))

    def test_the_result_is_whole_pixels(self) -> None:
        width, height = self.target(3333, 2222, (300, 200))
        self.assertIsInstance(width, int)
        self.assertIsInstance(height, int)


# ---- pictures


class ResizedPhotoTests(CompressCase):
    def test_a_photo_drawn_at_6_x_4_inches_becomes_900_x_600_jpeg(self) -> None:
        outcome = self.written(cf.photo_pdf())
        image = self.only_image(outcome.out)
        self.assertEqual((image.width, image.height), (900, 600))
        self.assertEqual(image.filter, "/DCTDecode")
        self.assertEqual(image.colorspace, "/DeviceRGB")
        self.assertEqual(outcome.detail, outcome.expected_detail("1 image resized"))
        self.assertLess(len(outcome.out), len(cf.photo_pdf()) / 4)

    def test_the_result_line_belongs_to_the_files_given(self) -> None:
        outcome = self.written(cf.photo_pdf())
        self.assertEqual(outcome.result.source, outcome.source)
        self.assertEqual(outcome.result.target, outcome.target)

    def test_the_resized_picture_still_looks_like_the_original(self) -> None:
        outcome = self.written(cf.photo_pdf())
        picture = self.only_image(outcome.out).pixels().convert("RGB")
        expected = cf.photo_pixels(3000, 2000).resize((900, 600), LANCZOS)
        self.assertLess(cf.mean_difference(picture, expected), 10)

    def test_a_renderer_shows_the_result_like_the_source(self) -> None:
        outcome = self.written(cf.photo_pdf())
        self.assertLess(cf.mean_difference(cf.render(outcome.original), cf.render(outcome.out)), 8)

    def test_strong_gives_600_x_400(self) -> None:
        recommended = self.written(cf.photo_pdf(), level="recommended")
        strong = self.written(cf.photo_pdf(), level="strong", name="b.pdf")
        image = self.only_image(strong.out)
        self.assertEqual((image.width, image.height), (600, 400))
        self.assertEqual(image.filter, "/DCTDecode")
        self.assertLess(len(strong.out), len(recommended.out))
        self.assertEqual(strong.detail, strong.expected_detail("1 image resized"))

    def test_the_size_is_set_by_the_largest_use_of_the_image(self) -> None:
        outcome = self.written(cf.two_uses_pdf())
        image = self.only_image(outcome.out)
        self.assertEqual((image.width, image.height), (900, 600))

    def test_a_picture_in_a_form_is_sized_by_the_matrix_of_the_form_too(self) -> None:
        # Seen at 216 x 144 points: 450 x 300 pixels, not the 900 x 600 of the page alone.
        outcome = self.written(cf.form_matrix_pdf())
        image = self.only_image(outcome.out)
        self.assertEqual((image.width, image.height), (450, 300))

    def test_every_side_is_decided_by_the_more_demanding_edge(self) -> None:
        outcome = self.written(cf.with_many_resizable(3))
        sizes = [(image.width, image.height) for image in cf.images_in(outcome.out)]
        self.assertEqual(sizes, [(300, 210)] * 3)
        self.assertEqual(outcome.detail, outcome.expected_detail("3 images resized"))

    def test_an_image_drawn_very_large_is_still_held_to_2000_pixels(self) -> None:
        outcome = self.written(cf.huge_drawn_pdf(True))
        image = self.only_image(outcome.out)
        self.assertEqual((image.width, image.height), (2000, 1000))

    def test_strong_holds_a_side_to_1400_pixels(self) -> None:
        outcome = self.written(cf.huge_drawn_pdf(True), level="strong")
        image = self.only_image(outcome.out)
        self.assertEqual((image.width, image.height), (1400, 700))

    def test_an_image_that_is_never_drawn_is_held_to_2000_pixels_too(self) -> None:
        outcome = self.written(cf.huge_drawn_pdf(False))
        image = self.only_image(outcome.out)
        self.assertEqual((image.width, image.height), (2000, 1000))

    def test_the_source_is_not_changed(self) -> None:
        outcome = self.written(cf.photo_pdf())
        self.assertEqual(outcome.source.read_bytes(), cf.photo_pdf())


class ReencodedImageTests(CompressCase):
    def test_an_image_shown_at_its_own_size_is_not_resized(self) -> None:
        source = cf.photo_pdf(400, 300, (400, 300), 95)
        outcome = self.written(source)
        image = self.only_image(outcome.out)
        self.assertEqual((image.width, image.height), (400, 300))
        self.assertEqual(image.filter, "/DCTDecode")
        self.assertLess(len(image.raw), len(self.only_image(source).raw))
        self.assertEqual(outcome.detail, outcome.expected_detail("1 re-encoded"))

    def test_a_smaller_image_is_never_enlarged_to_the_drawn_size(self) -> None:
        source = cf.photo_pdf(200, 150, (600, 450), 95)
        outcome = self.written(source)
        image = self.only_image(outcome.out)
        self.assertEqual((image.width, image.height), (200, 150))

    def _tables(self, quality: int) -> dict:
        buffer = io.BytesIO()
        Image.new("RGB", (16, 16), (90, 120, 150)).save(buffer, format="JPEG", quality=quality)
        return {key: list(values) for key, values in Image.open(io.BytesIO(buffer.getvalue())).quantization.items()}

    def _output_tables(self, level: str) -> tuple[dict, Image.Image]:
        outcome = self.written(cf.photo_pdf(400, 300, (400, 300), 95), level=level)
        picture = Image.open(io.BytesIO(self.only_image(outcome.out).raw))
        return {key: list(values) for key, values in picture.quantization.items()}, picture

    def test_recommended_writes_jpeg_quality_75(self) -> None:
        tables, _ = self._output_tables("recommended")
        self.assertEqual(tables, self._tables(75))

    def test_strong_writes_jpeg_quality_60(self) -> None:
        tables, _ = self._output_tables("strong")
        self.assertEqual(tables, self._tables(60))

    def test_colour_photos_use_4_2_0_subsampling(self) -> None:
        _, picture = self._output_tables("recommended")
        self.assertEqual(JpegImagePlugin.get_sampling(picture), 2)

    def test_a_noisy_flate_image_becomes_a_jpeg_of_the_same_size(self) -> None:
        outcome = self.written(cf.noisy_flate_pdf())
        image = self.only_image(outcome.out)
        self.assertEqual(image.filter, "/DCTDecode")
        self.assertEqual((image.width, image.height), (400, 300))
        self.assertEqual(image.colorspace, "/DeviceRGB")
        self.assertEqual(image.pixels().mode, "RGB")
        self.assertEqual(outcome.detail, outcome.expected_detail("1 re-encoded"))

    def test_an_image_stored_without_a_filter_is_read_as_flate_would_be(self) -> None:
        outcome = self.written(cf.unfiltered_noise_pdf())
        image = self.only_image(outcome.out)
        self.assertEqual(image.filter, "/DCTDecode")
        self.assertEqual((image.width, image.height), (400, 300))

    def test_a_gray_photo_stays_one_component_and_gray(self) -> None:
        outcome = self.written(cf.gray_noise_pdf())
        image = self.only_image(outcome.out)
        self.assertEqual(image.filter, "/DCTDecode")
        self.assertEqual(image.colorspace, "/DeviceGray")
        self.assertEqual(image.pixels().mode, "L")
        self.assertEqual((image.width, image.height), (400, 300))

    def test_an_icc_based_image_keeps_its_profile(self) -> None:
        outcome = self.written(cf.icc_noise_pdf(3))
        image = self.only_image(outcome.out)
        self.assertEqual(image.filter, "/DCTDecode")
        space = image.colorspace
        self.assertEqual(space[0], "/ICCBased")
        self.assertEqual(int(space[1].get_object()["/N"]), 3)
        self.assertEqual(image.pixels().mode, "RGB")

    def test_the_image_with_a_predictor_is_read_with_it(self) -> None:
        outcome = self.written(cf.predicted_pdf())
        image = self.only_image(outcome.out)
        self.assertEqual((image.width, image.height), (400, 300))
        difference = cf.mean_difference(image.pixels().convert("RGB"), cf.predicted_pixels())
        self.assertLess(difference, 8)


class FlatImageTests(CompressCase):
    def test_a_flat_image_stays_flate_and_keeps_every_pixel(self) -> None:
        outcome = self.written(cf.flat_pdf())
        image = self.only_image(outcome.out)
        self.assertEqual(image.filter, "/FlateDecode")
        self.assertEqual((image.width, image.height), cf.FLAT_SIZE)
        self.assertEqual(image.colorspace, "/DeviceRGB")
        self.assertEqual(image.stream.get_data()[: 400 * 300 * 3], cf.flat_raw())
        self.assertLess(len(image.raw), len(self.only_image(outcome.original).raw))
        self.assertEqual(outcome.detail, outcome.expected_detail("1 re-encoded"))

    def test_a_renderer_shows_a_flat_result_the_same(self) -> None:
        outcome = self.written(cf.flat_pdf())
        self.assertLess(cf.mean_difference(cf.render(outcome.original), cf.render(outcome.out)), 1.5)

    def test_a_big_flat_image_is_resized_and_stays_flate(self) -> None:
        outcome = self.written(cf.flat_large_pdf())
        image = self.only_image(outcome.out)
        self.assertEqual(image.filter, "/FlateDecode")
        self.assertEqual((image.width, image.height), (300, 225))
        expected = cf.flat_screenshot(1200, 900).resize((300, 225), LANCZOS)
        self.assertLess(cf.mean_difference(image.pixels(), expected), 3)
        self.assertEqual(outcome.detail, outcome.expected_detail("1 image resized"))


class SoftMaskTests(CompressCase):
    def test_a_soft_mask_is_resized_with_its_image_and_not_counted(self) -> None:
        outcome = self.written(cf.soft_masked_pdf())
        images = cf.images_in(outcome.out)
        self.assertEqual(len(images), 2)
        base = next(image for image in images if image.filter == "/DCTDecode")
        mask = next(image for image in images if image is not base)
        self.assertEqual((base.width, base.height), (450, 300))
        self.assertEqual((mask.width, mask.height), (450, 300))
        self.assertEqual(mask.colorspace, "/DeviceGray")
        self.assertEqual(base.stream["/SMask"].get_object()["/Width"], 450)
        self.assertEqual(outcome.detail, outcome.expected_detail("1 image resized"))

    def test_the_resized_mask_still_runs_from_dark_to_light(self) -> None:
        outcome = self.written(cf.soft_masked_pdf())
        mask = next(image for image in cf.images_in(outcome.out) if image.filter != "/DCTDecode")
        pixels = mask.pixels().convert("L")
        self.assertLess(pixels.getpixel((225, 0)), 20)
        self.assertGreater(pixels.getpixel((225, 299)), 235)

    def test_a_renderer_shows_the_masked_result_like_the_source(self) -> None:
        outcome = self.written(cf.soft_masked_pdf())
        self.assertLess(cf.mean_difference(cf.render(outcome.original), cf.render(outcome.out)), 10)


class LeftAsTheyWereTests(CompressCase):
    def test_images_the_rules_exclude_keep_their_bytes_and_are_counted(self) -> None:
        for kind in cf.SKIPPED_NAMES:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix="jzr-skip-") as folder:
                source_bytes = cf.skipped_image_pdf(kind)
                source = Path(folder) / "a.pdf"
                source.write_bytes(source_bytes)
                target = Path(folder) / "a_compressed.pdf"
                result, _ = pdf_compress.compress_file(
                    source, target, level="recommended", strip=False, overwrite=False, dry_run=False
                )
                self.assertEqual(result.status, "compressed", result.detail)
                before = self.single(cf.images_in(source_bytes))
                after = self.single(cf.images_in(target.read_bytes()))
                self.assertEqual(after.raw, before.raw)
                self.assertEqual((after.width, after.height), (before.width, before.height))
                self.assertEqual(after.filters, before.filters)
                for key in ("/ColorSpace", "/BitsPerComponent", "/Decode", "/Mask", "/ImageMask"):
                    self.assertEqual(str(after.stream.get(key)), str(before.stream.get(key)), key)
                self.assertTrue(result.detail.endswith("; 1 left as they were"), result.detail)
                self.assertNotIn("resized", result.detail)

    def single(self, found: list[cf.Found]) -> cf.Found:
        self.assertEqual(len(found), 1)
        return found[0]

    def test_a_new_encoding_that_is_not_smaller_is_not_used(self) -> None:
        # The headers of a JPEG outweigh the 200 bytes of an 8 x 8 picture.
        outcome = self.written(cf.tiny_noise_pdf())
        after = self.only_image(outcome.out)
        self.assertEqual(after.raw, self.only_image(outcome.original).raw)
        self.assertEqual(outcome.detail, outcome.expected_detail("1 left as they were"))

    def test_an_icc_profile_with_four_components_counts_as_cmyk(self) -> None:
        outcome = self.written(cf.icc_noise_pdf(4))
        after = self.only_image(outcome.out)
        self.assertEqual(after.raw, self.only_image(outcome.original).raw)
        self.assertTrue(outcome.detail.endswith("; 1 left as they were"), outcome.detail)

    def test_a_file_can_hold_resized_and_untouched_images_together(self) -> None:
        outcome = self.written(cf.with_unreadable_neighbour())
        self.assertEqual(outcome.detail, outcome.expected_detail("1 image resized, 1 left as they were"))
        by_size = {(image.width, image.height): image for image in cf.images_in(outcome.out)}
        self.assertIn((450, 300), by_size)
        cmyk = by_size[(300, 200)]
        original = next(image for image in cf.images_in(outcome.original) if image.width == 300)
        self.assertEqual(cmyk.raw, original.raw)


class LosslessTests(CompressCase):
    def test_lossless_leaves_the_image_bytes_alone(self) -> None:
        outcome = self.written(cf.photo_pdf(), level="lossless")
        after = self.only_image(outcome.out)
        before = self.only_image(outcome.original)
        self.assertEqual(after.raw, before.raw)
        self.assertEqual((after.width, after.height, after.filters), (3000, 2000, ("/DCTDecode",)))

    def test_lossless_names_no_images_in_the_detail(self) -> None:
        outcome = self.written(cf.photo_pdf(), level="lossless")
        self.assertEqual(outcome.detail, outcome.expected_detail())
        self.assertNotIn(";", outcome.detail)

    def test_a_file_without_images_has_no_image_part_at_any_level(self) -> None:
        for level in ("lossless", "recommended", "strong"):
            with self.subTest(level=level):
                outcome = self.written(cf.plain_content_pdf(), level=level, name=f"{level}.pdf")
                self.assertEqual(outcome.detail, outcome.expected_detail())

    def test_a_text_file_with_an_unused_object_and_a_thumbnail_gets_smaller(self) -> None:
        source = cf.thumb_and_junk_pdf()
        self.assertIn("/Thumb", PdfReader(io.BytesIO(source)).pages[0])
        outcome = self.written(source, level="lossless")
        self.assertLess(len(outcome.out), len(source) - cf.JUNK_SIZE + 2000)
        reader = PdfReader(io.BytesIO(outcome.out))
        for page, label in zip(reader.pages, cf.LABELS):
            self.assertNotIn("/Thumb", page)
            self.assertIn(label, page.extract_text())
        self.assertEqual(fx.markers(outcome.out), [1, 2, 3])

    def test_unfiltered_streams_are_compressed_and_read_back_the_same(self) -> None:
        source = cf.plain_content_pdf()
        outcome = self.written(source, level="lossless")
        before = PdfReader(io.BytesIO(source)).pages[0]["/Contents"]
        after = PdfReader(io.BytesIO(outcome.out)).pages[0]["/Contents"]
        self.assertNotIn("/Filter", before)
        self.assertEqual(after["/Filter"], "/FlateDecode")
        self.assertEqual(after.get_data(), before.get_data())
        self.assertEqual(after.get_data(), cf.CONTENT)
        self.assertLess(len(outcome.out), len(source) / 2)

    def test_identical_images_are_stored_once(self) -> None:
        source = cf.duplicate_images_pdf()
        self.assertEqual(len(set(cf.page_image_ids(source))), 2)
        outcome = self.written(source, level="lossless")
        self.assertEqual(len(cf.images_in(outcome.out)), 1)
        ids = cf.page_image_ids(outcome.out)
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids[0], ids[1])
        self.assertLess(len(outcome.out), len(source) - 25000)

    def test_a_file_that_cannot_get_smaller_is_skipped_and_not_written(self) -> None:
        outcome = self.compress(cf.minimal_pdf())
        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(outcome.detail, "already compact (no smaller version found)")
        self.assertEqual(self.names(), ["a.pdf"])

    def test_an_already_compact_file_is_skipped_at_every_level(self) -> None:
        for level in ("lossless", "recommended", "strong"):
            with self.subTest(level=level):
                outcome = self.compress(cf.minimal_pdf(), level=level, name=f"{level}.pdf")
                self.assertEqual(outcome.status, "skipped")
                self.assertEqual(outcome.detail, pdf_compress.ALREADY_COMPACT)


class MetadataTests(CompressCase):
    def test_strip_removes_the_info_the_xmp_and_the_piece_info(self) -> None:
        outcome = self.written(cf.metadata_pdf(), level="lossless", strip=True)
        reader = PdfReader(io.BytesIO(outcome.out))
        root = reader.trailer["/Root"]
        self.assertNotIn("/Metadata", root)
        self.assertNotIn("/PieceInfo", root)
        self.assertNotIn("/PieceInfo", reader.pages[0])
        meta = reader.metadata
        self.assertIsNone(getattr(meta, "title", None))
        self.assertIsNone(getattr(meta, "author", None))
        self.assertNotIn(b"Quarterly report", outcome.out)
        self.assertNotIn(b"Ada Lovelace", outcome.out)

    def test_without_strip_they_stay_and_the_xmp_stays_readable(self) -> None:
        outcome = self.written(cf.metadata_pdf(), level="lossless")
        reader = PdfReader(io.BytesIO(outcome.out))
        root = reader.trailer["/Root"]
        self.assertEqual(reader.metadata.title, "Quarterly report")
        self.assertEqual(reader.metadata.author, "Ada Lovelace")
        self.assertIn("/PieceInfo", root)
        self.assertIn("/PieceInfo", reader.pages[0])
        xmp = root["/Metadata"].get_object()
        self.assertNotIn("/Filter", xmp)
        self.assertEqual(xmp.get_data(), cf.XMP)

    def test_strip_applies_at_every_level(self) -> None:
        for level in ("recommended", "strong"):
            with self.subTest(level=level):
                outcome = self.written(cf.metadata_pdf(), level=level, strip=True, name=f"{level}.pdf")
                self.assertNotIn(b"Quarterly report", outcome.out)


class SurvivalTests(CompressCase):
    def test_pages_boxes_rotation_text_links_and_bookmarks_survive(self) -> None:
        source = cf.kept_parts_pdf()
        outcome = self.written(source)
        out = outcome.out
        self.assertEqual(fx.markers(out), [1, 2, 3])
        self.assertEqual(fx.rotations(out), [0, 90, 0])
        reader = PdfReader(io.BytesIO(out))
        self.assertEqual([float(v) for v in reader.pages[0].cropbox], [5, 5, 100, 190])
        for page, label in zip(reader.pages, cf.LABELS):
            self.assertIn(label, page.extract_text())
        self.assertEqual(fx.outline_titles(out), ["Chapter one", "Chapter three"])
        self.assertEqual(fx.link_uris(out), ["https://example.com/kept"])
        self.assertLess(len(out), len(source) - cf.JUNK_SIZE + 2000)

    def test_features_the_command_keeps_give_no_notes(self) -> None:
        outcome = self.written(cf.kept_parts_pdf())
        self.assertEqual(outcome.notes, [])

    def test_a_signed_looking_file_is_compressed_and_the_signature_noted(self) -> None:
        outcome = self.written(cf.signed_pdf())
        self.assertEqual(len(outcome.notes), 1)
        self.assertIn("a.pdf", outcome.notes[0])
        self.assertTrue(outcome.notes[0].endswith(SIGNATURE_SENTENCE), outcome.notes[0])

    def test_a_dry_run_reports_the_signature_note_too(self) -> None:
        outcome = self.compress(cf.signed_pdf(), dry_run=True)
        self.assertEqual(outcome.status, "planned")
        self.assertEqual(len(outcome.notes), 1)
        self.assertTrue(outcome.notes[0].endswith(SIGNATURE_SENTENCE), outcome.notes[0])


class RefusedInputTests(CompressCase):
    def refused(self, data: bytes, **options: object) -> Outcome:
        outcome = self.compress(data, **options)  # type: ignore[arg-type]
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(self.names(), ["a.pdf"])
        return outcome

    def test_an_encrypted_pdf_fails_with_the_encryption_text(self) -> None:
        self.assertEqual(self.refused(fx.encrypted()).detail, ENCRYPTED)

    def test_damaged_files_fail_with_the_damage_text(self) -> None:
        for name, data in (
            ("truncated", fx.truncated()),
            ("text", fx.not_a_pdf()),
            ("count", fx.wrong_count()),
            ("no-eof", fx.without_eof()),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.refused(data).detail, DAMAGED)

    def test_an_unsupported_page_fails_with_the_page_text(self) -> None:
        for name, data in (("huge", fx.huge_media_box()), ("unit", fx.user_unit(2)), ("angle", fx.rotate(45))):
            with self.subTest(name=name):
                self.assertEqual(self.refused(data).detail, UNSUPPORTED)

    def test_a_missing_file_fails_with_the_text_of_the_system(self) -> None:
        missing = self.root / "nope.pdf"
        with self.assertRaises(OSError) as caught:
            missing.read_bytes()
        result, notes = pdf_compress.compress_file(
            missing, self.root / "nope_compressed.pdf", level="recommended",
            strip=False, overwrite=False, dry_run=False,
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.detail, caught.exception.strerror)
        self.assertEqual(list(notes), [])
        self.assertEqual(self.names(), [])

    def test_a_dry_run_checks_the_source_too(self) -> None:
        for name, data, detail in (
            ("encrypted", fx.encrypted(), ENCRYPTED),
            ("damaged", fx.truncated(), DAMAGED),
        ):
            with self.subTest(name=name):
                outcome = self.compress(data, dry_run=True, name=f"{name}.pdf")
                self.assertEqual((outcome.status, outcome.detail), ("failed", detail))


class TargetTests(CompressCase):
    def test_an_existing_target_is_skipped_and_left_alone(self) -> None:
        target = self.put("a_compressed.pdf", b"keep me")
        outcome = self.compress(cf.photo_pdf(), target=target)
        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(outcome.detail, "target exists (use --overwrite)")
        self.assertEqual(target.read_bytes(), b"keep me")

    def test_overwrite_replaces_the_target(self) -> None:
        target = self.put("a_compressed.pdf", b"old")
        outcome = self.compress(cf.photo_pdf(), target=target, overwrite=True)
        self.assertEqual(outcome.status, "compressed", outcome.detail)
        self.assertEqual(self.only_image(target.read_bytes()).width, 900)

    def test_the_source_as_target_is_refused_even_with_overwrite(self) -> None:
        source = self.put("a.pdf", cf.photo_pdf())
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                result, _ = pdf_compress.compress_file(
                    source, source, level="recommended", strip=False, overwrite=overwrite, dry_run=False
                )
                self.assertEqual((result.status, result.detail), ("skipped", "source is the target"))
                self.assertEqual(source.read_bytes(), cf.photo_pdf())

    def test_another_spelling_of_the_source_path_is_the_same_file(self) -> None:
        source = self.put("sub/a.pdf", cf.photo_pdf())
        spelled = source.parent / ".." / "sub" / "a.pdf"
        result, _ = pdf_compress.compress_file(
            source, spelled, level="recommended", strip=False, overwrite=True, dry_run=False
        )
        self.assertEqual((result.status, result.detail), ("skipped", "source is the target"))
        self.assertEqual(source.read_bytes(), cf.photo_pdf())

    def test_a_dry_run_writes_nothing(self) -> None:
        outcome = self.compress(cf.photo_pdf(), dry_run=True)
        self.assertEqual(outcome.status, "planned")
        self.assertEqual(self.names(), ["a.pdf"])

    def test_a_dry_run_still_refuses_an_existing_target(self) -> None:
        target = self.put("a_compressed.pdf", b"keep me")
        outcome = self.compress(cf.photo_pdf(), target=target, dry_run=True)
        self.assertEqual(outcome.detail, "target exists (use --overwrite)")

    def test_the_result_is_renamed_from_a_temporary_name_and_none_is_left(self) -> None:
        source = self.put("a.pdf", cf.photo_pdf())
        target = self.root / "a_compressed.pdf"
        real = os.replace
        seen: list[tuple[str, str, bool]] = []

        def spy(origin, destination, *args, **kwargs):  # noqa: ANN001, ANN202
            seen.append((Path(origin).name, str(destination), Path(destination).exists()))
            return real(origin, destination, *args, **kwargs)

        with mock.patch("os.replace", side_effect=spy):
            result, _ = pdf_compress.compress_file(
                source, target, level="recommended", strip=False, overwrite=False, dry_run=False
            )
        self.assertEqual(result.status, "compressed", result.detail)
        self.assertEqual(len(seen), 1)
        name, destination, existed = seen[0]
        self.assertTrue(name.startswith(".jzr-tmp-"), name)
        self.assertEqual(Path(destination), target)
        self.assertFalse(existed)
        self.assertEqual(self.names(), ["a.pdf", "a_compressed.pdf"])

    def test_nothing_is_left_behind_when_the_file_is_skipped_or_fails(self) -> None:
        self.compress(cf.minimal_pdf(), name="small.pdf")
        self.compress(fx.truncated(), name="broken.pdf")
        self.assertEqual(self.names(), ["broken.pdf", "small.pdf"])

    def test_the_output_is_a_pdf_that_opens_and_has_the_same_pages(self) -> None:
        outcome = self.written(cf.two_uses_pdf())
        self.assertTrue(re.match(rb"%PDF-", outcome.out))
        self.assertEqual(len(PdfReader(io.BytesIO(outcome.out)).pages), 2)


if __name__ == "__main__":
    unittest.main()
