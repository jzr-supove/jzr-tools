"""JSON and JSON Lines."""

from __future__ import annotations

import json
from typing import Any

from jzr_tools.core.dataconv import decode
from jzr_tools.core.dataconv.model import TOO_DEEP, ConvertError, Notes, Pairs, count_text


class _Constant(Exception):
    """NaN or Infinity in the input."""


def _refuse_constant(name: str) -> Any:
    raise _Constant(name)


def _constant_position(text: str) -> tuple[int, int]:
    """Line and column of the first NaN or Infinity outside a string."""
    inside = escaped = False
    line, column = 1, 0
    for index, char in enumerate(text):
        column += 1
        if inside:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                inside = False
        elif char == '"':
            inside = True
        elif char in "NI-" and (
            text.startswith(("NaN", "Infinity"), index) or text.startswith("-Infinity", index)
        ):
            return line, column
        if char == "\n":
            line, column = line + 1, 0
    return line, column


def _message(error: json.JSONDecodeError) -> str:
    message = error.msg
    return message[:1].lower() + message[1:]


def _loads(text: str) -> Any:
    return json.loads(text, object_pairs_hook=Pairs, parse_constant=_refuse_constant)


def read_json(raw: bytes, options: object, notes: Notes, target: str) -> Any:
    text = decode(raw)
    try:
        return _loads(text)
    except _Constant:
        line, column = _constant_position(text)
        raise ConvertError(f"JSON has no NaN or Infinity (line {line} column {column})") from None
    except json.JSONDecodeError as error:
        raise ConvertError(
            f"invalid JSON at line {error.lineno} column {error.colno}: {_message(error)}"
        ) from None
    except RecursionError:
        raise ConvertError(TOO_DEEP) from None


def read_jsonl(raw: bytes, options: object, notes: Notes, target: str) -> list[Any]:
    text = decode(raw)
    values = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            values.append(_loads(line))
        except _Constant:
            _, column = _constant_position(line)
            raise ConvertError(f"line {number}: JSON has no NaN or Infinity (column {column})") from None
        except json.JSONDecodeError as error:
            raise ConvertError(
                f"line {number}: invalid JSON at column {error.colno}: {_message(error)}"
            ) from None
        except RecursionError:
            raise ConvertError(TOO_DEEP) from None
    return values


def write_json(value: Any, options: Any, notes: Notes) -> tuple[bytes, str]:
    if options.minify:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(value, ensure_ascii=False, indent=options.indent)
    return (text + "\n").encode("utf-8"), ""


def _line(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write_jsonl(value: Any, options: Any, notes: Notes) -> tuple[bytes, str]:
    items = value if isinstance(value, list) else [value]
    text = "".join(_line(item) + "\n" for item in items)
    return text.encode("utf-8"), count_text(len(items), "line")
