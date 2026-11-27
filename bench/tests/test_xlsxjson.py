"""Tests for the xlsxjson task (held out): orders.xlsx (two sheets) to orders.json.

The fixture is made once per class with the task's own prepare_fixtures. Correct answers are written
with the json module from the workbook's cells (read with openpyxl, the runner venv's); each wrong one
differs from a correct one in exactly one way.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench.tasks import HELD_OUT, TASKS, get_task  # noqa: E402
from jzr_support import HAVE_JZR, NEEDS_JZR, run_jzr  # noqa: E402
from openpyxl import load_workbook  # noqa: E402
from task_check_support import CheckCase  # noqa: E402

PROMPT = (
    "Convert orders.xlsx to JSON, orders.json: an object with one key for each sheet, holding that sheet's rows "
    "as objects keyed by the header row. Numbers must stay numbers."
)
HEADERS = {"Orders": ["id", "customer", "total", "date"], "Customers": ["customer", "country"]}
ROW_COUNTS = {"Orders": 12, "Customers": 5}


def read_sheets(path: Path) -> dict[str, tuple[list[str], list[list[object]]]]:
    """Sheet name -> (header, data rows) as openpyxl reads them (dates as datetime)."""
    book = load_workbook(path, data_only=True)
    try:
        sheets = {}
        for sheet in book.worksheets:
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
            sheets[sheet.title] = ([str(cell) for cell in rows[0]], rows[1:])
        return sheets
    finally:
        book.close()


def expected_json(path: Path, *, date_text=lambda d: d.date().isoformat(), number=lambda v: v) -> dict:
    """What a correct orders.json holds, with the way dates and numbers are written chosen by the caller."""
    result: dict[str, list[dict]] = {}
    for name, (header, rows) in read_sheets(path).items():
        records = []
        for row in rows:
            record = {}
            for key, value in zip(header, row, strict=True):
                if isinstance(value, dt.datetime):
                    value = date_text(value)
                elif isinstance(value, (int, float)):
                    value = number(value)
                record[key] = value
            records.append(record)
        result[name] = records
    return result


class XlsxjsonTaskTest(unittest.TestCase):
    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(get_task("xlsxjson").prompt, PROMPT)

    def test_it_is_named_xlsxjson_and_registered_as_held_out(self) -> None:
        task = get_task("xlsxjson")
        self.assertEqual(task.name, "xlsxjson")
        self.assertIs(TASKS["xlsxjson"], task)
        self.assertIn("xlsxjson", HELD_OUT)


class XlsxjsonFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("xlsxjson")
        cls.task.prepare_fixtures(cls.fixtures)
        cls.sheets = read_sheets(cls.fixtures / "orders.xlsx")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_prepare_fixtures_writes_exactly_orders_xlsx(self) -> None:
        self.assertEqual([p.name for p in self.fixtures.iterdir()], ["orders.xlsx"])

    def test_it_has_the_two_sheets_orders_and_customers(self) -> None:
        self.assertEqual(set(self.sheets), {"Orders", "Customers"})

    def test_the_header_rows(self) -> None:
        for name, header in HEADERS.items():
            with self.subTest(sheet=name):
                self.assertEqual(self.sheets[name][0], header)

    def test_twelve_orders_and_five_customers(self) -> None:
        for name, count in ROW_COUNTS.items():
            with self.subTest(sheet=name):
                self.assertEqual(len(self.sheets[name][1]), count)

    def test_ids_are_whole_numbers_and_distinct(self) -> None:
        ids = [row[0] for row in self.sheets["Orders"][1]]
        for value in ids:
            self.assertIsInstance(value, int)
            self.assertNotIsInstance(value, bool)
        self.assertEqual(len(set(ids)), 12)

    def test_totals_are_numbers_with_two_decimals_and_not_all_whole(self) -> None:
        totals = [row[2] for row in self.sheets["Orders"][1]]
        for value in totals:
            self.assertIsInstance(value, (int, float))
            self.assertAlmostEqual(value, round(value, 2), places=9)
        self.assertTrue(any(value != int(value) for value in totals))
        self.assertGreater(len(set(totals)), 6)

    def test_dates_are_real_date_cells(self) -> None:
        book = load_workbook(self.fixtures / "orders.xlsx")
        try:
            cells = [row[3] for row in book["Orders"].iter_rows(min_row=2)]
            self.assertEqual(len(cells), 12)
            for cell in cells:
                self.assertTrue(cell.is_date, cell.coordinate)
                self.assertIsInstance(cell.value, dt.date)
            self.assertGreater(len({cell.value.date() for cell in cells}), 6)
        finally:
            book.close()

    def test_customers_and_countries_are_text(self) -> None:
        for name, columns in (("Orders", (1,)), ("Customers", (0, 1))):
            for row in self.sheets[name][1]:
                for column in columns:
                    with self.subTest(sheet=name, row=row, column=column):
                        self.assertIsInstance(row[column], str)
                        self.assertTrue(row[column].strip())

    def test_the_five_customers_are_different(self) -> None:
        names = [row[0] for row in self.sheets["Customers"][1]]
        self.assertEqual(len(set(names)), 5)

    def test_setup_copies_it_into_the_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.task.setup(work, self.fixtures)
            self.assertEqual([p.name for p in work.iterdir()], ["orders.xlsx"])
            self.assertEqual((work / "orders.xlsx").read_bytes(), (self.fixtures / "orders.xlsx").read_bytes())
            self.assertFalse((work / "orders.json").exists())

    def test_setup_prepares_an_empty_fixture_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixtures, work = Path(tmp) / "f", Path(tmp) / "w"
            self.task.setup(work, fixtures)
            self.assertTrue((fixtures / "orders.xlsx").is_file())
            self.assertTrue((work / "orders.xlsx").is_file())


class XlsxjsonCheckTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("xlsxjson")
        cls.task.prepare_fixtures(cls.fixtures)
        cls.good = expected_json(cls.fixtures / "orders.xlsx")

    def write(self, data: object, **dump: Any) -> None:
        (self.work / "orders.json").write_text(json.dumps(data, **dump), encoding="utf-8")

    def changed(self, edit) -> dict:
        """A copy of the correct JSON after `edit(copy)`."""
        data = copy.deepcopy(self.good)
        edit(data)
        return data

    def test_the_inputs_of_these_tests_are_what_they_are_meant_to_be(self) -> None:
        # Guards the inputs of this test, so a pass or a failure below means something.
        self.assertEqual(set(self.good), {"Orders", "Customers"})
        self.assertEqual({k: len(v) for k, v in self.good.items()}, ROW_COUNTS)
        first = self.good["Orders"][0]
        self.assertEqual(set(first), set(HEADERS["Orders"]))
        self.assertRegex(first["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIsInstance(first["id"], int)
        self.assertIsInstance(first["total"], (int, float))

    # right answers

    def test_a_correct_file_passes(self) -> None:
        self.write(self.good)
        self.assert_passes()

    def test_final_text_does_not_matter(self) -> None:
        self.write(self.good)
        self.assertTrue(self.check("").passed)
        self.assertTrue(self.check("something unrelated").passed)

    def test_layout_and_order_of_keys_do_not_matter(self) -> None:
        reordered = {
            name: [dict(reversed(list(row.items()))) for row in rows] for name, rows in reversed(self.good.items())
        }
        layouts: dict[str, dict[str, object]] = {
            "indented": {"indent": 2},
            "compact": {"separators": (",", ":")},
            "sorted keys": {"sort_keys": True},
        }
        for label, dump in layouts.items():
            with self.subTest(layout=label):
                self.write(self.good, **dump)
                self.assert_passes()
        self.write(reordered, indent=4)
        self.assert_passes()

    def test_utf8_text_written_as_it_is_passes(self) -> None:
        self.write(self.good, ensure_ascii=False)
        self.assert_passes()

    def test_dates_may_have_a_time_part(self) -> None:
        for label, text in {
            "T": lambda d: d.strftime("%Y-%m-%dT%H:%M:%S"),
            "space": lambda d: d.strftime("%Y-%m-%d %H:%M:%S"),
            "Z": lambda d: d.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
            "milliseconds": lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.000"),
        }.items():
            with self.subTest(date=label):
                self.write(expected_json(self.fixtures / "orders.xlsx", date_text=text))
                self.assert_passes()

    def test_whole_numbers_may_be_written_as_7_or_7_0(self) -> None:
        for label, number in {"float": float, "int where whole": lambda v: int(v) if v == int(v) else v}.items():
            with self.subTest(numbers=label):
                self.write(expected_json(self.fixtures / "orders.xlsx", number=number))
                self.assert_passes()
        self.assertIn(".0,", json.dumps(expected_json(self.fixtures / "orders.xlsx", number=float)))

    def test_numbers_within_1e_9_pass(self) -> None:
        def nudge(data: dict) -> None:
            for row in data["Orders"]:
                row["total"] = row["total"] + 1e-12

        self.write(self.changed(nudge))
        self.assert_passes()

    # wrong answers

    def test_a_missing_file_fails(self) -> None:
        self.assert_fails_with(self.check(), "orders.json", "exist", "missing", "not found")

    def test_invalid_json_fails(self) -> None:
        text = json.dumps(self.good)
        for label, content in {
            "truncated": text[: len(text) // 2],
            "single quotes": text.replace('"', "'"),
            "trailing comma": text[:-1] + ",}",
            "text": "the orders are in the workbook\n",
            "empty": "",
        }.items():
            with self.subTest(content=label):
                (self.work / "orders.json").write_text(content, encoding="utf-8")
                self.assert_fails_with(self.check(), "json", "valid", "parse", "read", reasons=1)

    def test_something_other_than_an_object_fails(self) -> None:
        for label, data in {
            "list of the Orders rows": list(self.good["Orders"]),
            "string": "orders",
            "null": None,
        }.items():
            with self.subTest(top_level=label):
                self.write(data)
                self.assert_fails_with(self.check(), "object", "key", "sheet")

    def test_a_missing_sheet_fails(self) -> None:
        for sheet in ("Orders", "Customers"):
            with self.subTest(left_out=sheet):
                self.write({k: v for k, v in self.good.items() if k != sheet})
                result = self.check()
                self.assert_fails_with(result, sheet, "key", "sheet")

    def test_an_extra_key_fails(self) -> None:
        self.write({**self.good, "Sheet1": []})
        self.assert_fails_with(self.check(), "sheet1", "key", "sheet", "extra")

    def test_a_missing_row_fails(self) -> None:
        for sheet, count in (("Orders", 11), ("Customers", 4)):
            with self.subTest(sheet=sheet):
                self.write(self.changed(lambda data, sheet=sheet: data[sheet].pop()))
                result = self.check()
                self.assert_fails_with(result, sheet, "row", str(count), str(ROW_COUNTS[sheet]))

    def test_an_extra_row_fails(self) -> None:
        self.write(self.changed(lambda data: data["Customers"].append(dict(data["Customers"][0]))))
        self.assert_fails_with(self.check(), "customers", "row", "6", "5")

    def test_an_empty_list_for_a_sheet_fails(self) -> None:
        self.write(self.changed(lambda data: data.update(Customers=[])))
        self.assert_fails_with(self.check(), "customers", "row", "0", "5")

    def test_rows_that_are_not_objects_fail(self) -> None:
        # the header row and then plain lists: what a naive dump of the cells gives
        rows = [[*self.good["Orders"][0]], *[list(row.values()) for row in self.good["Orders"]]]
        self.write({**self.good, "Orders": rows})
        self.assert_fails_with(self.check(), "orders", "object", "row", "key")

    def test_wrong_header_keys_fail(self) -> None:
        def rename(key: str, new: str):
            def edit(data: dict) -> None:
                for row in data["Orders"]:
                    row[new] = row.pop(key)

            return edit

        for label, edit in {
            "capitalised": rename("total", "Total"),
            "another name": rename("total", "amount"),
            "spaced": rename("customer", "customer "),
            "default column letters": lambda data: data.update(
                Orders=[dict(zip("ABCD", row.values(), strict=True)) for row in data["Orders"]]
            ),
        }.items():
            with self.subTest(keys=label):
                self.write(self.changed(edit))
                self.assert_fails_with(self.check(), "key", "header", "column")

    def test_a_missing_or_extra_key_in_a_row_fails(self) -> None:
        for label, edit in {
            "a key missing": lambda data: data["Orders"][3].pop("date"),
            "an index key added": lambda data: data["Orders"][0].update(index=0),
            "a key missing in Customers": lambda data: data["Customers"][2].pop("country"),
        }.items():
            with self.subTest(case=label):
                self.write(self.changed(edit))
                self.assert_fails_with(self.check(), "key", "header", "column")

    def test_numbers_written_as_strings_fail(self) -> None:
        for column, make in {"id": str, "total": lambda v: f"{v:.2f}"}.items():
            with self.subTest(column=column):
                self.write(
                    self.changed(lambda data, c=column, m=make: data["Orders"][4].update({c: m(data["Orders"][4][c])}))
                )
                result = self.check()
                self.assert_fails_with(result, column, "number", "string", "text", reasons=1)

    def test_every_number_a_string_fails_and_both_columns_are_named(self) -> None:
        def stringify(data: dict) -> None:
            for row in data["Orders"]:
                row["id"], row["total"] = str(row["id"]), str(row["total"])

        self.write(self.changed(stringify))
        result = self.check()
        self.assert_fails_with(result, "number", "string", "text")
        self.assert_names(result, "id", "total")

    def test_a_different_number_fails(self) -> None:
        for column, value in {"id": 99, "total": 1.0}.items():
            with self.subTest(column=column):
                self.write(self.changed(lambda data, c=column, v=value: data["Orders"][7].update({c: v})))
                self.assert_fails_with(self.check(), column, "value", "differ", "row", "match", "equal")

    def test_a_total_off_by_a_cent_fails(self) -> None:
        self.write(self.changed(lambda data: data["Orders"][0].update(total=data["Orders"][0]["total"] + 0.01)))
        self.assert_fails_with(self.check(), "total", "value", "differ", "row", "match", "equal")

    def test_a_changed_text_value_fails(self) -> None:
        for sheet, column in (("Orders", "customer"), ("Customers", "country"), ("Customers", "customer")):
            with self.subTest(sheet=sheet, column=column):
                self.write(self.changed(lambda data, s=sheet, c=column: data[s][1].update({c: data[s][1][c] + "!"})))
                self.assert_fails_with(self.check(), column, "value", "differ", "row", "match", "equal", "text")

    def test_dates_that_do_not_start_with_the_date_fail(self) -> None:
        first = self.good["Orders"][0]["date"]
        year, month, day = first.split("-")
        serial = (dt.date.fromisoformat(first) - dt.date(1899, 12, 30)).days
        epoch_ms = int(dt.datetime.fromisoformat(first).timestamp() * 1000)
        for label, value in {
            "day first": f"{day}/{month}/{year}",
            "month first": f"{month}/{day}/{year}",
            "excel serial number": serial,
            "milliseconds since 1970": epoch_ms,
            "null": None,
            "text with the date inside": f"ordered on {first}",
            "a different day": (dt.date.fromisoformat(first) + dt.timedelta(days=1)).isoformat(),
        }.items():
            with self.subTest(date=label):
                self.write(self.changed(lambda data, v=value: data["Orders"][0].update(date=v)))
                self.assert_fails_with(self.check(), "date", reasons=1)

    # the source

    def test_a_modified_workbook_fails_even_with_correct_json(self) -> None:
        self.write(self.good)
        with (self.work / "orders.xlsx").open("ab") as handle:
            handle.write(b"\0")
        result = self.check()
        self.assert_fails_with(result, "orders.xlsx", reasons=1)
        self.assert_fails_with(result, "unchanged", "sha", "hash", "modified", "changed")

    def test_a_deleted_workbook_fails_even_with_correct_json(self) -> None:
        self.write(self.good)
        (self.work / "orders.xlsx").unlink()
        self.assert_fails_with(self.check(), "orders.xlsx", "unchanged", "missing", "changed")

    def test_check_does_not_modify_the_work_folder(self) -> None:
        self.write(self.good)
        self.assert_check_leaves_work_folder_alone()

    def test_check_does_not_modify_the_work_folder_when_it_fails(self) -> None:
        self.write(self.good["Orders"])
        self.assert_check_leaves_work_folder_alone()

    def assert_names(self, result, *needles: str) -> None:
        joined = " ".join(result.details).lower()
        for needle in needles:
            self.assertIn(needle.lower(), joined, result.details)


@unittest.skipUnless(HAVE_JZR, NEEDS_JZR)
class XlsxjsonJzrTest(CheckCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.task = get_task("xlsxjson")
        cls.task.prepare_fixtures(cls.fixtures)

    def test_jzr_data_convert_to_json_passes_the_check(self) -> None:
        run_jzr(["data", "convert", "orders.xlsx", "--to", "json", "-o", "orders.json"], self.work)
        self.assertTrue((self.work / "orders.json").is_file())
        self.assert_passes()


if __name__ == "__main__":
    unittest.main()
