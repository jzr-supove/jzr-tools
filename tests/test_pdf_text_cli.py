"""Tests of ``jzr pdf text`` through the real entry point, and of the text reader behind it.

PDFs are built in the test with the standard fonts, so no font file is needed.
Every CLI test runs with a temporary folder as the working directory, so the
paths in messages are the short names given on the command line.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from click.testing import CliRunner

from jzr_tools.cli import app
from tests import pdf_fixtures as fx
from tests import test_startup as startup
from tests import text_pdf_fixtures as tx
from tests.text_pdf_fixtures import Line, Link

runner = CliRunner()

DAMAGED = "damaged or not a PDF"
ENCRYPTED = "encrypted PDFs are not supported; use an unencrypted copy"
UNSUPPORTED = "unsupported page size, scaling or rotation"
NO_TEXT = "no selectable text; the PDF may be scanned (there is no OCR)"
COMPLEX_NOTE = (
    "note: this PDF has tables, columns or rotated text; check the structure and reading order"
)
USAGE_TAIL = "use page numbers and ranges such as 1-3,5"

TWO_PAGES = [
    [Line("First page", 40, 100), Line("Second line", 40, 130)],
    [Line("Page two", 40, 100)],
]


def flat(text: str) -> str:
    return " ".join(text.split())


def names(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


class TextCase(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-pdf-text-")
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name).resolve()
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)

    def put(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def text(self, *args: str):  # noqa: ANN201
        return runner.invoke(app, ["pdf", "text", *args])

    def describe(self, result) -> str:  # noqa: ANN001
        return f"exit={result.exit_code}\nstdout={result.stdout_bytes[:300]!r}\nstderr={result.stderr!r}"

    def assert_ok(self, result) -> None:  # noqa: ANN001
        self.assertEqual(result.exit_code, 0, self.describe(result))
        self.assertNotIn("Traceback", result.stderr)

    def assert_usage_error(self, result, message: str) -> None:  # noqa: ANN001
        self.assertEqual(result.exit_code, 2, self.describe(result))
        self.assertEqual(result.stdout_bytes, b"", self.describe(result))
        self.assertIn(message, flat(result.stderr), self.describe(result))
        self.assertNotIn("Traceback", result.stderr)

    def assert_failed(self, result, message: str) -> None:  # noqa: ANN001
        self.assertEqual(result.exit_code, 1, self.describe(result))
        self.assertEqual(result.stdout_bytes, b"", self.describe(result))
        self.assertEqual(result.stderr.strip(), message, self.describe(result))

    def stderr_lines(self, result) -> list[str]:  # noqa: ANN001
        return result.stderr.splitlines()


# ---- help


class HelpTests(unittest.TestCase):
    def help_of(self, *args: str) -> str:
        result = runner.invoke(app, [*args, "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        return flat(result.stdout)

    def test_the_group_lists_text_with_its_short_help(self) -> None:
        self.assertIn("Print the text of a PDF, or convert it to Markdown.", self.help_of("pdf"))
        self.assertIn("Print the text of a PDF, or convert it to Markdown.", self.help_of("pdf", "text"))

    def test_the_command_help_lists_its_options_and_the_limits(self) -> None:
        text = self.help_of("pdf", "text")
        for option in ("--markdown", "--pages", "--page-breaks", "--output", "-o", "--overwrite"):
            with self.subTest(option=option):
                self.assertRegex(text, rf"(?<![\w-]){re.escape(option)}(?![\w-])")
        self.assertIn("OCR", text)
        self.assertIn("form feed", text)
        self.assertIn("check the result", text)


# ---- plain text


class PlainTextTests(TextCase):
    def test_pages_are_separated_by_a_form_feed_on_its_own_line(self) -> None:
        self.put("a.pdf", tx.build(TWO_PAGES))
        result = self.text("a.pdf")
        self.assert_ok(result)
        self.assertEqual(result.stdout_bytes, b"First page\nSecond line\n\f\nPage two\n")
        self.assertEqual(result.stderr, "")

    def test_pages_selects_pages_and_keeps_the_order_of_the_file(self) -> None:
        self.put("a.pdf", tx.build(TWO_PAGES))
        self.assertEqual(self.text("a.pdf", "--pages", "2").stdout_bytes, b"Page two\n")
        self.assertEqual(self.text("a.pdf", "--pages", "1").stdout_bytes, b"First page\nSecond line\n")
        self.assertEqual(
            self.text("a.pdf", "--pages", "1-2").stdout_bytes, self.text("a.pdf").stdout_bytes
        )

    def test_nothing_is_written_beside_the_file_and_the_file_is_not_changed(self) -> None:
        data = tx.build(TWO_PAGES)
        self.put("a.pdf", data)
        self.assert_ok(self.text("a.pdf"))
        self.assertEqual(names(self.root), ["a.pdf"])
        self.assertEqual((self.root / "a.pdf").read_bytes(), data)

    def test_output_is_utf_8_with_line_feeds_only_including_accents(self) -> None:
        self.put("a.pdf", tx.build([[Line("café déjà vu", 40, 100), Line("€ 5 – ok", 40, 130)]]))
        result = self.text("a.pdf")
        self.assert_ok(result)
        self.assertEqual(result.stdout_bytes, "café déjà vu\n€ 5 – ok\n".encode("utf-8"))
        self.assertNotIn(b"\r", result.stdout_bytes)

    def test_a_real_process_writes_the_same_bytes_with_no_carriage_returns(self) -> None:
        path = self.put("a.pdf", tx.build(TWO_PAGES))
        done = subprocess.run(
            [sys.executable, "-m", "jzr_tools", "pdf", "text", str(path)],
            cwd=startup.PROJECT_ROOT,
            env=startup._env(),
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, b"First page\nSecond line\n\f\nPage two\n")

    def test_spaces_at_the_end_of_a_line_are_removed(self) -> None:
        self.put("a.pdf", tx.build([[Line("Trailing   ", 40, 100), Line("Next line", 40, 130)]]))
        self.assertEqual(self.text("a.pdf").stdout_bytes, b"Trailing\nNext line\n")

    def test_pages_without_text_are_named_and_get_no_form_feed(self) -> None:
        pages = [
            [Line("One", 40, 100)],
            [],
            [Line("Three", 40, 100)],
            [],
            [Line("Five", 40, 100)],
        ]
        self.put("a.pdf", tx.build(pages))
        result = self.text("a.pdf")
        self.assert_ok(result)
        self.assertEqual(result.stdout_bytes, b"One\n\f\nThree\n\f\nFive\n")
        self.assertEqual(self.stderr_lines(result), ["note: pages without selectable text: 2, 4"])

    def test_rotated_text_is_read_and_gets_no_structure_note_in_plain_mode(self) -> None:
        self.put("a.pdf", tx.rotated_text())
        result = self.text("a.pdf")
        self.assert_ok(result)
        output = result.stdout_bytes.decode("utf-8")
        self.assertIn("Straight line of text", output)
        self.assertIn("Turned sideways here", output)
        self.assertEqual(result.stderr, "")

    def test_no_text_at_all_is_an_error_for_images_and_blank_pages(self) -> None:
        for name, data in (("image.pdf", fx.with_image(2)), ("blank.pdf", fx.blank([(200, 200)]))):
            for extra in ((), ("--markdown",)):
                with self.subTest(file=name, extra=extra):
                    self.put(name, data)
                    result = self.text(name, *extra)
                    self.assert_failed(result, f"{name}: {NO_TEXT}")

    def test_no_text_with_an_output_file_writes_nothing(self) -> None:
        self.put("image.pdf", fx.with_image(1))
        result = self.text("image.pdf", "-o", "out.txt")
        self.assert_failed(result, f"image.pdf: {NO_TEXT}")
        self.assertEqual(names(self.root), ["image.pdf"])

    def test_text_only_on_selected_pages_is_what_counts(self) -> None:
        self.put("a.pdf", tx.build([[Line("Only here", 40, 100)], []]))
        self.assertEqual(self.text("a.pdf", "--pages", "1").stdout_bytes, b"Only here\n")
        self.assert_failed(self.text("a.pdf", "--pages", "2"), f"a.pdf: {NO_TEXT}")


# ---- Markdown


class MarkdownTests(TextCase):
    def test_a_title_a_bold_label_a_list_and_a_link_are_recognised(self) -> None:
        body = "This opening paragraph has enough words to set the body size."
        lines = [
            Line("Title", 40, 80, 24, bold=True),
            Line(body, 40, 130),
            Line("Label:", 40, 170, bold=True),
            Line(" Value one", 80, 170),
            Line("• First item", 40, 210),
            Line("• Second item", 40, 226),
            Line("Example link", 40, 270),
        ]
        self.put("a.pdf", tx.build([lines], {0: [Link(38, 258, 140, 274, "https://example.com/page")]}))
        result = self.text("a.pdf", "--markdown")
        self.assert_ok(result)
        self.assertEqual(
            result.stdout_bytes.decode("utf-8"),
            f"# Title\n\n{body}\n\n**Label:** Value one\n\n"
            "- First item\n- Second item\n\n[Example link](https://example.com/page)\n",
        )
        self.assertEqual(result.stderr, "")

    def test_text_in_one_size_gets_no_headings(self) -> None:
        self.put("a.pdf", tx.build([[Line("Plain first line.", 40, 100), Line("Plain second line.", 40, 114)]]))
        result = self.text("a.pdf", "--markdown")
        self.assert_ok(result)
        self.assertEqual(result.stdout_bytes, b"Plain first line. Plain second line.\n")

    def test_a_link_to_an_unsafe_address_leaves_plain_text(self) -> None:
        self.put(
            "a.pdf",
            tx.build([[Line("Click here", 40, 100)]], {0: [Link(38, 88, 120, 104, "javascript:alert(1)")]}),
        )
        result = self.text("a.pdf", "--markdown")
        self.assert_ok(result)
        self.assertEqual(result.stdout_bytes, b"Click here\n")

    def test_markdown_syntax_in_the_source_text_is_escaped(self) -> None:
        self.put("a.pdf", tx.build([[Line("Use *stars* and [brackets] here", 40, 100)]]))
        result = self.text("a.pdf", "--markdown")
        self.assert_ok(result)
        self.assertEqual(result.stdout_bytes, b"Use \\*stars\\* and \\[brackets\\] here\n")

    def test_page_breaks_put_a_rule_between_pages(self) -> None:
        self.put("a.pdf", tx.build([[Line("Page one", 40, 100)], [Line("Page two", 40, 100)]]))
        with_breaks = self.text("a.pdf", "--markdown", "--page-breaks")
        without = self.text("a.pdf", "--markdown")
        self.assert_ok(with_breaks)
        self.assertEqual(with_breaks.stdout_bytes, b"Page one\n\n---\n\nPage two\n")
        self.assertEqual(without.stdout_bytes, b"Page one\n\nPage two\n")

    def test_pages_without_text_get_the_note_and_no_rule(self) -> None:
        self.put("a.pdf", tx.build([[Line("Page one", 40, 100)], [], [Line("Page three", 40, 100)]]))
        result = self.text("a.pdf", "--markdown", "--page-breaks")
        self.assert_ok(result)
        self.assertEqual(result.stdout_bytes, b"Page one\n\n---\n\nPage three\n")
        self.assertEqual(self.stderr_lines(result), ["note: pages without selectable text: 2"])

    def test_pages_limits_what_is_converted(self) -> None:
        self.put("a.pdf", tx.build([[Line("Page one", 40, 100)], [Line("Page two", 40, 100)]]))
        self.assertEqual(self.text("a.pdf", "--markdown", "--pages", "2").stdout_bytes, b"Page two\n")

    def test_a_table_with_a_bold_header_becomes_a_markdown_table(self) -> None:
        rows = [["Product", "Units", "State"], ["Adapter", "2", "Ready"], ["Cable", "4", "Pending"]]
        lines = [Line("A paragraph before the grid.", 40, 80)]
        for index, row in enumerate(rows):
            for column, cell in enumerate(row):
                lines.append(Line(cell, [40, 250, 400][column], 130 + index * 20, bold=index == 0))
        lines.append(Line("A paragraph after the grid.", 40, 220))
        self.put("a.pdf", tx.build([lines]))
        result = self.text("a.pdf", "--markdown")
        self.assert_ok(result)
        self.assertEqual(
            result.stdout_bytes.decode("utf-8"),
            "A paragraph before the grid.\n\n"
            "| **Product** | **Units** | **State** |\n| --- | --- | --- |\n"
            "| Adapter | 2 | Ready |\n| Cable | 4 | Pending |\n\n"
            "A paragraph after the grid.\n",
        )
        self.assertEqual(self.stderr_lines(result), [COMPLEX_NOTE])

    def test_two_columns_get_the_structure_note_and_are_read_one_after_the_other(self) -> None:
        lines = []
        for index in range(3):
            lines.append(Line(f"Left {index}", 40, 120 + index * 16))
            lines.append(Line(f"Right {index}", 340, 120 + index * 16))
        self.put("a.pdf", tx.build([lines]))
        result = self.text("a.pdf", "--markdown")
        self.assert_ok(result)
        output = result.stdout_bytes.decode("utf-8")
        self.assertLess(output.index("Left 2"), output.index("Right 0"))
        self.assertEqual(self.stderr_lines(result), [COMPLEX_NOTE])

    def test_rotated_text_gets_the_structure_note(self) -> None:
        self.put("a.pdf", tx.rotated_text())
        result = self.text("a.pdf", "--markdown")
        self.assert_ok(result)
        self.assertIn(COMPLEX_NOTE, self.stderr_lines(result))

    def test_a_page_stored_turned_reads_as_one_line_without_a_note(self) -> None:
        self.put("a.pdf", tx.sideways_page())
        result = self.text("a.pdf", "--markdown")
        self.assert_ok(result)
        self.assertEqual(result.stdout_bytes, b"Turned sideways here\n")
        self.assertEqual(result.stderr, "")

    def test_a_plain_page_gets_no_note(self) -> None:
        self.put("a.pdf", tx.build([[Line("Just a sentence.", 40, 100)]]))
        result = self.text("a.pdf", "--markdown")
        self.assertEqual(result.stderr, "")

    def test_markdown_output_is_utf_8_with_line_feeds_only(self) -> None:
        self.put("a.pdf", tx.build([[Line("café crème", 40, 100)]]))
        result = self.text("a.pdf", "--markdown")
        self.assert_ok(result)
        self.assertEqual(result.stdout_bytes, "café crème\n".encode("utf-8"))


# ---- output file


class OutputFileTests(TextCase):
    def setUp(self) -> None:
        super().setUp()
        self.put("a.pdf", tx.build(TWO_PAGES))

    def test_o_writes_the_text_and_reports_pages_and_characters_on_stderr(self) -> None:
        result = self.text("a.pdf", "-o", "out.txt")
        self.assert_ok(result)
        self.assertEqual(result.stdout_bytes, b"")
        written = (self.root / "out.txt").read_bytes()
        self.assertEqual(written, b"First page\nSecond line\n\f\nPage two\n")
        lines = self.stderr_lines(result)
        self.assertEqual(len(lines), 1, lines)
        found = re.fullmatch(r"wrote out\.txt \((\d+) pages, (\d+) characters\)", lines[0])
        self.assertIsNotNone(found, lines[0])
        self.assertEqual(found[1], "2")
        self.assertEqual(int(found[2]), len(written.decode("utf-8")))

    def test_o_works_with_markdown_and_with_any_extension(self) -> None:
        for name in ("notes.md", "notes.txt", "notes.data", "notes"):
            with self.subTest(name=name):
                result = self.text("a.pdf", "--markdown", "-o", name)
                self.assert_ok(result)
                self.assertEqual((self.root / name).read_bytes(), b"First page\n\nSecond line\n\nPage two\n")
                self.assertRegex(result.stderr, rf"^wrote {re.escape(name)} \(2 pages, \d+ characters\)")

    def test_notes_follow_the_wrote_line(self) -> None:
        self.put("gap.pdf", tx.build([[Line("One", 40, 100)], [], [Line("Three", 40, 100)]]))
        result = self.text("gap.pdf", "-o", "out.txt")
        self.assert_ok(result)
        lines = self.stderr_lines(result)
        self.assertEqual(len(lines), 2, lines)
        self.assertRegex(lines[0], r"^wrote out\.txt \(\d+ pages?, \d+ characters\)$")
        self.assertEqual(lines[1], "note: pages without selectable text: 2")

    def test_an_existing_output_is_refused_and_left_alone(self) -> None:
        existing = self.put("out.txt", b"keep me")
        result = self.text("a.pdf", "-o", "out.txt")
        self.assert_usage_error(result, "out.txt exists; add --overwrite to replace it")
        self.assertEqual(existing.read_bytes(), b"keep me")

    def test_overwrite_replaces_it(self) -> None:
        self.put("out.txt", b"old")
        result = self.text("a.pdf", "-o", "out.txt", "--overwrite")
        self.assert_ok(result)
        self.assertEqual((self.root / "out.txt").read_bytes(), b"First page\nSecond line\n\f\nPage two\n")
        self.assertRegex(self.stderr_lines(result)[0], r"^wrote out\.txt \(")

    def test_the_output_may_not_be_the_input_under_any_spelling(self) -> None:
        before = (self.root / "a.pdf").read_bytes()
        for spelling in ("a.pdf", str(self.root / "a.pdf"), "./a.pdf"):
            for extra in ((), ("--overwrite",)):
                with self.subTest(spelling=spelling, extra=extra):
                    result = self.text("a.pdf", "-o", spelling, *extra)
                    self.assertEqual(result.exit_code, 2, self.describe(result))
                    self.assertIn("the output would replace a.pdf; give another -o", flat(result.stderr))
                    self.assertEqual((self.root / "a.pdf").read_bytes(), before)

    def test_no_temporary_files_are_left_behind(self) -> None:
        self.assert_ok(self.text("a.pdf", "-o", "out.txt"))
        self.assertEqual(names(self.root), ["a.pdf", "out.txt"])

    def test_the_output_may_be_in_another_folder(self) -> None:
        (self.root / "sub").mkdir()
        result = self.text("a.pdf", "-o", str(Path("sub") / "out.txt"))
        self.assert_ok(result)
        self.assertEqual((self.root / "sub" / "out.txt").read_bytes(), b"First page\nSecond line\n\f\nPage two\n")


# ---- arguments and bad files


class ArgumentTests(TextCase):
    def test_page_breaks_need_markdown(self) -> None:
        self.put("a.pdf", tx.build(TWO_PAGES))
        result = self.text("a.pdf", "--page-breaks")
        self.assert_usage_error(result, "--page-breaks only applies to --markdown.")
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_bad_page_lists_are_usage_errors_naming_the_option(self) -> None:
        self.put("a.pdf", tx.build(TWO_PAGES))
        for pages in ("9", "0", "1-9", "2-1", "1,,2", "a", "1;2", "1,1"):
            with self.subTest(pages=pages):
                result = self.text("a.pdf", "--pages", pages)
                self.assert_usage_error(result, f"--pages: {USAGE_TAIL} (pages 1 to 2)")
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_a_missing_file_or_no_file_is_a_usage_error(self) -> None:
        missing = self.text("nope.pdf")
        self.assertEqual(missing.exit_code, 2, self.describe(missing))
        self.assertIn("nope.pdf", missing.stderr)
        none = self.text()
        self.assertEqual(none.exit_code, 2, self.describe(none))
        self.assertIn("FILE", none.stderr)

    def test_an_unknown_option_is_a_usage_error(self) -> None:
        self.put("a.pdf", tx.build(TWO_PAGES))
        result = self.text("a.pdf", "--bogus")
        self.assert_usage_error(result, "No such option: --bogus")


class BadFileTests(TextCase):
    def test_each_unusable_file_fails_with_the_shared_text(self) -> None:
        cases = {
            "damaged.pdf": (fx.truncated(tx.build(TWO_PAGES)), DAMAGED),
            "plain.pdf": (fx.not_a_pdf(), DAMAGED),
            "locked.pdf": (fx.encrypted(2), ENCRYPTED),
            "huge.pdf": (fx.huge_media_box(), UNSUPPORTED),
        }
        for name, (data, reason) in cases.items():
            for extra in ((), ("--markdown",), ("-o", "out.txt")):
                with self.subTest(file=name, extra=extra):
                    self.put(name, data)
                    result = self.text(name, *extra)
                    self.assert_failed(result, f"{name}: {reason}")
        self.assertNotIn("out.txt", names(self.root))


# ---- the limit for Markdown

_HUGE: dict[str, bytes] = {}


class MuchTextTests(TextCase):
    @classmethod
    def setUpClass(cls) -> None:
        _HUGE["data"] = tx.huge_text()

    def test_markdown_stops_at_five_million_characters_and_pages_gets_round_it(self) -> None:
        self.put("big.pdf", _HUGE["data"])
        result = self.text("big.pdf", "--markdown")
        self.assertEqual(result.exit_code, 1, self.describe(result))
        self.assertEqual(result.stdout_bytes, b"")
        self.assertRegex(
            result.stderr.strip(),
            r"^big\.pdf: too much text for --markdown \(\d[\d,.]* characters\); "
            r"use --pages to convert part of it$",
        )
        self.assertEqual(names(self.root), ["big.pdf"])

        part = self.text("big.pdf", "--markdown", "--pages", "2")
        self.assert_ok(part)
        self.assertEqual(part.stdout_bytes, b"Short page\n")

    def test_the_limit_does_not_apply_to_plain_text(self) -> None:
        self.put("big.pdf", _HUGE["data"])
        result = self.text("big.pdf")
        self.assert_ok(result)
        self.assertGreater(len(result.stdout_bytes), 5_000_000)
        self.assertTrue(result.stdout_bytes.endswith(b"\n\f\nShort page\n"))

    def test_a_blocked_markdown_run_with_an_output_file_writes_nothing(self) -> None:
        self.put("big.pdf", _HUGE["data"])
        result = self.text("big.pdf", "--markdown", "-o", "out.md")
        self.assertEqual(result.exit_code, 1, self.describe(result))
        self.assertNotIn("out.md", names(self.root))


# ---- the reader behind the command


def runs_of(data: bytes, page: int = 0):  # noqa: ANN201
    """The runs of one page, read with pdfium. The text reader may return more than the runs."""
    import pypdfium2 as pdfium

    from jzr_tools.core import pdf_text

    document = pdfium.PdfDocument(data)
    try:
        found = pdf_text.text_runs(document[page])
    finally:
        document.close()
    runs = found[0] if isinstance(found, tuple) else found
    return list(runs)


class TextRunTests(unittest.TestCase):
    def test_text_set_at_12_under_a_double_scale_is_24_points(self) -> None:
        runs = runs_of(tx.scaled_text())
        self.assertEqual("".join(r.text for r in runs).strip(), "Scaled words")
        for item in runs:
            self.assertAlmostEqual(item.size, 24, delta=0.01)

    def test_a_run_is_a_word_and_a_space_ends_it_and_stays_on_it(self) -> None:
        runs = runs_of(tx.build([[Line("Hello world", 72, 100)]]))
        self.assertEqual([r.text for r in runs], ["Hello ", "world"])

    def test_position_is_the_baseline_measured_down_from_the_top_of_the_page(self) -> None:
        runs = runs_of(tx.build([[Line("Hello", 72, 100), Line("Lower", 72, 160)]]))
        self.assertEqual([r.text for r in runs], ["Hello", "Lower"])
        self.assertAlmostEqual(runs[0].x, 72, delta=0.5)
        self.assertAlmostEqual(runs[0].y, 100, delta=0.5)
        self.assertAlmostEqual(runs[1].y, 160, delta=0.5)

    def test_width_runs_from_the_first_origin_to_the_end_of_the_last_glyph(self) -> None:
        run = runs_of(tx.build([[Line("Hello", 72, 100)]]))[0]
        # Helvetica advances: H 722, e 556, l 222, l 222, o 556 of 1000 em, at 12 points.
        self.assertAlmostEqual(run.width, 27.3, delta=1.5)
        self.assertAlmostEqual(run.size, 12, delta=0.01)

    def test_the_crop_box_is_the_origin_of_the_coordinates(self) -> None:
        data = tx.cropped_text((50, 100, 562, 742), 100, 600, "Inside")
        run = runs_of(data)[0]
        self.assertAlmostEqual(run.x, 100 - 50, delta=0.5)
        self.assertAlmostEqual(run.y, 742 - 600, delta=0.5)

    def test_a_page_stored_turned_is_read_as_it_is_shown(self) -> None:
        runs = runs_of(tx.sideways_page())
        self.assertEqual([r.text for r in runs], ["Turned ", "sideways ", "here"])
        self.assertAlmostEqual(runs[0].x, 200, delta=0.5)
        for item in runs:
            self.assertAlmostEqual(item.y, 300, delta=0.5)
            self.assertAlmostEqual(item.size, 12, delta=0.01)
        # The words follow each other: each starts where the one before ends.
        self.assertAlmostEqual(runs[1].x, runs[0].x + runs[0].width, delta=0.5)

    def test_bold_comes_from_the_font_name(self) -> None:
        runs = runs_of(tx.build([[Line("Heading", 40, 100, bold=True), Line("Normal", 40, 140)]]))
        by_text = {r.text: r for r in runs}
        self.assertTrue(by_text["Heading"].bold)
        self.assertFalse(by_text["Normal"].bold)

    def test_a_change_of_font_starts_a_new_run(self) -> None:
        content = b"BT /F1 12 Tf 40 700 Td (Plain) Tj /F2 12 Tf (Bold) Tj ET\n"
        runs = runs_of(tx.raw(content))
        self.assertEqual([(r.text, r.bold) for r in runs], [("Plain", False), ("Bold", True)])

    def test_a_link_gives_its_address_to_the_runs_it_covers_and_only_those(self) -> None:
        data = tx.build(
            [[Line("Visit", 40, 100), Line("Example site", 100, 100), Line("Elsewhere", 40, 200)]],
            {0: [Link(98, 88, 200, 104, "https://example.com/page")]},
        )
        found = {r.text.strip(): r.url for r in runs_of(data)}
        self.assertEqual(found["Example"], "https://example.com/page")
        self.assertEqual(found["site"], "https://example.com/page")
        self.assertIsNone(found["Visit"])
        self.assertIsNone(found["Elsewhere"])

    def test_text_in_a_plain_page_is_not_given_an_address(self) -> None:
        runs = runs_of(tx.build([[Line("See https://example.com/page now", 40, 100)]]))
        self.assertTrue(runs)
        for item in runs:
            self.assertIsNone(item.url)

    def test_line_breaks_made_up_by_the_reader_are_not_in_the_runs(self) -> None:
        runs = runs_of(tx.build([[Line("Line one", 40, 100), Line("Line two", 40, 140)]]))
        self.assertEqual([r.text for r in runs], ["Line ", "one", "Line ", "two"])
        for item in runs:
            self.assertNotIn("\n", item.text)
            self.assertNotIn("\r", item.text)

    def test_white_space_only_runs_are_dropped(self) -> None:
        runs = runs_of(tx.with_spaces_only())
        self.assertTrue(runs)
        for item in runs:
            self.assertTrue(item.text.strip(), repr(item.text))
        self.assertIn("Word", [r.text.strip() for r in runs])

    def test_accented_letters_come_through_unchanged(self) -> None:
        runs = runs_of(tx.build([[Line("café", 40, 100)]]))
        self.assertEqual("".join(r.text for r in runs), "café")


if __name__ == "__main__":
    unittest.main()
