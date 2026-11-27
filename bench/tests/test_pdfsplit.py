"""Tests for the pdfsplit task (held out): split report.pdf (nine pages, P1 to P9) into three PDFs.

The fixture is made once per class with the task's own prepare_fixtures. Correct answers are built
with pypdf (the runner venv's); each wrong one differs from a correct one in exactly one way.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench.tasks import HELD_OUT, TASKS, get_task  # noqa: E402
from jzr_support import HAVE_JZR, NEEDS_JZR, run_jzr  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from task_check_support import CheckCase  # noqa: E402

PROMPT = "Split report.pdf into three PDFs: pages 1 to 3, pages 4 to 6, and the rest. Keep report.pdf."
LABELS = [f"P{n}" for n in range(1, 10)]


def page_labels(path: Path) -> list[list[str]]:
    """For each page, the labels (P1..P9) found in its extracted text."""
    return [[label for label in LABELS if label in (page.extract_text() or "")] for page in PdfReader(path).pages]


def build(dest: Path, fixtures: Path, pages: list[int]) -> Path:
    """Write a PDF made of the pages of report.pdf with these numbers (1-based, in this order), with pypdf."""
    source = PdfReader(fixtures / "report.pdf")
    writer = PdfWriter()
    for number in pages:
        writer.add_page(source.pages[number - 1])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        writer.write(handle)
    return dest


def pdfs_under(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.suffix.lower() == ".pdf")


class PdfsplitTaskTest(unittest.TestCase):
    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(get_task("pdfsplit").prompt, PROMPT)

    def test_it_is_named_pdfsplit_and_registered_as_held_out(self) -> None:
        task = get_task("pdfsplit")
        self.assertEqual(task.name, "pdfsplit")
        self.assertIs(TASKS["pdfsplit"], task)
        self.assertIn("pdfsplit", HELD_OUT)


class SharedPdfMakerTest(unittest.TestCase):
    """tasks/_pdfs.py: the page maker of the pdf task, shared by pdfsplit."""

    def test_it_makes_a_pdf_with_one_labelled_page_per_label(self) -> None:
        from jzr_bench.tasks._pdfs import _pdf_with_pages

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.pdf"
            path.write_bytes(_pdf_with_pages(["X1", "X2", "X3", "X4"]))
            reader = PdfReader(path)
            self.assertEqual(len(reader.pages), 4)
            for page, label in zip(reader.pages, ["X1", "X2", "X3", "X4"], strict=True):
                self.assertIn(label, page.extract_text())

    def test_the_pdf_task_still_makes_its_fixtures_with_it(self) -> None:
        from jzr_bench.tasks._pdfs import _pdf_with_pages

        with tempfile.TemporaryDirectory() as tmp:
            get_task("pdf").prepare_fixtures(Path(tmp))
            self.assertEqual((Path(tmp) / "a.pdf").read_bytes(), _pdf_with_pages(["A1", "A2", "A3"]))
            self.assertEqual((Path(tmp) / "b.pdf").read_bytes(), _pdf_with_pages(["B1", "B2"]))


class PdfsplitFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("pdfsplit")
        cls.task.prepare_fixtures(cls.fixtures)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_prepare_fixtures_writes_exactly_report_pdf(self) -> None:
        self.assertEqual([p.name for p in self.fixtures.iterdir()], ["report.pdf"])

    def test_it_has_nine_pages_labelled_p1_to_p9_in_order(self) -> None:
        self.assertEqual(len(PdfReader(self.fixtures / "report.pdf").pages), 9)
        self.assertEqual(page_labels(self.fixtures / "report.pdf"), [[label] for label in LABELS])

    def test_it_is_made_the_same_way_as_the_pdf_tasks_files(self) -> None:
        from jzr_bench.tasks._pdfs import _pdf_with_pages

        self.assertEqual((self.fixtures / "report.pdf").read_bytes(), _pdf_with_pages(LABELS))

    def test_it_is_the_same_every_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.task.prepare_fixtures(Path(tmp))
            self.assertEqual((Path(tmp) / "report.pdf").read_bytes(), (self.fixtures / "report.pdf").read_bytes())

    def test_setup_copies_it_into_the_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.task.setup(work, self.fixtures)
            self.assertEqual([p.name for p in work.iterdir()], ["report.pdf"])
            self.assertEqual((work / "report.pdf").read_bytes(), (self.fixtures / "report.pdf").read_bytes())

    def test_setup_prepares_an_empty_fixture_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixtures, work = Path(tmp) / "f", Path(tmp) / "w"
            self.task.setup(work, fixtures)
            self.assertTrue((fixtures / "report.pdf").is_file())
            self.assertTrue((work / "report.pdf").is_file())


class PdfsplitCheckTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("pdfsplit")
        cls.task.prepare_fixtures(cls.fixtures)

    def split(self, folder: str = ".", names: tuple[str, str, str] = ("one.pdf", "two.pdf", "three.pdf")) -> list[Path]:
        """The correct split of the work folder's report.pdf: pages 1-3, 4-6 and 7-9, with these names."""
        groups = ([1, 2, 3], [4, 5, 6], [7, 8, 9])
        return [
            build(self.work / folder / name, self.fixtures, pages) for name, pages in zip(names, groups, strict=True)
        ]

    def test_the_inputs_of_these_tests_are_what_they_are_meant_to_be(self) -> None:
        # Guards the inputs of this test, so a pass or a failure below means something.
        first, second, third = self.split()
        self.assertEqual(page_labels(first), [["P1"], ["P2"], ["P3"]])
        self.assertEqual(page_labels(second), [["P4"], ["P5"], ["P6"]])
        self.assertEqual(page_labels(third), [["P7"], ["P8"], ["P9"]])

    # right answers

    def test_three_pdfs_next_to_report_pdf_pass(self) -> None:
        self.split()
        self.assert_passes()

    def test_final_text_does_not_matter(self) -> None:
        self.split()
        self.assertTrue(self.check("").passed)
        self.assertTrue(self.check("something unrelated").passed)

    def test_any_names_pass(self) -> None:
        for names in (
            ("report_split-001.pdf", "report_split-002.pdf", "report_split-003.pdf"),
            ("part 1 (pages 1-3).pdf", "Part 2.pdf", "rest.pdf"),
            ("zzz.pdf", "mmm.pdf", "aaa.pdf"),  # the first group has the last name alphabetically
            ("report.1.pdf", "report.2.pdf", "report.3.pdf"),
        ):
            with self.subTest(names=names):
                self.split(names=names)
                self.assert_passes()
                for name in names:
                    (self.work / name).unlink()

    def test_files_in_a_subfolder_pass(self) -> None:
        self.split("parts")
        self.assert_passes()

    def test_files_at_different_depths_pass(self) -> None:
        build(self.work / "one.pdf", self.fixtures, [1, 2, 3])
        build(self.work / "parts" / "two.pdf", self.fixtures, [4, 5, 6])
        build(self.work / "parts" / "deeper" / "three.pdf", self.fixtures, [7, 8, 9])
        self.assert_passes()

    # wrong answers

    def test_no_output_fails(self) -> None:
        result = self.check()
        self.assert_fails_with(result, "pdf", "three", "0", "none", "missing")

    def test_two_pdfs_fail(self) -> None:
        build(self.work / "one.pdf", self.fixtures, [1, 2, 3])
        build(self.work / "two.pdf", self.fixtures, [4, 5, 6])
        self.assert_fails_with(self.check(), "three", "2", "missing", "p7")

    def test_a_fourth_pdf_fails(self) -> None:
        self.split()
        build(self.work / "extra.pdf", self.fixtures, [1, 2, 3])
        self.assert_fails_with(self.check(), "four", "4", "three", "extra", "more")

    def test_a_fourth_pdf_in_a_subfolder_fails(self) -> None:
        self.split()
        build(self.work / "backup" / "all.pdf", self.fixtures, list(range(1, 10)))
        self.assert_fails_with(self.check(), "four", "4", "three", "extra", "more")

    def test_a_copy_of_the_whole_report_as_a_fourth_pdf_fails(self) -> None:
        self.split()
        (self.work / "report copy.pdf").write_bytes((self.fixtures / "report.pdf").read_bytes())
        self.assert_fails_with(self.check(), "four", "4", "three", "extra", "more")

    def test_wrong_grouping_fails(self) -> None:
        for label, groups in {
            "1-2, 3-6, 7-9": ([1, 2], [3, 4, 5, 6], [7, 8, 9]),
            "1-4, 5-6, 7-9": ([1, 2, 3, 4], [5, 6], [7, 8, 9]),
            "1-3, 4-6, 8-9": ([1, 2, 3], [4, 5, 6], [8, 9]),
            "1-3, 4-6, 6-9": ([1, 2, 3], [4, 5, 6], [6, 7, 8, 9]),
            "every third page": ([1, 4, 7], [2, 5, 8], [3, 6, 9]),
            "one page each, nine PDFs": None,
        }.items():
            with self.subTest(grouping=label):
                if groups is None:
                    for number in range(1, 10):
                        build(self.work / f"page-{number}.pdf", self.fixtures, [number])
                else:
                    for index, pages in enumerate(groups):
                        build(self.work / f"part{index}.pdf", self.fixtures, pages)
                result = self.check()
                self.assert_fails_with(result, "page", "group", "p1", "p4", "p7")
                for path in list(self.work.glob("*.pdf")):
                    if path.name != "report.pdf":
                        path.unlink()

    def test_three_copies_of_the_first_group_fail(self) -> None:
        for name in ("one.pdf", "two.pdf", "three.pdf"):
            build(self.work / name, self.fixtures, [1, 2, 3])
        self.assert_fails_with(self.check(), "p4", "p7", "group", "page")

    def test_pages_out_of_order_fail(self) -> None:
        build(self.work / "one.pdf", self.fixtures, [3, 2, 1])
        build(self.work / "two.pdf", self.fixtures, [4, 5, 6])
        build(self.work / "three.pdf", self.fixtures, [7, 8, 9])
        self.assert_fails_with(self.check(), "order", "p3", "p1")

    def test_pages_out_of_order_in_the_last_group_fail(self) -> None:
        build(self.work / "one.pdf", self.fixtures, [1, 2, 3])
        build(self.work / "two.pdf", self.fixtures, [4, 5, 6])
        build(self.work / "three.pdf", self.fixtures, [7, 9, 8])
        self.assert_fails_with(self.check(), "order", "p9", "p8")

    def test_a_page_missing_from_a_group_fails(self) -> None:
        build(self.work / "one.pdf", self.fixtures, [1, 2, 3])
        build(self.work / "two.pdf", self.fixtures, [4, 6])
        build(self.work / "three.pdf", self.fixtures, [7, 8, 9])
        self.assert_fails_with(self.check(), "p5", "page", "group")

    def test_a_page_repeated_in_a_group_fails(self) -> None:
        build(self.work / "one.pdf", self.fixtures, [1, 2, 3, 3])
        build(self.work / "two.pdf", self.fixtures, [4, 5, 6])
        build(self.work / "three.pdf", self.fixtures, [7, 8, 9])
        self.assert_fails_with(self.check(), "p3", "page", "group")

    def test_an_unreadable_pdf_fails(self) -> None:
        build(self.work / "one.pdf", self.fixtures, [1, 2, 3])
        build(self.work / "two.pdf", self.fixtures, [4, 5, 6])
        whole = build(self.work / "three.pdf", self.fixtures, [7, 8, 9]).read_bytes()
        for label, data in {
            "text": b"P7 P8 P9\nthis is not a PDF\n",
            "empty": b"",
            "truncated": whole[:150],
        }.items():
            with self.subTest(third=label):
                (self.work / "three.pdf").write_bytes(data)
                result = self.check()
                self.assert_fails_with(result, "three.pdf", "valid", "read", "open", "unreadable")

    def test_a_png_named_pdf_fails(self) -> None:
        from PIL import Image

        build(self.work / "one.pdf", self.fixtures, [1, 2, 3])
        build(self.work / "two.pdf", self.fixtures, [4, 5, 6])
        Image.new("RGB", (40, 40), (200, 10, 10)).save(self.work / "three.pdf", format="PNG")
        self.assert_fails_with(self.check(), "three.pdf", "valid", "read", "open", "unreadable")

    def test_pages_without_their_label_text_fail(self) -> None:
        build(self.work / "one.pdf", self.fixtures, [1, 2, 3])
        build(self.work / "two.pdf", self.fixtures, [4, 5, 6])
        writer = PdfWriter()
        for _ in range(3):
            writer.add_blank_page(612, 792)
        with (self.work / "three.pdf").open("wb") as handle:
            writer.write(handle)
        self.assert_fails_with(self.check(), "p7", "label", "text", "group", "page")

    # the source

    def test_a_modified_report_pdf_fails_even_with_a_correct_split(self) -> None:
        self.split()
        with (self.work / "report.pdf").open("ab") as handle:
            handle.write(b"\n% tampered\n")
        result = self.check()
        self.assert_fails_with(result, "report.pdf", reasons=1)
        self.assert_fails_with(result, "unchanged", "sha", "hash", "modified", "changed")

    def test_a_deleted_report_pdf_fails_even_with_a_correct_split(self) -> None:
        # the tempting shortcut: split it and move it away
        self.split()
        (self.work / "report.pdf").unlink()
        self.assert_fails_with(self.check(), "report.pdf", "unchanged", "missing", "changed")

    def test_report_pdf_cut_down_to_its_first_group_fails(self) -> None:
        self.split()
        build(self.work / "report.pdf", self.fixtures, [1, 2, 3])
        self.assert_fails_with(self.check(), "report.pdf", "unchanged", "sha", "hash", "modified", "changed")

    def test_report_pdf_is_not_one_of_the_three(self) -> None:
        # only two other PDFs: report.pdf itself must not be counted as an answer
        build(self.work / "one.pdf", self.fixtures, [1, 2, 3])
        build(self.work / "two.pdf", self.fixtures, [4, 5, 6])
        self.assert_fails_with(self.check(), "three", "2", "p7", "missing")

    def test_check_does_not_modify_the_work_folder(self) -> None:
        self.split()
        self.assert_check_leaves_work_folder_alone()

    def test_check_does_not_modify_the_work_folder_when_it_fails(self) -> None:
        build(self.work / "one.pdf", self.fixtures, [3, 2, 1])
        self.assert_check_leaves_work_folder_alone()


@unittest.skipUnless(HAVE_JZR, NEEDS_JZR)
class PdfsplitJzrTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("pdfsplit")
        cls.task.prepare_fixtures(cls.fixtures)

    def test_jzr_pdf_split_with_three_groups_passes_the_check(self) -> None:
        run_jzr(["pdf", "split", "report.pdf", "--pages", "1-3;4-6;7-"], self.work)
        self.assertEqual(len(pdfs_under(self.work)), 4, pdfs_under(self.work))  # report.pdf and the three parts
        self.assert_passes()


if __name__ == "__main__":
    unittest.main()
