"""TOML: read with tomllib, written with tomli-w (imported inside)."""

from __future__ import annotations

from typing import Any

from jzr_tools.core.dataconv import decode
from jzr_tools.core.dataconv.model import ConvertError, Notes, join_path

_INT_LIMIT = 2**63


def read_toml(raw: bytes, options: object, notes: Notes, target: str) -> Any:
    import tomllib

    text = decode(raw)
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        message = " ".join(str(error).split())
        raise ConvertError(f"invalid TOML: {message}") from None


def _check(value: Any, path: str) -> None:
    if value is None:
        raise ConvertError(f"TOML has no null: {path}")
    if isinstance(value, bool):
        return
    if isinstance(value, int) and not -_INT_LIMIT <= value < _INT_LIMIT:
        raise ConvertError(f"TOML integers must fit in 64 bits: {path}")
    if isinstance(value, dict):
        for key, item in value.items():
            _check(item, join_path(path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check(item, join_path(path, index))


def write_toml(value: Any, options: object, notes: Notes) -> tuple[bytes, str]:
    if not isinstance(value, dict):
        raise ConvertError("TOML needs an object at the top")
    _check(value, "")
    import tomli_w

    return tomli_w.dumps(value).encode("utf-8"), ""
