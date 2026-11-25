"""Tests of ``jzr pdf images`` through the real entry point.

Every test runs with a temporary folder as the working directory, so the paths
printed in result lines are the short names given on the command line. Pages
are rendered for real and the written pictures are read back with Pillow.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from click.testing import CliRunner
from PIL import Image

from jzr_tools.cli import app
from tests import image_pdf_fixtures as ipf
from tests import pdf_fixtures as fx

runner = CliRunner()

DAMAGED = "damaged or not a PDF"
ENCRYPTED = "encrypted PDFs are not supported; use an unencrypted copy"
A4 = (595.276, 841.89)
PAGES_USAGE = "--pages: use page numbers and ranges such as 1-3,5 (pages 1 to 3)"


def images(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["pdf", "images", *args])


def flat(text: str) -> str:
    return " ".join(text.split())


def summary_parts(result) -> set[str]:  # noqa: ANN001
    """The pieces of the closing ``pdf: ...`` line, whatever order they come in."""
    lines = [line for line in result.stdout.splitlines() if line.startswith("pdf: ")]
    assert len(lines) == 1, result.output
    return set(lines[0][len("pdf: "):].split(", "))


def result_lines(result) -> list[str]:  # noqa: ANN001
    return [line for line in result.stdout.splitlines() if not line.startswith("pdf: ")]


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-pdf-images-cli-")
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

    def picture(self, name: str) -> Image.Image:
        image = Image.open(self.root / name)
        image.load()
        return image

    def assertUsageError(self, result, text: str) -> None:  # noqa: ANN001
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn(text, flat(result.output))


# ---- help and options


class HelpTests(unittest.TestCase):
    def test_the_group_lists_the_command(self) -> None:
        result = runner.invoke(app, ["pdf", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(result.stdout, r"(?m)^  images\s+\S")

    def test_the_help_names_the_options_and_defaults(self) -> None:
        result = runner.invoke(app, ["pdf", "images", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        text = flat(result.stdout)
        for word in ("--to", "--dpi", "--quality", "-q", "--pages", "--outdir", "--overwrite", "--dry-run"):
            self.assertIn(word, text)
        for word in ("150", "90", "_page-001"):
            self.assertIn(word, text)


# ---- rendering and names


class RenderTests(CliCase):
    def test_the_defaults_write_one_jpeg_per_page_at_150_dpi(self) -> None:
        self.put("report.pdf", fx.blank([A4, A4, A4]))
        result = images("report.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "report.pdf -> report_page-001.jpg (1241x1754)",
                "report.pdf -> report_page-002.jpg (1241x1754)",
                "report.pdf -> report_page-003.jpg (1241x1754)",
                "pdf: 3 written",
            ],
        )
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            self.tree(),
            ["report.pdf", "report_page-001.jpg", "report_page-002.jpg", "report_page-003.jpg"],
        )

    def test_the_written_pictures_have_the_reported_size_format_and_dpi(self) -> None:
        self.put("report.pdf", fx.blank([A4]))
        images("report.pdf")
        picture = self.picture("report_page-001.jpg")
        self.assertEqual((picture.format, picture.mode, picture.size), ("JPEG", "RGB", (1241, 1754)))
        dpi = picture.info["dpi"]
        self.assertEqual((round(dpi[0]), round(dpi[1])), (150, 150))

    def test_png_output(self) -> None:
        self.put("report.pdf", fx.blank([A4]))
        result = images("report.pdf", "--to", "png")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            ["report.pdf -> report_page-001.png (1241x1754)", "pdf: 1 written"],
        )
        picture = self.picture("report_page-001.png")
        self.assertEqual((picture.format, picture.mode, picture.size), ("PNG", "RGB", (1241, 1754)))
        dpi = picture.info["dpi"]
        self.assertEqual((round(dpi[0]), round(dpi[1])), (150, 150))

    def test_a_dpi_sets_the_pixel_size_rounded_up(self) -> None:
        self.put("a.pdf", fx.blank([(200, 100)]))
        result = images("a.pdf", "--dpi", "300")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[0], "a.pdf -> a_page-001.jpg (834x417)")
        self.assertEqual(self.picture("a_page-001.jpg").size, (834, 417))

    def test_a_lower_quality_gives_a_smaller_jpeg(self) -> None:
        self.put("a.pdf", ipf.red_quarter_pdf())
        images("a.pdf", "--outdir", "high", "-q", "95")
        images("a.pdf", "--outdir", "low", "-q", "10")
        self.assertLess(
            (self.root / "low" / "a_page-001.jpg").stat().st_size,
            (self.root / "high" / "a_page-001.jpg").stat().st_size,
        )

    def test_the_crop_box_sets_the_page_size(self) -> None:
        self.put("a.pdf", fx.crop_box(50, 50, 150, 250))
        result = images("a.pdf", "--dpi", "72")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[0], "a.pdf -> a_page-001.jpg (100x200)")
        self.assertEqual(self.picture("a_page-001.jpg").size, (100, 200))

    def test_a_rotated_page_is_rendered_turned(self) -> None:
        self.put("a.pdf", ipf.red_quarter_pdf(rotate=90))
        result = images("a.pdf", "--dpi", "72", "--to", "png")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[0], "a.pdf -> a_page-001.png (100x200)")
        picture = self.picture("a_page-001.png")
        self.assertEqual(picture.size, (100, 200))
        # The red top right quarter of the upright page is the bottom right one now.
        self.assertEqual(picture.getpixel((75, 150)), (255, 0, 0))
        self.assertEqual(picture.getpixel((75, 50)), (255, 255, 255))
        self.assertEqual(picture.getpixel((25, 150)), (255, 255, 255))

    def test_a_blank_page_is_white_at_its_corners(self) -> None:
        self.put("a.pdf", fx.blank([(200, 100)]))
        images("a.pdf", "--dpi", "72", "--to", "png")
        picture = self.picture("a_page-001.png")
        for corner in ((0, 0), (199, 0), (0, 99), (199, 99)):
            self.assertEqual(picture.getpixel(corner), (255, 255, 255), corner)

    def test_a_page_drawn_black_is_dark_and_content_lands_where_it_is_drawn(self) -> None:
        self.put("black.pdf", ipf.black_pdf())
        self.put("red.pdf", ipf.red_quarter_pdf())
        images("black.pdf")
        images("red.pdf", "--to", "png", "--dpi", "72")
        black = self.picture("black_page-001.jpg")
        for point in ((3, 3), (black.width // 2, black.height // 2), (black.width - 4, black.height - 4)):
            self.assertTrue(all(value < 20 for value in black.getpixel(point)), point)
        red = self.picture("red_page-001.png")
        self.assertEqual(red.getpixel((150, 25)), (255, 0, 0))
        self.assertEqual(red.getpixel((50, 75)), (255, 255, 255))
        self.assertEqual(red.getpixel((50, 25)), (255, 255, 255))

    def test_annotations_are_drawn(self) -> None:
        self.put("a.pdf", ipf.green_stamp_pdf())
        images("a.pdf", "--dpi", "72", "--to", "png")
        picture = self.picture("a_page-001.png")
        self.assertEqual(picture.getpixel((20, picture.height - 20)), (0, 255, 0))
        self.assertEqual(picture.getpixel((150, 20)), (255, 255, 255))

    def test_the_source_is_left_alone(self) -> None:
        data = fx.blank([A4])
        self.put("a.pdf", data)
        images("a.pdf")
        self.assertEqual((self.root / "a.pdf").read_bytes(), data)


# ---- pages and names


class PageSelectionTests(CliCase):
    def test_pages_write_only_those_with_their_source_numbers(self) -> None:
        self.put("a.pdf", fx.marked(4))
        result = images("a.pdf", "--pages", "2-3", "--dpi", "72")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "a.pdf -> a_page-002.jpg (120x200)",
                "a.pdf -> a_page-003.jpg (130x200)",
                "pdf: 2 written",
            ],
        )
        self.assertEqual(self.tree(), ["a.pdf", "a_page-002.jpg", "a_page-003.jpg"])

    def test_the_output_follows_source_order(self) -> None:
        self.put("a.pdf", fx.marked(4))
        result = images("a.pdf", "--pages", "4,1", "--dpi", "72")
        self.assertEqual(
            result_lines(result),
            ["a.pdf -> a_page-001.jpg (110x200)", "a.pdf -> a_page-004.jpg (140x200)"],
        )

    def test_a_bad_page_list_is_a_usage_error(self) -> None:
        self.put("a.pdf", fx.marked(3))
        for text in ("0", "4", "2-1", "1,1", "x", ""):
            with self.subTest(pages=text):
                self.assertUsageError(images("a.pdf", "--pages", text), PAGES_USAGE)
        self.assertEqual(self.tree(), ["a.pdf"])

    def test_the_number_of_digits_grows_with_the_page_count(self) -> None:
        self.put("big.pdf", fx.blank([(100, 100)] * 1000))
        result = images("big.pdf", "--pages", "5,1000", "--dry-run", "--dpi", "36")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "big.pdf -> big_page-0005.jpg (50x50)",
                "big.pdf -> big_page-1000.jpg (50x50)",
                "pdf: 2 planned",
            ],
        )

    def test_999_pages_still_use_three_digits(self) -> None:
        self.put("big.pdf", fx.blank([(100, 100)] * 999))
        result = images("big.pdf", "--pages", "5,999", "--dry-run", "--dpi", "36")
        self.assertEqual(
            result_lines(result),
            ["big.pdf -> big_page-005.jpg (50x50)", "big.pdf -> big_page-999.jpg (50x50)"],
        )


# ---- the page limit


class LimitTests(CliCase):
    def test_a_page_that_is_too_large_fails_alone(self) -> None:
        self.put("a.pdf", fx.blank([(200, 100), (200, 100), (3168, 2376)]))
        result = images("a.pdf", "--dpi", "300")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(
            result_lines(result),
            ["a.pdf -> a_page-001.jpg (834x417)", "a.pdf -> a_page-002.jpg (834x417)"],
        )
        self.assertEqual(
            result.stderr.splitlines(),
            ["fail a.pdf: page 3 is too large at 300 DPI (13,200x9,900); use a lower --dpi"],
        )
        self.assertEqual(summary_parts(result), {"2 written", "1 failed"})
        self.assertEqual(self.tree(), ["a.pdf", "a_page-001.jpg", "a_page-002.jpg"])

    def test_more_than_sixteen_million_pixels_is_too_large_even_with_short_sides(self) -> None:
        self.put("a.pdf", fx.blank([(4001, 4000)]))
        result = images("a.pdf", "--dpi", "72", "--dry-run")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(
            result.stderr.splitlines(),
            ["fail a.pdf: page 1 is too large at 72 DPI (4,001x4,000); use a lower --dpi"],
        )

    def test_exactly_sixteen_million_pixels_is_allowed(self) -> None:
        self.put("a.pdf", fx.blank([(4000, 4000)]))
        result = images("a.pdf", "--dpi", "72", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result_lines(result), ["a.pdf -> a_page-001.jpg (4000x4000)"])

    def test_8192_pixels_a_side_is_allowed_and_8193_is_not(self) -> None:
        self.put("ok.pdf", fx.blank([(8192, 10)]))
        self.put("bad.pdf", fx.blank([(8193, 10)]))
        ok = images("ok.pdf", "--dpi", "72", "--dry-run")
        self.assertEqual(ok.exit_code, 0, ok.output)
        self.assertEqual(result_lines(ok), ["ok.pdf -> ok_page-001.jpg (8192x10)"])
        bad = images("bad.pdf", "--dpi", "72", "--dry-run")
        self.assertEqual(bad.exit_code, 1, bad.output)
        self.assertEqual(
            bad.stderr.splitlines(),
            ["fail bad.pdf: page 1 is too large at 72 DPI (8,193x10); use a lower --dpi"],
        )


# ---- existing files, folders, dry runs


class OutputTests(CliCase):
    def test_an_existing_output_is_skipped_and_overwrite_replaces_it(self) -> None:
        self.put("a.pdf", fx.blank([(200, 100), (200, 100)]))
        self.put("a_page-002.jpg", b"old")
        result = images("a.pdf", "--dpi", "72")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stderr.splitlines(), ["skip a_page-002.jpg: target exists (use --overwrite)"]
        )
        self.assertEqual(result_lines(result), ["a.pdf -> a_page-001.jpg (200x100)"])
        self.assertEqual(summary_parts(result), {"1 written", "1 skipped"})
        self.assertEqual((self.root / "a_page-002.jpg").read_bytes(), b"old")

        again = images("a.pdf", "--dpi", "72", "--overwrite")
        self.assertEqual(again.exit_code, 0, again.output)
        self.assertEqual(again.stderr, "")
        self.assertEqual(self.picture("a_page-002.jpg").size, (200, 100))

    def test_outdir_is_created(self) -> None:
        self.put("a.pdf", fx.blank([(200, 100)]))
        result = images("a.pdf", "--outdir", "out/pages", "--dpi", "72")
        self.assertEqual(result.exit_code, 0, result.output)
        expected = str(Path("out") / "pages" / "a_page-001.jpg")
        self.assertEqual(result.stdout.splitlines()[0], f"a.pdf -> {expected} (200x100)")
        self.assertEqual(self.tree(), ["a.pdf", "out/pages/a_page-001.jpg"])

    def test_the_pictures_go_beside_the_file_when_it_is_in_another_folder(self) -> None:
        self.put("docs/a.pdf", fx.blank([(200, 100)]))
        result = images(str(Path("docs") / "a.pdf"), "--dpi", "72")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.tree(), ["docs/a.pdf", "docs/a_page-001.jpg"])

    def test_a_dry_run_prints_the_same_lines_and_writes_nothing(self) -> None:
        self.put("a.pdf", fx.blank([A4, A4]))
        planned = images("a.pdf", "--dry-run", "--outdir", "out")
        self.assertEqual(planned.exit_code, 0, planned.output)
        self.assertEqual(self.tree(), ["a.pdf"])
        self.assertEqual(summary_parts(planned), {"2 planned"})
        real = images("a.pdf", "--outdir", "out")
        self.assertEqual(result_lines(real), result_lines(planned))
        self.assertEqual(summary_parts(real), {"2 written"})

    def test_no_temporary_files_are_left_behind(self) -> None:
        self.put("a.pdf", fx.blank([(200, 100)]))
        images("a.pdf")
        self.assertEqual(self.tree(), ["a.pdf", "a_page-001.jpg"])


# ---- usage errors and bad files


class ErrorTests(CliCase):
    def test_quality_with_png_is_a_usage_error(self) -> None:
        self.put("a.pdf", fx.blank([(200, 100)]))
        self.assertUsageError(images("a.pdf", "--to", "png", "-q", "75"), "--quality only applies to jpg.")
        self.assertUsageError(images("a.pdf", "--to", "png", "--quality", "75"), "--quality only applies to jpg.")
        self.assertEqual(self.tree(), ["a.pdf"])

    def test_values_out_of_range_are_usage_errors(self) -> None:
        self.put("a.pdf", fx.blank([(200, 100)]))
        for args in (
            ["--dpi", "35"],
            ["--dpi", "601"],
            ["-q", "0"],
            ["-q", "101"],
            ["--to", "gif"],
        ):
            with self.subTest(args=args):
                self.assertEqual(images("a.pdf", *args).exit_code, 2)
        self.assertEqual(self.tree(), ["a.pdf"])

    def test_the_limits_of_the_ranges_are_accepted(self) -> None:
        self.put("a.pdf", fx.blank([(100, 100)]))
        for args in (["--dpi", "36"], ["--dpi", "72", "-q", "1"], ["--dpi", "72", "-q", "100"]):
            with self.subTest(args=args):
                self.assertEqual(images("a.pdf", "--overwrite", *args).exit_code, 0)
        self.assertEqual(images("a.pdf", "--dpi", "600", "--dry-run").exit_code, 0)

    def test_a_missing_file_is_a_usage_error(self) -> None:
        self.assertEqual(images("nothing.pdf").exit_code, 2)

    def test_a_file_must_be_given(self) -> None:
        self.assertEqual(images().exit_code, 2)

    def test_an_encrypted_pdf_fails_with_the_encryption_text(self) -> None:
        self.put("a.pdf", fx.encrypted())
        result = images("a.pdf")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), [f"fail a.pdf: {ENCRYPTED}"])
        self.assertEqual(summary_parts(result), {"1 failed"})
        self.assertEqual(self.tree(), ["a.pdf"])

    def test_damaged_and_unusable_pdfs_fail_with_their_texts(self) -> None:
        cases = {
            "text.pdf": (fx.not_a_pdf(), DAMAGED),
            "cut.pdf": (fx.truncated(), DAMAGED),
            "unit.pdf": (fx.user_unit(2), "unsupported page size, scaling or rotation"),
        }
        for name, (data, reason) in cases.items():
            with self.subTest(name=name):
                self.put(name, data)
                result = images(name)
                self.assertEqual(result.exit_code, 1, result.output)
                self.assertEqual(result.stderr.splitlines(), [f"fail {name}: {reason}"])
                self.assertEqual(summary_parts(result), {"1 failed"})
        self.assertEqual(self.tree(), sorted(cases))


if __name__ == "__main__":
    unittest.main()
