"""Format JSON by changing only its whitespace, and turn Python literals into JSON.

Python's ``json`` module rewrites what it reads: ``1.10`` becomes ``1.1``,
duplicate keys collapse and string escapes are decoded. This module keeps
every string, number and literal exactly as written and only lays the tokens
out again. Parsing uses an explicit stack, so deep nesting has no recursion
limit.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

_BOM = "﻿"

# Leading whitespace, then one token: a structural character, a complete
# string, a number or a literal. Anything else is looked at character by
# character to say what is wrong.
_TOKEN = re.compile(
    r'[ \t\n\r]*(?:'
    r'([{}\[\],:])'
    r'|("(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*")'
    r'|(-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)'
    r'|(true|false|null)'
    r')'
)
_SPACE = re.compile(r"[ \t\n\r]*")
_NUMBER_START = frozenset("-0123456789.")
_NUMBER_TAIL = frozenset("0123456789.eE")
_HEX = frozenset("0123456789abcdefABCDEF")
_ESCAPES = frozenset('"\\/bfnrt')

# Token kinds besides the structural characters themselves.
_SCALAR = "value"
_STRING = "string"
_END = "end"

EMPTY = "the input is empty"


class JsonError(ValueError):
    """Invalid JSON, with a 1-based line and column (in characters)."""

    def __init__(self, line: int, column: int, reason: str) -> None:
        super().__init__(f"invalid JSON at line {line} column {column}: {reason}")
        self.line = line
        self.column = column
        self.reason = reason


class LiteralError(ValueError):
    """A Python literal that cannot be read or has no JSON form."""


@dataclass(frozen=True)
class Style:
    """``indent``: 0-8 spaces, ``"\\t"`` for tabs, or None for no whitespace."""

    indent: int | str | None = 2
    sort_keys: bool = False


class _Object:
    __slots__ = ("members",)

    def __init__(self) -> None:
        self.members: list[tuple[str, object]] = []


class _Array:
    __slots__ = ("items",)

    def __init__(self) -> None:
        self.items: list[object] = []


def _error(text: str, position: int, reason: str) -> JsonError:
    line = text.count("\n", 0, position) + 1
    column = position - (text.rfind("\n", 0, position) + 1) + 1
    return JsonError(line, column, reason)


class _Tokens:
    def __init__(self, text: str) -> None:
        self.text = text
        self.position = 0

    def next(self) -> tuple[str, str, int]:
        """Return (kind, token text, start position) of the next token."""
        text = self.text
        match = _TOKEN.match(text, self.position)
        if match is not None and match.lastindex is not None:
            start = match.start(match.lastindex)
            end = match.end()
            kind = match.lastindex
            if kind == 1:
                self.position = end
                return text[start], text[start], start
            if kind == 2:
                self.position = end
                return _STRING, text[start:end], start
            if kind == 3 and end < len(text) and text[end] in _NUMBER_TAIL:
                raise _error(text, start, "invalid number")
            self.position = end
            return _SCALAR, text[start:end], start
        start = _SPACE.match(text, self.position).end()
        if start >= len(text):
            self.position = start
            return _END, "", start
        char = text[start]
        if char == '"':
            raise self._string_error(start)
        if char in _NUMBER_START:
            raise _error(text, start, "invalid number")
        raise _error(text, start, "unexpected character")

    def _string_error(self, start: int) -> JsonError:
        text = self.text
        index = start + 1
        while index < len(text):
            char = text[index]
            if char == '"':
                break
            if char in "\n\r":
                return _error(text, start, "unterminated string")
            if char < " ":
                return _error(text, index, "control character in string")
            if char == "\\":
                if index + 1 >= len(text):
                    return _error(text, start, "unterminated string")
                escape = text[index + 1]
                if escape == "u":
                    digits = text[index + 2 : index + 6]
                    if len(digits) < 4 or not all(c in _HEX for c in digits):
                        return _error(text, index, "invalid escape in string")
                    index += 6
                    continue
                if escape not in _ESCAPES:
                    return _error(text, index, "invalid escape in string")
                index += 2
                continue
            index += 1
        return _error(text, start, "unterminated string")


def _sort_key(member: tuple[str, object]) -> str:
    import json

    return json.loads(member[0])


def _parse(text: str, *, sort_keys: bool) -> object:
    if text.startswith(_BOM):
        text = text[1:]
    tokens = _Tokens(text)
    kind, token, start = tokens.next()
    if kind == _END:
        raise JsonError(1, 1, EMPTY)

    def fail(position: int, reason: str) -> JsonError:
        return _error(text, position, reason)

    def key(after_comma: bool) -> str:
        kind, token, start = tokens.next()
        if kind == _STRING:
            colon, _, colon_at = tokens.next()
            if colon == ":":
                return token
            if colon == _END:
                raise fail(colon_at, "unexpected end of input")
            raise fail(colon_at, "expected ':'")
        if kind == _END:
            raise fail(start, "unexpected end of input")
        if after_comma and kind == "}":
            raise fail(start, "trailing comma")
        raise fail(start, "expected a string key")

    stack: list[tuple[object, str | None]] = []
    after_comma = False
    while True:
        # `kind` starts a value.
        value: object
        if kind == "[":
            kind, token, start = tokens.next()
            if kind == "]":
                value = _Array()
            else:
                stack.append((_Array(), None))
                after_comma = False
                continue
        elif kind == "{":
            container = _Object()
            kind, token, start = tokens.next()
            if kind == "}":
                value = container
            else:
                tokens.position = start
                stack.append((container, key(after_comma=False)))
                kind, token, start = tokens.next()
                after_comma = False
                continue
        elif kind == _STRING or kind == _SCALAR:
            value = token
        elif kind == _END:
            raise fail(start, "unexpected end of input")
        elif after_comma and kind in "]}":
            raise fail(start, "trailing comma")
        else:
            raise fail(start, "expected a value")

        # `value` is complete: hand it to its container.
        while True:
            if not stack:
                kind, token, start = tokens.next()
                if kind != _END:
                    raise fail(start, "unexpected text after the JSON value")
                return value
            container, pending = stack[-1]
            if isinstance(container, _Array):
                container.items.append(value)
                closer, expected = "]", "expected ',' or ']'"
            else:
                container.members.append((pending, value))
                closer, expected = "}", "expected ',' or '}'"
            kind, token, start = tokens.next()
            if kind == ",":
                if isinstance(container, _Object):
                    stack[-1] = (container, key(after_comma=True))
                kind, token, start = tokens.next()
                # In an object the comma is followed by a key and ':'.
                after_comma = isinstance(container, _Array)
                break
            if kind == closer:
                stack.pop()
                if sort_keys and isinstance(container, _Object):
                    container.members.sort(key=_sort_key)
                value = container
                continue
            if kind == _END:
                raise fail(start, "unexpected end of input")
            raise fail(start, expected)


def _write(root: object, style: Style) -> str:
    indent = style.indent
    pretty = indent is not None
    unit = indent if isinstance(indent, str) else " " * (indent or 0)
    colon = ": " if pretty else ":"
    margins: list[str] = ["\n"]

    def margin(level: int) -> str:
        while len(margins) <= level:
            margins.append(margins[-1] + unit)
        return margins[level]

    out: list[str] = []
    # Frames: [container, next index, level of its members].
    stack: list[list] = []
    node = root
    while True:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, _Object):
            if node.members:
                out.append("{")
                stack.append([node, 0, len(stack) + 1])
            else:
                out.append("{}")
        elif node.items:  # type: ignore[attr-defined]
            out.append("[")
            stack.append([node, 0, len(stack) + 1])
        else:
            out.append("[]")
        while stack:
            frame = stack[-1]
            container, index, level = frame
            entries = container.members if isinstance(container, _Object) else container.items
            if index < len(entries):
                if index:
                    out.append(",")
                if pretty:
                    out.append(margin(level))
                if isinstance(container, _Object):
                    name, node = entries[index]
                    out.append(name)
                    out.append(colon)
                else:
                    node = entries[index]
                frame[1] = index + 1
                break
            stack.pop()
            if pretty:
                out.append(margin(level - 1))
            out.append("}" if isinstance(container, _Object) else "]")
        else:
            return "".join(out)


def format_json(text: str, style: Style = Style()) -> str:
    """Lay ``text`` out again in ``style``: "\\n" line ends, no final newline.

    A leading UTF-8 BOM is ignored. Raises JsonError.
    """
    return _write(_parse(text, sort_keys=style.sort_keys), style)


def check_json(text: str) -> None:
    """Raise JsonError when ``text`` is not one valid JSON value."""
    _parse(text, sort_keys=False)


_NOT_A_LITERAL = (
    "not a Python literal: only strings, numbers, True, False, None, "
    "lists, tuples and dicts can be read."
)


def _number(value: float) -> str:
    import json
    import math

    if math.isnan(value) or math.isinf(value):
        raise LiteralError("NaN and infinity have no JSON form.")
    return json.dumps(value)


def _key(value: object) -> str:
    import json

    if isinstance(value, str):
        name = value
    elif value is True:
        name = "true"
    elif value is False:
        name = "false"
    elif value is None:
        name = "null"
    elif isinstance(value, int):
        name = str(value)
    elif isinstance(value, float):
        name = _number(value)
    else:
        raise LiteralError("a dict key must be a string, number, True, False or None.")
    return json.dumps(name, ensure_ascii=False)


def _from_python(value: object, sort_keys: bool) -> object:
    import json

    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, (list, tuple)):
        array = _Array()
        array.items = [_from_python(item, sort_keys) for item in value]
        return array
    if isinstance(value, dict):
        members = [(_key(name), item) for name, item in value.items()]
        if sort_keys:
            members.sort(key=_sort_key)
        container = _Object()
        container.members = [(name, _from_python(item, sort_keys)) for name, item in members]
        return container
    if isinstance(value, (set, frozenset)):
        raise LiteralError("a set has no JSON form.")
    if isinstance(value, (bytes, bytearray)):
        raise LiteralError("bytes have no JSON form.")
    if isinstance(value, complex):
        raise LiteralError("a complex number has no JSON form.")
    raise LiteralError(_NOT_A_LITERAL)


def python_to_json(text: str, style: Style = Style()) -> str:
    """Read a Python literal (parsed, never run) and write it as JSON.

    Raises LiteralError.
    """
    import ast

    if text.startswith(_BOM):
        text = text[1:]
    if not text.strip():
        raise LiteralError(EMPTY)
    try:
        value = ast.literal_eval(text)
    except SyntaxError as error:
        raise LiteralError(
            f"not a Python literal at line {error.lineno} column {error.offset}: {error.msg}"
        ) from None
    except TypeError:
        # A list, dict or set used as a dict key or set member.
        raise LiteralError(
            "not a Python literal: a list, dict or set cannot be a dict key or set member."
        ) from None
    except (ValueError, RecursionError, MemoryError):
        raise LiteralError(_NOT_A_LITERAL) from None
    try:
        node = _from_python(value, style.sort_keys)
    except RecursionError:
        raise LiteralError("not a Python literal: it is nested too deeply.") from None
    return _write(node, style)
