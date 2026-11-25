"""Markdown from positioned text runs, by geometry alone.

The input is word-sized runs with a baseline position (y grows down the
page), a width and a font size. ``layout_page`` groups them into lines and
finds forms, ledgers, tables and columns; ``pages_to_markdown`` turns pages
of lines into Markdown with headings, lists and links. Nothing here reads a
PDF, so every rule can be tested with runs built by hand.

The rules guess structure from the layout; they never invent text.
``js_round`` rounds halves up, as JavaScript's ``Math.round`` does.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from functools import cmp_to_key
import math
import re
import unicodedata

NO_TEXT = "no selectable text; the PDF may be scanned (there is no OCR)"


class NoText(Exception):
    """No page had any selectable text."""

    def __init__(self) -> None:
        super().__init__(NO_TEXT)


@dataclass(slots=True)
class TextRun:
    text: str
    x: float
    y: float
    width: float
    size: float
    rtl: bool = False
    url: str | None = None
    bold: bool = False


@dataclass(eq=False)
class TextTable:
    rows: list[list[TextLine | None]]
    header: bool


@dataclass(eq=False)
class TextLine:
    """One line of runs; ``eq=False`` keeps identity hashing for sets of lines."""

    runs: list[TextRun]
    x: float
    right: float
    y: float
    size: float
    bottom: float | None = None
    field: bool = False
    cells: tuple[TextLine, TextLine] | None = None
    table: TextTable | None = None


@dataclass(eq=False)
class TextPage:
    number: int
    width: float
    lines: list[TextLine] = field(default_factory=list)
    complex: bool = False


# ---- small helpers


def js_round(value: float) -> int:
    """JavaScript ``Math.round``: halves go up, also below zero."""
    return math.floor(value + 0.5)


_SENTENCE_END = (".", "!", "?", "。", "！", "？")
_LIST_PREFIX = re.compile(r"(?:[•●◦▪‣⁃–—-]\s+|[0-9]{1,4}[.)]\s+)")
_MARKER_ONLY = re.compile(r"(?:[•●◦▪‣⁃–—-]|[0-9]{1,4}[.)])")
_FIELD_REST = re.compile(r"[^:：\n]{0,79}[:：](?:\s+|\Z)")
_ESCAPED = re.compile(r"([`*_{}\[\]<>#!|])")
_SPACES = re.compile(r"\s+")
_BOLD_SPAN = re.compile(r"\A(\s*)(.*?\S)(\s*)\Z", re.S)
_SAFE_URL_BAD = re.compile(r"[\s\x00-\x1f\x7f<>]")
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")


def _ends_sentence(text: str) -> bool:
    return text.endswith(_SENTENCE_END)


def _ends_space(text: str) -> bool:
    return bool(text) and text[-1].isspace()


def _starts_space(text: str) -> bool:
    return bool(text) and text[0].isspace()


def _field_prefix(text: str) -> int | None:
    """End of a leading ``Label:`` (a letter or digit first, a colon within 80), else None."""
    if not text:
        return None
    first = text[0]
    if not (first.isalpha() or unicodedata.category(first).startswith("N")):
        return None
    found = _FIELD_REST.match(text, 1)
    return found.end() if found else None


def _is_field(text: str) -> bool:
    return _field_prefix(text) is not None


def _is_list(text: str) -> bool:
    return _LIST_PREFIX.match(text) is not None


def escape_markdown(value: str) -> str:
    return _ESCAPED.sub(r"\\\1", value.replace("\\", "\\\\"))


def safe_url(value: object) -> str | None:
    """An http(s) or mailto address fit for a Markdown link, else None.

    The address is kept as written, apart from ``(`` and ``)``, which are
    percent-encoded so they cannot end the link early.
    """
    if not isinstance(value, str) or len(value) > 4096 or _SAFE_URL_BAD.search(value):
        return None
    scheme = _SCHEME.match(value)
    if scheme is None:
        return None
    name = scheme[1].lower()
    if name in ("http", "https"):
        rest = value[scheme.end() :]
        if not rest.startswith("//"):
            return None
        authority = re.split(r"[/?#]", rest[2:], maxsplit=1)[0]
        host = authority.rpartition("@")[2]
        if host.startswith("["):
            host = host.partition("]")[0] + "]"
        else:
            host = host.partition(":")[0]
        if not host or host == "[]":
            return None
    elif name != "mailto":
        return None
    return value.replace("(", "%28").replace(")", "%29")


_CURRENCY: str | None = None


def _currency_class() -> str:
    """The characters of Unicode category Sc, escaped for a character class."""
    global _CURRENCY
    if _CURRENCY is None:
        found = []
        for start, stop in ((0, 0x10000), (0x11FD0, 0x12000), (0x1E2F0, 0x1E300), (0x1ECB0, 0x1ECC0)):
            for code in range(start, stop):
                if unicodedata.category(chr(code)) == "Sc":
                    found.append(re.escape(chr(code)))
        _CURRENCY = "".join(found)
    return _CURRENCY


_NUMERIC: tuple[re.Pattern[str], re.Pattern[str]] | None = None
_LETTER = r"[^\W\d_]"
_NUMERIC_ONLY = re.compile(r"[\d\s.,'’%()+−:/-]+")
_CODE = re.compile(r"(?:^|(?<=[\s(]))([^\W\d_]{3})(?=\s|\d|\Z)")


def _numeric_patterns() -> tuple[re.Pattern[str], re.Pattern[str]]:
    global _NUMERIC
    if _NUMERIC is None:
        sc = _currency_class()
        shape = re.compile(
            rf"(?:(?:{_LETTER}|[.≈~])+\s+){{0,2}}\(?[+−-]?(?:[{sc}]|{_LETTER}{{1,5}})?\s*"
            rf"\d[\d\s.,'’:/%+−-]*\)?(?:\s*(?:{_LETTER}|[{sc}%]){{1,12}})?"
        )
        _NUMERIC = (shape, re.compile(f"[{sc}]"))
    return _NUMERIC


def _numeric_cell(line: TextLine) -> bool:
    """Numbers or amounts, so ledgers can be told from columns of prose."""
    text = line_text(line)
    if len(text) > 80 or not re.search(r"\d", text):
        return False
    shape, currency = _numeric_patterns()
    if not shape.fullmatch(text):
        return False
    if currency.search(text):
        return True
    for code in _CODE.finditer(text):
        if all(unicodedata.category(letter) == "Lu" for letter in code[1]):
            return True
    return _NUMERIC_ONLY.fullmatch(text) is not None


def _weight_median(values: list[tuple[float, float]]) -> float:
    """The size that half of the weight (characters) is at or below."""
    if not values:
        return 12
    ordered = sorted(values, key=lambda value: value[0])
    total = sum(weight for _size, weight in ordered)
    weight = 0.0
    for size, part in ordered:
        weight += part
        if weight >= total / 2:
            return size
    return ordered[-1][0]


def _right(runs: list[TextRun], start: float) -> float:
    return max([start, *(run.x + run.width for run in runs)])


def _mostly_rtl(runs: list[TextRun]) -> bool:
    return sum(1 for run in runs if run.rtl) > len(runs) / 2


def _gap(run: TextRun, previous: TextRun, rtl: bool) -> float:
    if rtl:
        return previous.x - run.x - run.width
    return run.x - previous.x - previous.width


def _merge_cell(first: TextLine, following: TextLine) -> TextLine:
    added = [
        run if index else replace(run, text=" " + run.text.lstrip())
        for index, run in enumerate(following.runs)
    ]
    return replace(first, right=max(first.right, following.right), runs=[*first.runs, *added])


def _assign(target: TextLine, source: TextLine) -> None:
    for item in fields(TextLine):
        setattr(target, item.name, getattr(source, item.name))


def line_text(line: TextLine) -> str:
    text = ""
    for index, run in enumerate(line.runs):
        if index:
            previous = line.runs[index - 1]
            if (
                _gap(run, previous, run.rtl) > min(run.size, previous.size) * 0.12
                and not _ends_space(text)
                and not _starts_space(run.text)
            ):
                text += " "
        text += run.text
    return _SPACES.sub(" ", text).strip()


# ---- forms, tables and ledgers


def _pair_form_rows(rows: list[list[TextLine]]) -> list[list[TextLine]]:
    """Join the values of repeated ``Label:`` rows to their labels."""
    output: list[list[TextLine]] = []
    pending: list[tuple[list[TextLine], str]] = []
    anchor: tuple[TextLine, TextLine] | None = None
    pairs = 0
    wraps = 0

    def label_only(line: TextLine) -> bool:
        text = line_text(line)
        end = _field_prefix(text)
        return end is not None and not text[end:].strip()

    def flush() -> None:
        nonlocal pending, anchor, pairs, wraps
        previous: TextLine | None = None
        for row, kind in pending:
            if pairs < 2:
                output.append(row)
                continue
            if kind == "wrap" and previous is not None:
                _assign(previous, _merge_cell(previous, row[0]))
                previous.bottom = row[0].y + row[0].size * 0.2
            else:
                base = _merge_cell(row[0], row[1]) if kind == "pair" else row[0]
                previous = replace(base, field=True)
                output.append([previous])
        pending = []
        anchor = None
        pairs = 0
        wraps = 0

    for index, row in enumerate(rows):
        pair = len(row) == 2 and label_only(row[0]) and not _is_field(line_text(row[1]))
        size = max(anchor[0].size, row[0].size) if anchor else row[0].size
        previous = pending[-1] if pending else None
        near = bool(
            anchor
            and previous
            and row[0].y > previous[0][0].y
            and row[0].y - previous[0][0].y <= size * 2.6
            and abs(row[0].size - anchor[0].size) <= size * 0.25
        )
        left = near and abs(row[0].x - anchor[0].x) <= size * 0.75
        aligned_pair = pair and left and abs(row[1].x - anchor[1].x) <= size * 0.75
        # An inline final field may use a smaller gap than the aligned value column.
        inline_pair = pair and left and anchor[1].x > row[1].x > row[0].right
        if aligned_pair or inline_pair:
            pending.append((row, "pair"))
            if aligned_pair:
                pairs += 1
            wraps = 0
        elif len(row) == 1 and left and _is_field(line_text(row[0])):
            pending.append((row, "label"))
            wraps = 0
        elif (
            len(row) == 1
            and near
            and previous[1] != "label"
            and row[0].y - previous[0][0].y <= size * 1.7
            and abs(row[0].x - anchor[1].x) <= size * 0.75
            and not _is_field(line_text(row[0]))
            and not _is_list(line_text(row[0]))
            and wraps < 8
        ):
            following = rows[index + 1][0] if index + 1 < len(rows) else None
            following_field = (
                following is not None
                and following.y - row[0].y <= size * 2.6
                and abs(following.x - anchor[0].x) <= size * 0.75
                and _is_field(line_text(following))
            )
            if not following_field and _ends_sentence(line_text(previous[0][-1])):
                flush()
                output.append(row)
            else:
                pending.append((row, "wrap"))
                wraps += 1
        else:
            flush()
            if pair:
                anchor = (row[0], row[1])
                pending.append((row, "pair"))
                pairs = 1
            else:
                output.append(row)
    flush()
    return output


def _table_seed(rows: list[list[TextLine]], start: int) -> list[TextLine]:
    """Split the first row at narrow gutters whose starts repeat on nearby rows."""
    seed = rows[start]
    size = max(cell.size for cell in seed)
    tolerance = size * 0.2
    nearby: list[set[int]] = []
    last_y = seed[0].y
    for following in range(start + 1, min(len(rows), start + 9)):
        row = rows[following]
        if (
            row[0].y - last_y > size * 2.6
            or len(row) > 36
            or any(len(line_text(cell)) > 240 for cell in row)
        ):
            break
        nearby.append({js_round(run.x / tolerance) for cell in row for run in cell.runs})
        last_y = row[0].y
    result: list[TextLine] = []
    for cell in seed:
        groups: list[list[TextRun]] = []
        group: list[TextRun] = []
        for run in sorted(cell.runs, key=lambda item: item.x):
            previous = group[-1] if group else None
            bucket = js_round(run.x / tolerance)
            repeated = sum(1 for starts in nearby if bucket in starts) >= 2
            if previous is not None and run.x - previous.x - previous.width >= size * 0.75 and repeated:
                groups.append(group)
                group = []
            group.append(run)
        if group:
            groups.append(group)
        for runs in groups:
            ordered = list(reversed(runs)) if any(run.rtl for run in runs) else runs
            result.append(replace(cell, runs=ordered, x=runs[0].x, right=_right(runs, runs[0].x)))
    return result


def _table_rows(rows: list[list[TextLine]]) -> tuple[list[list[TextLine]], bool]:
    """Find tables: a bold header row, or numbers repeated under aligned labels."""
    output: list[list[TextLine]] = []
    found = False

    def compact(cell: TextLine) -> bool:
        text = line_text(cell)
        return len(text) <= 160 and not _is_list(text) and not _is_field(text)

    start = 0
    while start < len(rows):
        original = rows[start]
        if not original or len(original) > 12 or not all(compact(cell) for cell in original):
            output.append(original)
            start += 1
            continue
        seed = _table_seed(rows, start)
        columns = len(seed)
        if columns < 2 or columns > 12 or not all(compact(cell) for cell in seed):
            output.append(original)
            start += 1
            continue
        size = max(cell.size for cell in seed)
        anchors: list[list[TextLine]] = [[cell] for cell in seed]

        def match(row: list[TextLine]) -> list[TextLine | None] | None:
            if not row or len(row) > columns * 3 or any(len(line_text(cell)) > 240 for cell in row):
                return None
            # A nearly full cell can sit less than one word gap from the next
            # column: split its runs at the column anchors, not at spaces.
            fragments: list[TextLine] = []
            for line in row:
                group: list[TextRun] = []
                current = -1

                def flush_group() -> None:
                    if group:
                        runs = list(reversed(group)) if _mostly_rtl(group) else list(group)
                        fragments.append(
                            TextLine(
                                runs=runs,
                                x=group[0].x,
                                right=_right(group, group[0].x),
                                y=line.y,
                                size=line.size,
                            )
                        )
                        group.clear()

                for run in sorted(line.runs, key=lambda item: item.x):
                    best = current
                    distance = size * 1.1
                    for column in range(max(0, current), columns):
                        for anchor in anchors[column]:
                            # Interior words may share another column's right edge;
                            # only an aligned start splits a cell that has words.
                            if group:
                                delta = abs(run.x - anchor.x)
                            else:
                                delta = min(
                                    abs(run.x - anchor.x), abs(run.x + run.width - anchor.right)
                                )
                            if delta <= distance:
                                distance = delta
                                best = column
                    if group and best != current:
                        flush_group()
                    group.append(run)
                    current = best
                flush_group()
            cells: list[TextLine | None] = [None] * columns
            last_column = -1
            for cell in fragments:
                if cell.size < size * 0.6 or cell.size > size * 1.4:
                    return None
                best = -1
                distance = math.inf
                for column in range(columns):
                    if column < last_column or (
                        column + 1 < columns and cell.right > seed[column + 1].x - size * 0.2
                    ):
                        continue
                    for anchor in anchors[column]:
                        delta = min(
                            abs(cell.x - anchor.x),
                            abs(cell.right - anchor.right),
                            abs((cell.x + cell.right - anchor.x - anchor.right) / 2),
                        )
                        if delta < distance:
                            distance = delta
                            best = column
                if best < 0 or distance > size * 1.1:
                    return None
                held = cells[best]
                cells[best] = _merge_cell(held, cell) if held is not None else cell
                last_column = best
            return cells

        bold_header = all(
            all(run.bold for run in cell.runs if run.text.strip()) for cell in seed
        )
        numeric = [_numeric_cell(cell) for cell in seed]
        evidence: list[list[TextLine]] = []
        last_y = seed[0].y
        for following in range(start + 1, min(len(rows), start + 9)):
            if rows[following][0].y - last_y > size * 2.6:
                break
            cells = match(rows[following])
            if cells is None:
                break
            if all(cell is not None for cell in cells):
                evidence.append(cells)  # type: ignore[arg-type]
                for column, cell in enumerate(cells):
                    if len(anchors[column]) < 3:
                        anchors[column].append(cell)
            last_y = rows[following][0].y
            if len(evidence) == 2:
                break
        numeric_column = len(evidence) >= 2 and any(
            all(_numeric_cell(row[column]) for row in evidence) for column in range(columns)
        )
        header = bold_header or (
            not any(numeric)
            and numeric_column
            and all(not _ends_sentence(line_text(cell)) for cell in seed)
        )

        def prose(cell: TextLine) -> bool:
            text = line_text(cell)
            return len(text) > 80 or len(_SPACES.split(text)) > 8 or _ends_sentence(text)

        prose_columns = (
            columns == 2
            and not numeric_column
            and any(any(prose(cell) for cell in row) for row in evidence)
        )
        if len(evidence) < 2 or prose_columns or (not header and not (columns >= 3 and numeric_column)):
            output.append(original)
            start += 1
            continue
        scalar_columns = [all(_numeric_cell(row[column]) for row in evidence) for column in range(columns)]
        row_spacing = min(
            row[0].y - (evidence[index - 1][0].y if index else seed[0].y)
            for index, row in enumerate(evidence)
        )
        logical_rows: list[list[TextLine | None]] = [seed]  # type: ignore[list-item]
        wrap_gaps: list[float] = []
        end = start
        bottom = seed[0].y
        wraps = 0
        following = start + 1
        while following < len(rows) and following <= start + 4000 and len(logical_rows) < 1000:
            row = rows[following]
            gap = row[0].y - bottom
            if gap <= 0 or gap > size * 2.6:
                break
            cells = match(row)
            if cells is None:
                break
            occupied = sum(1 for cell in cells if cell is not None)
            previous = logical_rows[-1]
            continuation = (
                occupied < columns
                and (occupied > 1 or any(abs(value - gap) <= size * 0.1 for value in wrap_gaps))
                and gap <= min(size * 1.6, row_spacing * 0.8)
                and all(
                    cell is None
                    or (
                        previous[column] is not None
                        and not scalar_columns[column]
                        and not _is_list(line_text(cell))
                    )
                    for column, cell in enumerate(cells)
                )
            )
            if occupied == 1 or continuation:
                column = next(index for index, cell in enumerate(cells) if cell is not None)
                cell = cells[column]
                before = previous[column]
                after = match(rows[following + 1]) if following + 1 < len(rows) else None
                next_row = (
                    after is not None
                    and rows[following + 1][0].y - row[0].y <= size * 2.6
                    and sum(1 for item in after if item is not None) >= 2
                )
                fills_cell = (
                    before is not None
                    and column + 1 < columns
                    and seed[column + 1].x - before.right <= size * 2
                )
                if (
                    before is None
                    or _is_list(line_text(cell))
                    or gap > size * 1.6
                    or wraps >= 3
                    or (not next_row and _ends_sentence(line_text(before)))
                    or (not continuation and not next_row and not fills_cell)
                ):
                    break
                for index, item in enumerate(cells):
                    if item is not None:
                        previous[index] = _merge_cell(previous[index], item)
                if len(wrap_gaps) < 8:
                    wrap_gaps.append(gap)
                wraps += 1
            else:
                logical_rows.append(cells)
                wraps = 0
            bottom = row[0].y
            end = following
            following += 1
        if len(logical_rows) < 3:
            output.append(original)
            start += 1
            continue
        runs = [run for row in logical_rows for cell in row if cell is not None for run in cell.runs]
        rtl = _mostly_rtl(runs)
        table = TextTable(
            rows=[list(reversed(row)) for row in logical_rows] if rtl else logical_rows,
            header=header,
        )
        output.append(
            [
                TextLine(
                    runs=runs,
                    x=seed[0].x,
                    right=_right(runs, seed[0].x),
                    y=seed[0].y,
                    size=size,
                    bottom=bottom + size * 0.2,
                    table=table,
                )
            ]
        )
        found = True
        start = end + 1
    return output, found


def _pair_numeric_rows(rows: list[list[TextLine]]) -> tuple[list[TextLine], bool]:
    """Three or more right-aligned label and amount rows stay together as a ledger."""
    lines: list[TextLine] = []
    pending: list[tuple[TextLine, TextLine]] = []
    paired = False

    def flush() -> None:
        nonlocal pending, paired
        if len(pending) >= 3:
            paired = True
            for cells in pending:
                runs = [*cells[0].runs, *cells[1].runs]
                lines.append(
                    TextLine(
                        runs=runs,
                        cells=cells,
                        x=cells[0].x,
                        right=cells[1].right,
                        y=cells[0].y,
                        size=_weight_median([(run.size, len(run.text)) for run in runs]),
                    )
                )
        else:
            for cells in pending:
                lines.extend(cells)
        pending = []

    for row in rows:
        if len(row) != 2 or len(line_text(row[0])) > 100 or not _numeric_cell(row[1]):
            flush()
            lines.extend(row)
            continue
        cells = (row[0], row[1])
        if pending:
            last = pending[-1]
            size = max(last[0].size, last[1].size, cells[0].size, cells[1].size)
            if (
                abs(last[0].x - cells[0].x) > size
                or abs(last[1].right - cells[1].right) > size
                or cells[0].y - last[0].y > size * 2.5
            ):
                flush()
        pending.append(cells)
    flush()
    return lines, paired


# ---- reading order


def _whitespace_gaps(lines: list[TextLine], axis: str) -> list[tuple[float, float]]:
    if axis == "x":
        intervals = [(line.x, line.right) for line in lines]
    else:
        intervals = [
            (line.y - line.size * 0.8, line.bottom if line.bottom is not None else line.y + line.size * 0.2)
            for line in lines
        ]
    intervals.sort(key=lambda interval: interval[0])
    gaps: list[tuple[float, float]] = []
    end = intervals[0][1]
    for start, stop in intervals[1:]:
        if start > end:
            gaps.append((end, start))
        end = max(end, stop)
    return gaps


def _reading_order(lines: list[TextLine], depth: int = 0) -> tuple[list[TextLine], bool]:
    """Split at wide blank bands first, then at a column gutter; at most 20 deep."""
    if len(lines) < 2:
        return lines, False
    if depth >= 20:
        return lines, True
    size = _weight_median([(line.size, sum(len(run.text) for run in line.runs)) for line in lines])
    gaps_y = _whitespace_gaps(lines, "y")
    middle_y = (lines[0].y + lines[-1].y) / 2

    def by_width_then_middle(a: tuple[float, float], b: tuple[float, float]) -> float:
        difference = (b[1] - b[0]) - (a[1] - a[0])
        if abs(difference) > size * 0.1:
            return difference
        return abs((a[0] + a[1]) / 2 - middle_y) - abs((b[0] + b[1]) / 2 - middle_y)

    gaps_y.sort(key=cmp_to_key(by_width_then_middle))

    def split(axis: str, gap: tuple[float, float]) -> tuple[list[TextLine], bool]:
        cut = (gap[0] + gap[1]) / 2
        before = [line for line in lines if (line.x if axis == "x" else line.y) < cut]
        after = [line for line in lines if (line.x if axis == "x" else line.y) >= cut]
        first, first_complex = _reading_order(before, depth + 1)
        second, second_complex = _reading_order(after, depth + 1)
        rtl = axis == "x" and sum(
            1 for line in lines for run in line.runs if run.rtl
        ) > sum(len(line.runs) for line in lines) / 2
        ordered = [*second, *first] if rtl else [*first, *second]
        return ordered, axis == "x" or first_complex or second_complex

    if gaps_y and gaps_y[0][1] - gaps_y[0][0] >= size * 1.5:
        return split("y", gaps_y[0])
    gaps_x = [gap for gap in _whitespace_gaps(lines, "x") if gap[1] - gap[0] >= size * 1.5]
    # Both columns need real vertical overlap, not merely diagonal text blocks.
    for gap in sorted(gaps_x, key=lambda item: -(item[1] - item[0])):
        before = [line for line in lines if line.right <= gap[0]]
        after = [line for line in lines if line.x >= gap[1]]
        if len(before) < 2 or len(after) < 2:
            continue
        overlap = min(before[-1].y, after[-1].y) - max(before[0].y, after[0].y)
        if overlap >= size:
            return split("x", gap)
    if gaps_y and gaps_y[0][1] - gaps_y[0][0] >= size * 0.5:
        return split("y", gaps_y[0])
    return lines, False


@dataclass
class _Row:
    y: float
    size: float
    runs: list[TextRun]


def layout_page(number: int, width: float, runs: list[TextRun], complex: bool = False) -> TextPage:  # noqa: A002
    """Group runs into lines, find forms, tables and ledgers, and put them in reading order."""
    usable = [
        run
        for run in runs
        if run.text.strip()
        and all(math.isfinite(value) for value in (run.x, run.y, run.width, run.size))
        and run.size > 0
    ]
    rows: list[_Row] = []
    for run in sorted(usable, key=lambda item: (item.y, item.x)):
        row = next(
            (
                candidate
                for candidate in rows[-8:]
                if abs(candidate.y - run.y) <= min(candidate.size, run.size) * 0.35
            ),
            None,
        )
        if row is not None:
            row.runs.append(run)
        else:
            rows.append(_Row(run.y, run.size, [run]))

    physical_rows: list[list[TextLine]] = []
    for row in rows:
        lines: list[TextLine] = []
        group: list[TextRun] = []

        def flush() -> None:
            if group:
                lines.append(
                    TextLine(
                        runs=list(reversed(group)) if _mostly_rtl(group) else list(group),
                        x=group[0].x,
                        right=_right(group, group[0].x),
                        y=row.y,
                        size=_weight_median([(run.size, len(run.text)) for run in group]),
                    )
                )
                group.clear()

        for run in sorted(row.runs, key=lambda item: item.x):
            if group:
                previous = group[-1]
                limit = max(min(previous.size, run.size) * 1.5, width * 0.015)
                if run.x - (previous.x + previous.width) > limit:
                    flush()
            group.append(run)
        flush()
        # Some PDFs place a list marker apart from its text: join the two
        # before tables or columns can separate them.
        joined: list[TextLine] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            following = lines[index + 1] if index + 1 < len(lines) else None
            if (
                following is not None
                and _MARKER_ONLY.fullmatch(line_text(line))
                and following.x - line.right <= max(line.size, following.size) * 4
            ):
                joined.append(_merge_cell(line, following))
                index += 2
            else:
                joined.append(line)
                index += 1
        physical_rows.append(joined)

    table_rows, tables = _table_rows(_pair_form_rows(physical_rows))
    lines, paired = _pair_numeric_rows(table_rows)
    lines.sort(key=lambda line: (line.y, line.x))
    ordered, columns = _reading_order(lines)
    return TextPage(number, width, ordered, complex or columns or paired or tables)


# ---- Markdown


@dataclass
class _Group:
    text: str
    url: str | None
    bold: bool


def _render_line(line: TextLine, skip: int = 0, keep_bold: bool = True, table_cell: bool = False) -> str:
    """Runs as Markdown; neighbouring runs of one link become one link."""
    groups: list[_Group] = []
    for index, run in enumerate(line.runs):
        space = ""
        if index:
            previous = line.runs[index - 1]
            if (
                _gap(run, previous, run.rtl) > min(run.size, previous.size) * 0.12
                and not _ends_space(previous.text)
                and not _starts_space(run.text)
            ):
                space = " "
        text = space + run.text
        if index == 0:
            text = text.lstrip()
        if skip:
            removed = min(skip, len(text))
            text = text[removed:]
            skip -= removed
        if not text:
            continue
        url = safe_url(run.url)
        bold = keep_bold and run.bold is True
        if groups and groups[-1].url == url and groups[-1].bold == bold:
            groups[-1].text += text
        else:
            groups.append(_Group(text, url, bold))
    output = ""
    for group in groups:
        text = _SPACES.sub(" ", group.text) if table_cell else group.text
        if group.bold and text.strip():
            content = _BOLD_SPAN.sub(
                lambda found: f"{found[1]}**{escape_markdown(found[2])}**{found[3]}", text, count=1
            )
        else:
            content = escape_markdown(text)
        if group.url:
            address = group.url.replace("|", "%7C") if table_cell else group.url
            output += f"[{content}]({address})"
        else:
            output += content
    return output.strip()


def _render_table(table: TextTable) -> str:
    def row(cells: list[TextLine | None]) -> str:
        parts = "".join(
            f" {_render_line(cell, 0, True, True) if cell is not None else ''} |" for cell in cells
        )
        return f"|{parts}\n"

    width = len(table.rows[0])
    output = row(table.rows[0] if table.header else [None] * width)
    output += "|" + " --- |" * width + "\n"
    for cells in table.rows[1 if table.header else 0 :]:
        output += row(cells)
    return output.rstrip()


@dataclass
class _Position:
    marker: float
    content: float


@dataclass
class _Level:
    marker: float
    content: float
    indent: str
    prefix: str


def _list_position(line: TextLine, prefix: str) -> _Position:
    """Where the marker and the item text start, measured in the reading direction."""
    rtl = _mostly_rtl(line.runs)
    marker = -line.right if rtl else line.x
    remaining = len(prefix)
    for index, run in enumerate(line.runs):
        if index:
            previous = line.runs[index - 1]
            if (
                _gap(run, previous, rtl) > min(run.size, previous.size) * 0.12
                and not _ends_space(previous.text)
                and not _starts_space(run.text)
            ):
                remaining -= 1
        text = _SPACES.sub(" ", run.text if index else run.text.lstrip())
        if remaining < len(text):
            advance = max(0, remaining) / max(1, len(text)) * run.width
            content = -(run.x + run.width - advance) if rtl else run.x + advance
            return _Position(marker, content)
        remaining -= len(text)
    return _Position(marker, marker + len(prefix) * line.size / 2)


def _render_list(lines: list[TextLine], start: int) -> tuple[str, int] | None:
    if not _is_list(line_text(lines[start])) or lines[start].cells or lines[start].table:
        return None
    levels: list[_Level] = []
    text = ""
    end = start
    previous = lines[start]
    for index in range(start, len(lines)):
        line = lines[index]
        gap = line.y - previous.y
        if (
            line.cells
            or line.table
            or (index > start and (gap <= 0 or gap > max(line.size, previous.size) * 1.8))
        ):
            break
        bullet = _LIST_PREFIX.match(line_text(line))
        if bullet:
            position = _list_position(line, bullet[0])
            while len(levels) > 1 and position.marker < levels[-1].marker - line.size * 0.6:
                levels.pop()
            last = levels[-1] if levels else None
            indent = last.indent if last else ""
            if last and position.marker > last.marker + line.size * 0.75:
                if len(levels) >= 8:
                    break
                indent += " " * len(last.prefix)
                levels.append(_Level(position.marker, position.content, indent, ""))
            elif not last:
                levels.append(_Level(position.marker, position.content, indent, ""))
            elif abs(position.marker - last.marker) > line.size * 0.6:
                break
            ordered = re.match(r"[0-9]+", bullet[0])
            prefix = f"{ordered[0]}. " if ordered else "- "
            levels[-1] = _Level(position.marker, position.content, indent, prefix)
            text += ("\n" if text else "") + indent + prefix + _render_line(line, len(bullet[0]))
        else:
            rtl = _mostly_rtl(line.runs)
            anchor = -line.right if rtl else line.x
            level = len(levels) - 1
            while level >= 0 and not (
                anchor >= levels[level].marker + line.size * 0.4
                and abs(anchor - levels[level].content) <= line.size * 0.9
            ):
                level -= 1
            if level < 0 or line.size > previous.size * 1.3:
                break
            parent_continuation = level < len(levels) - 1
            del levels[level + 1 :]
            # A parent continuation after nested items needs the item's
            # content indent; an ordinary wrap stays in the same paragraph.
            continuation = levels[level].indent + " " * len(levels[level].prefix)
            text += (f"\n\n{continuation}" if parent_continuation else " ") + _render_line(line)
        previous = line
        end = index
    return text, end


def pages_to_markdown(
    pages: list[TextPage], headings: bool = True, page_breaks: bool = False
) -> tuple[str, list[int], bool]:
    """Markdown for ``pages``, the numbers of pages without text, and whether any is complex.

    Raises NoText when no page has any text.
    """
    body_size = _weight_median(
        [(line.size, len(line_text(line))) for page in pages for line in page.lines]
    )
    sizes = sorted(
        {
            js_round(line.size)
            for page in pages
            for line in page.lines
            if line.size >= body_size * 1.18
        },
        reverse=True,
    )
    empty_pages: list[int] = []
    result: list[str] = []
    for page in pages:
        if not page.lines:
            empty_pages.append(page.number)
            continue
        blocks: list[str] = []
        # Larger print is also used for wrapped notices: a run of lines in one
        # size and indent that ends in a full stop stays prose, not headings.
        prose: set[TextLine] = set()
        flow: list[TextLine] = []

        def finish_flow() -> None:
            if len(flow) > 1 and _ends_sentence(line_text(flow[-1])):
                prose.update(flow)
            flow.clear()

        for line in page.lines:
            if line.table:
                finish_flow()
                continue
            last = flow[-1] if flow else None
            if last is not None and not (
                abs(last.size - line.size) <= line.size * 0.1
                and abs(last.x - line.x) <= line.size * 0.5
                and line.y > last.y
                and line.y - last.y <= line.size * 1.7
            ):
                finish_flow()
            flow.append(line)
        finish_flow()

        previous: TextLine | None = None
        kind = ""
        empty_field = False
        index = 0
        while index < len(page.lines):
            line = page.lines[index]
            raw = line_text(line)
            if not raw:
                index += 1
                continue
            listed = _render_list(page.lines, index)
            if line.table:
                text = _render_table(line.table)
            elif listed:
                text = listed[0]
            else:
                text = _render_line(line)
            next_kind = "paragraph"
            # A colon after a label, then a value or the end of the line, marks
            # a field in any language; URL schemes and clock times do not match.
            field_end = _field_prefix(raw)
            heading = (
                headings
                and line not in prose
                and not _ends_sentence(raw)
                and line.size >= body_size * 1.18
                and len(raw) <= 160
            )
            mixed_weight = any(run.bold for run in line.runs) and any(
                not run.bold and run.text.strip() for run in line.runs
            )
            if line.table:
                next_kind = "table"
            elif listed:
                next_kind = "list"
            elif line.cells:
                emphasize = headings and line.size >= body_size * 1.18
                text = " — ".join(_render_line(cell, 0, not emphasize) for cell in line.cells)
                if emphasize:
                    text = f"**{text}**"
                next_kind = "row"
            elif field_end is not None and (not heading or mixed_weight):
                next_kind = "field"
            elif heading:
                rounded = js_round(line.size)
                position = sizes.index(rounded) if rounded in sizes else -1
                level = min(3, max(1, position + 1))
                text = "#" * level + " " + _render_line(line, 0, False)
                next_kind = "heading"
            else:
                # Source lines must not turn into Markdown lists or rules by accident.
                text = re.sub(r"^([0-9]{1,9})\.", r"\1\\.", text, count=1)
                text = re.sub(r"^([-+=~])", r"\\\1", text, count=1)
            close = (
                previous is not None
                and line.y > previous.y
                and line.y - (previous.bottom if previous.bottom is not None else previous.y)
                <= max(previous.size, line.size) * (2.6 if previous.field and line.field else 1.7)
                and abs(line.x - previous.x) <= body_size * 1.5
            )
            standalone_bold = (
                previous is not None
                and all(run.bold for run in previous.runs)
                and not all(run.bold for run in line.runs)
            )
            join_paragraph = (
                blocks and kind == "paragraph" and next_kind == "paragraph" and close and not standalone_bold
            )
            join_row = blocks and kind == "row" and next_kind == "row" and close
            join_field = blocks and kind == "field" and next_kind in ("field", "paragraph") and close
            field_break = next_kind == "field" or empty_field
            if join_paragraph:
                blocks[-1] += f" {text}"
            elif join_row:
                blocks[-1] += f"  \n{text}"
            elif join_field:
                blocks[-1] += ("  \n" if field_break else " ") + text
                next_kind = "field"
            else:
                blocks.append(text)
            empty_field = bool(
                next_kind == "field" and field_end is not None and not raw[field_end:].strip()
            )
            if listed:
                index = listed[1]
            previous = page.lines[listed[1]] if listed else line
            kind = next_kind
            index += 1
        result.append("\n\n".join(blocks))
    markdown = ("\n\n---\n\n" if page_breaks else "\n\n").join(result).strip()
    if not markdown:
        raise NoText()
    return markdown + "\n", empty_pages, any(page.complex for page in pages)
