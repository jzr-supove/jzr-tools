"""Tests for the xlsx task (held out): sales.csv to sales.xlsx with real numbers.

The fixture is made once per class with the task's own prepare_fixtures; the correct workbook is
built from it with openpyxl (the runner venv's), the wrong ones differ from it in one way: numbers
stored as text, rows missing or changed, a wrong header, a file that is not an xlsx.
"""

from __future__ import annotations

import csv
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench.tasks import get_task  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from task_check_support import CheckCase  # noqa: E402

PROMPT = "Convert sales.csv to an Excel file, sales.xlsx. Numbers must be real numbers in Excel, not text."
HEADER = ["date", "region", "units", "price"]


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    return rows[0], rows[1:]


def write_xlsx(
    dest: Path,
    header: list[object] | None,
    rows: list[list[object]],
    *,
    first_sheet_empty: bool = False,
    extra_sheet: bool = False,
) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    if first_sheet_empty:
        ws = wb.create_sheet("Data")
    if header is not None:
        ws.append(header)
    for row in rows:
        ws.append(row)
    if extra_sheet:
        wb.create_sheet("Notes").append(["scratch"])
    wb.save(dest)
    return dest


class XlsxFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("xlsx")
        cls.task.prepare_fixtures(cls.fixtures)
        cls.header, cls.rows = read_csv(cls.fixtures / "sales.csv")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(self.task.prompt, PROMPT)

    def test_prepare_fixtures_writes_exactly_sales_csv(self) -> None:
        self.assertEqual([p.name for p in self.fixtures.iterdir()], ["sales.csv"])

    def test_header_row_is_date_region_units_price(self) -> None:
        first_line = (self.fixtures / "sales.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first_line, "date,region,units,price")
        self.assertEqual(self.header, HEADER)

    def test_it_has_40_data_rows_of_four_fields(self) -> None:
        self.assertEqual(len(self.rows), 40)
        for row in self.rows:
            self.assertEqual(len(row), 4, row)

    def test_units_are_whole_numbers_and_prices_have_two_decimals(self) -> None:
        for date, region, units, price in self.rows:
            self.assertTrue(date.strip() and region.strip(), (date, region))
            self.assertRegex(units, r"^\d+$")
            self.assertRegex(price, r"^\d+\.\d{2}$")

    def test_rows_differ_from_each_other(self) -> None:
        # otherwise "rows equal to the CSV's" cannot tell a missing row or a changed row from the right one
        self.assertEqual(len({tuple(r) for r in self.rows}), 40)
        self.assertGreater(len({r[1] for r in self.rows}), 1)
        self.assertGreater(len({r[2] for r in self.rows}), 5)
        self.assertGreater(len({r[3] for r in self.rows}), 5)

    def test_prices_are_not_all_whole_numbers(self) -> None:
        self.assertTrue(any(not re.fullmatch(r"\d+\.00", r[3]) for r in self.rows))

    def test_setup_copies_the_csv_into_the_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.task.setup(work, self.fixtures)
            self.assertEqual(
                (work / "sales.csv").read_bytes(),
                (self.fixtures / "sales.csv").read_bytes(),
            )
            self.assertFalse((work / "sales.xlsx").exists())


class XlsxCheckTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.fixtures = base / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("xlsx")
        cls.task.prepare_fixtures(cls.fixtures)
        header, text_rows = read_csv(cls.fixtures / "sales.csv")
        rows: list[list[object]] = [[d, r, int(u), float(p)] for d, r, u, p in text_rows]

        out = base / "results"
        out.mkdir()
        cls.results = out
        cls.rows = rows
        w = write_xlsx
        w(out / "good.xlsx", list(header), rows)
        # numeric but stored as float or int the other way round: still real numbers with the CSV's values
        w(
            out / "units-float.xlsx",
            list(header),
            [[d, r, float(u), p] for d, r, u, p in rows],
        )  # type: ignore[arg-type]
        w(out / "with-notes-sheet.xlsx", list(header), rows, extra_sheet=True)
        # numbers stored as text
        w(
            out / "units-text.xlsx",
            list(header),
            [[d, r, str(u), p] for d, r, u, p in rows],
        )
        w(
            out / "price-text.xlsx",
            list(header),
            [[d, r, u, f"{p:.2f}"] for d, r, u, p in rows],
        )
        w(
            out / "both-text.xlsx",
            list(header),
            [[d, r, str(u), f"{p:.2f}"] for d, r, u, p in rows],
        )
        w(out / "all-text.xlsx", list(header), [list(map(str, r)) for r in text_rows])
        # values changed
        changed_price = [list(r) for r in rows]
        changed_price[17][3] = round(float(rows[17][3]) + 0.01, 2)  # type: ignore[arg-type]
        w(out / "price-changed.xlsx", list(header), changed_price)
        changed_units = [list(r) for r in rows]
        changed_units[3][2] = int(rows[3][2]) + 1  # type: ignore[call-overload]
        w(out / "units-changed.xlsx", list(header), changed_units)
        changed_region = [list(r) for r in rows]
        changed_region[30][1] = "Nowhere"
        w(out / "region-changed.xlsx", list(header), changed_region)
        # row count and order
        w(out / "last-row-missing.xlsx", list(header), rows[:-1])
        w(out / "first-row-missing.xlsx", list(header), rows[1:])
        w(out / "extra-row.xlsx", list(header), [*rows, rows[0]])
        w(out / "no-rows.xlsx", list(header), [])
        w(out / "reversed.xlsx", list(header), rows[::-1])
        # header
        w(out / "header-renamed.xlsx", ["date", "region", "qty", "cost"], rows)
        w(out / "no-header.xlsx", None, rows)
        w(out / "header-reordered.xlsx", ["region", "date", "units", "price"], rows)
        # pandas.to_excel without index=False: an index column in front
        w(
            out / "index-column.xlsx",
            ["", *header],
            [[i, *r] for i, r in enumerate(rows)],
        )
        # the data is not on the first sheet
        w(out / "data-on-second-sheet.xlsx", list(header), rows, first_sheet_empty=True)
        # not an xlsx
        (out / "text.xlsx").write_text("this is not a workbook\n")
        shutil.copy(cls.fixtures / "sales.csv", out / "csv-named.xlsx")
        (out / "empty.xlsx").write_bytes(b"")
        with zipfile.ZipFile(out / "zip-named.xlsx", "w") as archive:
            archive.writestr("readme.txt", "a zip file, but not a workbook")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def install(self, name: str | None) -> None:
        if name is not None:
            shutil.copy(self.results / name, self.work / "sales.xlsx")

    def test_the_variants_are_what_they_are_meant_to_be(self) -> None:
        # Guards the inputs of this test, so a pass or a failure below means something.
        from openpyxl import load_workbook

        def first_data_row(name: str) -> list[object]:
            wb = load_workbook(self.results / name)
            try:
                return [c.value for c in wb.worksheets[0][2]]
            finally:
                wb.close()

        def cells(name: str, row: int = 2) -> list[type]:
            wb = load_workbook(self.results / name)
            try:
                return [type(c.value) for c in wb.worksheets[0][row]]
            finally:
                wb.close()

        self.assertEqual(cells("good.xlsx"), [str, str, int, float])
        self.assertEqual(cells("units-text.xlsx"), [str, str, str, float])
        self.assertEqual(cells("price-text.xlsx"), [str, str, int, str])
        self.assertEqual(cells("both-text.xlsx"), [str, str, str, str])
        self.assertEqual(first_data_row("good.xlsx"), self.rows[0])
        wb = load_workbook(self.results / "data-on-second-sheet.xlsx")
        self.assertEqual(wb.worksheets[0].max_row, 1)
        self.assertEqual(wb.worksheets[1].max_row, 41)
        wb.close()
        wb = load_workbook(self.results / "last-row-missing.xlsx")
        self.assertEqual(wb.worksheets[0].max_row, 40)
        wb.close()
        self.assertNotEqual(self.rows[17][3], round(float(self.rows[17][3]) + 0.01, 2))  # type: ignore[arg-type]

    def test_correct_workbook_passes(self) -> None:
        self.install("good.xlsx")
        self.assert_passes()

    def test_final_text_does_not_matter(self) -> None:
        self.install("good.xlsx")
        self.assertTrue(self.check("").passed)
        self.assertTrue(self.check("something unrelated").passed)

    def test_whole_numbers_stored_as_floats_pass(self) -> None:
        self.install("units-float.xlsx")
        self.assert_passes()

    def test_an_extra_sheet_after_the_data_sheet_passes(self) -> None:
        self.install("with-notes-sheet.xlsx")
        self.assert_passes()

    def test_missing_workbook_fails(self) -> None:
        self.install(None)
        self.assert_fails_with(self.check(), "sales.xlsx", "exist", "missing", "not found")

    def test_units_stored_as_text_fail(self) -> None:
        self.install("units-text.xlsx")
        self.assert_fails_with(self.check(), "units", reasons=1)

    def test_price_stored_as_text_fails(self) -> None:
        self.install("price-text.xlsx")
        self.assert_fails_with(self.check(), "price", reasons=1)

    def test_units_and_price_both_text_fail(self) -> None:
        for name in ("both-text.xlsx", "all-text.xlsx"):
            with self.subTest(variant=name):
                self.install(name)
                result = self.check()
                self.assert_fails_with(result, "numeric", "text", "number")
                joined = " ".join(result.details).lower()
                self.assertIn("units", joined)
                self.assertIn("price", joined)

    def test_a_changed_price_fails(self) -> None:
        self.install("price-changed.xlsx")
        self.assert_fails_with(self.check(), "price", "value", "row", "differ", "csv", "match")

    def test_a_changed_units_value_fails(self) -> None:
        self.install("units-changed.xlsx")
        self.assert_fails_with(self.check(), "units", "value", "row", "differ", "csv", "match")

    def test_a_changed_region_fails(self) -> None:
        self.install("region-changed.xlsx")
        self.assert_fails_with(self.check(), "region", "value", "row", "differ", "csv", "match")

    def test_missing_rows_fail(self) -> None:
        for name in ("last-row-missing.xlsx", "first-row-missing.xlsx", "no-rows.xlsx"):
            with self.subTest(variant=name):
                self.install(name)
                self.assert_fails_with(self.check(), "row", "40", "39", "0")

    def test_an_extra_row_fails(self) -> None:
        self.install("extra-row.xlsx")
        self.assert_fails_with(self.check(), "row", "40", "41")

    def test_rows_in_another_order_fail(self) -> None:
        self.install("reversed.xlsx")
        self.assert_fails_with(self.check(), "row", "order", "value", "differ", "csv", "match")

    def test_renamed_header_fails(self) -> None:
        self.install("header-renamed.xlsx")
        self.assert_fails_with(self.check(), "header", "date,region,units,price", "qty")

    def test_reordered_header_fails(self) -> None:
        self.install("header-reordered.xlsx")
        self.assert_fails_with(self.check(), "header", "date,region,units,price")

    def test_missing_header_row_fails(self) -> None:
        self.install("no-header.xlsx")
        self.assert_fails_with(self.check(), "header", "date,region,units,price")

    def test_index_column_in_front_fails(self) -> None:
        self.install("index-column.xlsx")
        self.assert_fails_with(self.check(), "header", "column", "date,region,units,price")

    def test_data_on_the_second_sheet_fails(self) -> None:
        self.install("data-on-second-sheet.xlsx")
        self.assert_fails_with(self.check(), "header", "sheet", "row")

    def test_text_file_named_sales_xlsx_fails(self) -> None:
        self.install("text.xlsx")
        self.assert_fails_with(self.check(), "xlsx", "open", "workbook", "valid", "read", reasons=1)

    def test_csv_renamed_to_xlsx_fails(self) -> None:
        self.install("csv-named.xlsx")
        self.assert_fails_with(self.check(), "xlsx", "open", "workbook", "valid", "read", reasons=1)

    def test_empty_file_named_sales_xlsx_fails(self) -> None:
        self.install("empty.xlsx")
        self.assert_fails_with(
            self.check(),
            "xlsx",
            "open",
            "workbook",
            "valid",
            "read",
            "empty",
            reasons=1,
        )

    def test_zip_that_is_not_a_workbook_fails(self) -> None:
        self.install("zip-named.xlsx")
        self.assert_fails_with(self.check(), "xlsx", "open", "workbook", "valid", "read", reasons=1)

    def test_modified_csv_fails_even_with_a_correct_workbook(self) -> None:
        self.install("good.xlsx")
        with (self.work / "sales.csv").open("a", encoding="utf-8") as handle:
            handle.write("2099-01-01,Nowhere,1,1.00\n")
        result = self.check()
        self.assert_fails_with(result, "sales.csv", reasons=1)
        self.assert_fails_with(result, "unchanged", "sha", "hash", "modified", "changed")

    def test_csv_with_a_value_edited_in_place_fails(self) -> None:
        # same length, one digit different: only a content hash notices
        self.install("good.xlsx")
        path = self.work / "sales.csv"
        data = path.read_bytes()
        index = data.rindex(b"1")
        path.write_bytes(data[:index] + b"2" + data[index + 1 :])
        self.assert_fails_with(self.check(), "sales.csv", "unchanged", "sha", "hash", "modified", "changed")

    def test_deleted_csv_fails_even_with_a_correct_workbook(self) -> None:
        self.install("good.xlsx")
        (self.work / "sales.csv").unlink()
        self.assert_fails_with(self.check(), "sales.csv", "unchanged", "sha", "hash", "missing", "changed")

    def test_check_does_not_modify_the_work_folder(self) -> None:
        self.install("good.xlsx")
        self.assert_check_leaves_work_folder_alone()

    def test_check_does_not_modify_the_work_folder_when_it_fails(self) -> None:
        self.install("units-text.xlsx")
        self.assert_check_leaves_work_folder_alone()


if __name__ == "__main__":
    unittest.main()
