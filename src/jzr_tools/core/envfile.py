"""Read ``.env`` files: keys for an example file, and key comparison.

Values are kept exactly as written (quotes included, nothing expanded), and
no error message ever contains one: they are often secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

_BLANK = re.compile(r"[\t ]*(?:#.*)?")
_ASSIGNMENT = re.compile(r"[\t ]*(export[\t ]+)?([A-Za-z_][A-Za-z0-9_]*)[\t ]*=[\t ]*(.*)")
_QUOTED = re.compile(r"""(["'])(.*)\1""", re.DOTALL)

KEY_RULE = (
    "Expected KEY=value. Keys may contain letters, digits and underscores, "
    "and cannot start with a digit."
)
BACKTICK = "Backtick quoting is not supported. Use single or double quotes."
AFTER_QUOTE = "Only a comment may follow a closing quote."
UNCLOSED = "Unclosed quoted value. Add its matching closing quote."
NULS = "Remove null characters from the file."


class EnvError(ValueError):
    """A line that cannot be read; ``str()`` is ``line N: REASON``."""

    def __init__(self, line: int, reason: str) -> None:
        super().__init__(f"line {line}: {reason}")
        self.line = line
        self.reason = reason


@dataclass(frozen=True)
class Entry:
    key: str
    value: str  # as written: quotes kept, lines of a quoted value joined by "\n"
    exported: bool
    line: int  # 1-based line of the key


@dataclass(frozen=True)
class Comparison:
    only_a: list[str]
    only_b: list[str]
    changed: list[str]


def _closing_quote(segment: str, quote: str, start: int) -> int:
    """Index of the first unescaped ``quote`` at or after ``start``, or -1."""
    escaped = False
    for index in range(start, len(segment)):
        char = segment[index]
        if char == quote and not escaped:
            return index
        escaped = char == "\\" and not escaped
    return -1


def parse_env(text: str) -> list[Entry]:
    """The assignments of ``text`` in file order. Raises EnvError at the first bad line."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    entries: list[Entry] = []
    index = 0
    while index < len(lines):
        line = index + 1
        current = lines[index]
        if "\0" in current:
            raise EnvError(line, NULS)
        if _BLANK.fullmatch(current):
            index += 1
            continue
        match = _ASSIGNMENT.fullmatch(current)
        if match is None:
            raise EnvError(line, KEY_RULE)
        prefix, key, rest = match.groups()
        if rest.startswith("`"):
            raise EnvError(line, BACKTICK)
        if rest[:1] in ("'", '"'):
            quote = rest[0]
            parts: list[str] = []
            segment, start = rest, 1
            while True:
                end = _closing_quote(segment, quote, start)
                if end >= 0:
                    parts.append(segment[: end + 1])
                    tail = segment[end + 1 :].strip()
                    if tail and not tail.startswith("#"):
                        raise EnvError(line, AFTER_QUOTE)
                    break
                parts.append(segment)
                if index + 1 >= len(lines):
                    raise EnvError(line, UNCLOSED)
                index += 1
                segment, start = lines[index], 0
                if "\0" in segment:
                    raise EnvError(index + 1, NULS)
            value = "\n".join(parts)
        else:
            hash_at = rest.find("#")
            value = (rest if hash_at < 0 else rest[:hash_at]).strip()
        entries.append(Entry(key, value, bool(prefix), line))
        index += 1
    return entries


def example_text(entries: list[Entry]) -> str:
    """``KEY=`` lines, each key once in first-seen order; "" when there are none."""
    keys = dict.fromkeys(entry.key for entry in entries)
    return "".join(f"{key}=\n" for key in keys)


def last_values(entries: list[Entry]) -> dict[str, str]:
    """Each key's last value as written, keys in first-seen order."""
    values: dict[str, str] = {}
    for entry in entries:
        values[entry.key] = entry.value
    return values


def effective(value: str) -> str:
    """The value without one pair of matching surrounding quotes."""
    match = _QUOTED.fullmatch(value)
    return match.group(2) if match else value


def compare(a: list[Entry], b: list[Entry]) -> Comparison:
    """Keys only in A, only in B, and shared keys whose last values differ."""
    values_a, values_b = last_values(a), last_values(b)
    return Comparison(
        only_a=[key for key in values_a if key not in values_b],
        only_b=[key for key in values_b if key not in values_a],
        changed=[
            key
            for key, value in values_a.items()
            if key in values_b and effective(value) != effective(values_b[key])
        ],
    )
