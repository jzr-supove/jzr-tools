"""Convert a two-sheet Excel workbook to JSON, one key per sheet (held out)."""

from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path

from .base import CheckResult, Task, sources_unchanged

PROMPT = (
    "Convert orders.xlsx to JSON, orders.json: an object with one key for each sheet, holding that sheet's rows "
    "as objects keyed by the header row. Numbers must stay numbers."
)
SOURCE = "orders.xlsx"
OUTPUT = "orders.json"
CUSTOMERS = (
    ("Acme Tools", "Germany"),
    ("Blue Harbor", "Norway"),
    ("Cedar & Sons", "Canada"),
    ("Dunes Trading", "Morocco"),
    ("Evergreen Ltd", "Japan"),
)
ORDERS = 12
TOLERANCE = 1e-9


def _prepare(fixture_dir: Path) -> None:
    from openpyxl import Workbook

    rng = random.Random(7)
    book = Workbook()
    orders = book.active
    orders.title = "Orders"
    orders.append(["id", "customer", "total", "date"])
    start = dt.datetime(2026, 3, 2)
    for index in range(ORDERS):
        total = rng.randint(500, 250000) / 100
        day = start + dt.timedelta(days=index * 4 + rng.randint(0, 2))
        orders.append([1001 + index, rng.choice(CUSTOMERS)[0], total, day])
    for row in orders.iter_rows(min_row=2):
        row[2].number_format = "0.00"
        row[3].number_format = "yyyy-mm-dd"
    customers = book.create_sheet("Customers")
    customers.append(["customer", "country"])
    for customer in CUSTOMERS:
        customers.append(list(customer))
    fixed = dt.datetime(2026, 1, 1)
    book.properties.created = fixed
    book.properties.modified = fixed
    book.save(fixture_dir / SOURCE)


def _sheets(path: Path) -> dict[str, tuple[list[str], list[list[object]]]]:
    """Sheet name -> (header, data rows), as openpyxl reads them."""
    from openpyxl import load_workbook

    book = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = {}
        for sheet in book.worksheets:
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
            sheets[sheet.title] = ([str(cell) for cell in rows[0]], rows[1:])
        return sheets
    finally:
        book.close()


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _cell_problem(cell: object, value: object) -> tuple[str, str] | None:
    """(kind, what) when the JSON value does not stand for the cell; kind groups the problems of a column."""
    if isinstance(cell, dt.datetime):
        want = cell.date().isoformat()
        if isinstance(value, str) and value.startswith(want):
            return None
        return "date", f"{value!r} does not start with the cell's date {want}"
    if _is_number(cell):
        if not _is_number(value):
            return "type", f"{value!r} is {type(value).__name__ if value is not None else 'null'}, not a JSON number"
        if abs(float(value) - float(cell)) > TOLERANCE:  # type: ignore[arg-type]
            return "value", f"{value!r}, the cell holds {cell!r}"
        return None
    if value != cell:
        return "value", f"{value!r}, the cell holds {cell!r}"
    return None


def _check_sheet(name: str, header: list[str], rows: list[list[object]], records: object) -> list[str]:
    if not isinstance(records, list):
        return [f"the key {name} holds {type(records).__name__}, not a list of the sheet's rows as objects"]
    problems = []
    if len(records) != len(rows):
        problems.append(f"{name} has {len(records)} rows, the sheet has {len(rows)} data rows")
    not_objects = [number for number, record in enumerate(records, 1) if not isinstance(record, dict)]
    if not_objects:
        problems.append(
            f"{len(not_objects)} rows of {name} are not objects keyed by the header row, first: row {not_objects[0]}"
        )
    wrong_keys = [
        (number, record)
        for number, record in enumerate(records, 1)
        if isinstance(record, dict) and set(record) != set(header)
    ]
    if wrong_keys:
        number, record = wrong_keys[0]
        problems.append(
            f"{len(wrong_keys)} rows of {name} do not have exactly the header keys {', '.join(header)}, "
            f"first: row {number} has keys {', '.join(map(str, record)) or 'none'}"
        )
    found: dict[tuple[str, str], list[str]] = {}
    for number, (record, row) in enumerate(zip(records, rows, strict=False), 1):
        if not isinstance(record, dict) or set(record) != set(header):
            continue
        for column, cell in zip(header, row, strict=False):
            problem = _cell_problem(cell, record[column])
            if problem is not None:
                kind, what = problem
                found.setdefault((column, kind), []).append(f"row {number}: {what}")
    for (column, kind), lines in found.items():
        label = {
            "date": "dates that are not a string starting with YYYY-MM-DD",
            "type": "values that are text (string) or another type, not numbers",
            "value": "values that differ from the cells",
        }[kind]
        problems.append(f"{name} column {column}: {len(lines)} {label}, first: {lines[0]}")
    return problems


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    problems = sources_unchanged(work_dir, fixture_dir, [SOURCE])
    path = work_dir / OUTPUT
    if not path.is_file():
        problems.append(f"{OUTPUT} does not exist")
        return CheckResult(False, problems)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        problems.append(f"{OUTPUT} is not valid JSON that can be parsed: {error}")
        return CheckResult(False, problems)
    if not isinstance(data, dict):
        shown = "null" if data is None else type(data).__name__
        problems.append(f"{OUTPUT} holds {shown}, not an object with one key per sheet")
        return CheckResult(False, problems)

    sheets = _sheets(fixture_dir / SOURCE)
    for name in sheets:
        if name not in data:
            problems.append(f"the key {name} is missing: there must be one key per sheet")
    extra = [key for key in data if key not in sheets]
    if extra:
        problems.append(
            f"extra keys {', '.join(map(repr, extra))}: the workbook has only the sheets {', '.join(sheets)}"
        )
    for name, (header, rows) in sheets.items():
        if name in data:
            problems.extend(_check_sheet(name, header, rows, data[name]))
    if problems:
        return CheckResult(False, problems)
    counts = ", ".join(f"{name} {len(rows)} rows" for name, (_, rows) in sheets.items())
    return CheckResult(True, [f"{counts} match, numbers are numbers"])


TASK = Task(name="xlsxjson", prompt=PROMPT, prepare_fixtures=_prepare, check=_check)
