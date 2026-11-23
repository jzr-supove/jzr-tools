"""Excel workbooks (.xlsx, .xlsm), read and written with openpyxl (imported inside).

Reading takes the saved values, not the formulas; a formula that was never
calculated has no saved value and becomes null. Writing puts each array of
objects on its own sheet, with a bold, frozen header row.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import math
import re
import zipfile
from typing import Any

from jzr_tools.core.dataconv.model import (
    ARRAYS_JOINED,
    CONTROL_CHARACTERS,
    FORMULA,
    LONG_NUMBERS,
    LONG_TEXT,
    UNREAD_ROWS,
    XL_ERRORS,
    XL_FORMULAS,
    XL_NAMES,
    ConvertError,
    Notes,
    Pairs,
    count_text,
    flatten,
    join_path,
    looks_like_formula,
    nest,
)

DAMAGED = "not an Excel workbook, or a damaged one"
ENCRYPTED = "encrypted workbooks are not supported"
SHAPE = "XLSX needs an array of objects, or an object of such arrays"
MAX_ROWS = 1_048_576
MAX_TEXT = 32_767
MAX_DIGITS = 15
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_SHEET_CHARACTERS = re.compile(r"[\[\]:*?/\\]")
_QUOTED = re.compile(r'"[^"]*"|\[[^\]]*\]')


# ---- reading


def _has_time(number_format: str | None) -> bool:
    text = _QUOTED.sub("", (number_format or "").lower())
    return "h" in text or "s" in text


def _duration(value: dt.timedelta) -> str:
    total = int(value.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    return f"{sign}{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _cell_value(cell: Any, title: str, notes: Notes, formulas: set[tuple[str, str]]) -> Any:
    value = cell.value
    if getattr(cell, "data_type", None) == "e":
        notes.add(XL_ERRORS, f"{title}!{cell.coordinate}")
        return str(value)
    if value is None:
        if (title, cell.coordinate) in formulas:
            notes.add(XL_FORMULAS, f"{title}!{cell.coordinate}")
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else value
    if isinstance(value, dt.datetime):
        if value.time() == dt.time() and not _has_time(cell.number_format):
            return value.date().isoformat()
        return value.isoformat(timespec="seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dt.timedelta):
        return _duration(value)
    return value


def _formula_cells(raw: bytes, titles: list[str]) -> set[tuple[str, str]]:
    """Cells that hold a formula, found in a second, read-only pass."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            has_formulas = any(
                name.startswith("xl/worksheets/")
                and (b"<f>" in (data := archive.read(name)) or b"<f " in data)
                for name in archive.namelist()
            )
    except (zipfile.BadZipFile, OSError, KeyError):
        return set()
    if not has_formulas:
        return set()
    from openpyxl import load_workbook

    found: set[tuple[str, str]] = set()
    workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=False)
    try:
        for title in titles:
            for row in workbook[title].iter_rows():
                for cell in row:
                    if getattr(cell, "data_type", None) == "f":
                        found.add((title, cell.coordinate))
    finally:
        workbook.close()
    return found


def _grid(sheet: Any, notes: Notes, formulas: set[tuple[str, str]]) -> list[tuple[int, list[Any]]]:
    """The rows that hold something, as (sheet row number, values), without empty columns."""
    title = sheet.title
    merged: dict[tuple[int, int], Any] = {}
    for area in sheet.merged_cells.ranges:
        top_left = sheet.cell(row=area.min_row, column=area.min_col)
        for row in range(area.min_row, area.max_row + 1):
            for column in range(area.min_col, area.max_col + 1):
                merged[(row, column)] = top_left
    rows: list[tuple[int, list[Any]]] = []
    width = 0
    for number, row in enumerate(
        sheet.iter_rows(min_row=1, max_row=sheet.max_row, min_col=1, max_col=sheet.max_column),
        start=1,
    ):
        values = [
            _cell_value(merged.get((number, index), cell), title, notes, formulas)
            for index, cell in enumerate(row, start=1)
        ]
        used = [index for index, value in enumerate(values, start=1) if value is not None and value != ""]
        if not used:
            continue
        width = max(width, used[-1])
        rows.append((number, values))
    return [(number, values[:width]) for number, values in rows]


def _names(header: list[Any], notes: Notes) -> list[str]:
    from openpyxl.utils import get_column_letter

    names: list[str] = []
    taken: set[str] = set()
    for index, value in enumerate(header, start=1):
        text = "" if value is None else str(value).strip()
        renamed = False
        if not text:
            text, renamed = f"column {get_column_letter(index)}", True
        if text in taken:
            base, count = text, 2
            while f"{base} ({count})" in taken:
                count += 1
            text, renamed = f"{base} ({count})", True
        if renamed:
            notes.add(XL_NAMES, text)
        taken.add(text)
        names.append(text)
    return names


def _table(grid: list[tuple[int, list[Any]]], options: Any, notes: Notes) -> list[Any]:
    if not options.header:
        return [values for _number, values in grid]
    width = len(grid[0][1]) if grid else 0
    if options.header_row is not None:
        last = grid[-1][0] if grid else 0
        if options.header_row > last:
            raise ConvertError(f"--header-row {options.header_row} is past the last row ({last})")
        if options.header_row > 1:
            skipped = "1" if options.header_row == 2 else f"1-{options.header_row - 1}"
            notes.add(UNREAD_ROWS, skipped)
        header = next((values for number, values in grid if number == options.header_row), [None] * width)
        body = [values for number, values in grid if number > options.header_row]
    else:
        if not grid:
            return []
        header, body = grid[0][1], [values for _number, values in grid[1:]]
    names = _names(header, notes)
    rows: list[Any] = []
    for values in body:
        row = dict(zip(names, values))
        rows.append(nest(row, notes) if options.nest else Pairs(row.items()))
    return rows


def read_xlsx(raw: bytes, options: Any, notes: Notes, target: str) -> Any:
    if raw.startswith(_OLE2):
        raise ConvertError(ENCRYPTED)
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(raw), data_only=True)
    except Exception:  # openpyxl raises many kinds for files it cannot read
        raise ConvertError(DAMAGED) from None
    sheets = workbook.worksheets
    if options.sheets:
        titles = [sheet.title for sheet in sheets]
        for wanted in options.sheets:
            if wanted not in titles:
                raise ConvertError(f'no sheet named "{wanted}"; the sheets are: {", ".join(titles)}')
        chosen = [sheet for sheet in sheets if sheet.title in options.sheets]
    else:
        chosen = [sheet for sheet in sheets if sheet.sheet_state == "visible"]
    formulas = _formula_cells(raw, [sheet.title for sheet in chosen])
    tables: list[tuple[str, list[Any]]] = []
    for sheet in chosen:
        grid = _grid(sheet, notes, formulas)
        if not grid and not options.sheets:
            continue
        tables.append((sheet.title, _table(grid, options, notes)))
    if not tables:
        raise ConvertError("no sheet has data")
    if len(tables) == 1:
        return tables[0][1]
    return Pairs(tables)


# ---- writing


def _sheet_name(name: str, taken: set[str]) -> str:
    base = _SHEET_CHARACTERS.sub("_", name)[:31] or "sheet"
    candidate, count = base, 2
    while candidate.lower() in taken:
        suffix = f" ({count})"
        candidate = base[: 31 - len(suffix)] + suffix
        count += 1
    taken.add(candidate.lower())
    return candidate


def _shape(value: Any) -> list[tuple[str, list[Any], str]]:
    def table(rows: Any) -> bool:
        return isinstance(rows, list) and (
            all(isinstance(row, dict) for row in rows) or all(isinstance(row, list) for row in rows)
        )

    if isinstance(value, list) and table(value):
        return [("data", value, "")]
    if isinstance(value, dict) and value and all(table(rows) for rows in value.values()):
        return [(str(key), rows, join_path("", key)) for key, rows in value.items()]
    raise ConvertError(SHAPE)


def _item_text(item: Any) -> str:
    if item is None:
        return "null"
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, float):
        return repr(item)
    return str(item)


class _Cells:
    """Turns values into cells for one sheet, collecting the notes."""

    def __init__(self, sheet: Any, notes: Notes) -> None:
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
        from openpyxl.styles import Font

        self.sheet = sheet
        self.notes = notes
        self.cell_class = WriteOnlyCell
        self.illegal = ILLEGAL_CHARACTERS_RE
        self.bold = Font(bold=True)

    def text(self, text: str, path: str, *, bold: bool = False) -> Any:
        if self.illegal.search(text):
            self.notes.add(CONTROL_CHARACTERS, path)
            text = self.illegal.sub("", text)
        if len(text) > MAX_TEXT:
            self.notes.add(LONG_TEXT, path)
            text = text[:MAX_TEXT]
        if not bold and looks_like_formula(text):
            self.notes.add(FORMULA)
        cell = self.cell_class(self.sheet, value=text)
        cell.data_type = "s"  # never a formula or an error code
        if bold:
            cell.font = self.bold
        return cell

    def value(self, value: Any, path: str) -> Any:
        if value is None or isinstance(value, (bool, float)):
            return value
        if isinstance(value, int):
            if len(str(abs(value))) > MAX_DIGITS:
                self.notes.add(LONG_NUMBERS, path)
                return self.text(str(value), path)
            return value
        if isinstance(value, list):
            self.notes.add(ARRAYS_JOINED)
            if any(isinstance(item, (list, dict)) for item in value):
                return self.text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), path)
            return self.text(", ".join(_item_text(item) for item in value), path)
        if isinstance(value, dict):
            return self.text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), path)
        return self.text(str(value), path)


def write_xlsx(value: Any, options: Any, notes: Notes) -> tuple[bytes, str]:
    sheets = _shape(value)
    from openpyxl import Workbook

    workbook = Workbook(write_only=True)
    taken: set[str] = set()
    total = 0
    for name, rows, prefix in sheets:
        sheet = workbook.create_sheet(title=_sheet_name(name, taken))
        cells = _Cells(sheet, notes)
        if rows and all(isinstance(row, list) for row in rows):
            if len(rows) > MAX_ROWS:
                raise ConvertError(f"a sheet has more rows than Excel allows ({MAX_ROWS:,})")
            for index, row in enumerate(rows):
                path = join_path(prefix, index)
                sheet.append([cells.value(item, join_path(path, column)) for column, item in enumerate(row)])
            total += len(rows)
            continue
        flat = [flatten(row) for row in rows]
        columns: dict[str, None] = {}
        for row in flat:
            for column in row:
                columns.setdefault(column, None)
        if len(flat) + 1 > MAX_ROWS:
            raise ConvertError(f"a sheet has more rows than Excel allows ({MAX_ROWS:,})")
        if columns:
            sheet.freeze_panes = "A2"
            sheet.append([cells.text(column, column, bold=True) for column in columns])
        for index, row in enumerate(flat):
            path = join_path(prefix, index)
            sheet.append(
                [cells.value(row.get(column), join_path(path, column)) for column in columns]
            )
        total += len(flat)
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue(), f"{count_text(len(sheets), 'sheet')}, {count_text(total, 'row')}"
