"""The xlsx summary of `jzr data convert` counts how the data cells were stored.

`docs/features/agent-cost/spec.md`, round 3, change 7: after the sheet and row
counts and before the size, the summary lists the data cells (header cells are
not counted) by kind, only the kinds that occur, in the order number, true/false,
empty, text: `wrote sales.xlsx (1 sheet, 40 rows; 80 number cells, 80 text cells, 9.9 kB)`.
An int too long for Excel is written as text and counts as text. A count of 1
is singular. The summaries of the other targets are unchanged.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core.dataconv import ReadOptions, WriteOptions, convert
from tests.size_fixtures import size_text

runner = CliRunner()


def summary_of(source: str, data: str, *, types: bool = False) -> str:
    result = convert(
        data.encode("utf-8"),
        source=source,
        target="xlsx",
        read=ReadOptions(types=types),
        write=WriteOptions(),
    )
    return result.summary


def stored_kinds(workbook: bytes) -> dict[str, int]:
    """Count the data cells (every row after the first) as openpyxl reads them back."""
    from openpyxl import load_workbook

    counts = {"number": 0, "true/false": 0, "empty": 0, "text": 0}
    for sheet in load_workbook(io.BytesIO(workbook)).worksheets:
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if cell.value is None:
                    counts["empty"] += 1
                elif isinstance(cell.value, bool):
                    counts["true/false"] += 1
                elif isinstance(cell.value, (int, float)):
                    counts["number"] += 1
                else:
                    counts["text"] += 1
    return counts


class CoreSummaryTests(unittest.TestCase):
    def test_all_four_kinds_come_in_the_order_number_true_false_empty_text(
        self,
    ) -> None:
        # The keys are in the reverse of the order the summary must use.
        data = '[{"t": "x", "e": null, "b": true, "n": 1}]'

        self.assertEqual(
            summary_of("json", data),
            "1 sheet, 1 row; 1 number cell, 1 true/false cell, 1 empty cell, 1 text cell",
        )

    def test_counts_above_one_are_plural_and_use_thousands_separators(self) -> None:
        rows = json.dumps(
            [{"n": i, "b": i % 2 == 0, "e": None, "t": "x"} for i in range(1204)]
        )

        self.assertEqual(
            summary_of("json", rows),
            "1 sheet, 1,204 rows; 1,204 number cells, 1,204 true/false cells, "
            "1,204 empty cells, 1,204 text cells",
        )

    def test_only_the_kinds_that_occur_are_listed(self) -> None:
        self.assertEqual(
            summary_of("json", '[{"a": 1, "b": 2.5}, {"a": 3, "b": 4}]'),
            "1 sheet, 2 rows; 4 number cells",
        )
        self.assertEqual(
            summary_of("json", '[{"a": true, "b": "x"}]'),
            "1 sheet, 1 row; 1 true/false cell, 1 text cell",
        )

    def test_header_cells_are_not_counted(self) -> None:
        # Three header cells, one data cell: counting the header would say 4 text cells.
        self.assertEqual(
            summary_of("json", '[{"first": 1, "second": null, "third": null}]'),
            "1 sheet, 1 row; 1 number cell, 2 empty cells",
        )

    def test_a_key_that_a_row_lacks_is_an_empty_cell(self) -> None:
        self.assertEqual(
            summary_of("json", '[{"a": 1, "b": 2}, {"a": 3}]'),
            "1 sheet, 2 rows; 3 number cells, 1 empty cell",
        )

    def test_a_table_without_data_rows_has_no_cell_counts(self) -> None:
        self.assertEqual(summary_of("json", "[]"), "1 sheet, 0 rows")

    def test_a_grid_of_arrays_counts_every_cell_because_it_has_no_header(self) -> None:
        self.assertEqual(
            summary_of("json", '[["a", "b"], [1, 2]]'),
            "1 sheet, 2 rows; 2 number cells, 2 text cells",
        )

    def test_csv_with_types_counts_numbers_true_false_and_empty_cells(self) -> None:
        csv_text = "id,price,paid,note,name\n1,2.5,true,,Ann\n2,3,false,,Bob\n"

        self.assertEqual(
            summary_of("csv", csv_text, types=True),
            "1 sheet, 2 rows; 4 number cells, 2 true/false cells, 2 empty cells, 2 text cells",
        )

    def test_csv_without_types_is_all_text(self) -> None:
        csv_text = "id,price,paid,name\n1,2.5,true,Ann\n2,3,false,Bob\n"

        self.assertEqual(summary_of("csv", csv_text), "1 sheet, 2 rows; 8 text cells")

    def test_nested_and_array_values_are_written_as_text(self) -> None:
        data = json.dumps(
            [
                {
                    "tags": ["a", "b"],
                    "nums": [1, 2],
                    "deep": [{"k": 1}, [2]],
                    "id": 7,
                }
            ]
        )

        self.assertEqual(
            summary_of("json", data), "1 sheet, 1 row; 1 number cell, 3 text cells"
        )

    def test_nested_objects_count_by_the_values_inside_them(self) -> None:
        data = '[{"id": 1, "user": {"name": "Ann", "age": 30, "admin": false}}]'

        self.assertEqual(
            summary_of("json", data),
            "1 sheet, 1 row; 2 number cells, 1 true/false cell, 1 text cell",
        )

    def test_an_int_too_long_for_excel_counts_as_text(self) -> None:
        data = '[{"ok": 999999999999999, "big": 1234567890123456789, "neg": -1234567890123456}]'

        self.assertEqual(
            summary_of("json", data), "1 sheet, 1 row; 1 number cell, 2 text cells"
        )

    def test_the_counts_are_summed_across_every_sheet(self) -> None:
        data = json.dumps(
            {
                "people": [{"name": "Ann", "age": 30}, {"name": "Bob", "age": None}],
                "places": [{"city": "Oslo", "pop": 700000, "capital": True}],
                "empty": [],
            }
        )

        self.assertEqual(
            summary_of("json", data),
            "3 sheets, 3 rows; 2 number cells, 1 true/false cell, 1 empty cell, 3 text cells",
        )

    def test_the_counts_match_the_cells_in_the_written_workbook(self) -> None:
        data = json.dumps(
            [
                {
                    "n": 1,
                    "f": 2.5,
                    "b": True,
                    "e": None,
                    "t": "x",
                    "l": [1, 2],
                    "big": 10**20,
                },
                {
                    "n": 2,
                    "f": 0.5,
                    "b": False,
                    "e": None,
                    "t": "y",
                    "l": ["q"],
                    "big": 5,
                },
            ]
        )
        result = convert(
            data.encode("utf-8"),
            source="json",
            target="xlsx",
            read=ReadOptions(),
            write=WriteOptions(),
        )

        kinds = stored_kinds(result.data)

        self.assertEqual(kinds, {"number": 5, "true/false": 2, "empty": 2, "text": 5})
        self.assertEqual(
            result.summary,
            "1 sheet, 2 rows; 5 number cells, 2 true/false cells, 2 empty cells, 5 text cells",
        )

    def test_other_targets_have_the_summaries_they_had(self) -> None:
        data = '[{"a": 1, "b": true, "c": null, "d": "x"}, {"a": 2, "b": false, "c": null, "d": "y"}]'
        for target, expected in (
            ("csv", "2 rows"),
            ("jsonl", "2 lines"),
            ("json", ""),
            ("yaml", ""),
        ):
            with self.subTest(target=target):
                result = convert(
                    data.encode("utf-8"),
                    source="json",
                    target=target,
                    read=ReadOptions(),
                    write=WriteOptions(),
                )
                self.assertEqual(result.summary, expected)


class CliLineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-xlsx-counts-")
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name).resolve()
        old = os.getcwd()
        os.chdir(self.folder)
        self.addCleanup(os.chdir, old)
        config = mock.patch.dict(
            os.environ, {"JZR_CONFIG_DIR": str(self.folder / ".config")}
        )
        config.start()
        self.addCleanup(config.stop)

    def convert(self, *args: str):  # noqa: ANN201
        return runner.invoke(app, ["data", "convert", *args])

    def test_the_wrote_line_has_the_counts_between_the_rows_and_the_size(self) -> None:
        lines = ["id,qty,name,region"]
        lines += [f"{i},{i * 2},item {i},north" for i in range(40)]
        (self.folder / "sales.csv").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

        result = self.convert("sales.csv", "--types", "-o", "sales.xlsx")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stderr.splitlines(),
            [
                "wrote sales.xlsx (1 sheet, 40 rows; 80 number cells, 80 text cells, "
                f"{size_text('sales.xlsx')})"
            ],
        )
        self.assertEqual(result.stdout, "")

    def test_a_csv_without_types_says_text_and_still_gets_its_note(self) -> None:
        (self.folder / "a.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        result = self.convert("a.csv", "-o", "a.xlsx")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stderr.splitlines()
        self.assertEqual(
            lines[0],
            f"wrote a.xlsx (1 sheet, 1 row; 2 text cells, {size_text('a.xlsx')})",
        )
        self.assertTrue(lines[1].startswith("note: CSV cells were kept as text"), lines)

    def test_json_with_every_kind_and_a_long_number(self) -> None:
        (self.folder / "d.json").write_text(
            json.dumps(
                [{"n": 1, "b": True, "e": None, "t": "x", "big": 12345678901234567890}]
            ),
            encoding="utf-8",
        )

        result = self.convert("d.json", "-o", "d.xlsx")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stderr.splitlines()
        self.assertEqual(
            lines[0],
            "wrote d.xlsx (1 sheet, 1 row; 1 number cell, 1 true/false cell, 1 empty cell, "
            f"2 text cells, {size_text('d.xlsx')})",
        )
        self.assertTrue(
            lines[1].startswith("note: numbers with more than 15 digits"), lines
        )

    def test_other_targets_wrote_lines_are_unchanged(self) -> None:
        (self.folder / "d.json").write_text('[{"a": 1, "b": true}]', encoding="utf-8")

        result = self.convert("d.json", "-o", "d.csv")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stderr.splitlines(), [f"wrote d.csv (1 row, {size_text('d.csv')})"]
        )


if __name__ == "__main__":
    unittest.main()
