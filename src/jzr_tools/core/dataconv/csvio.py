"""CSV, with a reader of its own so that errors can name their row.

Rows end at CRLF, LF or CR; a line break inside quotes is kept as written.
Blank lines are skipped. Rows are numbered by the line they start on,
counting the header as row 1.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from jzr_tools.core.dataconv import decode
from jzr_tools.core.dataconv.model import (
    ARRAYS_JSON,
    CSV_TEXT,
    EMPTY_CELLS,
    FORMULA,
    HEADER_ONLY,
    ONE_ROW,
    ConvertError,
    Notes,
    Pairs,
    count_text,
    flatten,
    looks_like_formula,
    nest,
    read_cell,
)

SHAPE = "CSV needs an array of objects (or one object)"


def records(text: str, delimiter: str) -> Iterator[tuple[int, list[str]]]:
    """Each record that is not a blank line, with the line it starts on."""
    index, size, line = 0, len(text), 1
    while index < size:
        start = line
        cells: list[str] = []
        quoted_any = False
        while True:
            if index < size and text[index] == '"':
                quoted_any = True
                index += 1
                buffer: list[str] = []
                while True:
                    if index >= size:
                        raise ConvertError(f"CSV quote is not closed (row {start})")
                    char = text[index]
                    if char == '"':
                        if index + 1 < size and text[index + 1] == '"':
                            buffer.append('"')
                            index += 2
                            continue
                        index += 1
                        break
                    if char == "\n" or (char == "\r" and text[index + 1 : index + 2] != "\n"):
                        line += 1
                    buffer.append(char)
                    index += 1
                cells.append("".join(buffer))
                if index < size and text[index] not in (delimiter, "\r", "\n"):
                    raise ConvertError(f"unexpected text after a closing CSV quote (row {start})")
            else:
                end = index
                while end < size and text[end] not in (delimiter, "\r", "\n"):
                    end += 1
                cells.append(text[index:end])
                index = end
            if index >= size:
                break
            char = text[index]
            if char == delimiter:
                index += 1
                continue
            # A line end closes the record.
            index += 2 if text.startswith("\r\n", index) else 1
            line += 1
            break
        if cells == [""] and not quoted_any:
            continue
        yield start, cells


def read_csv(raw: bytes, options: Any, notes: Notes, target: str) -> Any:
    text = decode(raw)
    found = list(records(text, options.delimiter))

    def cell(value: str) -> Any:
        return read_cell(value) if options.types else value

    if not options.header:
        rows: list[Any] = [[cell(value) for value in cells] for _line, cells in found]
        if rows and not options.types:
            notes.add(CSV_TEXT)
        return rows

    if not found:
        raise ConvertError("CSV needs a header row")
    names = found[0][1]
    seen: set[str] = set()
    for column, name in enumerate(names, start=1):
        if not name.strip():
            raise ConvertError(f"CSV column names must not be blank (column {column})")
        if name in seen:
            raise ConvertError(f"CSV column names must be unique: {name}")
        seen.add(name)

    rows = []
    for line, cells in found[1:]:
        if len(cells) != len(names):
            raise ConvertError(
                f"CSV row {line} has {len(cells)} cells; the header has {len(names)}"
            )
        row = dict(zip(names, (cell(value) for value in cells)))
        rows.append(nest(row, notes) if options.nest else Pairs(row.items()))
    if rows and not options.types:
        notes.add(CSV_TEXT)
    if not rows and target != "csv":
        notes.add(HEADER_ONLY)
    return rows


def _field(text: str, delimiter: str, *, quote_empty: bool) -> str:
    if (text == "" and quote_empty) or any(
        mark in text for mark in (delimiter, '"', "\r", "\n")
    ):
        return '"' + text.replace('"', '""') + '"'
    return text


def _cell(value: Any, notes: Notes, delimiter: str, *, missing: bool = False) -> str:
    if missing or value is None:
        notes.add(EMPTY_CELLS)
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (list, dict)):
        if isinstance(value, list):
            notes.add(ARRAYS_JSON)
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return _field(text, delimiter, quote_empty=False)
    text = str(value)
    if looks_like_formula(text):
        notes.add(FORMULA)
    return _field(text, delimiter, quote_empty=True)


def write_csv(value: Any, options: Any, notes: Notes) -> tuple[bytes, str]:
    delimiter = options.delimiter
    if isinstance(value, dict):
        notes.add(ONE_ROW)
        value = [value]
    if not isinstance(value, list):
        raise ConvertError(SHAPE)
    lines: list[str] = []
    if all(isinstance(row, dict) for row in value):
        flat = [flatten(row) for row in value]
        columns: dict[str, None] = {}
        for row in flat:
            for name in row:
                columns.setdefault(name, None)
        if columns:
            lines.append(delimiter.join(_field(name, delimiter, quote_empty=True) for name in columns))
        for row in flat:
            lines.append(
                delimiter.join(
                    _cell(row.get(name), notes, delimiter, missing=name not in row)
                    for name in columns
                )
            )
    elif all(isinstance(row, list) for row in value):
        for row in value:
            lines.append(delimiter.join(_cell(item, notes, delimiter) for item in row))
    else:
        raise ConvertError(SHAPE)
    text = "".join(line + "\r\n" for line in lines)
    return text.encode("utf-8"), count_text(len(value), "row")
