"""Workbooks built with openpyxl for the data conversion tests.

Each builder returns the bytes of a saved .xlsx file. openpyxl writes a
formula without a cached result, which is the "never calculated" case.
"""

from __future__ import annotations

import datetime as dt
import io


def save(workbook) -> bytes:
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def simple_workbook() -> bytes:
    """One sheet "Sheet1": name, age, score, ok; the second row has gaps."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["name", "age", "score", "ok"])
    ws.append(["Ann", 30, 1.5, True])
    ws.append(["Bob", None, 2, False])
    return save(wb)


def gaps_workbook() -> bytes:
    """Header on row 3, empty rows between data rows, an empty styled column."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.cell(row=3, column=1, value="id")
    ws.cell(row=3, column=2, value="word")
    ws.cell(row=4, column=1, value=1)
    ws.cell(row=4, column=2, value="one")
    ws.cell(row=7, column=1, value=2)
    ws.cell(row=7, column=2, value="two")
    ws.cell(row=3, column=4).font = Font(bold=True)  # styled, but holds nothing
    return save(wb)


def two_visible_one_hidden() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    first = wb.active
    first.title = "People"
    first.append(["name"])
    first.append(["Ann"])
    second = wb.create_sheet("Places")
    second.append(["city"])
    second.append(["Oslo"])
    hidden = wb.create_sheet("Secret")
    hidden.append(["key"])
    hidden.append(["x"])
    hidden.sheet_state = "hidden"
    empty = wb.create_sheet("Blank")
    empty.sheet_state = "visible"
    return save(wb)


def only_empty_sheets() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Nothing"
    return save(wb)


def merged_workbook() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["group", "value"])
    ws.append(["g1", 1])
    ws.append([None, 2])
    ws.append(["g2", 3])
    ws.merge_cells("A2:A3")
    return save(wb)


def error_and_formula_workbook() -> bytes:
    """B2 holds #N/A as an error cell; B3 holds a formula that was never calculated."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["key", "val"])
    ws.append(["err", "#N/A"])
    ws.append(["calc", "=1+1"])
    ws.append(["plain", 7])
    return save(wb)


def temporal_workbook() -> bytes:
    """Date, date and time, midnight with a time format, time of day and a duration."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["kind", "value"])
    rows = [
        ("date", dt.datetime(2026, 10, 24), "yyyy-mm-dd"),
        ("stamp", dt.datetime(2026, 10, 24, 10, 15, 0), "yyyy-mm-dd hh:mm:ss"),
        ("midnight", dt.datetime(2026, 10, 24), "yyyy-mm-dd hh:mm:ss"),
        ("time", dt.time(10, 15, 0), "hh:mm:ss"),
        ("duration", dt.timedelta(hours=36), "[h]:mm:ss"),
    ]
    for index, (kind, value, number_format) in enumerate(rows, start=2):
        ws.cell(row=index, column=1, value=kind)
        cell = ws.cell(row=index, column=2, value=value)
        cell.number_format = number_format
    return save(wb)


def named_columns_workbook(header: list) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(header)
    ws.append([f"v{i}" for i in range(1, len(header) + 1)])
    return save(wb)


def rows_workbook(rows: list[list], title: str = "Sheet1") -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    return save(wb)
