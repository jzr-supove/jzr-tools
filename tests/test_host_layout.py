"""Acceptance tests for the framed text layout of ``jzr host status``.

These pin the layout rules of the text output, not a snapshot of any report:
column spacing beyond those rules is left free.
"""

from __future__ import annotations

import dataclasses
import os
import re
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.commands.host import render_status
from jzr_tools.core.host import BatteryInfo, GpuInfo

from host_fixtures import (
    bar_fill,
    display_width,
    find_bars,
    make_bare,
    make_full,
    strip_ansi,
)

CHARS = {
    False: dict(tl="╭", tr="╮", bl="╰", br="╯", lt="├", rt="┤", h="─", v="│", cut="…"),
    True: dict(tl="+", tr="+", bl="+", br="+", lt="+", rt="+", h="-", v="|", cut=">"),
}
DRAWING = "╭╮╰╯├┤─│█░"

FIXTURES = {"full": make_full, "bare": make_bare}
KEY_RE = re.compile(
    r"^(Usage|Load|RAM|Available|Swap|Frequency|Temp|Cores|Memory|Driver|Power|Charge)(\s{2,})\S"
)
PAIR_RE = re.compile(r"\d+(?:\.\d)? / \d+\.\d (?:B|KiB|MiB|GiB|TiB)")
PCT_RE = re.compile(r"(\d+)%")

CONTENT_LEFT = 3  # "│" + two spaces


def render(status, *, detail=False, ascii=False, color=False, width=None) -> str:
    """Render and return the text with ANSI codes removed."""
    return strip_ansi(
        render_status(status, detail=detail, color=color, ascii=ascii, width=width)
    )


def combos():
    for name, make in FIXTURES.items():
        for detail in (False, True):
            for ascii in (False, True):
                yield name, make, detail, ascii


class Parsed:
    def __init__(self, test: unittest.TestCase, text: str, ascii: bool) -> None:
        c = CHARS[ascii]
        self.c = c
        self.text = text
        test.assertTrue(text.endswith("\n"), "report must end in a newline")
        test.assertFalse(text.endswith("\n\n"), "no trailing blank line")
        self.lines = text.splitlines()
        test.assertGreaterEqual(len(self.lines), 3, text)
        h, lt, rt = re.escape(c["h"]), re.escape(c["lt"]), re.escape(c["rt"])
        top = re.match(rf"^{re.escape(c['tl'])}{h} (.+?) {h}+{re.escape(c['tr'])}$", self.lines[0])
        test.assertIsNotNone(top, f"malformed top border: {self.lines[0]!r}")
        self.hostname = top.group(1)
        test.assertRegex(self.lines[-1], rf"^{re.escape(c['bl'])}{h}+{re.escape(c['br'])}$")
        self.host_lines: list[str] = []
        self.sections: list[tuple[str, list[str]]] = []
        self.content: list[str] = []  # every content line, right-stripped
        for line in self.lines[1:-1]:
            if line.startswith(c["lt"]):
                m = re.match(rf"^{lt}{h} (.+?) {h}+{rt}$", line)
                test.assertIsNotNone(m, f"malformed divider: {line!r}")
                self.sections.append((m.group(1), []))
                continue
            left, right = c["v"] + "  ", "  " + c["v"]
            test.assertTrue(line.startswith(left), f"content line must start with {left!r}: {line!r}")
            test.assertTrue(line.endswith(right), f"content line must end with {right!r}: {line!r}")
            body = line[len(left) : len(line) - len(right)].rstrip()
            self.content.append(body)
            (self.sections[-1][1] if self.sections else self.host_lines).append(body)

    @property
    def titles(self) -> list[str]:
        return [title for title, _ in self.sections]

    def section(self, title: str) -> list[str]:
        for name, rows in self.sections:
            if name == title:
                return rows
        raise AssertionError(f"no section {title!r} in {self.titles}")


def assert_rectangular(test: unittest.TestCase, text: str, ascii: bool) -> Parsed:
    parsed = Parsed(test, text, ascii)
    widths = {display_width(line) for line in parsed.lines}
    test.assertEqual(len(widths), 1, f"lines differ in display width {sorted(widths)}:\n{text}")
    return parsed


class FrameTests(unittest.TestCase):
    def test_frame_is_rectangular_with_borders_in_every_variant(self) -> None:
        for name, make, detail, ascii in combos():
            with self.subTest(fixture=name, detail=detail, ascii=ascii):
                text = render(make(), detail=detail, ascii=ascii)

                parsed = assert_rectangular(self, text, ascii)
                self.assertEqual(parsed.hostname, make().host.hostname)

    def test_default_sections_are_the_documented_four_in_order(self) -> None:
        for name, make, _, ascii in combos():
            with self.subTest(fixture=name, ascii=ascii):
                parsed = Parsed(self, render(make(), ascii=ascii), ascii)

                self.assertEqual(parsed.titles, ["CPU", "Memory", "Storage", "GPU"])

    def test_detail_sections_follow_in_the_documented_order(self) -> None:
        parsed = Parsed(self, render(make_full(), detail=True), False)

        self.assertEqual(
            parsed.titles,
            ["CPU", "Memory", "Storage", "GPU", "Network", "Temperatures", "Battery"],
        )

    def test_detail_sections_without_data_are_left_out_divider_included(self) -> None:
        for ascii in (False, True):
            with self.subTest(ascii=ascii):
                text = render(make_bare(), detail=True, ascii=ascii)
                parsed = Parsed(self, text, ascii)

                self.assertEqual(parsed.titles, ["CPU", "Memory", "Storage", "GPU"])
                for word in ("Network", "Temperatures", "Battery"):
                    self.assertNotIn(word, text)

    def test_each_empty_detail_section_is_dropped_independently(self) -> None:
        full = make_full()
        cases = {
            "Network": dataclasses.replace(full, network=()),
            "Temperatures": dataclasses.replace(full, temperatures=()),
            "Battery": dataclasses.replace(full, battery=None),
        }
        for gone, status in cases.items():
            with self.subTest(gone=gone):
                titles = Parsed(self, render(status, detail=True), False).titles

                self.assertNotIn(gone, titles)
                self.assertEqual(len(titles), 6)

    def test_swap_is_a_row_in_memory_not_a_divider(self) -> None:
        parsed = Parsed(self, render(make_full(), detail=True), False)

        self.assertNotIn("Swap", parsed.titles)
        self.assertTrue(any(r.startswith("Swap") for r in parsed.section("Memory")))

    def test_host_lines_come_before_the_first_divider(self) -> None:
        default = Parsed(self, render(make_full()), False)
        detail = Parsed(self, render(make_full(), detail=True), False)

        self.assertEqual(len(default.host_lines), 1)
        line = default.host_lines[0]
        for fact in ("Fedora Linux 42", "x86_64", "up 3d 4h 12m"):
            self.assertIn(fact, line)
        self.assertEqual(len(detail.host_lines), 2)
        self.assertEqual(detail.host_lines[0], line)
        self.assertIn("kernel 6.15.10-200.fc42.x86_64", detail.host_lines[1])
        self.assertRegex(detail.host_lines[1], r"booted \d{4}-\d\d-\d\d \d\d:\d\d")

    def test_blank_and_content_lines_keep_the_side_borders(self) -> None:
        text = render(make_full())
        blank = [ln for ln in text.splitlines() if ln.strip("│ ") == "" and ln.startswith("│")]

        self.assertTrue(blank, "expected a blank line between the two GPUs")
        for ln in blank:
            self.assertTrue(ln.endswith("│"))


class WideCharacterTests(unittest.TestCase):
    HOSTNAMES = ["サーバー", "ＡＢＣ", "서버", "主机-01", "cafe\u0301", "e\u0301e\u0301"]

    def with_names(self, hostname: str, mount: str):
        status = make_full(hostname=hostname)
        last = dataclasses.replace(status.storage[-1], mount=mount)
        return dataclasses.replace(status, storage=status.storage[:-1] + (last,))

    def test_wide_hostname_and_mount_keep_the_frame_rectangular(self) -> None:
        for hostname in self.HOSTNAMES:
            for ascii in (False, True):
                for detail in (False, True):
                    with self.subTest(hostname=hostname, ascii=ascii, detail=detail):
                        text = render(self.with_names(hostname, "/mnt/データ"), detail=detail, ascii=ascii)

                        parsed = assert_rectangular(self, text, ascii)
                        self.assertEqual(parsed.hostname, hostname)
                        self.assertIn("/mnt/データ", text)

    def test_wide_mount_does_not_misalign_the_bars(self) -> None:
        text = render(self.with_names("atlas", "/mnt/データ"))

        columns = {col for line in text.splitlines() for col, _ in find_bars(line)}
        self.assertEqual(len(columns), 1, columns)

    def test_wide_names_stay_rectangular_when_cut(self) -> None:
        status = self.with_names("サーバー東京", "/mnt/データ")
        natural = display_width(render(status).splitlines()[0])
        for width in range(natural - 12, natural):
            with self.subTest(width=width):
                text = render(status, width=width)

                assert_rectangular(self, text, False)
                self.assertTrue(all(display_width(ln) <= width for ln in text.splitlines()))


class KeyColumnTests(unittest.TestCase):
    def test_keyed_rows_and_bars_share_one_value_column(self) -> None:
        for name, make, detail, ascii in combos():
            with self.subTest(fixture=name, detail=detail, ascii=ascii):
                parsed = Parsed(self, render(make(), detail=detail, ascii=ascii), ascii)
                value_cols = set()
                for row in parsed.content:
                    m = KEY_RE.match(row)
                    if m and m.group(1) == "Cores":
                        # A grid of 4-column cells with the figures
                        # right-aligned: the first cell starts the value.
                        first = PCT_RE.search(row)
                        value_cols.add(CONTENT_LEFT + first.end() - 4)
                    elif m:
                        value_cols.add(CONTENT_LEFT + m.end(2))
                bar_cols = {
                    col
                    for line in parsed.lines
                    for col, _ in find_bars(line, ascii)
                }

                self.assertEqual(len(value_cols), 1, value_cols)
                self.assertEqual(len(bar_cols), 1, bar_cols)
                self.assertEqual(value_cols, bar_cols)

    def test_column_is_as_wide_as_the_longest_key_with_two_spaces_after_it(self) -> None:
        # default view of make_full: longest key is the mount point "/mnt/data"
        text = render(make_full())
        columns = {col for line in text.splitlines() for col, _ in find_bars(line)}

        self.assertEqual(columns, {CONTENT_LEFT + len("/mnt/data") + 2})

    def test_a_longer_key_pushes_every_value_right(self) -> None:
        mount = "/mnt/" + "d" * 15  # 20 columns
        status = make_full()
        last = dataclasses.replace(status.storage[-1], mount=mount)
        status = dataclasses.replace(status, storage=status.storage[:-1] + (last,))
        text = render(status)

        columns = {col for line in text.splitlines() for col, _ in find_bars(line)}
        self.assertEqual(columns, {CONTENT_LEFT + len(mount) + 2})

    def test_used_total_pairs_end_in_one_column(self) -> None:
        for name, make, detail, ascii in combos():
            with self.subTest(fixture=name, detail=detail, ascii=ascii):
                text = render(make(), detail=detail, ascii=ascii)
                ends = set()
                for line in text.splitlines():
                    for m in PAIR_RE.finditer(line):
                        ends.add(display_width(line[: m.end()]))

                self.assertTrue(ends, text)
                self.assertEqual(len(ends), 1, ends)

    def test_percentages_on_bar_rows_end_in_one_column(self) -> None:
        for name, make, detail, ascii in combos():
            with self.subTest(fixture=name, detail=detail, ascii=ascii):
                text = render(make(), detail=detail, ascii=ascii)
                ends = set()
                for line in text.splitlines():
                    if find_bars(line, ascii):
                        m = PCT_RE.search(line)
                        self.assertIsNotNone(m, line)
                        ends.add(display_width(line[: m.end()]))

                self.assertEqual(len(ends), 1, ends)


class LongKeyTests(unittest.TestCase):
    LONG = "/run/media/someone/My Passport Ultra"  # 36 columns

    def status_with_mount(self, mount: str):
        status = make_full()
        last = dataclasses.replace(status.storage[-1], mount=mount)
        return dataclasses.replace(status, storage=status.storage[:-1] + (last,))

    def key_of(self, text: str, mount: str, ascii: bool) -> tuple[str, int]:
        """The key field of the first storage row and the column its bar starts in."""
        for line in text.splitlines():
            bars = find_bars(line, ascii)
            if bars and line.startswith(CHARS[ascii]["v"] + "  " + mount[:4]):
                col = bars[0][0]
                # everything between the frame prefix and the bar is the key
                chars, width = 0, 0
                while width < col:
                    width += display_width(line[chars])
                    chars += 1
                return line[CONTENT_LEFT:chars].rstrip(), col
        raise AssertionError(f"no storage row for {mount!r}:\n{text}")

    def test_a_key_over_24_columns_is_shortened_in_the_middle_to_exactly_24(self) -> None:
        for ascii in (False, True):
            with self.subTest(ascii=ascii):
                text = render(self.status_with_mount(self.LONG), ascii=ascii)
                marker = CHARS[ascii]["cut"]

                key, bar_col = self.key_of(text, self.LONG, ascii)

                self.assertEqual(key.count(marker), 1, key)
                self.assertEqual(display_width(key), 24, key)
                head, tail = key.split(marker)
                self.assertTrue(head and tail, key)
                self.assertTrue(self.LONG.startswith(head), key)
                self.assertTrue(self.LONG.endswith(tail), key)
                self.assertNotEqual(key, self.LONG)

    def test_bars_still_start_in_one_column_after_shortening(self) -> None:
        for ascii in (False, True):
            for detail in (False, True):
                with self.subTest(ascii=ascii, detail=detail):
                    text = render(self.status_with_mount(self.LONG), detail=detail, ascii=ascii)

                    columns = {c for ln in text.splitlines() for c, _ in find_bars(ln, ascii)}
                    self.assertEqual(columns, {CONTENT_LEFT + 24 + 2})
                    assert_rectangular(self, text, ascii)

    def test_the_full_long_mount_does_not_appear(self) -> None:
        text = render(self.status_with_mount(self.LONG))

        self.assertNotIn(self.LONG, text)

    def test_a_key_of_exactly_24_columns_is_shown_whole(self) -> None:
        mount = "/mnt/" + "x" * 19
        self.assertEqual(len(mount), 24)
        for ascii in (False, True):
            with self.subTest(ascii=ascii):
                text = render(self.status_with_mount(mount), ascii=ascii)

                self.assertIn(mount, text)
                key, _ = self.key_of(text, mount, ascii)
                self.assertEqual(key, mount)

    def test_a_key_of_25_columns_is_shortened(self) -> None:
        mount = "/mnt/" + "x" * 20
        text = render(self.status_with_mount(mount))

        self.assertNotIn(mount, text)
        key, _ = self.key_of(text, mount, False)
        self.assertEqual(display_width(key), 24)
        self.assertEqual(key.count("…"), 1)

    def test_a_default_report_with_a_long_mount_fits_80_columns(self) -> None:
        for ascii in (False, True):
            with self.subTest(ascii=ascii):
                text = render(self.status_with_mount(self.LONG), ascii=ascii)

                self.assertTrue(all(display_width(ln) <= 80 for ln in text.splitlines()), text)

    def test_detail_line_of_a_shortened_mount_keeps_device_and_type(self) -> None:
        text = render(self.status_with_mount(self.LONG), detail=True)

        self.assertRegex(text, r"free 1\.0 TiB.*/dev/sda1.*ext4")


class BarTests(unittest.TestCase):
    def test_bars_are_20_cells_filled_from_the_left_and_follow_the_percentage(self) -> None:
        for name, make, detail, ascii in combos():
            with self.subTest(fixture=name, detail=detail, ascii=ascii):
                text = render(make(), detail=detail, ascii=ascii)
                seen = 0
                for line in text.splitlines():
                    for _, cells in find_bars(line, ascii):
                        seen += 1
                        self.assertEqual(len(cells), 20, line)
                        fill = bar_fill(cells, ascii)
                        self.assertIsNotNone(fill, f"not filled from the left: {line}")
                        pct = int(PCT_RE.search(line[line.index(cells) :]).group(1))
                        self.assertLess(abs(fill - pct / 5), 1.0, line)
                self.assertGreaterEqual(seen, 3)

    def test_zero_and_full_bars(self) -> None:
        status = make_full()
        memory = dataclasses.replace(status.memory, percent=0.0, used_bytes=0)
        empty = render(dataclasses.replace(status, memory=memory))
        memory = dataclasses.replace(status.memory, percent=100.0, used_bytes=status.memory.total_bytes)
        full = render(dataclasses.replace(status, memory=memory))

        ram_empty = next(ln for ln in empty.splitlines() if ln.startswith("│  RAM"))
        ram_full = next(ln for ln in full.splitlines() if ln.startswith("│  RAM"))
        self.assertEqual(find_bars(ram_empty)[0][1], "░" * 20)
        self.assertEqual(find_bars(ram_full)[0][1], "█" * 20)

    def test_ascii_bars_use_brackets_hash_and_dot(self) -> None:
        text = render(make_full(), ascii=True)

        self.assertRegex(text, r"\[#{14}\.{6}\]")  # RAM 72%
        self.assertRegex(text, r"\[#{7}\.{13}\]")  # CPU 37%
        self.assertNotRegex(text, "[█░]")


class AsciiTests(unittest.TestCase):
    def test_ascii_frame_has_no_drawing_characters_and_uses_plus_dash_pipe(self) -> None:
        for name, make, detail, _ in combos():
            with self.subTest(fixture=name, detail=detail):
                text = render(make(), detail=detail, ascii=True)
                lines = text.splitlines()

                for ch in DRAWING:
                    self.assertNotIn(ch, text)
                self.assertTrue(lines[0].startswith("+-") and lines[0].endswith("+"), lines[0])
                self.assertTrue(lines[-1].startswith("+-") and lines[-1].endswith("+"))
                self.assertTrue(re.fullmatch(r"\+-+\+", lines[-1]))
                for ln in lines[1:-1]:
                    self.assertIn(ln[0], "+|")
                    self.assertIn(ln[-1], "+|")

    def test_ascii_has_the_same_facts_as_unicode(self) -> None:
        for name, make, detail, _ in combos():
            with self.subTest(fixture=name, detail=detail):
                uni = render(make(), detail=detail)
                asc = render(make(), detail=detail, ascii=True)

                def facts(text: str) -> list[str]:
                    out = []
                    for ln in text.splitlines()[1:-1]:
                        if ln[0] in "├+":
                            continue
                        ln = re.sub(r"[█░]+|\[[#.]{20}\]", "", ln)
                        ln = re.sub(r"^[│|] +| +[│|]$", "", ln)
                        out.append(re.sub(r"\s+", " ", ln).strip())
                    return [x for x in out if x and not set(x) <= set("─-+├┤│|")]

                self.assertEqual(facts(uni), facts(asc))

    def test_ascii_divider_titles_are_the_same(self) -> None:
        parsed = Parsed(self, render(make_full(), detail=True, ascii=True), True)

        self.assertEqual(
            parsed.titles,
            ["CPU", "Memory", "Storage", "GPU", "Network", "Temperatures", "Battery"],
        )


class WidthTests(unittest.TestCase):
    def natural(self, make, detail, ascii) -> str:
        return render(make(), detail=detail, ascii=ascii)

    def check_cut(self, nat_text: str, text: str, width: int, ascii: bool) -> None:
        nat, cut = Parsed(self, nat_text, ascii), Parsed(self, text, ascii)
        marker = CHARS[ascii]["cut"]
        assert_rectangular(self, text, ascii)
        frame = display_width(cut.lines[0])
        self.assertLessEqual(frame, width)
        self.assertEqual(frame, width, "a cut frame fills the width it was given")
        self.assertEqual(len(nat.content), len(cut.content), "lines are cut, never wrapped")
        area = frame - 6
        cut_lines = 0
        for before, after in zip(nat.content, cut.content):
            if display_width(before) <= area:
                self.assertEqual(after, before)
            else:
                cut_lines += 1
                self.assertTrue(after.endswith(marker), f"{after!r} should end in {marker!r}")
                self.assertLessEqual(display_width(after), area)
                self.assertTrue(before.startswith(after[:-1].rstrip()), (before, after))
        self.assertGreater(cut_lines, 0)
        self.assertEqual(
            [t for t, _ in nat.sections], [t for t, _ in cut.sections], "dividers stay"
        )

    def test_narrower_than_natural_cuts_lines_and_keeps_the_frame(self) -> None:
        for name, make, detail, ascii in combos():
            nat_text = self.natural(make, detail, ascii)
            natural = display_width(nat_text.splitlines()[0])
            for width in (natural - 10, natural - 1, 30):
                if width >= natural:
                    continue
                with self.subTest(fixture=name, detail=detail, ascii=ascii, width=width):
                    text = render(make(), detail=detail, ascii=ascii, width=width)

                    self.check_cut(nat_text, text, width, ascii)

    def test_width_at_or_above_natural_changes_nothing(self) -> None:
        for name, make, detail, ascii in combos():
            nat_text = self.natural(make, detail, ascii)
            natural = display_width(nat_text.splitlines()[0])
            for width in (natural, natural + 1, natural + 100, 1000):
                with self.subTest(fixture=name, detail=detail, ascii=ascii, width=width):
                    self.assertEqual(
                        render(make(), detail=detail, ascii=ascii, width=width), nat_text
                    )

    def test_none_means_natural_width(self) -> None:
        self.assertEqual(render(make_full(), width=None), render(make_full()))

    def test_cut_with_colour_matches_the_plain_cut(self) -> None:
        for name, make, detail, ascii in combos():
            natural = display_width(self.natural(make, detail, ascii).splitlines()[0])
            width = natural - 8
            with self.subTest(fixture=name, detail=detail, ascii=ascii, width=width):
                coloured = render_status(
                    make(), detail=detail, color=True, ascii=ascii, width=width
                )
                plain = render(make(), detail=detail, ascii=ascii, width=width)

                self.assertIn("\x1b", coloured)
                self.assertEqual(strip_ansi(coloured), plain)
                self.assertTrue(
                    all(display_width(ln) <= width for ln in strip_ansi(coloured).splitlines())
                )

    def test_frame_is_never_narrower_than_20(self) -> None:
        for width in (1, 10, 19):
            with self.subTest(width=width):
                text = render(make_full(), width=width)

                assert_rectangular(self, text, False)
                self.assertGreaterEqual(display_width(text.splitlines()[0]), 20)


class ContentTests(unittest.TestCase):
    def parsed(self, status, detail=False, ascii=False) -> Parsed:
        return Parsed(self, render(status, detail=detail, ascii=ascii), ascii)

    def heads(self, rows: list[str]) -> list[str]:
        return [r.split()[0] if r.strip() else "" for r in rows]

    # CPU
    def test_cpu_rows_default(self) -> None:
        rows = self.parsed(make_full()).section("CPU")

        self.assertRegex(rows[0], r"^Intel Core i7-12700K\s+\(12 cores, 20 threads\)$")
        self.assertEqual(self.heads(rows[1:]), ["Usage", "Load"])
        self.assertRegex(rows[1], r"^Usage\s+\S+\s+37%$")
        self.assertRegex(rows[2], r"^Load\s+2\.50\s+3\.50\s+4\.50$")

    def test_cpu_has_no_load_row_where_the_system_has_none(self) -> None:
        for detail in (False, True):
            with self.subTest(detail=detail):
                rows = self.parsed(make_bare(), detail=detail).section("CPU")

                self.assertIn("Generic CPU", rows[0])
                self.assertNotIn("Load", self.heads(rows))

    def test_cpu_detail_rows(self) -> None:
        rows = self.parsed(make_full(), detail=True).section("CPU")
        heads = self.heads(rows)

        for key in ("Usage", "Load", "Frequency", "Temp", "Cores"):
            self.assertIn(key, heads)
        self.assertRegex(next(r for r in rows if r.startswith("Frequency")), r"3600|3\.6")
        self.assertRegex(next(r for r in rows if r.startswith("Temp")), r"58°C$")

    def test_cores_are_ten_to_a_row_in_order(self) -> None:
        rows = self.parsed(make_full(), detail=True).section("CPU")
        at = next(i for i, r in enumerate(rows) if r.startswith("Cores"))
        numbers = [
            [float(n) for n in re.findall(r"\d+(?:\.\d+)?", r[len("Cores") :] if j == 0 else r)]
            for j, r in enumerate(rows[at : at + 2])
        ]

        self.assertEqual(numbers[0], [float(81 + i) for i in range(10)])
        self.assertEqual(numbers[1], [float(91 + i) for i in range(10)])

    def test_cores_with_four_logical_cores_fit_one_row(self) -> None:
        rows = self.parsed(make_bare(), detail=True).section("CPU")
        row = next(r for r in rows if r.startswith("Cores"))

        self.assertEqual(
            [float(n) for n in re.findall(r"\d+(?:\.\d+)?", row[len("Cores") :])],
            [4.0, 6.0, 5.0, 5.0],
        )

    def test_default_cpu_view_has_no_detail_rows(self) -> None:
        heads = self.heads(self.parsed(make_full()).section("CPU"))

        for key in ("Frequency", "Temp", "Cores"):
            self.assertNotIn(key, heads)

    # Memory
    def test_memory_default_row(self) -> None:
        rows = self.parsed(make_full()).section("Memory")

        self.assertEqual(self.heads(rows), ["RAM"])
        self.assertRegex(rows[0], r"^RAM\s+\S+\s+72%\s+22\.5 / 31\.1 GiB$")

    def test_memory_detail_rows(self) -> None:
        rows = self.parsed(make_full(), detail=True).section("Memory")

        self.assertEqual(self.heads(rows), ["RAM", "Available", "Swap"])
        self.assertRegex(rows[1], r"^Available\s+8\.5 GiB$")
        self.assertRegex(rows[2], r"^Swap\s.*25%.*1\.0 / 4\.0 GiB$")

    def test_swap_says_none_when_not_configured(self) -> None:
        rows = self.parsed(make_bare(), detail=True).section("Memory")

        self.assertRegex(next(r for r in rows if r.startswith("Swap")), r"^Swap\s+none$")

    def test_default_memory_view_has_no_available_or_swap(self) -> None:
        for make in (make_full, make_bare):
            self.assertEqual(self.heads(self.parsed(make()).section("Memory")), ["RAM"])

    # Storage
    def test_storage_default_has_one_row_per_shown_mount(self) -> None:
        rows = self.parsed(make_full()).section("Storage")

        self.assertEqual(self.heads(rows), ["/", "/mnt/data"])
        self.assertRegex(rows[0], r"75%\s+150\.0 / 200\.0 GiB$")
        self.assertRegex(rows[1], r"50%\s+1\.0 / 2\.0 TiB$")

    def test_storage_detail_adds_a_line_under_each_mount(self) -> None:
        rows = self.parsed(make_full(), detail=True).section("Storage")

        self.assertEqual(len(rows), 6)
        self.assertEqual(self.heads(rows)[::2], ["/", "/home", "/mnt/data"])
        self.assertRegex(rows[1], r"free 50\.0 GiB.*/dev/nvme0n1p2.*btrfs")
        self.assertRegex(rows[3], r"free 50\.0 GiB.*/dev/nvme0n1p2.*btrfs")
        self.assertRegex(rows[5], r"free 1\.0 TiB.*/dev/sda1.*ext4")
        for index in (1, 3, 5):
            self.assertNotIn("%", rows[index])

    def test_storage_detail_for_the_bare_machine(self) -> None:
        rows = self.parsed(make_bare(), detail=True).section("Storage")

        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].startswith("C:\\"))
        self.assertRegex(rows[1], r"free 400\.0 GiB.*C:\\.*NTFS")

    def test_no_storage_says_none_found(self) -> None:
        status = dataclasses.replace(make_full(), storage=())

        self.assertEqual(self.parsed(status).section("Storage"), ["none found"])
        self.assertEqual(self.parsed(status, detail=True).section("Storage"), ["none found"])

    # GPU
    def test_gpu_blocks_default(self) -> None:
        rows = self.parsed(make_full()).section("GPU")

        self.assertEqual(
            self.heads(rows),
            ["NVIDIA", "Usage", "Memory", "Temp", "", "AMD", "Usage", "Memory", "Temp"],
        )
        self.assertEqual(rows[0], "NVIDIA GeForce RTX 4080")
        self.assertRegex(rows[1], r"31%$")
        self.assertRegex(rows[2], r"25%\s+4\.0 / 16\.0 GiB$")
        self.assertRegex(rows[3], r"^Temp\s+63°C$")
        self.assertEqual(rows[5], "AMD Radeon RX 6800")
        self.assertRegex(rows[7], r"25%\s+2\.0 / 8\.0 GiB$")
        self.assertRegex(rows[8], r"^Temp\s+52°C$")

    def test_gpu_detail_adds_driver_and_power_to_each_block(self) -> None:
        rows = self.parsed(make_full(), detail=True).section("GPU")
        heads = self.heads(rows)

        self.assertEqual(heads.count("Driver"), 2)
        self.assertEqual(heads.count("Power"), 2)
        self.assertEqual(heads.count(""), 1)
        self.assertRegex(next(r for r in rows if "580.105.08" in r), r"^Driver\s+580\.105\.08$")
        self.assertRegex(next(r for r in rows if r.startswith("Power")), r"^Power\s+17\.6")
        self.assertRegex(next(r for r in rows if "amdgpu" in r), r"^Driver\s+amdgpu$")

    def test_gpu_rows_appear_only_when_known(self) -> None:
        gpu = GpuInfo(index=0, name="Intel UHD Graphics 770", vendor="Intel")
        status = dataclasses.replace(make_full(), gpus=(gpu,))
        for detail in (False, True):
            with self.subTest(detail=detail):
                self.assertEqual(
                    self.parsed(status, detail=detail).section("GPU"),
                    ["Intel UHD Graphics 770"],
                )

    def test_gpu_memory_total_alone_has_no_bar(self) -> None:
        gpu = GpuInfo(index=0, name="Apple M2", memory_total_bytes=16 * 1024**3)
        status = dataclasses.replace(make_full(), gpus=(gpu,))
        rows = self.parsed(status).section("GPU")

        self.assertEqual(self.heads(rows), ["Apple", "Memory"])
        self.assertRegex(rows[1], r"^Memory\s+16\.0 GiB$")
        self.assertNotIn("%", rows[1])

    def test_gpu_usage_only(self) -> None:
        gpu = GpuInfo(index=0, name="Some GPU", utilization_percent=50.0)
        status = dataclasses.replace(make_full(), gpus=(gpu,))
        rows = self.parsed(status).section("GPU")

        self.assertEqual(self.heads(rows), ["Some", "Usage"])
        self.assertRegex(rows[1], r"50%$")

    def test_no_gpu_says_none_detected(self) -> None:
        for detail in (False, True):
            with self.subTest(detail=detail):
                self.assertEqual(
                    self.parsed(make_bare(), detail=detail).section("GPU"), ["none detected"]
                )

    # Network, temperatures, battery
    def test_network_has_a_header_then_a_row_per_interface_with_one_address_a_line(self) -> None:
        rows = self.parsed(make_full(), detail=True).section("Network")
        eth = next(i for i, r in enumerate(rows) if "eth0" in r)
        wlan = next(i for i, r in enumerate(rows) if "wlan0" in r)

        self.assertGreaterEqual(eth, 1, "expected a header line first")
        self.assertLess(eth, wlan)
        self.assertRegex(rows[eth], r"\bup\b")
        self.assertRegex(rows[wlan], r"\bdown\b")
        self.assertLess(rows[eth].index("1.0 MiB"), rows[eth].index("5.0 MiB"))
        a = next(i for i, r in enumerate(rows) if "192.168.1.20" in r)
        b = next(i for i, r in enumerate(rows) if "fe80::1" in r)
        self.assertNotEqual(a, b, "one address per line")
        self.assertTrue(eth <= a < b < wlan)

    def test_temperature_blocks_are_keyed_by_chip(self) -> None:
        rows = self.parsed(make_full(), detail=True).section("Temperatures")
        core = next(i for i, r in enumerate(rows) if "coretemp" in r)
        nvme = next(i for i, r in enumerate(rows) if "nvme" in r)
        first, second = sorted((core, nvme))
        blocks = {first: " ".join(rows[first:second]), second: " ".join(rows[second:])}

        self.assertIn("Package id 0", blocks[core])
        self.assertIn("58°C", blocks[core])
        self.assertNotIn("Composite", blocks[core])
        self.assertIn("Composite", blocks[nvme])
        self.assertIn("41°C", blocks[nvme])

    def test_battery_charge_row(self) -> None:
        rows = self.parsed(make_full(), detail=True).section("Battery")

        self.assertEqual(self.heads(rows), ["Charge"])
        self.assertIn("64%", rows[0])
        self.assertIn("plugged in", rows[0])

    def test_battery_on_battery(self) -> None:
        battery = BatteryInfo(percent=40.0, plugged=False, seconds_left=5400)
        status = dataclasses.replace(make_full(), battery=battery)
        row = self.parsed(status, detail=True).section("Battery")[0]

        self.assertIn("40%", row)
        self.assertIn("on battery", row)
        self.assertNotIn("plugged in", row)

    # Formats
    def test_missing_values_never_print_none_in_any_variant(self) -> None:
        for name, make, detail, ascii in combos():
            with self.subTest(fixture=name, detail=detail, ascii=ascii):
                text = render(make(), detail=detail, ascii=ascii)

                self.assertNotIn("None", text)
                self.assertNotIn("null", text)

    def test_colour_only_adds_escape_codes_in_both_character_sets(self) -> None:
        for name, make, detail, ascii in combos():
            with self.subTest(fixture=name, detail=detail, ascii=ascii):
                coloured = render_status(make(), detail=detail, color=True, ascii=ascii)

                self.assertIn("\x1b", coloured)
                self.assertEqual(
                    strip_ansi(coloured), render_status(make(), detail=detail, ascii=ascii)
                )


class CommandEncodingTests(unittest.TestCase):
    def run_status(self, charset: str, *args: str, status=None):
        runner = CliRunner(charset=charset)
        with mock.patch("jzr_tools.commands.host.StatusSampler") as cls:
            cls.return_value.sample.side_effect = [status or make_full()]
            return runner.invoke(app, ["host", "status", *args])

    def test_utf8_output_uses_the_unicode_frame(self) -> None:
        result = self.run_status("utf-8")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[0][0], "╭")
        self.assertEqual(result.stdout.rstrip("\n"), render_status(make_full(), ascii=False).rstrip("\n"))
        self.assertIn("63°C", result.stdout)

    def test_cp1252_output_uses_the_ascii_frame_and_keeps_the_degree_sign(self) -> None:
        result = self.run_status("cp1252", "--detail")

        self.assertIsNone(result.exception, repr(result.exception))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[0][0], "+")
        self.assertEqual(
            result.stdout.rstrip("\n"),
            render_status(make_full(), detail=True, ascii=True).rstrip("\n"),
        )
        self.assertIn("63°C", result.stdout)

    def test_ascii_only_output_never_raises_and_is_the_ascii_frame(self) -> None:
        for args in ((), ("--detail",)):
            for status in (make_full(), make_bare()):
                with self.subTest(args=args, host=status.host.hostname):
                    result = self.run_status("ascii", *args, status=status)

                    self.assertIsNone(result.exception, repr(result.exception))
                    self.assertEqual(result.exit_code, 0, result.output)
                    lines = result.stdout.splitlines()
                    self.assertTrue(lines[0].startswith("+-"))
                    self.assertTrue(lines[-1].startswith("+-"))
                    for ch in DRAWING:
                        self.assertNotIn(ch, result.stdout)
                    self.assertEqual({len(ln) for ln in lines}, {len(lines[0])})

    def test_ascii_output_with_an_unencodable_hostname_is_replaced_not_a_crash(self) -> None:
        result = self.run_status("ascii", status=make_full(hostname="サーバー"))

        self.assertIsNone(result.exception, repr(result.exception))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(result.stdout.splitlines()[0].startswith("+-"))

    def test_unicode_frame_with_an_unencodable_hostname_does_not_crash_on_cp1252(self) -> None:
        result = self.run_status("cp1252", status=make_full(hostname="サーバー"))

        self.assertIsNone(result.exception, repr(result.exception))
        self.assertEqual(result.exit_code, 0, result.output)

    def test_nothing_is_cut_when_stdout_is_not_a_terminal(self) -> None:
        narrow = os.terminal_size((40, 10))
        with mock.patch.dict(os.environ, {"COLUMNS": "40", "LINES": "10"}), mock.patch(
            "shutil.get_terminal_size", return_value=narrow
        ), mock.patch("os.get_terminal_size", return_value=narrow):
            result = self.run_status("utf-8", "--detail")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.rstrip("\n"), render_status(make_full(), detail=True).rstrip("\n")
        )
        self.assertNotIn("…", result.stdout)
        self.assertGreater(display_width(result.stdout.splitlines()[0]), 40)


if __name__ == "__main__":
    unittest.main()
