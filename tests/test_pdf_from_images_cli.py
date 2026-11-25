"""Tests of ``jzr pdf from-images`` through the real entry point.

Every test runs with a temporary folder as the working directory, so the paths
printed in result lines are the short names given on the command line. Results
are checked by reading the written PDF back with pypdf and by drawing it with
pdfium.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from click.testing import CliRunner
from PIL import Image, ImageOps
from pypdf import PdfReader

from jzr_tools.cli import app
from jzr_tools.core.image_strip import strip_bytes
from jzr_tools.core.media import format_size
from tests import compress_fixtures as cf
from tests import image_pdf_fixtures as ipf

runner = CliRunner()

A4 = (595.276, 841.89)
A4_LANDSCAPE = (841.89, 595.276)
LETTER = (612.0, 792.0)
OUTPUT_NOT_PDF = "--output result.txt does not end in .pdf."


def build(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["pdf", "from-images", *args])


def flat(text: str) -> str:
    return " ".join(text.split())


def summary_parts(result) -> set[str]:  # noqa: ANN001
    lines = [line for line in result.stdout.splitlines() if line.startswith("pdf: ")]
    assert len(lines) == 1, result.output
    return set(lines[0][len("pdf: "):].split(", "))


def page_image(reader: PdfReader, index: int):  # noqa: ANN201
    """The image XObject drawn by page ``index`` (0-based)."""
    page = reader.pages[index]
    return page["/Resources"]["/XObject"].raw_get("/Im0").get_object()


def filters_of(image) -> tuple[str, ...]:  # noqa: ANN001
    value = image.get("/Filter")
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return (str(value),)


def matrix_of(reader: PdfReader, index: int) -> tuple[float, ...]:
    content = reader.pages[index].get_contents().get_data().decode("latin-1")
    found = re.fullmatch(
        r"\s*q\s+((?:-?[\d.]+\s+){5}-?[\d.]+)\s*cm\s+/Im0\s+Do\s+Q\s*", content
    )
    assert found, content
    return tuple(float(part) for part in found[1].split())


def page_size(reader: PdfReader, index: int) -> tuple[float, float]:
    box = reader.pages[index].mediabox
    return float(box.right) - float(box.left), float(box.top) - float(box.bottom)


def box_of(matrix) -> tuple[float, float, float, float]:  # noqa: ANN001
    a, b, c, d, e, f = matrix
    xs = [e, e + a, e + c, e + a + c]
    ys = [f, f + b, f + d, f + b + d]
    return min(xs), min(ys), max(xs), max(ys)


def flate_pixels(image) -> Image.Image:  # noqa: ANN001
    """The samples of an 8-bit Flate image (predictors undone by pypdf) as a picture."""
    space = str(image["/ColorSpace"])
    mode, channels = {"/DeviceRGB": ("RGB", 3), "/DeviceGray": ("L", 1)}[space]
    width, height = int(image["/Width"]), int(image["/Height"])
    data = image.get_data()
    assert len(data) == width * height * channels, (len(data), width, height, channels)
    return Image.frombytes(mode, (width, height), data)


def mean_difference(first: Image.Image, second: Image.Image) -> float:
    return cf.mean_difference(first.convert("RGB"), second.convert("RGB"))


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-pdf-from-images-cli-")
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)

    def put(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def tree(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.root)).replace("\\", "/")
            for path in self.root.rglob("*")
            if path.is_file()
        )

    def read(self, name: str) -> PdfReader:
        return PdfReader(io.BytesIO((self.root / name).read_bytes()))

    def make(self, name: str, data: bytes, *options: str, out: str = "out.pdf") -> PdfReader:
        """Build a one-page PDF from ``data`` and read it back."""
        self.put(name, data)
        result = build(name, "-o", out, *options)
        self.assertEqual(result.exit_code, 0, result.output)
        return self.read(out)

    def assertUsageError(self, result, text: str) -> None:  # noqa: ANN001
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn(text, flat(result.output))

    def assertSize(self, got: tuple[float, float], want: tuple[float, float]) -> None:
        self.assertAlmostEqual(got[0], want[0], delta=0.01)
        self.assertAlmostEqual(got[1], want[1], delta=0.01)


# ---- help


class HelpTests(unittest.TestCase):
    def test_the_group_lists_the_command(self) -> None:
        result = runner.invoke(app, ["pdf", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(result.stdout, r"(?m)^  from-images\s+\S")

    def test_the_help_names_the_options_and_says_the_margin_is_ignored_by_original(self) -> None:
        result = runner.invoke(app, ["pdf", "from-images", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        text = flat(result.stdout)
        for word in ("--output", "-o", "--paper", "--orientation", "--margin", "--smaller",
                     "--overwrite", "--dry-run", "a4", "letter", "original", "_combined.pdf"):
            self.assertIn(word, text)
        self.assertRegex(text, r"(?i)margin[^.]*original|original[^.]*margin")


# ---- result lines and names


class ResultTests(CliCase):
    def test_several_images_give_one_line_with_the_size_and_a_summary(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg())
        self.put("b.png", ipf.rgb_png())
        self.put("c.bmp", ipf.bmp())
        self.put("d.webp", ipf.webp_rgb())
        result = build("a.jpg", "b.png", "c.bmp", "d.webp")
        self.assertEqual(result.exit_code, 0, result.output)
        size = format_size((self.root / "a_combined.pdf").stat().st_size)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"a.jpg + 3 more -> a_combined.pdf (4 pages, {size})", "pdf: 1 combined"],
        )
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(self.read("a_combined.pdf").pages), 4)

    def test_one_image_gives_a_one_page_pdf(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg())
        result = build("a.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        size = format_size((self.root / "a_combined.pdf").stat().st_size)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"a.jpg -> a_combined.pdf (1 page, {size})", "pdf: 1 combined"],
        )

    def test_the_default_name_is_beside_the_first_image(self) -> None:
        self.put("photos/first.jpg", ipf.solid_jpeg())
        self.put("other/second.png", ipf.rgb_png())
        result = build(str(Path("photos") / "first.jpg"), str(Path("other") / "second.png"))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            self.tree(), ["other/second.png", "photos/first.jpg", "photos/first_combined.pdf"]
        )
        self.assertIn(f"-> {Path('photos') / 'first_combined.pdf'} (", result.stdout)

    def test_pages_follow_the_order_given(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg(size=(40, 30)))
        self.put("b.jpg", ipf.solid_jpeg(size=(50, 30)))
        self.put("c.jpg", ipf.solid_jpeg(size=(60, 30)))
        build("c.jpg", "a.jpg", "b.jpg", "-o", "out.pdf")
        reader = self.read("out.pdf")
        self.assertEqual([int(page_image(reader, i)["/Width"]) for i in range(3)], [60, 40, 50])

    def test_output_goes_where_o_says_and_its_folders_are_created(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg())
        result = build("a.jpg", "-o", "deep/er/result.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.tree(), ["a.jpg", "deep/er/result.pdf"])

    def test_an_output_name_without_pdf_is_a_usage_error(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg())
        self.assertUsageError(build("a.jpg", "-o", "result.txt"), OUTPUT_NOT_PDF)
        self.assertEqual(self.tree(), ["a.jpg"])

    def test_a_dry_run_prints_the_plan_after_reading_every_image(self) -> None:
        for name in ("a.jpg", "b.jpg"):
            self.put(name, ipf.solid_jpeg())
        result = build("a.jpg", "b.jpg", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            ["a.jpg + 1 more -> a_combined.pdf (2 pages)", "pdf: 1 planned"],
        )
        self.assertEqual(self.tree(), ["a.jpg", "b.jpg"])

    def test_a_dry_run_still_fails_for_an_unreadable_image(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg())
        self.put("b.png", ipf.not_an_image())
        result = build("a.jpg", "b.png", "--dry-run")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), ["fail b.png: not an image Pillow can read"])
        self.assertEqual(summary_parts(result), {"1 failed"})

    def test_the_result_reads_back_with_pdf_info(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg(size=(90, 40)))
        self.put("b.png", ipf.rgb_png())
        build("a.jpg", "b.png")
        result = runner.invoke(app, ["pdf", "info", "--json", "a_combined.pdf"])
        self.assertEqual(result.exit_code, 0, result.output)
        facts = json.loads(result.stdout)[0]
        self.assertEqual(facts["pages"], 2)
        self.assertEqual(facts["images"], 2)
        self.assertIsNone(facts["problem"])
        self.assertEqual(facts["page_sizes"][0]["paper"], "A4")
        self.assertEqual(facts["page_sizes"][0]["orientation"], "landscape")

    def test_a_missing_image_argument_is_a_usage_error(self) -> None:
        self.assertEqual(build().exit_code, 2)

    def test_options_out_of_range_are_usage_errors(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg())
        for args in (
            ["--paper", "a5"],
            ["--orientation", "sideways"],
            ["--margin", "31"],
            ["--margin", "-1"],
            ["--margin", "x"],
        ):
            with self.subTest(args=args):
                self.assertEqual(build("a.jpg", *args).exit_code, 2)
        self.assertEqual(self.tree(), ["a.jpg"])

    def test_the_margin_limits_are_accepted(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg())
        for value in ("0", "30", "12.5"):
            with self.subTest(margin=value):
                self.assertEqual(build("a.jpg", "--margin", value, "--overwrite").exit_code, 0)


# ---- existing targets and bad images


class TargetTests(CliCase):
    def test_an_existing_target_is_skipped_and_nothing_is_written(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg())
        self.put("a_combined.pdf", b"old")
        result = build("a.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stderr.splitlines(), ["skip a_combined.pdf: target exists (use --overwrite)"]
        )
        self.assertEqual(summary_parts(result), {"1 skipped"})
        self.assertEqual((self.root / "a_combined.pdf").read_bytes(), b"old")
        self.assertEqual(self.tree(), ["a.jpg", "a_combined.pdf"])

    def test_overwrite_replaces_the_target(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg())
        self.put("a_combined.pdf", b"old")
        result = build("a.jpg", "--overwrite")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(self.read("a_combined.pdf").pages), 1)
        self.assertEqual(self.tree(), ["a.jpg", "a_combined.pdf"])

    def test_a_target_that_is_one_of_the_images_is_never_replaced(self) -> None:
        data = ipf.solid_jpeg()
        self.put("pic.pdf", data)
        self.put("b.png", ipf.rgb_png())
        for extra in ([], ["--overwrite"]):
            with self.subTest(extra=extra):
                result = build("pic.pdf", "b.png", "-o", "pic.pdf", *extra)
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(
                    result.stderr.splitlines(), ["skip pic.pdf: source is the target"]
                )
                self.assertEqual(summary_parts(result), {"1 skipped"})
                self.assertEqual((self.root / "pic.pdf").read_bytes(), data)
        self.assertEqual(self.tree(), ["b.png", "pic.pdf"])

    def test_a_bad_image_among_good_ones_writes_nothing(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg())
        self.put("x.heic", ipf.not_an_image())
        self.put("b.png", ipf.rgb_png())
        result = build("a.jpg", "x.heic", "b.png")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), ["fail x.heic: not an image Pillow can read"])
        self.assertEqual(summary_parts(result), {"1 failed"})
        self.assertEqual(self.tree(), ["a.jpg", "b.png", "x.heic"])

    def test_every_bad_image_is_named_and_counted(self) -> None:
        self.put("a.jpg", ipf.solid_jpeg())
        self.put("x.png", ipf.not_an_image())
        result = build("a.jpg", "missing.jpg", "x.png")
        self.assertEqual(result.exit_code, 1, result.output)
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 2, result.output)
        self.assertTrue(lines[0].startswith("fail missing.jpg: "), lines)
        self.assertIn("No such file or directory", lines[0])
        self.assertEqual(lines[1], "fail x.png: not an image Pillow can read")
        self.assertEqual(summary_parts(result), {"2 failed"})
        self.assertEqual(self.tree(), ["a.jpg", "x.png"])

    def test_a_picture_past_the_pixel_limit_fails_with_its_size(self) -> None:
        self.put("huge.png", ipf.too_many_pixels_png())
        result = build("huge.png")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertRegex(result.stderr.strip(), r"^fail huge\.png: image is too large \(\d+ MP\)$")
        self.assertEqual(summary_parts(result), {"1 failed"})
        self.assertEqual(self.tree(), ["huge.png"])

    def test_original_pages_over_14400_points_fail_that_image(self) -> None:
        self.put("wide.png", ipf.wide_png(19201))
        self.put("a.jpg", ipf.solid_jpeg())
        result = build("a.jpg", "wide.png", "--paper", "original")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(
            result.stderr.splitlines(),
            ["fail wide.png: image is too large for a page at 96 DPI"],
        )
        self.assertEqual(summary_parts(result), {"1 failed"})
        self.assertEqual(self.tree(), ["a.jpg", "wide.png"])

    def test_original_pages_of_exactly_14400_points_are_allowed(self) -> None:
        reader = self.make("wide.png", ipf.wide_png(19200), "--paper", "original")
        self.assertSize(page_size(reader, 0), (14400, 3))

    def test_a_wide_image_is_fine_on_a_paper_page(self) -> None:
        reader = self.make("wide.png", ipf.wide_png(19201), "--paper", "a4")
        self.assertSize(page_size(reader, 0), A4_LANDSCAPE)


# ---- layout of the pages


class PageLayoutTests(CliCase):
    def test_a4_portrait_with_the_default_margin(self) -> None:
        reader = self.make("a.png", ipf.encode(ipf.noise(100, 200), "PNG"))
        self.assertSize(page_size(reader, 0), A4)
        x0, y0, x1, y1 = box_of(matrix_of(reader, 0))
        for got, want in zip((x0, y0, x1, y1), (101.339, 28.346, 101.339 + 392.598, 28.346 + 785.197)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_a_wide_picture_turns_an_auto_page_to_landscape(self) -> None:
        reader = self.make("a.png", ipf.encode(ipf.noise(200, 100), "PNG"))
        self.assertSize(page_size(reader, 0), A4_LANDSCAPE)
        box = box_of(matrix_of(reader, 0))
        for got, want in zip(box, (28.346, 101.339, 28.346 + 785.197, 101.339 + 392.598)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_letter_paper(self) -> None:
        reader = self.make("a.png", ipf.encode(ipf.noise(100, 100), "PNG"), "--paper", "letter", "--margin", "0")
        self.assertSize(page_size(reader, 0), LETTER)
        for got, want in zip(box_of(matrix_of(reader, 0)), (0, 90, 612, 702)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_forced_landscape(self) -> None:
        reader = self.make(
            "a.png", ipf.encode(ipf.noise(100, 200), "PNG"),
            "--paper", "letter", "--orientation", "landscape", "--margin", "0",
        )
        self.assertSize(page_size(reader, 0), (792, 612))
        for got, want in zip(box_of(matrix_of(reader, 0)), (243, 0, 549, 612)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_forced_portrait_for_a_wide_picture(self) -> None:
        reader = self.make("a.png", ipf.encode(ipf.noise(200, 100), "PNG"), "--orientation", "portrait")
        self.assertSize(page_size(reader, 0), A4)
        for got, want in zip(box_of(matrix_of(reader, 0)), (28.346, 286.299, 28.346 + 538.583, 286.299 + 269.291)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_margin_zero_fills_the_page_width(self) -> None:
        reader = self.make("a.png", ipf.encode(ipf.noise(100, 100), "PNG"), "--margin", "0")
        for got, want in zip(box_of(matrix_of(reader, 0)), (0, 123.307, 595.276, 123.307 + 595.276)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_original_makes_the_page_the_picture_size_at_96_dpi(self) -> None:
        reader = self.make("a.png", ipf.encode(ipf.noise(960, 480), "PNG"), "--paper", "original")
        self.assertSize(page_size(reader, 0), (720, 360))
        for got, want in zip(matrix_of(reader, 0), (720, 0, 0, 360, 0, 0)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_original_ignores_the_margin_without_an_error(self) -> None:
        reader = self.make(
            "a.png", ipf.encode(ipf.noise(960, 480), "PNG"), "--paper", "original", "--margin", "25"
        )
        self.assertSize(page_size(reader, 0), (720, 360))
        for got, want in zip(matrix_of(reader, 0), (720, 0, 0, 360, 0, 0)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_each_page_is_laid_out_on_its_own(self) -> None:
        self.put("a.png", ipf.encode(ipf.noise(200, 100), "PNG"))
        self.put("b.png", ipf.encode(ipf.noise(100, 200), "PNG"))
        build("a.png", "b.png", "-o", "out.pdf")
        reader = self.read("out.pdf")
        self.assertSize(page_size(reader, 0), A4_LANDSCAPE)
        self.assertSize(page_size(reader, 1), A4)
        self.assertEqual(reader.pages[0].mediabox.left, 0)
        self.assertEqual(reader.pages[0].mediabox.bottom, 0)

    def test_a_page_has_no_rotation(self) -> None:
        reader = self.make("a.jpg", ipf.jpeg_with_exif(6))
        self.assertEqual(int(reader.pages[0].get("/Rotate", 0)), 0)


# ---- JPEG embedding


class JpegTests(CliCase):
    def test_the_jpeg_is_embedded_unchanged_except_for_its_metadata(self) -> None:
        original = ipf.jpeg_with_exif(6)
        reader = self.make("a.jpg", original)
        image = page_image(reader, 0)
        self.assertEqual(filters_of(image), ("/DCTDecode",))
        self.assertEqual(str(image["/ColorSpace"]), "/DeviceRGB")
        self.assertEqual(int(image["/BitsPerComponent"]), 8)
        self.assertEqual((int(image["/Width"]), int(image["/Height"])), (90, 40))
        raw = bytes(image._data)
        self.assertEqual(raw[:2], b"\xff\xd8")
        self.assertEqual(raw, strip_bytes(original).data)

    def test_no_camera_or_gps_data_reaches_the_pdf(self) -> None:
        original = ipf.jpeg_with_exif(6)
        self.assertTrue(ipf.has_gps(original))
        self.put("a.jpg", original)
        build("a.jpg", "-o", "out.pdf")
        data = (self.root / "out.pdf").read_bytes()
        raw = bytes(page_image(PdfReader(io.BytesIO(data)), 0)._data)
        self.assertFalse(ipf.has_gps(raw))
        self.assertNotIn(b"AcmeCam", data)

    def test_the_pixels_are_not_rotated_for_an_exif_orientation(self) -> None:
        reader = self.make("a.jpg", ipf.jpeg_with_exif(6, size=(90, 40)))
        image = page_image(reader, 0)
        self.assertEqual((int(image["/Width"]), int(image["/Height"])), (90, 40))

    def test_orientation_6_gives_a_portrait_page_and_a_turned_matrix(self) -> None:
        reader = self.make("a.jpg", ipf.jpeg_with_exif(6, size=(90, 40)))
        self.assertSize(page_size(reader, 0), A4)
        a, b, c, d, e, f = matrix_of(reader, 0)
        self.assertAlmostEqual(a, 0, delta=0.01)
        self.assertAlmostEqual(d, 0, delta=0.01)
        self.assertLess(b, 0)
        self.assertGreater(c, 0)
        self.assertAlmostEqual(c, 348.976, delta=0.01)
        self.assertAlmostEqual(-b, 785.197, delta=0.01)
        for got, want in zip(box_of((a, b, c, d, e, f)), (123.15, 28.346, 472.126, 813.543)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_the_same_picture_without_orientation_is_placed_on_a_landscape_page(self) -> None:
        reader = self.make("a.jpg", ipf.jpeg_with_exif(None, size=(90, 40)))
        self.assertSize(page_size(reader, 0), A4_LANDSCAPE)
        a, b, c, d, _e, _f = matrix_of(reader, 0)
        self.assertGreater(a, 0)
        self.assertGreater(d, 0)
        self.assertAlmostEqual(b, 0, delta=0.01)
        self.assertAlmostEqual(c, 0, delta=0.01)

    def test_a_grey_jpeg_stays_grey(self) -> None:
        original = ipf.grey_jpeg()
        image = page_image(self.make("g.jpg", original), 0)
        self.assertEqual(filters_of(image), ("/DCTDecode",))
        self.assertEqual(str(image["/ColorSpace"]), "/DeviceGray")
        self.assertEqual(bytes(image._data), strip_bytes(original).data)

    def test_a_red_jpeg_is_drawn_red(self) -> None:
        self.put("r.jpg", ipf.solid_jpeg(ipf.RED, (60, 40)))
        build("r.jpg", "-o", "out.pdf")
        page = cf.render((self.root / "out.pdf").read_bytes(), scale=1)
        red, green, blue = page.getpixel((page.width // 2, page.height // 2))
        self.assertGreater(red, 230)
        self.assertLess(green, 30)
        self.assertLess(blue, 30)
        self.assertEqual(page.getpixel((3, 3)), (255, 255, 255))


# ---- the eight orientations, drawn


class OrientationDrawingTests(CliCase):
    def check(self, name: str, data: bytes, exif: int) -> None:
        self.put(name, data)
        out = f"{name}.pdf"
        result = build(name, "-o", out, "--paper", "original")
        self.assertEqual(result.exit_code, 0, result.output)
        drawn = cf.render((self.root / out).read_bytes(), scale=2)
        with Image.open(io.BytesIO(data)) as stored:
            upright = ImageOps.exif_transpose(stored).convert("RGB")
        expected = upright.resize(drawn.size, Image.NEAREST)
        self.assertLess(mean_difference(drawn, expected), 14, f"{name} orientation {exif}")

    def test_jpegs_are_drawn_upright_for_every_orientation(self) -> None:
        for exif in range(1, 9):
            with self.subTest(exif=exif):
                self.check(f"j{exif}.jpg", ipf.jpeg_with_exif(exif, gps=False, image=ipf.quadrants()), exif)

    def test_pngs_are_drawn_upright_for_every_orientation(self) -> None:
        for exif in range(1, 9):
            with self.subTest(exif=exif):
                self.check(f"p{exif}.png", ipf.png_with_exif(exif), exif)

    def test_other_formats_are_turned_upright_too(self) -> None:
        for exif in (3, 6, 8):
            with self.subTest(format="webp", exif=exif):
                self.check(f"w{exif}.webp", ipf.webp_with_orientation(exif), exif)
            with self.subTest(format="tiff", exif=exif):
                self.check(f"t{exif}.tif", ipf.tiff_with_orientation(exif), exif)

    def test_a_turned_png_keeps_its_stored_pixels(self) -> None:
        reader = self.make("a.png", ipf.png_with_exif(6))
        image = page_image(reader, 0)
        self.assertEqual((int(image["/Width"]), int(image["/Height"])), (96, 48))
        self.assertEqual(filters_of(image), ("/FlateDecode",))
        self.assertEqual(flate_pixels(image).tobytes(), ipf.quadrants().tobytes())


# ---- PNG embedding


class PngTests(CliCase):
    def test_an_rgba_png_gives_flate_and_a_soft_mask_with_equal_pixels(self) -> None:
        picture = ipf.rgba_picture()
        image = page_image(self.make("a.png", ipf.rgba_png()), 0)
        self.assertEqual(filters_of(image), ("/FlateDecode",))
        self.assertEqual(str(image["/ColorSpace"]), "/DeviceRGB")
        self.assertEqual(int(image["/BitsPerComponent"]), 8)
        self.assertEqual(flate_pixels(image).tobytes(), picture.convert("RGB").tobytes())
        mask = image["/SMask"].get_object()
        self.assertEqual(filters_of(mask), ("/FlateDecode",))
        self.assertEqual(str(mask["/ColorSpace"]), "/DeviceGray")
        self.assertEqual((int(mask["/Width"]), int(mask["/Height"])), picture.size)
        self.assertEqual(flate_pixels(mask).tobytes(), picture.getchannel("A").tobytes())

    def test_a_png_without_transparency_has_no_soft_mask(self) -> None:
        picture = ipf.noise(40, 30, 7)
        image = page_image(self.make("a.png", ipf.rgb_png()), 0)
        self.assertNotIn("/SMask", image)
        self.assertEqual(filters_of(image), ("/FlateDecode",))
        self.assertEqual(flate_pixels(image).tobytes(), picture.tobytes())

    def test_a_fully_opaque_alpha_channel_is_left_out(self) -> None:
        picture = ipf.noise(40, 30, 6)
        image = page_image(self.make("a.png", ipf.opaque_rgba_png()), 0)
        self.assertNotIn("/SMask", image)
        self.assertEqual(flate_pixels(image).tobytes(), picture.tobytes())

    def test_a_grey_png_with_alpha(self) -> None:
        picture = ipf.grey_alpha_picture()
        image = page_image(self.make("a.png", ipf.grey_alpha_png()), 0)
        self.assertEqual(str(image["/ColorSpace"]), "/DeviceGray")
        self.assertEqual(flate_pixels(image).tobytes(), picture.getchannel("L").tobytes())
        self.assertEqual(
            flate_pixels(image["/SMask"].get_object()).tobytes(), picture.getchannel("A").tobytes()
        )

    def test_a_palette_png_becomes_rgb(self) -> None:
        picture = ipf.palette_picture()
        image = page_image(self.make("a.png", ipf.palette_png()), 0)
        self.assertEqual(filters_of(image), ("/FlateDecode",))
        self.assertEqual(str(image["/ColorSpace"]), "/DeviceRGB")
        self.assertEqual(flate_pixels(image).tobytes(), picture.convert("RGB").tobytes())
        self.assertNotIn("/SMask", image)

    def test_a_palette_colour_marked_transparent_becomes_a_soft_mask(self) -> None:
        picture = ipf.palette_picture()
        image = page_image(self.make("a.png", ipf.palette_png(transparent_index=0)), 0)
        self.assertEqual(flate_pixels(image).tobytes(), picture.convert("RGB").tobytes())
        alpha = [0 if index == 0 else 255 for index in picture.getdata()]
        self.assertEqual(list(flate_pixels(image["/SMask"].get_object()).getdata()), alpha)

    def test_a_one_bit_png_is_embedded_at_eight_bits(self) -> None:
        image = page_image(self.make("a.png", ipf.one_bit_png()), 0)
        self.assertEqual(int(image["/BitsPerComponent"]), 8)
        with Image.open(io.BytesIO(ipf.one_bit_png())) as source:
            expected = source.convert("L")
        self.assertEqual(flate_pixels(image).convert("L").tobytes(), expected.tobytes())

    def test_a_16_bit_png_is_embedded_at_eight_bits_with_the_tones_kept(self) -> None:
        image = page_image(self.make("a.png", ipf.sixteen_bit_png()), 0)
        self.assertEqual(int(image["/BitsPerComponent"]), 8)
        self.assertEqual(str(image["/ColorSpace"]), "/DeviceGray")
        row = [flate_pixels(image).getpixel((x, 0)) for x in range(20)]
        self.assertEqual(row[0], 0)
        self.assertEqual(row[-1], 255)
        self.assertAlmostEqual(row[10], 134, delta=3)
        self.assertEqual(row, sorted(row))

    def test_a_png_is_drawn_with_its_colours_and_transparency(self) -> None:
        # Opaque left half red, right half fully transparent.
        picture = Image.new("RGBA", (40, 40), (255, 0, 0, 255))
        picture.paste((0, 0, 255, 0), (20, 0, 40, 40))
        self.put("a.png", ipf.encode(picture, "PNG"))
        build("a.png", "-o", "out.pdf", "--paper", "original")
        page = cf.render((self.root / "out.pdf").read_bytes(), scale=96 / 72)
        self.assertEqual(page.getpixel((10, 20)), (255, 0, 0))
        self.assertEqual(page.getpixel((30, 20)), (255, 255, 255))


# ---- every other format


class OtherFormatTests(CliCase):
    def check_jpeg(self, name: str, data: bytes, picture: Image.Image, limit: float = 14) -> None:
        image = page_image(self.make(name, data), 0)
        self.assertEqual(filters_of(image), ("/DCTDecode",))
        self.assertEqual(str(image["/ColorSpace"]), "/DeviceRGB")
        self.assertEqual(int(image["/BitsPerComponent"]), 8)
        self.assertNotIn("/SMask", image)
        stored = Image.open(io.BytesIO(bytes(image._data)))
        self.assertEqual((stored.format, stored.mode, stored.size), ("JPEG", "RGB", picture.size))
        self.assertLess(mean_difference(stored, picture), limit)

    def test_webp_without_alpha_becomes_a_jpeg(self) -> None:
        self.check_jpeg("a.webp", ipf.webp_rgb(), ipf.quadrants(48, 32))

    def test_bmp_becomes_a_jpeg(self) -> None:
        self.check_jpeg("a.bmp", ipf.bmp(), ipf.quadrants(48, 32))

    def test_tiff_becomes_a_jpeg(self) -> None:
        self.check_jpeg("a.tif", ipf.tiff(), ipf.quadrants(48, 32))

    def test_heic_becomes_a_jpeg(self) -> None:
        try:
            data = ipf.heic()
        except Exception as error:  # noqa: BLE001 - no HEIF encoder in this build
            self.skipTest(f"cannot make a HEIC file here: {error}")
        self.check_jpeg("a.heic", data, ipf.quadrants(48, 32), limit=20)

    def test_the_extension_does_not_decide_the_format(self) -> None:
        self.check_jpeg("really-a-bmp.png", ipf.bmp(), ipf.quadrants(48, 32))

    def test_webp_with_alpha_is_lossless_with_a_soft_mask(self) -> None:
        picture = ipf.rgba_picture()
        image = page_image(self.make("a.webp", ipf.webp_rgba()), 0)
        self.assertEqual(filters_of(image), ("/FlateDecode",))
        self.assertEqual(flate_pixels(image).tobytes(), picture.convert("RGB").tobytes())
        self.assertEqual(
            flate_pixels(image["/SMask"].get_object()).tobytes(), picture.getchannel("A").tobytes()
        )

    def test_a_gif_uses_its_first_frame(self) -> None:
        reader = self.make("a.gif", ipf.gif_two_frames())
        self.assertEqual(len(reader.pages), 1)
        image = page_image(reader, 0)
        self.assertEqual(filters_of(image), ("/DCTDecode",))
        stored = Image.open(io.BytesIO(bytes(image._data))).convert("RGB")
        red, green, blue = stored.getpixel((12, 8))
        self.assertGreater(red, 230)
        self.assertLess(green, 30)
        self.assertLess(blue, 30)

    def test_a_cmyk_jpeg_becomes_an_rgb_jpeg_with_the_same_colour(self) -> None:
        self.put("c.jpg", ipf.cmyk_jpeg())
        build("c.jpg", "-o", "out.pdf")
        reader = self.read("out.pdf")
        image = page_image(reader, 0)
        self.assertEqual(filters_of(image), ("/DCTDecode",))
        self.assertEqual(str(image["/ColorSpace"]), "/DeviceRGB")
        self.assertEqual(Image.open(io.BytesIO(bytes(image._data))).mode, "RGB")
        page = cf.render((self.root / "out.pdf").read_bytes(), scale=1)
        red, green, blue = page.getpixel((page.width // 2, page.height // 2))
        self.assertGreater(red, 220)
        self.assertLess(green, 50)
        self.assertLess(blue, 50)

    def test_every_format_in_one_run_gives_one_page_each(self) -> None:
        files = {
            "a.jpg": ipf.solid_jpeg(),
            "b.png": ipf.rgba_png(),
            "c.webp": ipf.webp_rgb(),
            "d.bmp": ipf.bmp(),
            "e.tif": ipf.tiff(),
            "f.gif": ipf.gif_two_frames(),
            "g.jpg": ipf.cmyk_jpeg(),
        }
        for name, data in files.items():
            self.put(name, data)
        result = build(*files, "-o", "all.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(self.read("all.pdf").pages), len(files))


# ---- --smaller


class SmallerTests(CliCase):
    def test_smaller_shrinks_a_large_noisy_jpeg(self) -> None:
        self.put("big.jpg", ipf.noisy_large_jpeg())
        build("big.jpg", "-o", "plain.pdf")
        result = build("big.jpg", "-o", "small.pdf", "--smaller")
        self.assertEqual(result.exit_code, 0, result.output)
        plain, small = self.root / "plain.pdf", self.root / "small.pdf"
        self.assertLess(small.stat().st_size, plain.stat().st_size)
        image = page_image(self.read("small.pdf"), 0)
        self.assertEqual(filters_of(image), ("/DCTDecode",))
        self.assertEqual(int(image["/Width"]), 2000)
        self.assertAlmostEqual(int(image["/Height"]), 1333, delta=1)
        original = page_image(self.read("plain.pdf"), 0)
        self.assertEqual((int(original["/Width"]), int(original["/Height"])), (2400, 1600))

    def test_smaller_keeps_the_original_when_the_copy_would_not_be_smaller(self) -> None:
        original = ipf.tiny_jpeg()
        self.put("tiny.jpg", original)
        build("tiny.jpg", "-o", "plain.pdf")
        build("tiny.jpg", "-o", "small.pdf", "--smaller")
        image = page_image(self.read("small.pdf"), 0)
        self.assertEqual(bytes(image._data), strip_bytes(original).data)
        self.assertEqual((int(image["/Width"]), int(image["/Height"])), (64, 64))
        self.assertEqual(
            (self.root / "small.pdf").stat().st_size, (self.root / "plain.pdf").stat().st_size
        )

    def test_smaller_leaves_lossless_pngs_alone(self) -> None:
        self.put("a.png", ipf.rgba_png())
        build("a.png", "-o", "plain.pdf")
        build("a.png", "-o", "small.pdf", "--smaller")
        image = page_image(self.read("small.pdf"), 0)
        self.assertEqual(filters_of(image), ("/FlateDecode",))
        self.assertEqual(flate_pixels(image).tobytes(), ipf.rgba_picture().convert("RGB").tobytes())


if __name__ == "__main__":
    unittest.main()
