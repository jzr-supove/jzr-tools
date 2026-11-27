"""Tests for the pdf task: combine a.pdf (3 pages) and b.pdf (2 pages) without the last page of a.pdf.

Fixtures are made once per class with the task's own prepare_fixtures. Results are built from them
with pypdf (the runner venv's) or, when the poppler tools are installed, with pdfseparate and pdfunite
the way a user without pypdf would; the wrong ones differ from the correct one in exactly one way.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench.tasks import get_task  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from task_check_support import CheckCase  # noqa: E402

PROMPT = "Combine a.pdf and b.pdf into combined.pdf, a.pdf first, without the last page of a.pdf."
LABELS = ("A1", "A2", "A3", "B1", "B2")
PDFUNITE = shutil.which("pdfunite")
PDFSEPARATE = shutil.which("pdfseparate")


def page_labels(path: Path) -> list[list[str]]:
    """For each page, the labels found in its extracted text."""
    reader = PdfReader(path)
    return [[label for label in LABELS if label in (page.extract_text() or "")] for page in reader.pages]


def build(dest: Path, fixtures: Path, order: list[str]) -> Path:
    """Write a PDF made of the fixture pages named in order (A1..B2), with pypdf."""
    sources = {"A": PdfReader(fixtures / "a.pdf"), "B": PdfReader(fixtures / "b.pdf")}
    writer = PdfWriter()
    for label in order:
        writer.add_page(sources[label[0]].pages[int(label[1]) - 1])
    with dest.open("wb") as handle:
        writer.write(handle)
    return dest


class PdfFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("pdf")
        cls.task.prepare_fixtures(cls.fixtures)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(self.task.prompt, PROMPT)

    def test_prepare_fixtures_writes_exactly_a_and_b(self) -> None:
        self.assertEqual(sorted(p.name for p in self.fixtures.iterdir()), ["a.pdf", "b.pdf"])

    def test_a_has_three_pages_and_b_has_two(self) -> None:
        self.assertEqual(len(PdfReader(self.fixtures / "a.pdf").pages), 3)
        self.assertEqual(len(PdfReader(self.fixtures / "b.pdf").pages), 2)

    def test_each_page_carries_exactly_its_own_label_as_extractable_text(self) -> None:
        self.assertEqual(page_labels(self.fixtures / "a.pdf"), [["A1"], ["A2"], ["A3"]])
        self.assertEqual(page_labels(self.fixtures / "b.pdf"), [["B1"], ["B2"]])

    def test_setup_copies_both_files_into_the_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.task.setup(work, self.fixtures)
            self.assertEqual(sorted(p.name for p in work.iterdir()), ["a.pdf", "b.pdf"])
            for name in ("a.pdf", "b.pdf"):
                self.assertEqual((work / name).read_bytes(), (self.fixtures / name).read_bytes())
            self.assertFalse((work / "combined.pdf").exists())


class PdfCheckTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.fixtures = base / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("pdf")
        cls.task.prepare_fixtures(cls.fixtures)

        out = base / "results"
        out.mkdir()
        cls.results = out
        build(out / "good.pdf", cls.fixtures, ["A1", "A2", "B1", "B2"])
        build(out / "a-last-kept.pdf", cls.fixtures, ["A1", "A2", "A3", "B1", "B2"])  # A3 kept
        build(out / "b-first.pdf", cls.fixtures, ["B1", "B2", "A1", "A2"])
        build(out / "a-reversed.pdf", cls.fixtures, ["A2", "A1", "B1", "B2"])
        build(out / "b-reversed.pdf", cls.fixtures, ["A1", "A2", "B2", "B1"])
        build(out / "interleaved.pdf", cls.fixtures, ["A1", "B1", "A2", "B2"])
        build(out / "b2-missing.pdf", cls.fixtures, ["A1", "A2", "B1"])  # a page missing
        build(out / "a2-missing.pdf", cls.fixtures, ["A1", "B1", "B2"])
        build(out / "wrong-page-dropped.pdf", cls.fixtures, ["A2", "A3", "B1", "B2"])  # dropped A1, kept A3
        build(out / "only-a.pdf", cls.fixtures, ["A1", "A2"])
        # four pages of the right size but no text at all (a rasterised or blank result)
        writer = PdfWriter()
        for _ in range(4):
            writer.add_blank_page(612, 792)
        with (out / "no-text.pdf").open("wb") as handle:
            writer.write(handle)
        (out / "text.pdf").write_text("A1 A2 B1 B2\nthis is not a PDF\n")
        (out / "empty.pdf").write_bytes(b"")
        # truncated: the first half of a good PDF
        good_bytes = (out / "good.pdf").read_bytes()
        (out / "truncated.pdf").write_bytes(good_bytes[: len(good_bytes) // 2])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def install(self, name: str | None) -> None:
        if name is not None:
            shutil.copy(self.results / name, self.work / "combined.pdf")

    def test_the_variants_are_what_they_are_meant_to_be(self) -> None:
        # Guards the fixtures of this test, so a pass or a failure below means something.
        expected = {
            "good.pdf": [["A1"], ["A2"], ["B1"], ["B2"]],
            "a-last-kept.pdf": [["A1"], ["A2"], ["A3"], ["B1"], ["B2"]],
            "b-first.pdf": [["B1"], ["B2"], ["A1"], ["A2"]],
            "a-reversed.pdf": [["A2"], ["A1"], ["B1"], ["B2"]],
            "b-reversed.pdf": [["A1"], ["A2"], ["B2"], ["B1"]],
            "interleaved.pdf": [["A1"], ["B1"], ["A2"], ["B2"]],
            "b2-missing.pdf": [["A1"], ["A2"], ["B1"]],
            "a2-missing.pdf": [["A1"], ["B1"], ["B2"]],
            "wrong-page-dropped.pdf": [["A2"], ["A3"], ["B1"], ["B2"]],
            "only-a.pdf": [["A1"], ["A2"]],
            "no-text.pdf": [[], [], [], []],
        }
        for name, labels in expected.items():
            with self.subTest(variant=name):
                self.assertEqual(page_labels(self.results / name), labels)

    def test_correct_combined_pdf_passes(self) -> None:
        self.install("good.pdf")
        self.assert_passes()

    def test_final_text_does_not_matter(self) -> None:
        self.install("good.pdf")
        self.assertTrue(self.check("").passed)
        self.assertTrue(self.check("something unrelated").passed)

    @unittest.skipUnless(PDFUNITE and PDFSEPARATE, "pdfunite and pdfseparate are not installed")
    def test_result_made_with_pdfseparate_and_pdfunite_passes(self) -> None:
        assert PDFUNITE and PDFSEPARATE
        parts = self.work / "parts"
        parts.mkdir()
        subprocess.run(
            [
                PDFSEPARATE,
                "-f",
                "1",
                "-l",
                "2",
                str(self.work / "a.pdf"),
                str(parts / "a-%d.pdf"),
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        subprocess.run(
            [
                PDFUNITE,
                str(parts / "a-1.pdf"),
                str(parts / "a-2.pdf"),
                str(self.work / "b.pdf"),
                str(self.work / "combined.pdf"),
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        shutil.rmtree(parts)
        self.assertEqual(page_labels(self.work / "combined.pdf"), [["A1"], ["A2"], ["B1"], ["B2"]])
        self.assert_passes()

    def test_missing_combined_pdf_fails(self) -> None:
        self.install(None)
        self.assert_fails_with(self.check(), "combined.pdf", "exist", "missing", "not found")

    def test_a3_kept_fails(self) -> None:
        self.install("a-last-kept.pdf")
        self.assert_fails_with(self.check(), "a3", "pages", "5")

    def test_b_before_a_fails(self) -> None:
        self.install("b-first.pdf")
        self.assert_fails_with(self.check(), "order", "a1", "b1", "page")

    def test_pages_of_a_swapped_fails(self) -> None:
        self.install("a-reversed.pdf")
        self.assert_fails_with(self.check(), "order", "a1", "a2", "page")

    def test_pages_of_b_swapped_fails(self) -> None:
        self.install("b-reversed.pdf")
        self.assert_fails_with(self.check(), "order", "b1", "b2", "page")

    def test_interleaved_pages_fail(self) -> None:
        self.install("interleaved.pdf")
        self.assert_fails_with(self.check(), "order", "a2", "b1", "page")

    def test_a_page_missing_fails(self) -> None:
        for name in ("b2-missing.pdf", "a2-missing.pdf", "only-a.pdf"):
            with self.subTest(variant=name):
                self.install(name)
                self.assert_fails_with(self.check(), "page", "4", "3", "2")

    def test_wrong_page_dropped_fails_and_mentions_a3(self) -> None:
        self.install("wrong-page-dropped.pdf")
        self.assert_fails_with(self.check(), "a3")

    def test_pdf_without_extractable_text_fails(self) -> None:
        self.install("no-text.pdf")
        self.assert_fails_with(self.check(), "text", "a1", "b1", "order", "page")

    def test_text_file_named_combined_pdf_fails(self) -> None:
        self.install("text.pdf")
        self.assert_fails_with(self.check(), "pdf", "open", "read", "valid")

    def test_empty_combined_pdf_fails(self) -> None:
        self.install("empty.pdf")
        self.assert_fails_with(self.check(), "pdf", "open", "read", "valid", "empty")

    def test_truncated_pdf_fails(self) -> None:
        self.install("truncated.pdf")
        self.assert_fails_with(self.check(), "pdf", "open", "read", "valid", "page")

    def test_png_named_combined_pdf_fails(self) -> None:
        from PIL import Image

        Image.new("RGB", (50, 50), (200, 10, 10)).save(self.work / "combined.pdf", format="PNG")
        self.assert_fails_with(self.check(), "pdf", "open", "read", "valid")

    def test_each_single_fault_gives_a_single_reason(self) -> None:
        # (a missing or extra page can legitimately be reported as both a page count and an order
        # problem, so only faults with one possible reason are listed)
        for name in ("b-first.pdf", "text.pdf", "empty.pdf"):
            with self.subTest(variant=name):
                self.install(name)
                result = self.check()
                self.assertFalse(result.passed)
                self.assertEqual(len(result.details), 1, result.details)

    def test_modified_a_pdf_fails_even_with_a_correct_combined_pdf(self) -> None:
        self.install("good.pdf")
        with (self.work / "a.pdf").open("ab") as handle:
            handle.write(b"\n% tampered\n")
        result = self.check()
        self.assert_fails_with(result, "a.pdf", reasons=1)
        self.assert_fails_with(result, "unchanged", "sha", "hash", "modified", "changed")

    def test_modified_b_pdf_fails_even_with_a_correct_combined_pdf(self) -> None:
        self.install("good.pdf")
        with (self.work / "b.pdf").open("ab") as handle:
            handle.write(b"\n% tampered\n")
        result = self.check()
        self.assert_fails_with(result, "b.pdf", reasons=1)
        self.assert_fails_with(result, "unchanged", "sha", "hash", "modified", "changed")

    def test_a_pdf_rewritten_without_its_last_page_fails(self) -> None:
        # the tempting shortcut: edit a.pdf in place and then merge
        self.install("good.pdf")
        build(self.work / "a.pdf", self.fixtures, ["A1", "A2"])
        self.assert_fails_with(self.check(), "a.pdf", "unchanged", "sha", "hash", "modified", "changed")

    def test_deleted_source_fails_even_with_a_correct_combined_pdf(self) -> None:
        self.install("good.pdf")
        (self.work / "b.pdf").unlink()
        self.assert_fails_with(self.check(), "b.pdf", "unchanged", "sha", "hash", "missing", "changed")

    def test_check_does_not_modify_the_work_folder(self) -> None:
        self.install("good.pdf")
        self.assert_check_leaves_work_folder_alone()

    def test_check_does_not_modify_the_work_folder_when_it_fails(self) -> None:
        self.install("text.pdf")
        self.assert_check_leaves_work_folder_alone()


if __name__ == "__main__":
    unittest.main()
