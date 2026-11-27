"""Convert a CSV to an Excel workbook with real numbers (held out)."""

from __future__ import annotations

import csv
import datetime as dt
import random
from pathlib import Path

from .base import CheckResult, Task, sources_unchanged

PROMPT = "Convert sales.csv to an Excel file, sales.xlsx. Numbers must be real numbers in Excel, not text."
HEADER = ["date", "region", "units", "price"]
ROWS = 40
NUMERIC = ("units", "price")


def _prepare(fixture_dir: Path) -> None:
    rng = random.Random(42)
    regions = ["North", "South", "East", "West"]
    start = dt.date(2026, 1, 5)
    lines = [",".join(HEADER)]
    for index in range(ROWS):
        day = start + dt.timedelta(days=index * 3)
        lines.append(
            f"{day.isoformat()},{rng.choice(regions)},{rng.randint(1, 250)},{rng.randint(199, 99999) / 100:.2f}"
        )
    (fixture_dir / "sales.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _same(column: str, cell: object, expected: str) -> bool:
    if column in NUMERIC:
        try:
            return abs(float(cell) - float(expected)) < 1e-9  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
    if isinstance(cell, dt.datetime):
        cell = cell.date()
    if isinstance(cell, dt.date):
        return cell.isoformat() == expected
    return str(cell) == expected


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    from openpyxl import load_workbook

    problems = sources_unchanged(work_dir, fixture_dir, ["sales.csv"])
    book_path = work_dir / "sales.xlsx"
    if not book_path.is_file():
        problems.append("sales.xlsx does not exist")
        return CheckResult(False, problems)
    try:
        book = load_workbook(book_path, read_only=True, data_only=True)
        sheet = book.worksheets[0]
        rows = [list(row) for row in sheet.iter_rows(values_only=True)]
        book.close()
    except Exception as error:  # openpyxl raises several kinds for files that are not workbooks
        problems.append(f"sales.xlsx is not a valid xlsx workbook that can be read (empty?): {error}")
        return CheckResult(False, problems)

    with (fixture_dir / "sales.csv").open(newline="", encoding="utf-8") as handle:
        expected = list(csv.reader(handle))[1:]
    rows = [row for row in rows if any(cell not in (None, "") for cell in row)]
    header = [str(cell) for cell in rows[0]] if rows else []
    if header != HEADER:
        problems.append(f"the first sheet's header row is {header or 'missing'}, must be date,region,units,price")
        return CheckResult(False, problems)
    data = [row[: len(HEADER)] + [None] * (len(HEADER) - len(row)) for row in rows[1:]]

    if len(data) != len(expected):
        problems.append(f"sales.xlsx has {len(data)} data rows, the CSV has {len(expected)}")
    for column in NUMERIC:
        index = HEADER.index(column)
        text = sum(1 for row in data if not isinstance(row[index], (int, float)) or isinstance(row[index], bool))
        if text:
            problems.append(f"{text} {column} cells are text, not numeric numbers")
    mismatches = []
    for number, (row, want) in enumerate(zip(data, expected, strict=False), 1):
        for column, cell, value in zip(HEADER, row, want, strict=True):
            if not _same(column, cell, value):
                mismatches.append(f"row {number} {column}: {cell!r}, CSV has {value!r}")
    if mismatches:
        problems.append(f"{len(mismatches)} values differ from the CSV, first: {mismatches[0]}")
    if problems:
        return CheckResult(False, problems)
    return CheckResult(True, [f"{len(data)} rows match, units and price numeric"])


TASK = Task(name="xlsx", prompt=PROMPT, prepare_fixtures=_prepare, check=_check)
