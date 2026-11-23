"""The value model shared by every reader and writer.

Readers return plain Python values. Objects come back as ``Pairs`` (a list of
key/value pairs) so that duplicate keys and keys that are not text can be
noticed; ``normalize`` turns them into dicts, checks the limits and turns
what the target cannot hold into text, naming it in the notes.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any

MAX_VALUES = 1_000_000
MAX_DEPTH = 100
MAX_LISTED = 5

TOO_MANY = "the data has more than 1,000,000 values"
TOO_MANY_YAML = "YAML aliases expand past 1,000,000 values"
TOO_DEEP = "the data is nested more than 100 levels deep"
CYCLIC = "cyclic YAML aliases cannot be converted"

# Note texts. ``{paths}`` is filled with at most five items.
DUPLICATE = "duplicate keys: {paths}; the last value was kept"
KEYS = "keys that were not text became text: {paths}"
YAML = "YAML comments and anchors are not kept"
DATES = "dates and times became ISO 8601 text: {paths}"
CSV_TEXT = "CSV cells were kept as text; --types reads numbers, true/false and empty cells"
HEADER_ONLY = "the CSV has a header but no rows, so the result is an empty array"
ONE_ROW = "the object became one row"
EMPTY_CELLS = "null and missing values became empty cells"
ARRAYS_JSON = "arrays were written as JSON text"
ARRAYS_JOINED = 'arrays were joined with ", "'
FORMULA = "some cells start with =, +, - or @, and spreadsheets may treat them as formulas"
LONG_NUMBERS = (
    "numbers with more than 15 digits were written as text so that no digit is lost: {paths}"
)
LONG_TEXT = "text longer than 32,767 characters was cut to fit an Excel cell: {paths}"
CONTROL_CHARACTERS = "characters that Excel cannot hold were removed: {paths}"
XL_ERRORS = "cells with errors such as #N/A were kept as text: {paths}"
XL_FORMULAS = "formulas without a saved value became null: {paths}"
XL_NAMES = "blank or repeated column names were renamed: {paths}"
NEST_FLAT = "keys kept flat because nesting would clash: {paths}"
UNREAD_ROWS = "the first rows above the header were skipped: {paths}"

_FORMULA_START = re.compile(r"^\s*[=+@-]")
_INT = re.compile(r"-?(?:0|[1-9][0-9]*)")
_FLOAT = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?")
_PLAIN_KEY = re.compile(r'^[^.\[\]"\s]+$')

# What a target calls itself when it cannot hold NaN or Infinity.
_NO_NAN = {"json": "JSON", "jsonl": "JSON", "toml": "TOML", "xlsx": "Excel"}
# Targets that keep dates and times as they are.
_KEEPS_DATES = frozenset({"toml", "yaml"})


class ConvertError(Exception):
    """A conversion that cannot be done; ``str()`` is the text shown for it."""


class Pairs(list):
    """An object as read: its key/value pairs in written order, repeats included."""


class Notes:
    """Notes in the order they first arose; each appears once."""

    def __init__(self) -> None:
        self._notes: dict[str, list[str]] = {}

    def add(self, template: str, item: str | None = None) -> None:
        items = self._notes.setdefault(template, [])
        if item is not None and item not in items:
            items.append(item)

    def texts(self) -> tuple[str, ...]:
        found = []
        for template, items in self._notes.items():
            if "{paths}" in template:
                found.append(template.format(paths=listed(items)))
            else:
                found.append(template)
        return tuple(found)


def listed(items: list[str]) -> str:
    """At most five items, then ``and N more``."""
    shown = ", ".join(items[:MAX_LISTED])
    if len(items) > MAX_LISTED:
        shown += f" and {len(items) - MAX_LISTED} more"
    return shown


# ---- paths


def key_part(key: str, *, first: bool) -> str:
    if _PLAIN_KEY.match(key):
        return key if first else f".{key}"
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'["{escaped}"]'


def join_path(path: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return path + key_part(key, first=not path)


def key_text(key: object) -> str:
    """A key that is not text, as text."""
    if key is True:
        return "true"
    if key is False:
        return "false"
    if key is None:
        return "null"
    if isinstance(key, float):
        return repr(key)
    return str(key)


# ---- the walk


def normalize(value: Any, *, source: str, target: str, notes: Notes) -> Any:
    """Check the limits and give back plain values the target can hold."""
    count = 0
    on_path: set[int] = set()
    no_nan = _NO_NAN.get(target)
    keep_dates = target in _KEEPS_DATES

    def visit(item: Any, path: str, depth: int) -> Any:
        nonlocal count
        count += 1
        if count > MAX_VALUES:
            raise ConvertError(TOO_MANY_YAML if source == "yaml" else TOO_MANY)
        if isinstance(item, (list, dict)):
            if depth >= MAX_DEPTH:
                raise ConvertError(TOO_DEEP)
            marker = id(item)
            if marker in on_path:
                raise ConvertError(CYCLIC)
            on_path.add(marker)
            try:
                if isinstance(item, Pairs):
                    return _object(item, path, depth)
                if isinstance(item, dict):
                    return _object(Pairs(item.items()), path, depth)
                return [visit(entry, join_path(path, index), depth + 1) for index, entry in enumerate(item)]
            finally:
                on_path.discard(marker)
        if isinstance(item, float) and (math.isnan(item) or math.isinf(item)):
            if no_nan is not None:
                where = f": {path}" if path else ""
                raise ConvertError(f"{no_nan} has no NaN or Infinity{where}")
            return item
        if isinstance(item, (dt.date, dt.time)):  # datetime is a date too
            if keep_dates:
                return item
            notes.add(DATES, path or "(top)")
            return item.isoformat()
        return item

    def _object(pairs: Pairs, path: str, depth: int) -> dict:
        result: dict[str, Any] = {}
        for key, entry in pairs:
            if not isinstance(key, str):
                text = key_text(key)
                notes.add(KEYS, f"{path}.{text}" if path else text)
                key = text
            child = join_path(path, key)
            if key in result:
                notes.add(DUPLICATE, child)
            result[key] = visit(entry, child, depth + 1)
        return result

    return visit(value, "", 0)


# ---- rows


def flatten(row: dict) -> dict:
    """Nested objects as dotted columns; an empty object is a null cell."""
    flat: dict[str, Any] = {}

    def put(name: str, value: Any) -> None:
        if name in flat:
            raise ConvertError(f"nested key {name} clashes with a column of the same name")
        flat[name] = value

    def walk(prefix: str, value: dict) -> None:
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(item, dict) and item:
                walk(name, item)
            else:
                put(name, None if isinstance(item, dict) else item)

    walk("", row)
    return flat


def nest(row: dict, notes: Notes) -> Pairs:
    """Split dotted keys into nested objects; a key that would clash stays flat."""
    keys = set(row)
    nested: dict[str, Any] = {}
    for key, value in row.items():
        parts = key.split(".")
        if len(parts) == 1 or any(not part for part in parts):
            _put_flat(nested, key, value)
            continue
        prefixes = [".".join(parts[:end]) for end in range(1, len(parts))]
        if any(prefix in keys for prefix in prefixes) or not _can_nest(nested, parts):
            notes.add(NEST_FLAT, key)
            _put_flat(nested, key, value)
            continue
        place = nested
        for part in parts[:-1]:
            place = place.setdefault(part, {})
        place[parts[-1]] = value
    return Pairs(nested.items())


def _put_flat(nested: dict, key: str, value: Any) -> None:
    nested[key] = value


def _can_nest(nested: dict, parts: list[str]) -> bool:
    place: Any = nested
    for part in parts[:-1]:
        if part not in place:
            return True
        place = place[part]
        if not isinstance(place, dict):
            return False
    return parts[-1] not in place


def read_cell(text: str) -> Any:
    """A CSV cell read with ``--types``."""
    if text == "":
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if _INT.fullmatch(text):
        return int(text)
    if _FLOAT.fullmatch(text):
        return float(text)
    return text


def looks_like_formula(text: str) -> bool:
    return bool(_FORMULA_START.match(text))


def count_text(count: int, word: str) -> str:
    return f"{count:,} {word}" + ("" if count == 1 else "s")
