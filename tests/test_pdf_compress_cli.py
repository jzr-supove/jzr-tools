"""Tests of ``jzr pdf compress`` through the real entry point.

Every test runs with a temporary folder as the working directory, so the
paths printed in result lines are the short names given on the command line.
Results are checked by reading the written files back.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import re
import tempfile
import unittest

from click.testing import CliRunner
from pypdf import PdfReader

from jzr_tools.cli import app
from jzr_tools.core.image_compress import change_text
from jzr_tools.core.media import format_size
from tests import compress_fixtures as cf
from tests import pdf_fixtures as fx

runner = CliRunner()

DAMAGED = "damaged or not a PDF"
ENCRYPTED = "encrypted PDFs are not supported; use an unencrypted copy"
SIGNATURE_NOTE = "note: a.pdf: digital signatures will not validate in the new PDF"
ALREADY_COMPACT = "already compact (no smaller version found)"

# A photo that the default level shrinks to 450 x 300 and strong to 300 x 200.
PHOTO = (1500, 1000, (216, 144))


def compress(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["pdf", "compress", *args])


def flat(text: str) -> str:
    return " ".join(text.split())


def photo() -> bytes:
    width, height, drawn = PHOTO
    return cf.photo_pdf(width, height, drawn)


def size_change(before: Path | bytes, after: Path) -> str:
    before_size = len(before) if isinstance(before, bytes) else before.stat().st_size
    after_size = after.stat().st_size
    return f"{format_size(before_size)} -> {format_size(after_size)}, {change_text(before_size, after_size)}"


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-pdf-compress-cli-")
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

    def names(self, folder: str = ".") -> list[str]:
        return sorted(entry.name for entry in (self.root / folder).iterdir())

    def tree(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.root)).replace("\\", "/")
            for path in self.root.rglob("*")
            if path.is_file()
        )

    def image_of(self, name: str) -> cf.Found:
        found = cf.images_in((self.root / name).read_bytes())
        self.assertEqual(len(found), 1)
        return found[0]

    def assertUsageError(self, result, text: str) -> None:  # noqa: ANN001
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn(text, flat(result.output))


# ---- help


class HelpTests(unittest.TestCase):
    def help_text(self) -> str:
        result = runner.invoke(app, ["pdf", "compress", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        return flat(result.stdout)

    def test_the_group_lists_the_command_with_its_short_help(self) -> None:
        result = runner.invoke(app, ["pdf", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(
            result.stdout, r"(?m)^  compress\s+Make PDF files smaller, mostly by shrinking their images\."
        )

    def test_the_help_describes_outputs_and_levels(self) -> None:
        text = self.help_text()
        self.assertIn("Make PDF files smaller, mostly by shrinking their images.", text)
        self.assertIn("_compressed.pdf", text)
        for word in ("lossless", "recommended", "strong", "150 DPI", "100 DPI"):
            self.assertIn(word, text)

    def test_the_option_help_texts(self) -> None:
        text = self.help_text()
        self.assertIn("[lossless|recommended|strong]", text)
        self.assertIn("How hard to compress images (default recommended).", text)
        self.assertIn("Also remove the document info and XMP metadata.", text)

    def test_the_command_lists_the_batch_options(self) -> None:
        text = self.help_text()
        for option in ("--level", "--strip", "--output", "-o", "--outdir", "--dir", "-d",
                       "--pattern", "-p", "--recursive", "-r", "--overwrite", "--dry-run"):
            with self.subTest(option=option):
                self.assertRegex(text, rf"(?<![\w-]){re.escape(option)}(?![\w-])")


# ---- results


class ResultTests(CliCase):
    def test_one_source_gives_a_result_line_and_a_summary(self) -> None:
        self.put("a.pdf", photo())
        result = compress("a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        change = size_change(photo(), self.root / "a_compressed.pdf")
        self.assertEqual(
            result.stdout.splitlines(),
            [f"a.pdf -> a_compressed.pdf ({change}; 1 image resized)", "pdf: 1 compressed"],
        )
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.names(), ["a.pdf", "a_compressed.pdf"])

    def test_the_written_file_has_the_smaller_image_and_the_source_is_untouched(self) -> None:
        self.put("a.pdf", photo())
        compress("a.pdf")
        image = self.image_of("a_compressed.pdf")
        self.assertEqual((image.width, image.height, image.filter), (450, 300, "/DCTDecode"))
        self.assertEqual((self.root / "a.pdf").read_bytes(), photo())

    def test_several_sources_are_each_reported(self) -> None:
        self.put("one.pdf", photo())
        self.put("two.pdf", photo())
        result = compress("one.pdf", "two.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertTrue(lines[0].startswith("one.pdf -> one_compressed.pdf ("), lines)
        self.assertTrue(lines[1].startswith("two.pdf -> two_compressed.pdf ("), lines)
        self.assertEqual(lines[-1], "pdf: 2 compressed")

    def test_no_temporary_file_is_left(self) -> None:
        self.put("a.pdf", photo())
        compress("a.pdf")
        self.assertFalse([name for name in self.names() if name.startswith(".jzr-tmp-")])

    def test_a_file_that_cannot_get_smaller_is_skipped_with_exit_0(self) -> None:
        self.put("small.pdf", cf.minimal_pdf())
        result = compress("small.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.splitlines(), [f"skip small.pdf: {ALREADY_COMPACT}"])
        self.assertEqual(result.stdout.splitlines(), ["pdf: 1 skipped"])
        self.assertEqual(self.names(), ["small.pdf"])

    def test_written_and_skipped_files_are_counted_together(self) -> None:
        self.put("a.pdf", photo())
        self.put("small.pdf", cf.minimal_pdf())
        result = compress("a.pdf", "small.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[-1], "pdf: 1 compressed, 1 skipped")

    def test_lossless_names_no_images(self) -> None:
        self.put("a.pdf", photo())
        result = compress("a.pdf", "--level", "lossless")
        self.assertEqual(result.exit_code, 0, result.output)
        change = size_change(photo(), self.root / "a_compressed.pdf")
        self.assertEqual(result.stdout.splitlines()[0], f"a.pdf -> a_compressed.pdf ({change})")
        before = cf.images_in(photo())[0]
        after = self.image_of("a_compressed.pdf")
        self.assertEqual((after.raw, after.width, after.height), (before.raw, before.width, before.height))

    def test_strong_makes_a_smaller_image_than_the_default(self) -> None:
        self.put("a.pdf", photo())
        self.put("b.pdf", photo())
        self.assertEqual(compress("a.pdf").exit_code, 0)
        self.assertEqual(compress("b.pdf", "--level", "strong").exit_code, 0)
        self.assertEqual(self.image_of("a_compressed.pdf").width, 450)
        strong = self.image_of("b_compressed.pdf")
        self.assertEqual((strong.width, strong.height), (300, 200))

    def test_the_level_can_be_named_as_the_default(self) -> None:
        self.put("a.pdf", photo())
        result = compress("a.pdf", "--level", "recommended")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.image_of("a_compressed.pdf").width, 450)

    def test_strip_removes_the_document_info_and_the_xmp(self) -> None:
        self.put("kept.pdf", cf.metadata_pdf())
        self.put("clean.pdf", cf.metadata_pdf())
        self.assertEqual(compress("kept.pdf").exit_code, 0)
        self.assertEqual(compress("clean.pdf", "--strip").exit_code, 0)
        kept = (self.root / "kept_compressed.pdf").read_bytes()
        clean = (self.root / "clean_compressed.pdf").read_bytes()
        self.assertEqual(PdfReader(io.BytesIO(kept)).metadata.title, "Quarterly report")
        self.assertNotIn(b"Quarterly report", clean)
        self.assertNotIn("/Metadata", PdfReader(io.BytesIO(clean)).trailer["/Root"])

    def test_a_signed_file_is_written_and_the_note_follows_its_line(self) -> None:
        self.put("a.pdf", cf.signed_pdf())
        result = compress("a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(SIGNATURE_NOTE, result.stderr.splitlines())
        self.assertTrue((self.root / "a_compressed.pdf").is_file())
        lines = result.output.splitlines()
        self.assertLess(
            next(i for i, line in enumerate(lines) if line.startswith("a.pdf -> a_compressed.pdf")),
            lines.index(SIGNATURE_NOTE),
        )


# ---- one bad file among others


class FailureTests(CliCase):
    def test_a_failing_file_fails_alone_and_the_exit_code_is_1(self) -> None:
        self.put("good1.pdf", photo())
        self.put("bad.pdf", fx.not_a_pdf())
        self.put("good2.pdf", photo())
        result = compress("good1.pdf", "bad.pdf", "good2.pdf")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(self.names(), ["bad.pdf", "good1.pdf", "good1_compressed.pdf", "good2.pdf", "good2_compressed.pdf"])
        self.assertIn(f"fail bad.pdf: {DAMAGED}", result.stderr.splitlines())
        self.assertEqual(result.stdout.splitlines()[-1], "pdf: 2 compressed, 1 failed")

    def test_an_encrypted_file_fails_with_its_text(self) -> None:
        self.put("locked.pdf", fx.encrypted())
        result = compress("locked.pdf")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), [f"fail locked.pdf: {ENCRYPTED}"])
        self.assertEqual(result.stdout.splitlines(), ["pdf: 1 failed"])
        self.assertEqual(self.names(), ["locked.pdf"])

    def test_a_skipped_file_alone_does_not_fail_the_run(self) -> None:
        self.put("a.pdf", photo())
        self.put("a_compressed.pdf", b"already here")
        result = compress("a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)


# ---- choosing sources


class SourceTests(CliCase):
    def test_without_sources_the_current_folder_is_scanned_for_pdf_files(self) -> None:
        self.put("a.pdf", photo())
        self.put("b.PDF", photo())
        self.put("notes.txt", b"not a pdf")
        self.put("sub/c.pdf", photo())
        result = compress()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[-1], "pdf: 2 compressed")
        self.assertEqual(
            self.tree(),
            ["a.pdf", "a_compressed.pdf", "b.PDF", "b_compressed.pdf", "notes.txt", "sub/c.pdf"],
        )

    def test_earlier_results_are_not_compressed_again(self) -> None:
        self.put("a.pdf", photo())
        self.put("a_compressed.pdf", photo())
        result = compress()
        self.assertEqual(result.stdout.splitlines()[-1], "pdf: 1 skipped")
        self.assertIn("target exists", result.stderr)
        self.assertNotIn("a_compressed_compressed.pdf", self.names())

    def test_a_listed_file_is_compressed_even_when_it_ends_in_compressed(self) -> None:
        self.put("old_compressed.pdf", photo())
        result = compress("old_compressed.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue((self.root / "old_compressed_compressed.pdf").is_file())

    def test_dir_picks_the_folder_to_scan(self) -> None:
        self.put("scans/x.pdf", photo())
        self.put("other.pdf", photo())
        result = compress("-d", "scans")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.names("scans"), ["x.pdf", "x_compressed.pdf"])
        self.assertNotIn("other_compressed.pdf", self.names())
        first = result.stdout.splitlines()[0]
        self.assertTrue(first.startswith(f"{Path('scans') / 'x.pdf'} -> {Path('scans') / 'x_compressed.pdf'} ("), first)

    def test_recursive_goes_into_subfolders(self) -> None:
        self.put("a.pdf", photo())
        self.put("sub/b.pdf", photo())
        self.assertEqual(compress("-r").exit_code, 0)
        self.assertEqual(self.tree(), ["a.pdf", "a_compressed.pdf", "sub/b.pdf", "sub/b_compressed.pdf"])

    def test_a_scan_without_recursive_stays_in_the_folder(self) -> None:
        self.put("a.pdf", photo())
        self.put("sub/b.pdf", photo())
        compress()
        self.assertEqual(self.tree(), ["a.pdf", "a_compressed.pdf", "sub/b.pdf"])

    def test_pattern_filters_a_scan(self) -> None:
        self.put("report.pdf", photo())
        self.put("invoice.pdf", photo())
        result = compress("-p", "^rep")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.names(), ["invoice.pdf", "report.pdf", "report_compressed.pdf"])

    def test_pattern_filters_listed_sources(self) -> None:
        self.put("report.pdf", photo())
        self.put("invoice.pdf", photo())
        result = compress("report.pdf", "invoice.pdf", "-p", "^inv")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.names(), ["invoice.pdf", "invoice_compressed.pdf", "report.pdf"])

    def test_nothing_to_compress_says_so_and_exits_1(self) -> None:
        self.put("notes.txt", b"x")
        self.put("done_compressed.pdf", photo())
        result = compress()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), ["No matching files."])
        self.assertEqual(self.names(), ["done_compressed.pdf", "notes.txt"])

    def test_a_pattern_that_matches_nothing_says_so(self) -> None:
        self.put("a.pdf", photo())
        result = compress("-p", "zzz")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), ["No matching files."])


# ---- where outputs go


class OutputTests(CliCase):
    def test_output_names_the_result_of_one_source(self) -> None:
        self.put("a.pdf", photo())
        result = compress("a.pdf", "-o", "small.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.names(), ["a.pdf", "small.pdf"])
        self.assertTrue(result.stdout.startswith("a.pdf -> small.pdf ("), result.stdout)

    def test_output_may_end_in_capital_letters(self) -> None:
        self.put("a.pdf", photo())
        self.assertEqual(compress("a.pdf", "-o", "SMALL.PDF").exit_code, 0)
        self.assertIn("SMALL.PDF", self.names())

    def test_output_with_several_sources_is_a_usage_error(self) -> None:
        self.put("a.pdf", photo())
        self.put("b.pdf", photo())
        result = compress("a.pdf", "b.pdf", "-o", "x.pdf")
        self.assertUsageError(result, "list exactly one SOURCE")
        self.assertEqual(self.names(), ["a.pdf", "b.pdf"])

    def test_output_without_a_source_is_a_usage_error(self) -> None:
        self.put("a.pdf", photo())
        result = compress("-o", "x.pdf")
        self.assertUsageError(result, "list exactly one SOURCE")
        self.assertEqual(self.names(), ["a.pdf"])

    def test_output_that_does_not_end_in_pdf_is_a_usage_error(self) -> None:
        self.put("a.pdf", photo())
        result = compress("a.pdf", "-o", "x.txt")
        self.assertUsageError(result, "--output x.txt does not end in .pdf.")
        self.assertEqual(self.names(), ["a.pdf"])

    def test_output_and_outdir_do_not_combine(self) -> None:
        self.put("a.pdf", photo())
        result = compress("a.pdf", "-o", "x.pdf", "--outdir", "out")
        self.assertUsageError(result, "--output and --outdir cannot be used together.")

    def test_outdir_collects_the_results(self) -> None:
        self.put("a.pdf", photo())
        result = compress("a.pdf", "--outdir", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.names("out"), ["a_compressed.pdf"])
        self.assertEqual(self.names(), ["a.pdf", "out"])

    def test_outdir_recreates_the_folders_of_a_scan(self) -> None:
        self.put("scans/x.pdf", photo())
        self.put("scans/sub/y.pdf", photo())
        result = compress("-d", "scans", "-r", "--outdir", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            [name for name in self.tree() if name.startswith("out/")],
            ["out/sub/y_compressed.pdf", "out/x_compressed.pdf"],
        )

    def test_two_sources_with_one_name_get_a_number_in_the_same_outdir(self) -> None:
        self.put("one/x.pdf", photo())
        self.put("two/x.pdf", photo())
        result = compress("one/x.pdf", "two/x.pdf", "--outdir", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.names("out"), ["x_compressed-2.pdf", "x_compressed.pdf"])
        self.assertEqual(result.stdout.splitlines()[-1], "pdf: 2 compressed")

    def test_an_existing_target_is_skipped_unless_overwrite_is_given(self) -> None:
        self.put("a.pdf", photo())
        target = self.put("a_compressed.pdf", b"keep me")
        result = compress("a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.splitlines(), ["skip a.pdf: target exists (use --overwrite)"])
        self.assertEqual(result.stdout.splitlines(), ["pdf: 1 skipped"])
        self.assertEqual(target.read_bytes(), b"keep me")

        result = compress("a.pdf", "--overwrite")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[-1], "pdf: 1 compressed")
        self.assertEqual(self.image_of("a_compressed.pdf").width, 450)

    def test_a_target_that_is_the_source_is_skipped(self) -> None:
        self.put("a.pdf", photo())
        result = compress("a.pdf", "-o", "a.pdf", "--overwrite")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.splitlines(), ["skip a.pdf: source is the target"])
        self.assertEqual((self.root / "a.pdf").read_bytes(), photo())

    def test_a_dry_run_shows_the_plan_and_writes_nothing(self) -> None:
        self.put("a.pdf", photo())
        result = compress("a.pdf", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines(), ["a.pdf -> a_compressed.pdf", "pdf: 1 planned"])
        self.assertEqual(self.names(), ["a.pdf"])

    def test_a_dry_run_still_fails_for_a_file_that_cannot_be_used(self) -> None:
        self.put("bad.pdf", fx.not_a_pdf())
        result = compress("bad.pdf", "--dry-run")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), [f"fail bad.pdf: {DAMAGED}"])
        self.assertEqual(self.names(), ["bad.pdf"])

    def test_a_dry_run_with_a_scan_writes_nothing(self) -> None:
        self.put("a.pdf", photo())
        self.put("scans/b.pdf", photo())
        result = compress("-d", "scans", "--dry-run", "--outdir", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.tree(), ["a.pdf", "scans/b.pdf"])


# ---- argument errors


class ArgumentTests(CliCase):
    def test_an_unknown_level_is_a_usage_error(self) -> None:
        self.put("a.pdf", photo())
        result = compress("a.pdf", "--level", "extreme")
        self.assertUsageError(result, "'extreme'")
        self.assertIn("lossless", flat(result.output))
        self.assertEqual(self.names(), ["a.pdf"])

    def test_dir_and_recursive_do_not_go_with_listed_sources(self) -> None:
        self.put("a.pdf", photo())
        (self.root / "scans").mkdir()
        for extra in (["--dir", "scans"], ["-r"]):
            with self.subTest(extra=extra):
                result = compress("a.pdf", *extra)
                self.assertUsageError(result, "--dir and --recursive only apply when no SOURCES are listed.")
        self.assertEqual(self.names(), ["a.pdf", "scans"])

    def test_a_bad_pattern_is_a_usage_error(self) -> None:
        self.put("a.pdf", photo())
        result = compress("-p", "(")
        self.assertUsageError(result, "not a valid regular expression")
        self.assertEqual(self.names(), ["a.pdf"])


if __name__ == "__main__":
    unittest.main()
