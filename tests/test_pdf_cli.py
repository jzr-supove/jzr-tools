"""Tests of ``jzr pdf info``, ``merge``, ``split`` and ``organize`` through the real entry point.

Every test runs with a temporary folder as the working directory, so the paths
printed in result lines are the short names given on the command line. Outputs
are checked by reading them back with pypdf.
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
from pypdf import PdfReader

from jzr_tools.cli import app
from jzr_tools.core.media import format_size
from tests import pdf_fixtures as fx
from tests.size_fixtures import with_size

runner = CliRunner()

DAMAGED = "damaged or not a PDF"
ENCRYPTED = "encrypted PDFs are not supported; use an unencrypted copy"
UNSUPPORTED = "unsupported page size, scaling or rotation"
USAGE_TAIL = "use page numbers and ranges such as 1-3,5"

A4 = (595.276, 841.89)
LETTER = (612.0, 792.0)

NOTE_TEXTS = {
    "structure": "bookmarks, page labels or accessibility tags may be lost or changed",
    "annotations": "comments, annotations or internal links may be lost or changed",
    "opening-view": "the preferred opening page and zoom are not kept",
    "links-removed": "malformed web or email links are disabled; the page content is kept",
    "forms": "form fields and their values may be lost or changed",
    "signatures": "digital signatures will not validate in the new PDF",
    "active-content": "scripts and interactive actions may be kept, lost or changed; "
    "jzr does not run or remove them",
    "attachments": "attachments and embedded media may be lost or changed",
}


def pdf(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["pdf", *args])


def flat(text: str) -> str:
    return " ".join(text.split())


def names(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


def stdout_lines(result) -> list[str]:  # noqa: ANN001
    return result.stdout.splitlines()


def stderr_lines(result) -> list[str]:  # noqa: ANN001
    return result.stderr.splitlines()


def size_text(path: str | Path) -> str:
    return format_size(os.path.getsize(path))


class PdfCase(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-pdf-cli-")
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

    def markers_of(self, name: str) -> list[int]:
        return fx.markers((self.root / name).read_bytes())

    def rotations_of(self, name: str) -> list[int]:
        return fx.rotations((self.root / name).read_bytes())


# ---- help and registration


class HelpTests(unittest.TestCase):
    def help_of(self, *args: str) -> str:
        result = runner.invoke(app, [*args, "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        return flat(result.stdout)

    def test_the_root_help_lists_pdf_with_its_help(self) -> None:
        result = runner.invoke(app, ["-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(
            result.stdout, r"(?m)^  pdf\s+Merge, split, compress, render and read PDF files\."
        )

    def test_the_group_lists_its_four_commands(self) -> None:
        result = runner.invoke(app, ["pdf", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        listed = re.findall(
            r"^  (\S+)", result.stdout.split("Commands:", 1)[1], flags=re.MULTILINE
        )
        for name in ("info", "merge", "split", "organize"):
            self.assertIn(name, listed)

    def test_each_command_has_its_help_text(self) -> None:
        expected = {
            "info": "Show the pages, sizes, metadata and features of PDF files.",
            "merge": "Join PDF files into one, in the order given.",
            "split": "Split a PDF into several: every page, every N pages, or groups.",
            "organize": "Reorder, rotate or remove the pages of a PDF.",
        }
        for command, text in expected.items():
            with self.subTest(command=command):
                self.assertIn(text, self.help_of("pdf", command))

    def test_the_group_listing_shows_the_short_help_of_each_command(self) -> None:
        result = runner.invoke(app, ["pdf", "-h"])
        listing = flat(result.stdout)
        self.assertIn("Join PDF files into one, in the order given.", listing)
        self.assertIn("Reorder, rotate or remove the pages of a PDF.", listing)

    def test_every_command_says_sources_are_never_changed_and_notes_name_features(self) -> None:
        for command in ("merge", "split", "organize"):
            with self.subTest(command=command):
                text = self.help_of("pdf", command)
                self.assertIn("never changed", text)
                self.assertIn("notes", text)

    def test_option_help_texts(self) -> None:
        # The examples with "last" are checked in test_the_page_options_give_a_last_example.
        split = self.help_of("pdf", "split")
        self.assertIn("--every N", split)
        self.assertIn("Put N pages in each file.", split)
        self.assertIn('Groups of pages, such as "1-3;4,6;7-"', split)
        self.assertIn("(one file per group)", split)
        organize = self.help_of("pdf", "organize")
        self.assertIn("Pages in their new order, such as 3,1,2 or 12-1", organize)
        self.assertIn("pages not listed are dropped.", organize)
        self.assertIn("Pages to remove, such as 2,5-7", organize)
        self.assertIn(
            "Turn pages clockwise: 90, 180 or 270, or PAGES:ANGLE such as 2-4:90", organize
        )
        self.assertIn("Repeatable.", organize)

    def test_the_page_options_give_a_last_example(self) -> None:
        split = self.help_of("pdf", "split").split("Options:")[1]
        organize = self.help_of("pdf", "organize").split("Options:")[1]
        sections = {
            "--pages": split[split.index("--pages"):split.index("--outdir")],
            "--order": organize[organize.index("--order"):organize.index("--delete")],
            "--delete": organize[organize.index("--delete"):organize.index("--rotate")],
            "--rotate": organize[organize.index("--rotate"):organize.index("--output")],
        }
        for option, text in sections.items():
            with self.subTest(option=option):
                self.assertRegex(text, r"\blast\b")
        self.assertIn("such as 2,5-7 or last", sections["--delete"])

    def test_every_command_lists_its_options(self) -> None:
        wanted = {
            "info": ["--json"],
            "merge": ["--output", "-o", "--overwrite", "--dry-run"],
            "split": ["--every", "--pages", "--outdir", "--overwrite", "--dry-run"],
            "organize": ["--order", "--delete", "--rotate", "--output", "--overwrite", "--dry-run"],
        }
        for command, options in wanted.items():
            text = self.help_of("pdf", command)
            for option in options:
                with self.subTest(command=command, option=option):
                    self.assertRegex(text, rf"(?<![\w-]){re.escape(option)}(?![\w-])")


# ---- info


class InfoTests(PdfCase):
    def info(self, *args: str):  # noqa: ANN201
        return pdf("info", *args)

    def rows(self, result) -> dict[str, str]:  # noqa: ANN001
        parsed: dict[str, str] = {}
        for line in stdout_lines(result)[1:]:
            match = re.match(r"^  (\S.*?)  +(\S.*)$", line)
            if match:
                parsed.setdefault(match[1], match[2])
        return parsed

    def labels(self, result) -> list[str]:  # noqa: ANN001
        return [
            m[1] for line in stdout_lines(result)[1:] if (m := re.match(r"^  (\S.*?)  +\S", line))
        ]

    def test_a_plain_file_prints_a_block_with_padded_rows(self) -> None:
        data = fx.blank([A4])
        self.put("a.pdf", data)
        version = re.match(rb"%PDF-(\d\.\d)", data)[1].decode()
        result = self.info("a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr, "")
        lines = stdout_lines(result)
        self.assertEqual(lines[0], "a.pdf")
        self.assertIn(f"  version    {version}", lines)
        self.assertIn("  pages      1", lines)
        self.assertIn("  page size  A4 portrait (210 x 297 mm)", lines)
        self.assertIn(f"  file size  {size_text('a.pdf')}", lines)
        columns = {re.match(r"^  (.+?)  +(\S.*)$", row).start(2) for row in lines[1:]}
        self.assertEqual(columns, {2 + len("page size") + 2})

    def test_rows_come_in_the_documented_order(self) -> None:
        data = fx.with_metadata(Title="T", Author="A", Subject="S", Creator="C", Producer="P")
        self.put("a.pdf", data)
        result = self.info("a.pdf")
        order = [
            "version", "pages", "page size", "title", "author", "subject", "creator",
            "producer", "text", "images", "file size",
        ]
        labels = [label for label in self.labels(result) if label in order]
        self.assertEqual(labels, [label for label in order if label in labels])
        for label in ("version", "pages", "page size", "title", "author", "file size"):
            self.assertIn(label, labels)

    def test_metadata_rows(self) -> None:
        self.put(
            "a.pdf",
            fx.with_metadata(
                Title="Annual report", Author="Ada Lovelace", Subject="Numbers",
                Creator="Writer", Producer="Engine 1",
            ),
        )
        rows = self.rows(self.info("a.pdf"))
        self.assertEqual(rows["title"], "Annual report")
        self.assertEqual(rows["author"], "Ada Lovelace")
        self.assertEqual(rows["subject"], "Numbers")
        self.assertEqual(rows["creator"], "Writer")
        self.assertEqual(rows["producer"], "Engine 1")

    def test_pdf_dates_are_shown_with_their_offset(self) -> None:
        self.put(
            "a.pdf",
            fx.with_metadata(CreationDate="D:20260102100000+05'00'", ModDate="D:20260305230000+01'00'"),
        )
        rows = self.rows(self.info("a.pdf"))
        self.assertEqual(rows["created"], "2026-01-02 10:00:00 +05:00")
        self.assertEqual(rows["modified"], "2026-03-05 23:00:00 +01:00")

    def test_rows_without_a_value_are_left_out(self) -> None:
        self.put("a.pdf", fx.blank([A4]))
        result = self.info("a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        labels = self.labels(result)
        self.assertIn("pages", labels)
        for label in ("title", "author", "subject", "created", "modified", "rotated", "features"):
            self.assertNotIn(label, labels)

    def test_named_paper_sizes_and_orientation(self) -> None:
        cases = {
            "A4 portrait (210 x 297 mm)": A4,
            "A4 landscape (297 x 210 mm)": (A4[1], A4[0]),
            "Letter portrait (216 x 279 mm)": LETTER,
        }
        for text, size in cases.items():
            with self.subTest(text=text):
                self.put("p.pdf", fx.blank([size]))
                self.assertEqual(self.rows(self.info("p.pdf"))["page size"], text)

    def test_an_unnamed_size_is_given_in_whole_millimetres(self) -> None:
        self.put("a.pdf", fx.blank([(300, 400)]))
        self.assertEqual(self.rows(self.info("a.pdf"))["page size"], "106 x 141 mm")

    def test_several_sizes_get_one_row_each_with_their_pages(self) -> None:
        self.put("a.pdf", fx.blank([A4, A4, LETTER]))
        result = self.info("a.pdf")
        self.assertIn("A4 portrait (210 x 297 mm): 1-2", result.stdout)
        self.assertIn("Letter portrait (216 x 279 mm): 3", result.stdout)
        self.assertEqual(self.rows(result)["pages"], "3")

    def test_unnamed_sizes_list_their_pages(self) -> None:
        self.put("a.pdf", fx.marked(3))
        out = self.info("a.pdf").stdout
        for text in ("39 x 71 mm: 1", "42 x 71 mm: 2", "46 x 71 mm: 3"):
            self.assertIn(text, out)

    def test_rotated_pages_are_grouped_by_angle(self) -> None:
        self.put("a.pdf", fx.blank([A4] * 8, rotate={3: 90, 5: 90, 7: 180}))
        self.assertEqual(self.rows(self.info("a.pdf"))["rotated"], "3, 5 (90°); 7 (180°)")

    def test_text_row_counts_pages_with_text(self) -> None:
        self.put("both.pdf", fx.text_pdf(["one", "two"]))
        self.put("one.pdf", fx.text_pdf(["hello", " "]))
        self.put("none.pdf", fx.marked(2))
        self.assertEqual(self.rows(self.info("both.pdf"))["text"], "on 2 of 2 pages")
        self.assertEqual(self.rows(self.info("one.pdf"))["text"], "on 1 of 2 pages")
        self.assertEqual(self.rows(self.info("none.pdf"))["text"], "none (scanned?)")

    def test_images_row_counts_distinct_images(self) -> None:
        self.put("a.pdf", fx.with_image(3))
        self.assertEqual(self.rows(self.info("a.pdf"))["images"], "1")

    def test_features_are_listed_by_name_without_their_sentences(self) -> None:
        self.put("a.pdf", fx.with_signature())
        result = self.info("a.pdf")
        self.assertRegex(result.stdout, r"(?m)^  features\s+.*\bsignatures\b")
        self.assertNotIn("will not validate", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_a_file_with_forms_and_outlines_lists_every_code_found(self) -> None:
        self.put("form.pdf", fx.with_form_field())
        self.put("outline.pdf", fx.with_outline())
        self.assertRegex(self.info("form.pdf").stdout, r"(?m)^  features\s+.*\bforms\b")
        self.assertRegex(self.info("outline.pdf").stdout, r"(?m)^  features\s+structure$")

    def test_an_encrypted_file_gets_two_rows_and_does_not_fail(self) -> None:
        self.put("locked.pdf", fx.encrypted())
        result = self.info("locked.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            ["locked.pdf", "  encrypted  yes", f"  file size  {size_text('locked.pdf')}"],
        )
        self.assertEqual(result.stderr, "")

    def test_an_unsupported_page_is_reported_next_to_the_normal_rows(self) -> None:
        self.put("odd.pdf", fx.rotate(45))
        result = self.info("odd.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        rows = self.rows(result)
        self.assertEqual(rows["pages"], "1")
        self.assertEqual(rows["problem"], UNSUPPORTED)

    def test_a_damaged_file_fails_alone_with_exit_one(self) -> None:
        self.put("bad.pdf", fx.truncated())
        self.put("good.pdf", fx.blank([A4]))
        result = self.info("bad.pdf", "good.pdf")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr.strip(), f"fail bad.pdf: {DAMAGED}")
        self.assertEqual(stdout_lines(result)[0], "good.pdf")
        self.assertNotIn("bad.pdf", result.stdout)
        self.assertNotIn("Traceback", result.output)

    def test_a_text_file_is_damaged(self) -> None:
        self.put("note.pdf", fx.not_a_pdf())
        result = self.info("note.pdf")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr.strip(), f"fail note.pdf: {DAMAGED}")
        self.assertEqual(result.stdout.strip(), "")

    def test_files_are_separated_by_one_blank_line(self) -> None:
        self.put("a.pdf", fx.blank([A4]))
        self.put("b.pdf", fx.blank([LETTER]))
        lines = self.info("a.pdf", "b.pdf").stdout.split("\n")
        self.assertEqual(lines[0], "a.pdf")
        blank = lines.index("")
        self.assertEqual(lines[blank + 1], "b.pdf")
        self.assertEqual(lines.count(""), 2)
        self.assertEqual(lines[-1], "")

    def test_a_missing_file_or_no_file_is_a_usage_error(self) -> None:
        missing = self.info("nope.pdf")
        self.assertEqual(missing.exit_code, 2)
        self.assertIn("nope.pdf", missing.stderr)
        self.assertEqual(self.info().exit_code, 2)
        self.assertIn("FILES", self.info().stderr)

    def test_the_file_is_not_changed(self) -> None:
        data = fx.with_outline()
        path = self.put("a.pdf", data)
        self.assertEqual(self.info("a.pdf").exit_code, 0)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(names(self.root), ["a.pdf"])

    # ---- json

    JSON_KEYS = {
        "file", "version", "pages", "page_sizes", "rotated", "title", "author", "subject",
        "creator", "producer", "created", "modified", "text_pages", "images", "features",
        "file_size", "encrypted", "problem",
    }

    def test_json_is_one_indented_array_with_the_documented_keys(self) -> None:
        path = self.put("a.pdf", fx.blank([A4, A4, LETTER], rotate={2: 90}))
        result = self.info("--json", "a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(result.stdout.startswith('[\n  {\n    "file"'))
        data = json.loads(result.stdout)
        self.assertEqual(len(data), 1)
        entry = data[0]
        self.assertEqual(set(entry), self.JSON_KEYS)
        self.assertEqual(entry["file"], "a.pdf")
        self.assertEqual(entry["pages"], 3)
        self.assertEqual(entry["file_size"], os.path.getsize(path))
        self.assertIs(entry["encrypted"], False)
        self.assertIsNone(entry["problem"])
        self.assertEqual(entry["rotated"], {"90": [2]})
        sizes = entry["page_sizes"]
        self.assertEqual(len(sizes), 2)
        self.assertEqual(sizes[0]["paper"], "A4")
        self.assertEqual(sizes[0]["orientation"], "portrait")
        self.assertEqual(sizes[0]["pages"], [1, 2])
        self.assertAlmostEqual(sizes[0]["width_mm"], 210, delta=0.5)
        self.assertAlmostEqual(sizes[0]["height_mm"], 297, delta=0.5)
        self.assertEqual(sizes[1]["paper"], "Letter")
        self.assertEqual(sizes[1]["pages"], [3])

    def test_json_metadata_text_images_and_features(self) -> None:
        self.put("a.pdf", fx.with_metadata(Title="Report", Author="Ada"))
        self.put("t.pdf", fx.text_pdf(["one", "two"]))
        self.put("i.pdf", fx.with_image(2))
        self.put("s.pdf", fx.with_signature())
        meta = json.loads(self.info("--json", "a.pdf").stdout)[0]
        self.assertEqual((meta["title"], meta["author"]), ("Report", "Ada"))
        self.assertIsNone(meta["subject"])
        self.assertEqual(meta["features"], [])
        self.assertEqual(json.loads(self.info("--json", "t.pdf").stdout)[0]["text_pages"], 2)
        self.assertEqual(json.loads(self.info("--json", "i.pdf").stdout)[0]["images"], 1)
        self.assertIn("signatures", json.loads(self.info("--json", "s.pdf").stdout)[0]["features"])

    def test_json_for_an_encrypted_and_an_unsupported_file(self) -> None:
        self.put("locked.pdf", fx.encrypted())
        self.put("odd.pdf", fx.rotate(45))
        data = json.loads(self.info("--json", "locked.pdf", "odd.pdf").stdout)
        self.assertEqual([entry["file"] for entry in data], ["locked.pdf", "odd.pdf"])
        self.assertIs(data[0]["encrypted"], True)
        self.assertIsNone(data[0]["pages"])
        self.assertEqual(data[1]["problem"], UNSUPPORTED)
        self.assertEqual(data[1]["pages"], 1)

    def test_json_with_a_failing_file_still_lists_the_others(self) -> None:
        self.put("bad.pdf", fx.truncated())
        self.put("good.pdf", fx.blank([A4]))
        result = self.info("--json", "bad.pdf", "good.pdf")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual([entry["file"] for entry in json.loads(result.stdout)], ["good.pdf"])
        self.assertEqual(result.stderr.strip(), f"fail bad.pdf: {DAMAGED}")


# ---- merge


class MergeTests(PdfCase):
    def test_fewer_than_two_files_is_a_usage_error(self) -> None:
        self.put("a.pdf", fx.marked(2))
        for args in (["a.pdf"], []):
            with self.subTest(args=args):
                result = pdf("merge", *args)
                self.assertEqual(result.exit_code, 2, result.output)
                if args:
                    self.assertIn("give at least two PDFs to merge", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_three_files_are_joined_in_the_order_given(self) -> None:
        self.put("a.pdf", fx.marked(2))
        self.put("b.pdf", fx.marked(3, first=11))
        self.put("c.pdf", fx.marked(1, first=21))
        result = pdf("merge", "a.pdf", "c.pdf", "b.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_merged.pdf"), [1, 2, 21, 11, 12, 13])
        self.assertEqual(
            stdout_lines(result),
            [
                f"a.pdf + 2 more -> a_merged.pdf (6 pages, {size_text('a_merged.pdf')})",
                "pdf: 1 merged",
            ],
        )
        self.assertEqual(result.stderr, "")
        self.assertEqual(names(self.root), ["a.pdf", "a_merged.pdf", "b.pdf", "c.pdf"])

    def test_two_files_read_one_more(self) -> None:
        self.put("a.pdf", fx.marked(1))
        self.put("b.pdf", fx.marked(1))
        result = pdf("merge", "a.pdf", "b.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result)[0],
            f"a.pdf + 1 more -> a_merged.pdf (2 pages, {size_text('a_merged.pdf')})",
        )

    def test_the_output_is_named_after_the_first_file_in_its_folder(self) -> None:
        self.put("sub/first.pdf", fx.marked(1))
        self.put("other.pdf", fx.marked(1, first=5))
        result = pdf("merge", str(Path("sub") / "first.pdf"), "other.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "sub"), ["first.pdf", "first_merged.pdf"])
        self.assertIn("first_merged.pdf", stdout_lines(result)[0])

    def test_output_option_names_the_result(self) -> None:
        self.put("a.pdf", fx.marked(1))
        self.put("b.pdf", fx.marked(2, first=5))
        result = pdf("merge", "a.pdf", "b.pdf", "-o", "all.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("all.pdf"), [1, 5, 6])
        self.assertIn("-> all.pdf (3 pages,", stdout_lines(result)[0])
        self.assertEqual(names(self.root), ["a.pdf", "all.pdf", "b.pdf"])

    def test_output_without_the_pdf_extension_is_a_usage_error(self) -> None:
        self.put("a.pdf", fx.marked(1))
        self.put("b.pdf", fx.marked(1))
        result = pdf("merge", "a.pdf", "b.pdf", "-o", "all.txt")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertRegex(result.stderr, r"--output (all\.txt|FILE) does not end in \.pdf\.")
        self.assertEqual(names(self.root), ["a.pdf", "b.pdf"])

    def test_an_existing_output_is_skipped_unless_overwrite_is_given(self) -> None:
        self.put("a.pdf", fx.marked(1))
        self.put("b.pdf", fx.marked(1))
        old = self.put("a_merged.pdf", b"old content")
        result = pdf("merge", "a.pdf", "b.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("skip a_merged.pdf: target exists (use --overwrite)", stderr_lines(result))
        self.assertEqual(old.read_bytes(), b"old content")
        self.assertNotIn("->", result.stdout)

        result = pdf("merge", "a.pdf", "b.pdf", "--overwrite")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_merged.pdf"), [1, 1])
        self.assertEqual(stdout_lines(result)[-1], "pdf: 1 merged")

    def test_a_dry_run_says_what_would_happen_and_writes_nothing(self) -> None:
        self.put("a.pdf", fx.marked(2))
        self.put("b.pdf", fx.marked(3))
        result = pdf("merge", "a.pdf", "b.pdf", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result), ["a.pdf + 1 more -> a_merged.pdf (5 pages)", "pdf: 1 planned"]
        )
        self.assertEqual(names(self.root), ["a.pdf", "b.pdf"])

    def test_a_bad_source_stops_the_merge_and_writes_nothing(self) -> None:
        self.put("a.pdf", fx.marked(1))
        self.put("b.pdf", fx.truncated())
        self.put("c.pdf", fx.encrypted())
        self.put("d.pdf", fx.rotate(45))
        result = pdf("merge", "a.pdf", "b.pdf", "c.pdf", "d.pdf")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(f"fail b.pdf: {DAMAGED}", stderr_lines(result))
        self.assertIn(f"fail c.pdf: {ENCRYPTED}", stderr_lines(result))
        self.assertIn(f"fail d.pdf: {UNSUPPORTED}", stderr_lines(result))
        self.assertNotIn("a.pdf:", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf", "b.pdf", "c.pdf", "d.pdf"])
        self.assertNotIn("merged", result.stdout)

    def test_a_bad_source_is_a_failure_even_in_a_dry_run(self) -> None:
        self.put("a.pdf", fx.marked(1))
        self.put("b.pdf", fx.not_a_pdf())
        result = pdf("merge", "a.pdf", "b.pdf", "--dry-run")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(f"fail b.pdf: {DAMAGED}", stderr_lines(result))

    def test_sources_are_never_changed(self) -> None:
        first, second = fx.with_outline(), fx.marked(2, rotate={1: 90})
        a, b = self.put("a.pdf", first), self.put("b.pdf", second)
        self.assertEqual(pdf("merge", "a.pdf", "b.pdf").exit_code, 0)
        self.assertTrue((self.root / "a_merged.pdf").exists())
        self.assertEqual(a.read_bytes(), first)
        self.assertEqual(b.read_bytes(), second)

    def test_boxes_and_rotations_survive(self) -> None:
        self.put("a.pdf", fx.marked(2, rotate={2: 270}))
        self.put("b.pdf", fx.crop_box(10, 20, 150, 250))
        result = pdf("merge", "a.pdf", "b.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.rotations_of("a_merged.pdf"), [0, 270, 0])
        last = PdfReader("a_merged.pdf").pages[2]
        self.assertEqual([float(v) for v in last.cropbox], [10.0, 20.0, 150.0, 250.0])

    def test_a_file_can_be_merged_with_itself(self) -> None:
        self.put("a.pdf", fx.marked(2))
        result = pdf("merge", "a.pdf", "a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_merged.pdf"), [1, 2, 1, 2])

    def test_text_and_bookmarks_come_through(self) -> None:
        self.put("a.pdf", fx.text_pdf(["alpha", "beta"]))
        self.put("b.pdf", fx.with_outline())
        result = pdf("merge", "a.pdf", "b.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        data = Path("a_merged.pdf").read_bytes()
        self.assertEqual(
            [p.extract_text().strip() for p in PdfReader(io.BytesIO(data)).pages][:2],
            ["alpha", "beta"],
        )
        self.assertEqual(fx.outline_titles(data), ["Chapter one", "Chapter two"])

    def test_each_source_gets_its_own_notes_once(self) -> None:
        self.put("a.pdf", fx.with_outline())
        self.put("b.pdf", fx.with_signature())
        self.put("c.pdf", fx.marked(1))
        result = pdf("merge", "a.pdf", "b.pdf", "c.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        lines = stderr_lines(result)
        self.assertEqual(lines.count(f"note: a.pdf: {NOTE_TEXTS['structure']}"), 1)
        self.assertEqual(lines.count(f"note: b.pdf: {NOTE_TEXTS['signatures']}"), 1)
        self.assertFalse([line for line in lines if line.startswith("note: c.pdf")])
        self.assertTrue((self.root / "a_merged.pdf").exists())

    def test_a_malformed_link_is_disabled_in_the_result(self) -> None:
        self.put("a.pdf", fx.with_bad_link())
        self.put("b.pdf", fx.with_link("https://example.com/ok"))
        result = pdf("merge", "a.pdf", "b.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            fx.link_uris(Path("a_merged.pdf").read_bytes()), [None, "https://example.com/ok"]
        )
        self.assertIn(f"note: a.pdf: {NOTE_TEXTS['links-removed']}", stderr_lines(result))


# ---- split


class SplitTests(PdfCase):
    def test_by_default_every_page_becomes_a_file(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("split", "a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [
                with_size("a.pdf -> a_split-001.pdf (page 1)", "a_split-001.pdf"),
                with_size("a.pdf -> a_split-002.pdf (page 2)", "a_split-002.pdf"),
                with_size("a.pdf -> a_split-003.pdf (page 3)", "a_split-003.pdf"),
                "pdf: 3 written",
            ],
        )
        self.assertEqual(result.stderr, "")
        for index in (1, 2, 3):
            self.assertEqual(self.markers_of(f"a_split-00{index}.pdf"), [index])
        self.assertEqual(len(names(self.root)), 4)

    def test_a_single_output_has_no_number(self) -> None:
        self.put("a.pdf", fx.marked(1))
        result = pdf("split", "a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [with_size("a.pdf -> a_split.pdf (page 1)", "a_split.pdf"), "pdf: 1 written"],
        )
        self.assertEqual(names(self.root), ["a.pdf", "a_split.pdf"])

    def test_every_n_pages(self) -> None:
        self.put("a.pdf", fx.marked(7))
        result = pdf("split", "a.pdf", "--every", "3")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [
                with_size("a.pdf -> a_split-001.pdf (pages 1-3)", "a_split-001.pdf"),
                with_size("a.pdf -> a_split-002.pdf (pages 4-6)", "a_split-002.pdf"),
                with_size("a.pdf -> a_split-003.pdf (page 7)", "a_split-003.pdf"),
                "pdf: 3 written",
            ],
        )
        self.assertEqual(self.markers_of("a_split-002.pdf"), [4, 5, 6])
        self.assertEqual(self.markers_of("a_split-003.pdf"), [7])

    def test_every_n_larger_than_the_document_gives_one_file(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("split", "a.pdf", "--every", "10")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [with_size("a.pdf -> a_split.pdf (pages 1-3)", "a_split.pdf"), "pdf: 1 written"],
        )
        self.assertEqual(self.markers_of("a_split.pdf"), [1, 2, 3])

    def test_every_takes_one_or_more(self) -> None:
        self.put("a.pdf", fx.marked(3))
        for value in ("0", "-2", "x"):
            with self.subTest(value=value):
                result = pdf("split", "a.pdf", "--every", value)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn("--every", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_every_and_pages_do_not_combine(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("split", "a.pdf", "--every", "2", "--pages", "1-2")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--every and --pages do not combine.", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_groups_make_one_file_each_and_left_out_pages_get_a_note(self) -> None:
        self.put("a.pdf", fx.marked(9))
        result = pdf("split", "a.pdf", "--pages", "1-3;4,6;7-8")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [
                with_size("a.pdf -> a_split-001.pdf (pages 1-3)", "a_split-001.pdf"),
                with_size("a.pdf -> a_split-002.pdf (pages 4, 6)", "a_split-002.pdf"),
                with_size("a.pdf -> a_split-003.pdf (pages 7-8)", "a_split-003.pdf"),
                "pdf: 3 written",
            ],
        )
        self.assertEqual(self.markers_of("a_split-002.pdf"), [4, 6])
        self.assertEqual(self.markers_of("a_split-003.pdf"), [7, 8])
        self.assertEqual(stderr_lines(result), ["note: pages not written: 5, 9"])

    def test_an_open_range_runs_to_the_last_page(self) -> None:
        self.put("a.pdf", fx.marked(8))
        result = pdf("split", "a.pdf", "--pages", "1-3;7-")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_split-002.pdf"), [7, 8])
        self.assertEqual(
            stdout_lines(result)[1],
            with_size("a.pdf -> a_split-002.pdf (pages 7-8)", "a_split-002.pdf"),
        )

    def test_pages_within_a_group_keep_their_source_order(self) -> None:
        self.put("a.pdf", fx.marked(7))
        result = pdf("split", "a.pdf", "--pages", "6,4")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_split.pdf"), [4, 6])
        self.assertEqual(
            stdout_lines(result)[0], with_size("a.pdf -> a_split.pdf (pages 4, 6)", "a_split.pdf")
        )

    def test_a_single_group_with_a_single_page_names_the_page(self) -> None:
        self.put("a.pdf", fx.marked(5))
        result = pdf("split", "a.pdf", "--pages", "5")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result)[0], with_size("a.pdf -> a_split.pdf (page 5)", "a_split.pdf")
        )
        self.assertEqual(self.markers_of("a_split.pdf"), [5])

    def test_bad_group_text_is_a_usage_error_naming_the_option(self) -> None:
        self.put("a.pdf", fx.marked(3))
        for text in ("9", "0", "1-9", "1,,2", "a"):
            with self.subTest(text=text):
                result = pdf("split", "a.pdf", "--pages", text)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn(f"--pages: {USAGE_TAIL}", result.stderr)
                self.assertIn("(pages 1 to 3)", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_last_stands_for_the_last_page_in_a_group(self) -> None:
        self.put("a.pdf", fx.marked(5))
        result = pdf("split", "a.pdf", "--pages", "1-2;3-last;last")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [
                with_size("a.pdf -> a_split-001.pdf (pages 1-2)", "a_split-001.pdf"),
                with_size("a.pdf -> a_split-002.pdf (pages 3-5)", "a_split-002.pdf"),
                with_size("a.pdf -> a_split-003.pdf (page 5)", "a_split-003.pdf"),
                "pdf: 3 written",
            ],
        )
        self.assertEqual(self.markers_of("a_split-002.pdf"), [3, 4, 5])
        self.assertEqual(self.markers_of("a_split-003.pdf"), [5])

    def test_the_last_page_alone_is_one_file(self) -> None:
        self.put("a.pdf", fx.marked(7))
        result = pdf("split", "a.pdf", "--pages", "last")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result)[0], with_size("a.pdf -> a_split.pdf (page 7)", "a_split.pdf")
        )
        self.assertEqual(self.markers_of("a_split.pdf"), [7])
        self.assertEqual(names(self.root), ["a.pdf", "a_split.pdf"])

    def test_last_works_on_a_one_page_file(self) -> None:
        self.put("a.pdf", fx.marked(1))
        result = pdf("split", "a.pdf", "--pages", "last")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_split.pdf"), [1])
        self.assertEqual(stderr_lines(result), [])

    def test_last_in_a_group_leaves_the_other_pages_to_the_note(self) -> None:
        self.put("a.pdf", fx.marked(4))
        result = pdf("split", "a.pdf", "--pages", "1;last")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(stderr_lines(result), ["note: pages not written: 2-3"])

    def test_last_twice_in_a_group_is_a_usage_error(self) -> None:
        self.put("a.pdf", fx.marked(3))
        for text in ("3,last", "last,last", "2-last,3", "1;1-last,last"):
            with self.subTest(text=text):
                result = pdf("split", "a.pdf", "--pages", text)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn(f"--pages: {USAGE_TAIL}", result.stderr)
                self.assertIn("(pages 1 to 3)", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_only_lowercase_last_is_a_page(self) -> None:
        self.put("a.pdf", fx.marked(3))
        for text in ("Last", "LAST", "end", "1-end", "1-Last", "last-1", "lastly"):
            with self.subTest(text=text):
                result = pdf("split", "a.pdf", "--pages", text)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn(f"--pages: {USAGE_TAIL}", result.stderr)
                self.assertIn("last", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_the_usage_error_mentions_last(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("split", "a.pdf", "--pages", "x")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertRegex(result.stderr, r"\blast\b")

    def test_outdir_receives_the_outputs(self) -> None:
        self.put("a.pdf", fx.marked(2))
        (self.root / "out").mkdir()
        result = pdf("split", "a.pdf", "--outdir", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "out"), ["a_split-001.pdf", "a_split-002.pdf"])
        self.assertEqual(names(self.root), ["a.pdf", "out"])

    def test_numbers_grow_with_the_count(self) -> None:
        self.put("a.pdf", fx.blank([(100, 100)] * 1000))
        result = pdf("split", "a.pdf", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        lines = stdout_lines(result)
        self.assertEqual(lines[0], "a.pdf -> a_split-0001.pdf (page 1)")
        self.assertEqual(lines[999], "a.pdf -> a_split-1000.pdf (page 1000)")
        self.assertEqual(lines[-1], "pdf: 1000 planned")
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_a_dry_run_lists_the_outputs_and_writes_nothing(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("split", "a.pdf", "--every", "2", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        lines = stdout_lines(result)
        self.assertEqual(len(lines), 3)
        self.assertIn("a_split-001.pdf", lines[0])
        self.assertIn("a_split-002.pdf", lines[1])
        self.assertEqual(lines[-1], "pdf: 2 planned")
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_an_existing_output_stops_the_run_before_anything_is_written(self) -> None:
        self.put("a.pdf", fx.marked(3))
        old = self.put("a_split-002.pdf", b"old content")
        result = pdf("split", "a.pdf")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("fail a.pdf: a_split-002.pdf exists (use --overwrite)", stderr_lines(result))
        self.assertEqual(names(self.root), ["a.pdf", "a_split-002.pdf"])
        self.assertEqual(old.read_bytes(), b"old content")

    def test_overwrite_replaces_existing_outputs(self) -> None:
        self.put("a.pdf", fx.marked(3))
        self.put("a_split-002.pdf", b"old content")
        result = pdf("split", "a.pdf", "--overwrite")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_split-002.pdf"), [2])
        self.assertEqual(stdout_lines(result)[-1], "pdf: 3 written")

    def test_a_source_that_fails_a_check_writes_nothing(self) -> None:
        cases = {
            "damaged.pdf": (fx.truncated(), DAMAGED),
            "locked.pdf": (fx.encrypted(), ENCRYPTED),
            "odd.pdf": (fx.user_unit(2), UNSUPPORTED),
        }
        for name, (data, text) in cases.items():
            with self.subTest(name=name):
                self.put(name, data)
                result = pdf("split", name)
                self.assertEqual(result.exit_code, 1, result.output)
                self.assertIn(f"fail {name}: {text}", stderr_lines(result))
                self.assertNotIn("->", result.stdout)
        self.assertEqual(names(self.root), ["damaged.pdf", "locked.pdf", "odd.pdf"])

    def test_a_missing_source_is_a_usage_error(self) -> None:
        missing = pdf("split", "nope.pdf")
        self.assertEqual(missing.exit_code, 2)
        self.assertIn("nope.pdf", missing.stderr)
        self.assertEqual(pdf("split").exit_code, 2)
        self.assertIn("FILE", pdf("split").stderr)

    def test_rotation_and_boxes_are_kept_in_each_piece(self) -> None:
        self.put("a.pdf", fx.marked(3, rotate={2: 90}))
        pdf("split", "a.pdf")
        self.assertEqual(self.rotations_of("a_split-001.pdf"), [0])
        self.assertEqual(self.rotations_of("a_split-002.pdf"), [90])
        self.assertEqual(self.markers_of("a_split-002.pdf"), [2])

    def test_a_note_is_printed_once_per_source_not_once_per_output(self) -> None:
        self.put("a.pdf", fx.with_outline(3))
        result = pdf("split", "a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(stderr_lines(result).count(f"note: a.pdf: {NOTE_TEXTS['structure']}"), 1)

    def test_a_malformed_link_is_disabled_in_the_piece(self) -> None:
        self.put("a.pdf", fx.with_bad_link())
        result = pdf("split", "a.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(fx.link_uris(Path("a_split.pdf").read_bytes()), [None])

    def test_the_source_is_never_changed(self) -> None:
        data = fx.with_outline()
        path = self.put("a.pdf", data)
        self.assertEqual(pdf("split", "a.pdf").exit_code, 0)
        self.assertTrue((self.root / "a_split-001.pdf").exists())
        self.assertEqual(path.read_bytes(), data)


# ---- organize


class OrganizeTests(PdfCase):
    def test_no_option_is_a_usage_error(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("organize", "a.pdf")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("give --order, --delete or --rotate.", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_order_sets_the_pages_and_drops_the_rest(self) -> None:
        self.put("a.pdf", fx.marked(4))
        result = pdf("organize", "a.pdf", "--order", "3,1")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_organized.pdf"), [3, 1])
        line = stdout_lines(result)[0]
        self.assertTrue(line.startswith("a.pdf -> a_organized.pdf (2 pages"), line)
        self.assertIn("reordered", line)
        self.assertEqual(stdout_lines(result)[-1], "pdf: 1 organized")
        self.assertEqual(result.stderr, "")

    def test_an_order_that_changes_nothing_is_not_called_reordered(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("organize", "a.pdf", "--order", "1-3")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [with_size("a.pdf -> a_organized.pdf (3 pages)", "a_organized.pdf"), "pdf: 1 organized"],
        )
        self.assertEqual(self.markers_of("a_organized.pdf"), [1, 2, 3])

    def test_a_backwards_range_reverses_the_document(self) -> None:
        self.put("a.pdf", fx.marked(5))
        result = pdf("organize", "a.pdf", "--order", "5-1")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_organized.pdf"), [5, 4, 3, 2, 1])
        self.assertIn("reordered", stdout_lines(result)[0])

    def test_delete_removes_pages_by_source_number(self) -> None:
        self.put("a.pdf", fx.marked(5))
        result = pdf("organize", "a.pdf", "--delete", "2,4-5")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_organized.pdf"), [1, 3])
        self.assertEqual(
            stdout_lines(result)[0],
            with_size("a.pdf -> a_organized.pdf (2 pages; 3 removed)", "a_organized.pdf"),
        )

    def test_rotate_with_pages_turns_those_pages_clockwise(self) -> None:
        self.put("a.pdf", fx.marked(4))
        result = pdf("organize", "a.pdf", "--rotate", "2-3:90")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.rotations_of("a_organized.pdf"), [0, 90, 90, 0])
        self.assertEqual(self.markers_of("a_organized.pdf"), [1, 2, 3, 4])
        self.assertEqual(
            stdout_lines(result)[0],
            with_size("a.pdf -> a_organized.pdf (4 pages; 2 rotated)", "a_organized.pdf"),
        )

    def test_rotate_without_pages_turns_every_page(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("organize", "a.pdf", "--rotate", "180")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.rotations_of("a_organized.pdf"), [180, 180, 180])
        self.assertEqual(
            stdout_lines(result)[0],
            with_size("a.pdf -> a_organized.pdf (3 pages; 3 rotated)", "a_organized.pdf"),
        )

    def test_rotation_is_added_to_the_page_own_rotation(self) -> None:
        self.put("a.pdf", fx.marked(3, rotate={1: 90, 2: 270}))
        result = pdf("organize", "a.pdf", "--rotate", "1:90", "--rotate", "2:90", "--rotate", "3:270")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.rotations_of("a_organized.pdf"), [180, 0, 270])

    def test_the_rotate_option_can_be_repeated_for_the_same_page(self) -> None:
        self.put("a.pdf", fx.marked(2))
        result = pdf("organize", "a.pdf", "--rotate", "2:90", "--rotate", "2:90")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.rotations_of("a_organized.pdf"), [0, 180])

    def test_all_three_options_apply_in_order_then_delete_then_rotate(self) -> None:
        self.put("a.pdf", fx.marked(6))
        result = pdf(
            "organize", "a.pdf", "--order", "6,5,4,3,2", "--delete", "3", "--rotate", "6:90"
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_organized.pdf"), [6, 5, 4, 2])
        self.assertEqual(self.rotations_of("a_organized.pdf"), [90, 0, 0, 0])

    def test_delete_and_rotate_together_report_both(self) -> None:
        self.put("a.pdf", fx.marked(6))
        result = pdf("organize", "a.pdf", "--delete", "1", "--rotate", "2-3:90")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result)[0],
            with_size("a.pdf -> a_organized.pdf (5 pages; 2 rotated, 1 removed)", "a_organized.pdf"),
        )

    def test_removing_every_page_is_a_usage_error(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("organize", "a.pdf", "--delete", "1-3")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--delete would remove every page", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_a_bad_angle_is_a_usage_error(self) -> None:
        self.put("a.pdf", fx.marked(3))
        for item in ("45", "0", "2:45", "x"):
            with self.subTest(item=item):
                result = pdf("organize", "a.pdf", "--rotate", item)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn(
                    "--rotate takes 90, 180 or 270, optionally after PAGES: (such as 2-4:90)",
                    result.stderr,
                )
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_bad_pages_name_their_option(self) -> None:
        self.put("a.pdf", fx.marked(3))
        cases = {
            ("--order", "9"): "--order",
            ("--delete", "0"): "--delete",
            ("--rotate", "9:90"): "--rotate",
        }
        for args, option in cases.items():
            with self.subTest(args=args):
                result = pdf("organize", "a.pdf", *args)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn(f"{option}: {USAGE_TAIL}", result.stderr)
                self.assertIn("(pages 1 to 3)", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_delete_last_removes_the_last_page(self) -> None:
        self.put("a.pdf", fx.marked(4))
        result = pdf("organize", "a.pdf", "--delete", "last")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_organized.pdf"), [1, 2, 3])
        self.assertEqual(
            stdout_lines(result)[0],
            with_size("a.pdf -> a_organized.pdf (3 pages; 1 removed)", "a_organized.pdf"),
        )

    def test_a_range_to_last_can_be_deleted(self) -> None:
        self.put("a.pdf", fx.marked(6))
        result = pdf("organize", "a.pdf", "--delete", "2,4-last")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_organized.pdf"), [1, 3])

    def test_last_down_to_one_reverses_the_document(self) -> None:
        self.put("a.pdf", fx.marked(5))
        result = pdf("organize", "a.pdf", "--order", "last-1")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_organized.pdf"), [5, 4, 3, 2, 1])
        self.assertIn("reordered", stdout_lines(result)[0])

    def test_last_can_come_first_in_an_order(self) -> None:
        self.put("a.pdf", fx.marked(4))
        result = pdf("organize", "a.pdf", "--order", "last,1-3")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_organized.pdf"), [4, 1, 2, 3])

    def test_rotate_last_turns_the_last_page(self) -> None:
        self.put("a.pdf", fx.marked(4))
        result = pdf("organize", "a.pdf", "--rotate", "last:90")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.rotations_of("a_organized.pdf"), [0, 0, 0, 90])
        self.assertEqual(
            stdout_lines(result)[0],
            with_size("a.pdf -> a_organized.pdf (4 pages; 1 rotated)", "a_organized.pdf"),
        )

    def test_rotate_accepts_last_in_a_range(self) -> None:
        self.put("a.pdf", fx.marked(4))
        result = pdf("organize", "a.pdf", "--rotate", "3-last:180", "--rotate", "1,last:90")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.rotations_of("a_organized.pdf"), [90, 0, 180, 270])

    def test_last_in_all_three_options_means_the_same_source_page(self) -> None:
        self.put("a.pdf", fx.marked(5))
        result = pdf(
            "organize", "a.pdf", "--order", "last-1", "--delete", "last", "--rotate", "last:90"
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_organized.pdf"), [4, 3, 2, 1])
        self.assertEqual(self.rotations_of("a_organized.pdf"), [0, 0, 0, 0])

    def test_last_on_a_one_page_file_is_page_one(self) -> None:
        self.put("a.pdf", fx.marked(1))
        result = pdf("organize", "a.pdf", "--order", "last-1", "--rotate", "last:270")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_organized.pdf"), [1])
        self.assertEqual(self.rotations_of("a_organized.pdf"), [270])

    def test_deleting_last_of_a_one_page_file_would_remove_every_page(self) -> None:
        self.put("a.pdf", fx.marked(1))
        result = pdf("organize", "a.pdf", "--delete", "last")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--delete would remove every page", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_last_listed_with_the_page_it_stands_for_is_a_usage_error(self) -> None:
        self.put("a.pdf", fx.marked(3))
        cases = {
            ("--order", "3,last"): "--order",
            ("--delete", "1-last,2"): "--delete",
            ("--rotate", "3,last:90"): "--rotate",
        }
        for args, option in cases.items():
            with self.subTest(args=args):
                result = pdf("organize", "a.pdf", *args)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn(f"{option}: {USAGE_TAIL}", result.stderr)
                self.assertIn("(pages 1 to 3)", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_other_words_are_not_pages(self) -> None:
        self.put("a.pdf", fx.marked(3))
        cases = {
            ("--order", "Last"): "--order",
            ("--order", "LAST-1"): "--order",
            ("--delete", "LAST"): "--delete",
            ("--delete", "end"): "--delete",
        }
        for args, option in cases.items():
            with self.subTest(args=args):
                result = pdf("organize", "a.pdf", *args)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn(f"{option}: {USAGE_TAIL}", result.stderr)
                self.assertRegex(result.stderr, r"\blast\b")
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_other_words_before_a_rotate_angle_are_not_pages(self) -> None:
        self.put("a.pdf", fx.marked(3))
        for item in ("Last:90", "LAST:90", "end:90"):
            with self.subTest(item=item):
                result = pdf("organize", "a.pdf", "--rotate", item)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn("--rotate", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_rotate_last_without_an_angle_still_needs_one(self) -> None:
        self.put("a.pdf", fx.marked(3))
        for item in ("last", "last:45", "last:"):
            with self.subTest(item=item):
                result = pdf("organize", "a.pdf", "--rotate", item)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn("--rotate takes 90, 180 or 270", result.stderr)
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_output_option_names_the_result(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("organize", "a.pdf", "--delete", "1", "-o", "less.pdf")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("less.pdf"), [2, 3])
        self.assertEqual(
            stdout_lines(result)[0], with_size("a.pdf -> less.pdf (2 pages; 1 removed)", "less.pdf")
        )
        self.assertEqual(names(self.root), ["a.pdf", "less.pdf"])

    def test_output_without_the_pdf_extension_is_a_usage_error(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("organize", "a.pdf", "--delete", "1", "-o", "less.txt")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertRegex(result.stderr, r"--output (less\.txt|FILE) does not end in \.pdf\.")

    def test_an_existing_output_is_skipped_unless_overwrite_is_given(self) -> None:
        self.put("a.pdf", fx.marked(3))
        old = self.put("a_organized.pdf", b"old content")
        result = pdf("organize", "a.pdf", "--delete", "1")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("skip a_organized.pdf: target exists (use --overwrite)", stderr_lines(result))
        self.assertEqual(old.read_bytes(), b"old content")

        result = pdf("organize", "a.pdf", "--delete", "1", "--overwrite")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.markers_of("a_organized.pdf"), [2, 3])

    def test_a_dry_run_writes_nothing(self) -> None:
        self.put("a.pdf", fx.marked(3))
        result = pdf("organize", "a.pdf", "--delete", "1", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("a_organized.pdf", stdout_lines(result)[0])
        self.assertEqual(stdout_lines(result)[-1], "pdf: 1 planned")
        self.assertEqual(names(self.root), ["a.pdf"])

    def test_a_source_that_fails_a_check_writes_nothing(self) -> None:
        cases = {
            "damaged.pdf": (fx.truncated(), DAMAGED),
            "locked.pdf": (fx.encrypted(), ENCRYPTED),
            "odd.pdf": (fx.huge_media_box(), UNSUPPORTED),
        }
        for name, (data, text) in cases.items():
            with self.subTest(name=name):
                self.put(name, data)
                result = pdf("organize", name, "--rotate", "90")
                self.assertEqual(result.exit_code, 1, result.output)
                self.assertIn(f"fail {name}: {text}", stderr_lines(result))
        self.assertEqual(names(self.root), ["damaged.pdf", "locked.pdf", "odd.pdf"])

    def test_page_boxes_survive(self) -> None:
        self.put("a.pdf", fx.crop_box(10, 20, 150, 250))
        result = pdf("organize", "a.pdf", "--rotate", "90")
        self.assertEqual(result.exit_code, 0, result.output)
        page = PdfReader("a_organized.pdf").pages[0]
        self.assertEqual([float(v) for v in page.mediabox], [0.0, 0.0, 200.0, 300.0])
        self.assertEqual([float(v) for v in page.cropbox], [10.0, 20.0, 150.0, 250.0])

    def test_the_source_is_never_changed(self) -> None:
        data = fx.with_outline()
        path = self.put("a.pdf", data)
        self.assertEqual(pdf("organize", "a.pdf", "--order", "3,2,1", "--rotate", "90").exit_code, 0)
        self.assertTrue((self.root / "a_organized.pdf").exists())
        self.assertEqual(path.read_bytes(), data)


# ---- notes about features that may not survive


class NoteTests(PdfCase):
    CASES = {
        "structure": fx.with_outline,
        "annotations": fx.with_link,
        "opening-view": fx.with_open_action,
        "links-removed": fx.with_bad_link,
        "forms": fx.with_form_field,
        "signatures": fx.with_signature,
        "active-content": fx.with_javascript_action,
        "attachments": fx.with_attachment,
    }

    def test_each_feature_has_its_note_and_the_command_goes_ahead(self) -> None:
        for code, build in self.CASES.items():
            with self.subTest(code=code):
                self.put("a.pdf", build())
                result = pdf("organize", "a.pdf", "--rotate", "90", "--overwrite")
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn(f"note: a.pdf: {NOTE_TEXTS[code]}", stderr_lines(result))
                self.assertTrue((self.root / "a_organized.pdf").exists())

    def test_a_file_without_features_gets_no_note(self) -> None:
        for build in (lambda: fx.marked(2), lambda: fx.text_pdf(["a", "b"]), lambda: fx.with_image(2)):
            self.put("a.pdf", build())
            result = pdf("split", "a.pdf", "--overwrite")
            self.assertEqual(result.stderr, "")

    def test_notes_name_the_source_as_it_was_given(self) -> None:
        self.put("sub/doc.pdf", fx.with_signature())
        given = str(Path("sub") / "doc.pdf")
        result = pdf("organize", given, "--rotate", "90")
        self.assertIn(f"note: {given}: {NOTE_TEXTS['signatures']}", stderr_lines(result))

    def test_a_dry_run_still_prints_the_notes(self) -> None:
        self.put("a.pdf", fx.with_signature())
        result = pdf("organize", "a.pdf", "--rotate", "90", "--dry-run")
        self.assertIn(f"note: a.pdf: {NOTE_TEXTS['signatures']}", stderr_lines(result))
        self.assertEqual(names(self.root), ["a.pdf"])


if __name__ == "__main__":
    unittest.main()
