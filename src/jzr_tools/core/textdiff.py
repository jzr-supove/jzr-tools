"""Unified diffs of two texts, with edited line pairs marked.

The hunks are those of ``difflib.unified_diff`` (GNU ``diff -u`` ranges). On
top of that, inside each block of removed and added lines, a removed line
that was edited rather than replaced is placed directly above its new
version, and the changed characters of both are given as a span. Each side
keeps its own order, so the result is still a valid patch.
"""

from __future__ import annotations

from dataclasses import dataclass

NOTE = "\\ No newline at end of file"
_PAIR_WINDOW = 100


def decode(data: bytes) -> str | None:
    """The text of ``data``, or None when it has a NUL byte or is not UTF-8."""
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


@dataclass(frozen=True)
class Line:
    text: str  # without the "\n"; a "\r" before it is kept
    terminated: bool  # ended with "\n"


@dataclass(frozen=True)
class Row:
    kind: str  # "hunk", "context", "removed", "added" or "note"
    text: str
    span: tuple[int, int] | None = None  # changed characters of an edited pair


def split_lines(text: str) -> list[Line]:
    """Split on "\\n" only; a last line without one is not terminated."""
    pieces = text.split("\n")
    lines = [Line(piece, True) for piece in pieces[:-1]]
    if pieces[-1]:
        lines.append(Line(pieces[-1], False))
    return lines


def _without_cr(text: str) -> str:
    return text[:-1] if text.endswith("\r") else text


def _shared(a: str, b: str) -> tuple[int, int]:
    """Lengths of the common prefix and the common suffix, never overlapping."""
    shortest = min(len(a), len(b))
    prefix = 0
    while prefix < shortest and a[prefix] == b[prefix]:
        prefix += 1
    suffix = 0
    while suffix < shortest - prefix and a[len(a) - 1 - suffix] == b[len(b) - 1 - suffix]:
        suffix += 1
    return prefix, suffix


def similar(a: str, b: str) -> bool:
    """Whether the shared start and end make up at least half of the longer line."""
    longest = max(len(a), len(b))
    if not longest:
        return False
    prefix, suffix = _shared(a, b)
    return (prefix + suffix) * 2 >= longest


def _range(start: int, stop: int) -> str:
    """A unified-diff range as GNU diff writes it."""
    beginning, length = start + 1, stop - start
    if length == 1:
        return f"{beginning}"
    if not length:
        beginning -= 1
    return f"{beginning},{length}"


def _rows_of(kind: str, line: Line) -> list[Row]:
    rows = [Row(kind, line.text)]
    if not line.terminated:
        rows.append(Row("note", NOTE))
    return rows


def _paired_block(removed: list[Line], added: list[Line]) -> list[Row]:
    """Rows of one replacement block, each edited line above its new version."""
    out: list[Row] = []
    next_removed = next_added = 0
    for position, old in enumerate(removed):
        old_text = _without_cr(old.text)
        limit = min(len(added), next_added + _PAIR_WINDOW)
        match = -1
        for index in range(next_added, limit):
            if similar(old_text, _without_cr(added[index].text)):
                match = index
                break
        if match < 0:
            continue
        for line in removed[next_removed:position]:
            out.extend(_rows_of("removed", line))
        for line in added[next_added:match]:
            out.extend(_rows_of("added", line))
        new = added[match]
        new_text = _without_cr(new.text)
        prefix, suffix = _shared(old_text, new_text)
        for kind, line, text in (("removed", old, old_text), ("added", new, new_text)):
            end = len(text) - suffix
            span = (prefix, end) if end > prefix else None
            out.append(Row(kind, line.text, span))
            if not line.terminated:
                out.append(Row("note", NOTE))
        next_removed, next_added = position + 1, match + 1
    for line in removed[next_removed:]:
        out.extend(_rows_of("removed", line))
    for line in added[next_added:]:
        out.extend(_rows_of("added", line))
    return out


def _key(line: Line, ignore_space: bool, strict_eol: bool) -> tuple[str, bool]:
    text = line.text if strict_eol else _without_cr(line.text)
    if ignore_space:
        text = text.strip()
    return text, line.terminated


def diff_rows(
    old: list[Line],
    new: list[Line],
    *,
    context: int = 3,
    ignore_space: bool = False,
    strict_eol: bool = False,
) -> list[Row]:
    """The hunks of a unified diff; [] when the texts are equal under the options."""
    import difflib

    keys_old = [_key(line, ignore_space, strict_eol) for line in old]
    keys_new = [_key(line, ignore_space, strict_eol) for line in new]
    if keys_old == keys_new:
        return []
    matcher = difflib.SequenceMatcher(None, keys_old, keys_new, autojunk=False)
    rows: list[Row] = []
    for group in matcher.get_grouped_opcodes(context):
        first, last = group[0], group[-1]
        rows.append(
            Row("hunk", f"@@ -{_range(first[1], last[2])} +{_range(first[3], last[4])} @@")
        )
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for line in old[i1:i2]:
                    rows.extend(_rows_of("context", line))
            elif tag == "delete":
                for line in old[i1:i2]:
                    rows.extend(_rows_of("removed", line))
            elif tag == "insert":
                for line in new[j1:j2]:
                    rows.extend(_rows_of("added", line))
            else:
                rows.extend(_paired_block(old[i1:i2], new[j1:j2]))
    return rows
