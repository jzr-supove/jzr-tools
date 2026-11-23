"""Converting structured data between JSON, JSON Lines, YAML, CSV, TOML and Excel.

``convert`` reads the bytes of one format and writes another, collecting
notes about anything that changed on the way. The format libraries (PyYAML,
openpyxl, tomli-w) are imported only by the reader or writer that needs them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Callable

from jzr_tools.core.dataconv.model import (
    MAX_DEPTH,
    MAX_VALUES,
    TOO_DEEP,
    ConvertError,
    Notes,
    normalize,
)

__all__ = [
    "DELIMITERS",
    "EXTENSIONS",
    "FORMATS",
    "MAX_DEPTH",
    "MAX_VALUES",
    "REFUSED",
    "ConvertError",
    "Converted",
    "ReadOptions",
    "WriteOptions",
    "convert",
    "default_delimiter",
    "format_of",
]

FORMATS = ("json", "jsonl", "yaml", "csv", "toml", "xlsx")
EXTENSIONS = {
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".csv": "csv",
    ".tsv": "csv",
    ".toml": "toml",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
}
REFUSED = {".xls", ".ods", ".xlsb"}
DELIMITERS = {",": ",", ";": ";", "tab": "\t", "|": "|"}

EMPTY = "the input is empty"
NOT_UTF8 = "not UTF-8 text"
_BOM = b"\xef\xbb\xbf"


def _suffix(path: Path | str) -> str:
    return PurePath(str(path)).suffix.lower()


def format_of(path: Path | str) -> str | None:
    """The format a file name stands for, or None; old spreadsheet formats are refused."""
    suffix = _suffix(path)
    if suffix in REFUSED:
        raise ConvertError(f"{suffix} is not supported; save it as .xlsx")
    return EXTENSIONS.get(suffix)


def default_delimiter(path: Path | str | None) -> str:
    return "\t" if path is not None and _suffix(path) == ".tsv" else ","


@dataclass(frozen=True)
class ReadOptions:
    delimiter: str = ","
    header: bool = True
    header_row: int | None = None
    types: bool = False
    nest: bool = False
    sheets: tuple[str, ...] = ()


@dataclass(frozen=True)
class WriteOptions:
    indent: int = 2
    minify: bool = False
    delimiter: str = ","


@dataclass(frozen=True)
class Converted:
    data: bytes
    notes: tuple[str, ...]
    summary: str


def decode(raw: bytes) -> str:
    """UTF-8 text without its BOM; empty or blank input is refused."""
    if raw.startswith(_BOM):
        raw = raw[len(_BOM):]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConvertError(NOT_UTF8) from None
    if not text.strip():
        raise ConvertError(EMPTY)
    return text


def _reader(source: str) -> Callable[..., Any]:
    if source == "json":
        from jzr_tools.core.dataconv.jsonio import read_json

        return read_json
    if source == "jsonl":
        from jzr_tools.core.dataconv.jsonio import read_jsonl

        return read_jsonl
    if source == "yaml":
        from jzr_tools.core.dataconv.yamlio import read_yaml

        return read_yaml
    if source == "csv":
        from jzr_tools.core.dataconv.csvio import read_csv

        return read_csv
    if source == "toml":
        from jzr_tools.core.dataconv.tomlio import read_toml

        return read_toml
    if source == "xlsx":
        from jzr_tools.core.dataconv.xlsxio import read_xlsx

        return read_xlsx
    raise ValueError(f"unknown format {source!r}")


def _writer(target: str) -> Callable[..., tuple[bytes, str]]:
    if target == "json":
        from jzr_tools.core.dataconv.jsonio import write_json

        return write_json
    if target == "jsonl":
        from jzr_tools.core.dataconv.jsonio import write_jsonl

        return write_jsonl
    if target == "yaml":
        from jzr_tools.core.dataconv.yamlio import write_yaml

        return write_yaml
    if target == "csv":
        from jzr_tools.core.dataconv.csvio import write_csv

        return write_csv
    if target == "toml":
        from jzr_tools.core.dataconv.tomlio import write_toml

        return write_toml
    if target == "xlsx":
        from jzr_tools.core.dataconv.xlsxio import write_xlsx

        return write_xlsx
    raise ValueError(f"unknown format {target!r}")


def convert(
    raw: bytes,
    *,
    source: str,
    target: str,
    read: ReadOptions = ReadOptions(),
    write: WriteOptions = WriteOptions(),
) -> Converted:
    """Read ``raw`` as ``source`` and write it as ``target``; raises ConvertError."""
    reader, writer = _reader(source), _writer(target)
    notes = Notes()
    value = reader(raw, read, notes, target)
    try:
        value = normalize(value, source=source, target=target, notes=notes)
    except RecursionError:
        raise ConvertError(TOO_DEEP) from None
    data, summary = writer(value, write, notes)
    return Converted(data, notes.texts(), summary)

