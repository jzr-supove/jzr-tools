"""Acceptance tests for ``jzr host procs`` with process data and prompts faked."""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import select
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import psutil
from click.testing import CliRunner

import jzr_tools
from jzr_tools.cli import app

try:
    import fcntl
    import pty
    import struct
    import termios
except ImportError:  # Windows
    pty = None

from host_fixtures import bar_fill, display_width, find_bars, strip_ansi
from test_host_layout import CHARS, DRAWING, Parsed, assert_rectangular

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = Path(jzr_tools.__file__).resolve().parents[1]
GIB = 1024**3
MIB = 1024**2
KIB = 1024

runner = CliRunner()

# These tests pin the layout with a Swap column, which only systems that keep
# a per-process share of swap show. The Windows layout is pinned in
# test_procs_windows, whose tests patch the platform themselves.
_linux_layout = mock.patch("jzr_tools.core.procs._windows", return_value=False)


def setUpModule() -> None:  # noqa: N802
    _linux_layout.start()


def tearDownModule() -> None:  # noqa: N802
    _linux_layout.stop()


def make_proc(pid: int, **overrides):
    procs = importlib.import_module("jzr_tools.core.procs")
    values = dict(
        pid=pid,
        name="app",
        exe="/usr/bin/app",
        user="alice",
        command="/usr/bin/app --run",
        memory_bytes=10 * MIB,
        swap_bytes=1 * MIB,
        started=1000.0,
        protected="",
    )
    values.update(overrides)
    return procs.Proc(**values)


def end_result(ended=(), survivors=(), failed=()):
    procs = importlib.import_module("jzr_tools.core.procs")
    return procs.EndResult(ended=tuple(ended), survivors=tuple(survivors), failed=tuple(failed))


TOTALS = {
    "total_bytes": 32 * GIB,
    "used_bytes": 12 * GIB,
    "swap_total_bytes": 8 * GIB,
    "swap_used_bytes": 3 * GIB // 2,
}

# Groups by memory: firefox (3 procs, 1.5 GiB), editor (1, 512 MiB),
# systemd (2, protected, 98.5 MiB), tiny (1, 64 MiB).


def sample_processes():
    return [
        make_proc(10, name="firefox", exe="/usr/lib/firefox/firefox", memory_bytes=1000 * MIB,
                  swap_bytes=300 * KIB, command="/usr/lib/firefox/firefox --new-window"),
        make_proc(11, name="firefox", exe="/usr/lib/firefox/firefox", memory_bytes=400 * MIB,
                  swap_bytes=200 * KIB, command="/usr/lib/firefox/firefox -contentproc tab"),
        make_proc(12, name="firefox", exe="/usr/lib/firefox/firefox", memory_bytes=136 * MIB,
                  swap_bytes=100 * KIB, command="/usr/lib/firefox/firefox -contentproc gpu"),
        make_proc(20, name="editor", exe="/usr/bin/editor", memory_bytes=512 * MIB,
                  swap_bytes=50 * KIB, user="bob", command="/usr/bin/editor notes.txt"),
        make_proc(1, name="systemd", exe="/usr/lib/systemd/systemd", memory_bytes=60 * MIB,
                  swap_bytes=10 * KIB, user="root", protected="system", command="/sbin/init"),
        make_proc(2, name="systemd", exe="/usr/lib/systemd/systemd", memory_bytes=38 * MIB + MIB // 2,
                  swap_bytes=20 * KIB, user="root", protected="system", command="/lib/systemd --user"),
        make_proc(30, name="tiny", exe="/usr/bin/tiny", memory_bytes=64 * MIB,
                  swap_bytes=70 * KIB, command="/usr/bin/tiny"),
    ]


SIZE = re.compile(r"\d+\.\d (?:B|KiB|MiB|GiB|TiB)")


def render_procs(*args, **kwargs) -> str:
    """Import lazily so a missing renderer fails the tests that need it, not the module."""
    module = importlib.import_module("jzr_tools.commands.host_procs")
    return module.render_procs(*args, **kwargs)


def sample_groups():
    return importlib.import_module("jzr_tools.core.procs").group_processes(sample_processes())


def sample_singles():
    """Single processes, largest memory first, as ``--each`` lists them."""
    return sorted(sample_processes(), key=lambda p: -p.memory_bytes)


def frame_text(text: str) -> str:
    """The text with escape codes removed and exactly one trailing newline."""
    return strip_ansi(text).rstrip("\n") + "\n"


def table_rows(parsed: Parsed) -> list[str]:
    """Content rows of the Applications/Processes section, header excluded."""
    for title, rows in parsed.sections:
        if title.startswith(("Applications", "Processes")):
            return rows[1:]
    raise AssertionError(f"no table section in {parsed.titles}")


def table_header(parsed: Parsed) -> str:
    for title, rows in parsed.sections:
        if title.startswith(("Applications", "Processes")):
            return rows[0]
    raise AssertionError(f"no table section in {parsed.titles}")


def right_edges(body: str, each: bool) -> dict[str, int]:
    """Display column where the numbers of a table or total row end."""
    sizes = list(SIZE.finditer(body))
    edges = {"memory": sizes[0].end(), "swap": sizes[1].end()}
    if not each:
        # The count is the number right before the memory figure; a label
        # such as "Top 2" ends in a number too and must not be taken for it.
        edges["count"] = re.search(r"\d+(?=\s+$)", body[: sizes[0].start()]).end()
    return edges


class FakedCommand(unittest.TestCase):
    """Patches the data and prompt seams; subclasses call ``self.run_cli``."""

    processes = None
    interactive = False

    def setUp(self) -> None:
        self.procs = importlib.import_module("jzr_tools.core.procs")
        importlib.import_module("jzr_tools.prompts")
        self.snapshot = self.patch(self.procs, "snapshot", return_value=list(self.processes or sample_processes()))
        self.totals = self.patch(self.procs, "memory_totals", return_value=dict(TOTALS))
        self.end = self.patch(self.procs, "end_processes", side_effect=self._end)
        self.kill = self.patch(self.procs, "kill_processes", side_effect=self._kill)
        prompts = importlib.import_module("jzr_tools.prompts")
        self.choose = self.patch(prompts, "choose", return_value=[])
        self.confirm = self.patch(prompts, "confirm", return_value=True)
        self.end_calls: list[list] = []
        self.kill_calls: list[list] = []
        self.end_return = None
        self.kill_return = None

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _end(self, processes, **kwargs):
        listed = list(processes)
        self.end_calls.append(listed)
        return self.end_return if self.end_return is not None else end_result(ended=listed)

    def _kill(self, processes, **kwargs):
        listed = list(processes)
        self.kill_calls.append(listed)
        return self.kill_return if self.kill_return is not None else end_result(ended=listed)

    def run_cli(self, *args, interactive=None, charset=None):
        interactive = self.interactive if interactive is None else interactive
        with contextlib.ExitStack() as stack:
            if interactive:
                stack.enter_context(
                    mock.patch("jzr_tools.commands.host_procs._is_interactive", return_value=True)
                )
            invoker = CliRunner(charset=charset) if charset else runner
            return invoker.invoke(app, ["host", "procs", *args])

    def tick(self, *names):
        """Make the fake checkbox list tick the rows whose label contains a name."""
        prompts = importlib.import_module("jzr_tools.prompts")

        def choose(message, entries, **kwargs):
            self.entries = [e for e in entries if isinstance(e, prompts.Choice)]
            return [e.value for e in self.entries if any(n in e.label for n in names)]

        self.choose.side_effect = choose

    @staticmethod
    def lines(result):
        return result.output.splitlines()

    def row_index(self, result, word):
        for i, line in enumerate(self.lines(result)):
            if re.search(rf"(^|\s){re.escape(word)}(\s|$)", line):
                return i
        self.fail(f"no row for {word!r} in:\n{result.output}")

    def parsed(self, result) -> Parsed:
        self.assertEqual(result.exit_code, 0, result.output)
        return assert_rectangular(self, result.stdout, ascii=False)

    def rows(self, result) -> list[str]:
        """The table rows (content between the borders), header excluded."""
        return table_rows(self.parsed(result))

    def row(self, result, word) -> str:
        """The content of the one table row whose first cell or name is ``word``."""
        found = [r for r in self.rows(result) if re.search(rf"(^|\s){re.escape(word)}(\s|$)", r)]
        self.assertTrue(found, f"no row for {word!r} in:\n{result.output}")
        return found[0]


# --------------------------------------------------------------------------
# Table view
# --------------------------------------------------------------------------


class GroupTableTests(FakedCommand):
    def test_exits_zero_and_prints_the_group_columns_in_order(self) -> None:
        result = self.run_cli()

        header = table_header(self.parsed(result))
        positions = [header.index(c) for c in ("Application", "Procs", "Memory", "Swap", "User")]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(set(positions)), 5)
        self.assertNotIn("PID", header)
        self.assertNotIn("Command", header)

    def test_memory_section_shows_ram_and_swap_in_use_out_of_the_totals(self) -> None:
        result = self.run_cli()

        parsed = self.parsed(result)
        self.assertEqual(parsed.hostname, "Memory")
        ram, swap = parsed.host_lines
        self.assertRegex(ram, r"^RAM\s")
        self.assertRegex(swap, r"^Swap\s")
        self.assertIn("12.0 / 32.0 GiB", ram)
        self.assertIn("1.5 / 8.0 GiB", swap)

    def test_one_row_per_application_largest_memory_first(self) -> None:
        result = self.run_cli()

        names = [r.split()[0] for r in self.rows(result)]
        self.assertEqual(names, ["firefox", "editor", "systemd", "tiny"], result.output)

    def test_row_shows_count_summed_memory_swap_and_user(self) -> None:
        result = self.run_cli()

        self.assertRegex(self.row(result, "firefox"), r"^firefox\s+3\s+1\.5 GiB\s+600\.0 KiB\s+alice$")
        self.assertRegex(self.row(result, "editor"), r"^editor\s+1\s+512\.0 MiB\s+50\.0 KiB\s+bob$")

    def test_numbers_are_right_aligned(self) -> None:
        result = self.run_cli()
        rows = self.rows(result)

        edges = [right_edges(r, each=False) for r in rows]
        for column in ("count", "memory", "swap"):
            self.assertEqual(len({e[column] for e in edges}), 1, (column, rows))

    def test_protected_group_ends_with_its_reason_in_parentheses(self) -> None:
        result = self.run_cli()

        self.assertTrue(self.row(result, "systemd").endswith("root (system)"), self.row(result, "systemd"))
        self.assertNotIn("(", self.row(result, "firefox"))

    def test_group_with_a_mix_of_protected_and_free_processes_is_not_marked(self) -> None:
        self.snapshot.return_value = [
            make_proc(1, protected="this terminal"),
            make_proc(2),
        ]

        result = self.run_cli()

        self.assertNotIn("(this terminal)", self.row(result, "app"))

    def test_each_reason_is_printed(self) -> None:
        self.snapshot.return_value = [
            make_proc(1, exe="/a/one", name="one", protected="this terminal"),
            make_proc(2, exe="/a/two", name="two", protected="another user"),
        ]

        result = self.run_cli()

        self.assertTrue(self.row(result, "one").endswith("(this terminal)"))
        self.assertTrue(self.row(result, "two").endswith("(another user)"))

    def test_piped_output_is_the_unicode_frame_without_colour(self) -> None:
        result = self.run_cli()

        self.assertNotIn("\x1b", result.output)
        parsed = self.parsed(result)
        self.assertEqual(parsed.lines[0][0], "╭")
        self.assertEqual(parsed.lines[-1][0], "╰")

    def test_never_opens_a_prompt(self) -> None:
        self.run_cli()
        self.run_cli("--each")
        self.run_cli("--json")

        self.choose.assert_not_called()
        self.confirm.assert_not_called()
        self.end.assert_not_called()
        self.kill.assert_not_called()


class EachTableTests(FakedCommand):
    def test_columns_in_order(self) -> None:
        result = self.run_cli("--each")

        header = table_header(self.parsed(result))
        positions = [header.index(c) for c in ("PID", "Name", "Memory", "Swap", "User", "Command")]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(set(positions)), 6)
        self.assertNotIn("Application", header)

    def test_one_row_per_process_largest_first_with_pid_and_command(self) -> None:
        result = self.run_cli("--each")

        rows = self.rows(result)
        self.assertEqual([int(r.split()[0]) for r in rows], [10, 20, 11, 12, 30, 1, 2])
        self.assertRegex(rows[0], r"^\s*10\s+firefox\s+1000\.0 MiB\s+300\.0 KiB\s+alice\s+/usr/lib/firefox/firefox --new-window$")

    def test_protected_process_ends_with_its_reason_after_the_command(self) -> None:
        result = self.run_cli("--each")

        row = next(r for r in self.rows(result) if r.split()[0] == "1")
        self.assertTrue(row.endswith("/sbin/init (system)"), row)

    def test_piped_output_is_not_cut(self) -> None:
        long_command = "/usr/bin/app " + "--option-value-" * 40
        self.snapshot.return_value = [make_proc(5, command=long_command)]

        result = self.run_cli("--each")

        self.assertIn(long_command, result.output)
        self.parsed(result)  # and the frame still has equal-width lines

    def test_numbers_are_right_aligned(self) -> None:
        result = self.run_cli("--each")
        rows = self.rows(result)

        pid_ends = {re.match(r"\s*\d+", r).end() for r in rows}
        edges = [right_edges(r, each=True) for r in rows]
        self.assertEqual(len(pid_ends), 1, rows)
        self.assertEqual(len({e["memory"] for e in edges}), 1, rows)
        self.assertEqual(len({e["swap"] for e in edges}), 1, rows)


class SelectionTests(FakedCommand):
    def test_pattern_matches_the_name_ignoring_case(self) -> None:
        result = self.run_cli("FIRE")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("firefox", result.output)
        self.assertNotIn("editor", result.output)
        self.assertNotIn("systemd", result.output)

    def test_pattern_matches_the_command_line(self) -> None:
        result = self.run_cli("notes.txt")

        self.assertIn("editor", result.output)
        self.assertNotIn("firefox", result.output)

    def test_pattern_filters_processes_before_grouping(self) -> None:
        result = self.run_cli("contentproc")

        row = self.row(result, "firefox")
        self.assertRegex(row, r"firefox\s+2\s+")  # two of the three processes match
        self.assertIn("536.0 MiB", row)

    def test_pattern_with_each_lists_only_matching_processes(self) -> None:
        result = self.run_cli("--each", "contentproc")

        pids = [int(r.split()[0]) for r in self.rows(result)]
        self.assertEqual(pids, [11, 12])

    def test_no_match_says_so_and_exits_zero(self) -> None:
        result = self.run_cli("nothing-like-this")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.output.strip(), "No matching processes.")
        self.assertNotIn("firefox", result.output)

    def test_no_match_with_each_says_so(self) -> None:
        result = self.run_cli("--each", "nothing-like-this")

        self.assertEqual(result.exit_code, 0)
        self.assertIn("No matching processes.", result.output)

    def test_top_limits_the_groups_to_the_biggest(self) -> None:
        result = self.run_cli("--top", "2")

        self.assertIn("firefox", result.output)
        self.assertIn("editor", result.output)
        self.assertNotIn("tiny", result.output)
        self.assertNotIn("systemd", result.output)

    def test_short_option_n_is_top(self) -> None:
        result = self.run_cli("-n", "1")

        self.assertIn("firefox", result.output)
        self.assertNotIn("editor", result.output)

    def test_top_applies_to_each_process_rows(self) -> None:
        result = self.run_cli("--each", "-n", "3")

        pids = [int(r.split()[0]) for r in self.rows(result)]
        self.assertEqual(pids, [10, 20, 11])

    def test_default_top_is_fifteen(self) -> None:
        self.snapshot.return_value = [
            make_proc(100 + i, name=f"prog{i:02d}", exe=f"/bin/prog{i:02d}", memory_bytes=(100 - i) * MIB)
            for i in range(20)
        ]

        result = self.run_cli()

        shown = [n for n in (f"prog{i:02d}" for i in range(20)) if n in result.output]
        self.assertEqual(shown, [f"prog{i:02d}" for i in range(15)])

    def test_top_is_applied_after_the_pattern(self) -> None:
        result = self.run_cli("--top", "1", "editor")

        self.assertIn("editor", result.output)
        self.assertNotIn("firefox", result.output)


class JsonTests(FakedCommand):
    def test_group_document_shape(self) -> None:
        result = self.run_cli("--json")

        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(result.stdout)
        self.assertEqual(set(data), {"memory", "rows", "totals"})
        self.assertEqual(data["memory"], TOTALS)
        self.assertEqual([r["name"] for r in data["rows"]], ["firefox", "editor", "systemd", "tiny"])
        for row in data["rows"]:
            self.assertEqual(
                set(row),
                {"name", "user", "count", "memory_bytes", "swap_bytes", "pids", "protected"},
            )

    def test_group_row_values(self) -> None:
        rows = {r["name"]: r for r in json.loads(self.run_cli("--json").stdout)["rows"]}

        firefox = rows["firefox"]
        self.assertEqual(firefox["user"], "alice")
        self.assertEqual(firefox["count"], 3)
        self.assertEqual(firefox["memory_bytes"], 1536 * MIB)
        self.assertEqual(firefox["swap_bytes"], 600 * KIB)
        self.assertEqual(firefox["pids"], [10, 11, 12])
        self.assertIsNone(firefox["protected"])
        self.assertEqual(rows["systemd"]["protected"], "system")
        self.assertEqual(rows["systemd"]["count"], 2)

    def test_each_document_shape_and_values(self) -> None:
        data = json.loads(self.run_cli("--json", "--each").stdout)

        self.assertEqual([r["pid"] for r in data["rows"]], [10, 20, 11, 12, 30, 1, 2])
        for row in data["rows"]:
            self.assertEqual(
                set(row),
                {"pid", "name", "user", "memory_bytes", "swap_bytes", "command", "protected"},
            )
        first = data["rows"][0]
        self.assertEqual(first["name"], "firefox")
        self.assertEqual(first["memory_bytes"], 1000 * MIB)
        self.assertEqual(first["swap_bytes"], 300 * KIB)
        self.assertEqual(first["command"], "/usr/lib/firefox/firefox --new-window")
        self.assertIsNone(first["protected"])
        self.assertEqual(data["rows"][5]["protected"], "system")

    def test_json_respects_pattern_and_top(self) -> None:
        data = json.loads(self.run_cli("--json", "-n", "1", "e").stdout)

        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["name"], "firefox")

    def test_json_with_no_match_is_an_empty_rows_document(self) -> None:
        result = self.run_cli("--json", "nothing-like-this")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.stdout)["rows"], [])

    def test_json_has_no_text_table_or_control_codes(self) -> None:
        result = self.run_cli("--json")

        self.assertNotIn("\x1b", result.stdout)
        json.loads(result.stdout)  # the whole of stdout is one document

    # -- totals ---------------------------------------------------------

    def json_totals(self, *args):
        result = self.run_cli("--json", *args)
        self.assertEqual(result.exit_code, 0, result.output)
        return json.loads(result.stdout)["totals"]

    def test_totals_have_shown_all_and_outside(self) -> None:
        totals = self.json_totals()

        self.assertEqual(set(totals), {"shown", "all", "outside"})
        for key in ("shown", "all"):
            self.assertEqual(set(totals[key]), {"rows", "processes", "memory_bytes", "swap_bytes"})
        self.assertEqual(set(totals["outside"]), {"memory_bytes", "swap_bytes"})

    def test_outside_is_in_use_minus_every_process(self) -> None:
        for args in ((), ("--each",), ("--top", "1"), ("--each", "-n", "2")):
            with self.subTest(args=args):
                self.assertEqual(self.json_totals(*args)["outside"], SAMPLE_OUTSIDE)

    def test_all_plus_outside_equals_in_use_in_bytes(self) -> None:
        for args in ((), ("--each",), ("--top", "2")):
            with self.subTest(args=args):
                data = json.loads(self.run_cli("--json", *args).stdout)

                totals = data["totals"]
                self.assertEqual(
                    totals["all"]["memory_bytes"] + totals["outside"]["memory_bytes"],
                    data["memory"]["used_bytes"],
                )
                self.assertEqual(
                    totals["all"]["swap_bytes"] + totals["outside"]["swap_bytes"],
                    data["memory"]["swap_used_bytes"],
                )

    def test_outside_is_clamped_at_zero_per_field(self) -> None:
        # memory in use is below the processes' sum; swap in use is above it
        self.totals.return_value = dict(TOTALS, used_bytes=GIB, swap_used_bytes=GIB)

        outside = self.json_totals()["outside"]

        self.assertEqual(outside, {"memory_bytes": 0, "swap_bytes": GIB - 750 * KIB})

    def test_outside_is_null_with_a_pattern(self) -> None:
        for args in (("fire",), ("fire", "--each"), ("e", "--top", "1")):
            with self.subTest(args=args):
                totals = self.json_totals(*args)

                self.assertIn("outside", totals)
                self.assertIsNone(totals["outside"])

    def test_outside_is_null_when_a_pattern_matches_nothing(self) -> None:
        data = json.loads(self.run_cli("--json", "nothing-like-this").stdout)

        self.assertIsNone(data["totals"]["outside"])
        self.assertEqual(data["totals"]["all"]["rows"], 0)

    def test_group_totals_sum_every_row_and_every_process(self) -> None:
        totals = self.json_totals()
        memory = (1536 + 512 + 64 + 98) * MIB + MIB // 2
        swap = (600 + 50 + 30 + 70) * KIB

        expected = {"rows": 4, "processes": 7, "memory_bytes": memory, "swap_bytes": swap}
        self.assertEqual(totals["shown"], expected)
        self.assertEqual(totals["all"], expected)

    def test_group_totals_with_top_split_shown_from_all(self) -> None:
        totals = self.json_totals("--top", "2")

        self.assertEqual(
            totals["shown"],
            {"rows": 2, "processes": 4, "memory_bytes": 2048 * MIB, "swap_bytes": 650 * KIB},
        )
        self.assertEqual(totals["all"]["rows"], 4)
        self.assertEqual(totals["all"]["processes"], 7)
        self.assertEqual(totals["all"]["memory_bytes"], 2210 * MIB + MIB // 2)
        self.assertEqual(totals["all"]["swap_bytes"], 750 * KIB)

    def test_each_totals_count_rows_and_processes_alike(self) -> None:
        totals = self.json_totals("--each", "-n", "3")

        self.assertEqual(
            totals["shown"],
            {"rows": 3, "processes": 3, "memory_bytes": 1912 * MIB, "swap_bytes": 550 * KIB},
        )
        self.assertEqual(
            totals["all"],
            {"rows": 7, "processes": 7, "memory_bytes": 2210 * MIB + MIB // 2, "swap_bytes": 750 * KIB},
        )

    def test_all_follows_the_pattern_and_shown_follows_top(self) -> None:
        totals = self.json_totals("-n", "1", "e")  # firefox, editor, systemd match; tiny does not

        self.assertEqual(
            totals["shown"],
            {"rows": 1, "processes": 3, "memory_bytes": 1536 * MIB, "swap_bytes": 600 * KIB},
        )
        self.assertEqual(
            totals["all"],
            {"rows": 3, "processes": 6, "memory_bytes": 2146 * MIB + MIB // 2, "swap_bytes": 680 * KIB},
        )

    def test_totals_agree_with_the_rows(self) -> None:
        data = json.loads(self.run_cli("--json", "--top", "3").stdout)

        shown = data["totals"]["shown"]
        self.assertEqual(shown["rows"], len(data["rows"]))
        self.assertEqual(shown["processes"], sum(r["count"] for r in data["rows"]))
        self.assertEqual(shown["memory_bytes"], sum(r["memory_bytes"] for r in data["rows"]))
        self.assertEqual(shown["swap_bytes"], sum(r["swap_bytes"] for r in data["rows"]))

    def test_json_totals_are_unaffected_by_the_terminal_frame(self) -> None:
        result = self.run_cli("--json", charset="ascii")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("totals", json.loads(result.stdout))


# --------------------------------------------------------------------------
# Frame, directly on render_procs
# --------------------------------------------------------------------------

# Clean percentages so rounding cannot matter: RAM 25 %, Swap 75 %.
CLEAN_TOTALS = {
    "total_bytes": 32 * GIB,
    "used_bytes": 8 * GIB,
    "swap_total_bytes": 8 * GIB,
    "swap_used_bytes": 6 * GIB,
}

VARIANTS = [(each, ascii) for each in (False, True) for ascii in (False, True)]


def render_variant(each: bool, ascii: bool, top: int | None = None, totals=None, **kwargs) -> str:
    rows = sample_singles() if each else sample_groups()
    shown = rows if top is None else rows[:top]
    return render_procs(shown, rows, totals or CLEAN_TOTALS, each=each, ascii=ascii, **kwargs)


class RenderFrameTests(unittest.TestCase):
    def test_every_line_has_the_same_width_with_borders_in_every_variant(self) -> None:
        for each, ascii in VARIANTS:
            with self.subTest(each=each, ascii=ascii):
                text = frame_text(render_variant(each, ascii))

                parsed = assert_rectangular(self, text, ascii)
                self.assertEqual(parsed.hostname, "Memory")

    def test_dividers_are_the_table_then_total(self) -> None:
        for each, ascii in VARIANTS:
            with self.subTest(each=each, ascii=ascii):
                parsed = Parsed(self, frame_text(render_variant(each, ascii)), ascii)

                expected = "Processes (7 of 7)" if each else "Applications (4 of 4)"
                self.assertEqual(parsed.titles, [expected, "Total"])

    def test_n_of_m_follows_the_rows_shown_and_the_rows_matching(self) -> None:
        for each, expected in ((False, "Applications (2 of 4)"), (True, "Processes (3 of 7)")):
            with self.subTest(each=each):
                top = 3 if each else 2
                parsed = Parsed(self, frame_text(render_variant(each, False, top=top)), False)

                self.assertEqual(parsed.titles, [expected, "Total"])

    def test_content_lines_have_two_spaces_inside_both_borders(self) -> None:
        for each, ascii in VARIANTS:
            with self.subTest(each=each, ascii=ascii):
                v = CHARS[ascii]["v"]
                lines = frame_text(render_variant(each, ascii)).splitlines()
                content = [l for l in lines[1:-1] if not l.startswith(CHARS[ascii]["lt"])]

                self.assertGreater(len(content), 6)
                for line in content:
                    self.assertTrue(line.startswith(v + "  "), line)
                    self.assertTrue(line.endswith("  " + v), line)

    def test_wide_characters_do_not_push_the_right_border_out(self) -> None:
        procs = [make_proc(1, name="サーバー", exe="/opt/サーバー", user="管理者", command="/opt/サーバー --起動")]
        groups = importlib.import_module("jzr_tools.core.procs").group_processes(procs)
        for each, rows in ((False, groups), (True, procs)):
            with self.subTest(each=each):
                text = frame_text(render_procs(rows, rows, CLEAN_TOTALS, each=each))

                assert_rectangular(self, text, False)
                self.assertIn("サーバー", text)

    def test_text_ends_with_the_bottom_border(self) -> None:
        text = render_variant(False, False)

        self.assertRegex(text.rstrip("\n").splitlines()[-1], r"^╰─+╯$")
        self.assertRegex(render_variant(False, True).rstrip("\n").splitlines()[-1], r"^\+-+\+$")


class RenderMemoryTests(unittest.TestCase):
    def memory_lines(self, ascii=False, totals=None, each=False) -> list[str]:
        text = frame_text(render_variant(each, ascii, totals=totals))
        return Parsed(self, text, ascii).host_lines

    def test_ram_and_swap_rows_have_a_bar_the_percentage_and_used_over_total(self) -> None:
        ram, swap = self.memory_lines()

        self.assertRegex(ram, r"^RAM\s+[█░]{20}\s+25%\s+8\.0 / 32\.0 GiB$")
        self.assertRegex(swap, r"^Swap\s+[█░]{20}\s+75%\s+6\.0 / 8\.0 GiB$")

    def test_ascii_rows_use_bracketed_hash_bars(self) -> None:
        ram, swap = self.memory_lines(ascii=True)

        self.assertRegex(ram, r"^RAM\s+\[#{5}\.{15}\]\s+25%\s+8\.0 / 32\.0 GiB$")
        self.assertRegex(swap, r"^Swap\s+\[#{15}\.{5}\]\s+75%\s+6\.0 / 8\.0 GiB$")

    def test_bars_are_20_cells_filled_from_the_left_in_proportion(self) -> None:
        for ascii in (False, True):
            with self.subTest(ascii=ascii):
                ram, swap = self.memory_lines(ascii=ascii)

                (_, ram_cells), = find_bars(ram, ascii)
                (_, swap_cells), = find_bars(swap, ascii)
                self.assertEqual((len(ram_cells), bar_fill(ram_cells, ascii)), (20, 5))
                self.assertEqual((len(swap_cells), bar_fill(swap_cells, ascii)), (20, 15))

    def test_empty_and_full_bars(self) -> None:
        empty = dict(CLEAN_TOTALS, used_bytes=0, swap_used_bytes=0)
        full = dict(CLEAN_TOTALS, used_bytes=32 * GIB, swap_used_bytes=8 * GIB)
        for totals, fill, pct in ((empty, 0, "0%"), (full, 20, "100%")):
            with self.subTest(percent=pct):
                for line in self.memory_lines(totals=totals):
                    (_, cells), = find_bars(line)
                    self.assertEqual(bar_fill(cells), fill, line)
                    self.assertRegex(line, rf"\s{pct}\s")

    def test_the_two_bars_start_in_the_same_column(self) -> None:
        for ascii in (False, True):
            with self.subTest(ascii=ascii):
                ram, swap = self.memory_lines(ascii=ascii)

                self.assertEqual(find_bars(ram, ascii)[0][0], find_bars(swap, ascii)[0][0])

    def test_percentages_and_pairs_end_in_one_column(self) -> None:
        mixed = dict(CLEAN_TOTALS, used_bytes=32 * GIB, swap_used_bytes=2 * GIB)  # 100 % and 25 %
        ram, swap = self.memory_lines(totals=mixed)

        self.assertEqual(ram.index("%"), swap.index("%"))
        self.assertEqual(len(ram.rstrip()), len(swap.rstrip()))
        self.assertTrue(ram.endswith("32.0 / 32.0 GiB"), ram)
        self.assertTrue(swap.endswith("2.0 / 8.0 GiB"), swap)

    def test_swap_reads_none_without_swap(self) -> None:
        no_swap = dict(CLEAN_TOTALS, swap_total_bytes=0, swap_used_bytes=0)
        for ascii in (False, True):
            with self.subTest(ascii=ascii):
                ram, swap = self.memory_lines(ascii=ascii, totals=no_swap)

                self.assertRegex(swap, r"^Swap\s+none\b")
                self.assertNotIn("%", swap)
                self.assertNotIn("/", swap)
                self.assertIn("25%", ram)  # RAM is unaffected

    def test_memory_section_is_the_same_for_groups_and_each(self) -> None:
        self.assertEqual(self.memory_lines(each=False), self.memory_lines(each=True))


class RenderTableTests(unittest.TestCase):
    def parsed(self, each=False, ascii=False, top=None) -> Parsed:
        return Parsed(self, frame_text(render_variant(each, ascii, top=top)), ascii)

    def test_group_header_and_rows(self) -> None:
        for ascii in (False, True):
            with self.subTest(ascii=ascii):
                parsed = self.parsed(ascii=ascii)

                self.assertRegex(table_header(parsed), r"^Application\s+Procs\s+Memory\s+Swap\s+User$")
                rows = table_rows(parsed)
                self.assertRegex(rows[0], r"^firefox\s+3\s+1\.5 GiB\s+600\.0 KiB\s+alice$")
                self.assertRegex(rows[1], r"^editor\s+1\s+512\.0 MiB\s+50\.0 KiB\s+bob$")
                self.assertRegex(rows[2], r"^systemd\s+2\s+98\.5 MiB\s+30\.0 KiB\s+root \(system\)$")
                self.assertRegex(rows[3], r"^tiny\s+1\s+64\.0 MiB\s+70\.0 KiB\s+alice$")
                self.assertEqual(len(rows), 4)

    def test_each_header_and_rows(self) -> None:
        for ascii in (False, True):
            with self.subTest(ascii=ascii):
                parsed = self.parsed(each=True, ascii=ascii)

                self.assertRegex(table_header(parsed), r"^\s*PID\s+Name\s+Memory\s+Swap\s+User\s+Command$")
                rows = table_rows(parsed)
                self.assertEqual([int(r.split()[0]) for r in rows], [10, 20, 11, 12, 30, 1, 2])
                self.assertRegex(
                    rows[0],
                    r"^\s*10\s+firefox\s+1000\.0 MiB\s+300\.0 KiB\s+alice\s+/usr/lib/firefox/firefox --new-window$",
                )
                self.assertTrue(rows[5].endswith("/sbin/init (system)"), rows[5])

    def test_only_the_rows_given_are_listed(self) -> None:
        parsed = self.parsed(top=2)

        self.assertEqual([r.split()[0] for r in table_rows(parsed)], ["firefox", "editor"])

    def test_numbers_are_right_aligned_under_their_columns(self) -> None:
        for each in (False, True):
            with self.subTest(each=each):
                rows = table_rows(self.parsed(each=each))

                edges = [right_edges(r, each) for r in rows]
                for column in edges[0]:
                    self.assertEqual(len({e[column] for e in edges}), 1, (column, rows))

    def test_reason_is_the_last_thing_on_a_protected_line(self) -> None:
        procs = [
            make_proc(1, name="one", exe="/a/one", protected="this terminal"),
            make_proc(2, name="two", exe="/a/two", protected="another user", command="/a/two -x"),
            make_proc(3, name="free", exe="/a/free"),
        ]
        groups = importlib.import_module("jzr_tools.core.procs").group_processes(procs)
        for each, rows in ((False, groups), (True, procs)):
            with self.subTest(each=each):
                text = frame_text(render_procs(rows, rows, CLEAN_TOTALS, each=each))

                table = {r.split()[0 if not each else 1]: r for r in table_rows(Parsed(self, text, False))}
                self.assertTrue(table["one"].endswith("(this terminal)"), table["one"])
                self.assertTrue(table["two"].endswith("(another user)"), table["two"])
                self.assertNotIn("(", table["free"])


# What the command passes as ``outside`` for the faked snapshot and TOTALS:
# the machine's use minus the sum over every process.
SAMPLE_PROCESS_MEMORY = 2210 * MIB + MIB // 2
SAMPLE_PROCESS_SWAP = 750 * KIB
SAMPLE_OUTSIDE = {
    "memory_bytes": TOTALS["used_bytes"] - SAMPLE_PROCESS_MEMORY,
    "swap_bytes": TOTALS["swap_used_bytes"] - SAMPLE_PROCESS_SWAP,
}

# Independent of the processes and of CLEAN_TOTALS (8.0 / 6.0 GiB in use), so a
# renderer that computed one figure from another would show the wrong number.
OUTSIDE = {"memory_bytes": 5 * GIB + GIB // 2, "swap_bytes": 3 * GIB + GIB // 2}

ANY_SIZE = re.compile(r"\d+(?:\.\d)? (?:B|KiB|MiB|GiB|TiB)")


def size_edges(body: str) -> tuple[int, int]:
    """Display columns where the Memory and Swap figures of a row end."""
    sizes = [m.end() for m in ANY_SIZE.finditer(body)]
    assert len(sizes) >= 2, body
    return sizes[0], sizes[1]


class RenderTotalTests(unittest.TestCase):
    def total(self, rows, matching, each, ascii=False, outside=None) -> list[str]:
        text = frame_text(render_procs(rows, matching, CLEAN_TOTALS, each=each, ascii=ascii, outside=outside))
        return Parsed(self, text, ascii).section("Total")

    # -- Top N / All M ---------------------------------------------------

    def test_top_and_all_rows_when_top_left_rows_out(self) -> None:
        for ascii in (False, True):
            with self.subTest(ascii=ascii):
                groups = sample_groups()

                top, every = self.total(groups[:2], groups, each=False, ascii=ascii)

                self.assertRegex(top, r"^Top 2\s+4\s+2\.0 GiB\s+650\.0 KiB$")
                self.assertRegex(every, r"^All 4\s+7\s+2\.2 GiB\s+750\.0 KiB$")

    def test_only_the_all_row_when_nothing_was_left_out(self) -> None:
        groups = sample_groups()

        (row,) = self.total(groups, groups, each=False)

        self.assertRegex(row, r"^All 4\s+7\s+2\.2 GiB\s+750\.0 KiB$")

    def test_no_row_is_labelled_shown_or_total_any_more(self) -> None:
        groups, singles = sample_groups(), sample_singles()
        for each, rows in ((False, groups), (True, singles)):
            for shown in (rows, rows[:2]):
                with self.subTest(each=each, shown=len(shown)):
                    for row in self.total(shown, rows, each=each, outside=OUTSIDE):
                        self.assertNotRegex(row, r"^\s*(Shown|Total)\b")
                        self.assertNotRegex(row, r"^\s*All\s+\d+\s+\(")

    def test_group_sums_are_right_aligned_under_their_columns(self) -> None:
        groups = sample_groups()
        text = frame_text(render_procs(groups[:2], groups, CLEAN_TOTALS))
        parsed = Parsed(self, text, False)
        table = [right_edges(r, each=False) for r in table_rows(parsed)]

        for row in parsed.section("Total"):
            edges = right_edges(row, each=False)
            for column in ("count", "memory", "swap"):
                self.assertEqual(edges[column], table[0][column], (column, row))

    def test_each_counts_rows_without_a_parenthesised_process_count(self) -> None:
        singles = sample_singles()

        top, every = self.total(singles[:3], singles, each=True)

        self.assertRegex(top, r"^Top 3\s+1\.9 GiB\s+550\.0 KiB$")
        self.assertRegex(every, r"^All 7\s+2\.2 GiB\s+750\.0 KiB$")
        self.assertNotIn("(", top + every)

    def test_each_with_nothing_left_out_has_only_the_all_row(self) -> None:
        singles = sample_singles()

        (row,) = self.total(singles, singles, each=True)

        self.assertRegex(row, r"^All 7\s+2\.2 GiB\s+750\.0 KiB$")

    def test_each_has_nothing_under_the_pid_and_name_columns_but_the_label(self) -> None:
        singles = sample_singles()

        top, every = self.total(singles[:3], singles, each=True)

        self.assertEqual(ANY_SIZE.sub("", top).split(), ["Top", "3"])
        self.assertEqual(ANY_SIZE.sub("", every).split(), ["All", "7"])

    def test_each_sums_are_right_aligned_under_memory_and_swap(self) -> None:
        singles = sample_singles()
        text = frame_text(render_procs(singles[:3], singles, CLEAN_TOTALS, each=True))
        parsed = Parsed(self, text, False)
        table = right_edges(table_rows(parsed)[0], each=True)

        for row in parsed.section("Total"):
            edges = right_edges(row, each=True)
            self.assertEqual(edges, table, row)

    def test_all_covers_rows_that_are_not_shown(self) -> None:
        groups = sample_groups()

        top, every = self.total(groups[:1], groups, each=False)

        self.assertRegex(top, r"^Top 1\s+3\s+1\.5 GiB\s+600\.0 KiB$")
        self.assertRegex(every, r"^All 4\s+7\s+2\.2 GiB\s+750\.0 KiB$")

    def test_all_follows_the_matching_rows_it_is_given(self) -> None:
        groups = [g for g in sample_groups() if g.name != "tiny"]  # as if PATTERN dropped it

        top, every = self.total(groups[:1], groups, each=False)

        self.assertRegex(every, r"^All 3\s+6\s+2\.1 GiB\s+680\.0 KiB$")

    def test_protected_rows_are_part_of_the_sums(self) -> None:
        groups = [g for g in sample_groups() if g.name == "systemd"]

        (row,) = self.total(groups, groups, each=False)

        self.assertRegex(row, r"^All 1\s+2\s+98\.5 MiB\s+30\.0 KiB$")

    # -- Outside processes / In use -------------------------------------

    def test_outside_and_in_use_follow_all_in_that_order(self) -> None:
        groups = sample_groups()
        for shown in (groups, groups[:2]):
            with self.subTest(shown=len(shown)):
                rows = self.total(shown, groups, each=False, outside=OUTSIDE)

                labels = [re.match(r"Top \d+|All \d+|Outside processes|In use", r).group(0) for r in rows]
                expected = ["All 4"] if shown is groups else ["Top 2", "All 4"]
                self.assertEqual(labels, expected + ["Outside processes", "In use"])

    def test_outside_shows_the_given_figures_and_in_use_the_machines_totals(self) -> None:
        for ascii in (False, True):
            with self.subTest(ascii=ascii):
                groups = sample_groups()

                *_, outside, in_use = self.total(groups, groups, each=False, ascii=ascii, outside=OUTSIDE)

                self.assertRegex(outside, r"^Outside processes\s+5\.5 GiB\s+3\.5 GiB$")
                self.assertRegex(in_use, r"^In use\s+8\.0 GiB\s+6\.0 GiB$")  # CLEAN_TOTALS

    def test_in_use_is_the_totals_even_when_outside_does_not_add_up(self) -> None:
        groups = sample_groups()
        totals = dict(CLEAN_TOTALS, used_bytes=20 * GIB, swap_used_bytes=2 * GIB)

        text = frame_text(render_procs(groups, groups, totals, outside=OUTSIDE))
        *_, outside, in_use = Parsed(self, text, False).section("Total")

        self.assertRegex(in_use, r"^In use\s+20\.0 GiB\s+2\.0 GiB$")
        self.assertRegex(outside, r"^Outside processes\s+5\.5 GiB\s+3\.5 GiB$")

    def test_nothing_sits_under_procs_in_the_two_rows(self) -> None:
        groups = sample_groups()

        *_, outside, in_use = self.total(groups, groups, each=False, outside=OUTSIDE)

        for row in (outside, in_use):
            self.assertEqual(len(ANY_SIZE.findall(row)), 2, row)
            self.assertNotRegex(ANY_SIZE.sub("", row), r"\d", row)

    def test_the_figures_end_where_the_memory_and_swap_columns_end(self) -> None:
        for each in (False, True):
            for ascii in (False, True):
                with self.subTest(each=each, ascii=ascii):
                    rows = sample_singles() if each else sample_groups()
                    text = frame_text(render_procs(rows[:3], rows, CLEAN_TOTALS, each=each,
                                                   ascii=ascii, outside=OUTSIDE))
                    parsed = Parsed(self, text, ascii)
                    table = size_edges(table_rows(parsed)[0])

                    total = parsed.section("Total")
                    self.assertEqual(len(total), 4)
                    for row in total:
                        self.assertEqual(size_edges(row), table, row)

    def test_a_label_wider_than_the_first_column_widens_it_and_keeps_the_alignment(self) -> None:
        procs = [make_proc(7, name="a", exe="/bin/a", command="a", user="u")]
        groups = importlib.import_module("jzr_tools.core.procs").group_processes(procs)
        for each, rows in ((False, groups), (True, procs)):
            with self.subTest(each=each):
                text = frame_text(render_procs(rows, rows, CLEAN_TOTALS, each=each, outside=OUTSIDE))
                parsed = Parsed(self, text, False)

                assert_rectangular(self, text, False)
                table = size_edges(table_rows(parsed)[0])
                outside, in_use = parsed.section("Total")[-2:]
                self.assertRegex(outside, r"^Outside processes\s+5\.5 GiB\s+3\.5 GiB$")
                self.assertEqual(size_edges(outside), table)
                self.assertEqual(size_edges(in_use), table)
                # header and data rows moved with it
                header = table_header(parsed)
                self.assertGreater(header.index("Memory"), len("Outside processes") - 1)

    def test_a_row_label_wider_than_the_total_labels_keeps_the_alignment_too(self) -> None:
        long_name = "x" * 30
        procs = [make_proc(7, name=long_name, exe=f"/bin/{long_name}", command="x")]
        groups = importlib.import_module("jzr_tools.core.procs").group_processes(procs)
        text = frame_text(render_procs(groups, groups, CLEAN_TOTALS, outside=OUTSIDE))
        parsed = Parsed(self, text, False)

        table = size_edges(table_rows(parsed)[0])
        for row in parsed.section("Total"):
            self.assertEqual(size_edges(row), table, row)

    def test_zero_outside_is_shown_as_a_zero_row_not_dropped(self) -> None:
        groups = sample_groups()

        *_, outside, in_use = self.total(groups, groups, each=False,
                                         outside={"memory_bytes": 0, "swap_bytes": 0})

        self.assertRegex(outside, r"^Outside processes\s+0(?:\.0)? B\s+0(?:\.0)? B$")
        self.assertTrue(in_use.startswith("In use"))

    def test_without_outside_neither_row_is_drawn(self) -> None:
        groups = sample_groups()
        for kwargs in ({}, {"outside": None}):
            with self.subTest(kwargs=kwargs):
                text = frame_text(render_procs(groups, groups, CLEAN_TOTALS, **kwargs))

                self.assertNotIn("Outside processes", text)
                self.assertNotIn("In use", text)
                self.assertEqual(len(Parsed(self, text, False).section("Total")), 1)

    def test_the_frame_stays_rectangular_in_every_variant(self) -> None:
        for each, ascii in VARIANTS:
            for outside in (None, OUTSIDE):
                for top in (None, 2):
                    with self.subTest(each=each, ascii=ascii, outside=bool(outside), top=top):
                        text = frame_text(render_variant(each, ascii, top=top, outside=outside))

                        parsed = assert_rectangular(self, text, ascii)
                        self.assertEqual(parsed.hostname, "Memory")
                        self.assertEqual(
                            parsed.titles,
                            ["Processes (%d of 7)" % (top or 7) if each
                             else "Applications (%d of 4)" % (top or 4), "Total"],
                        )

    def test_the_rows_are_cut_like_any_other_when_the_width_is_small(self) -> None:
        for each, ascii in VARIANTS:
            with self.subTest(each=each, ascii=ascii):
                rows = sample_singles() if each else sample_groups()
                natural = frame_text(render_procs(rows, rows, CLEAN_TOTALS, each=each, ascii=ascii, outside=OUTSIDE))
                width = display_width(natural.splitlines()[0]) - 10

                text = frame_text(render_procs(rows, rows, CLEAN_TOTALS, each=each, ascii=ascii,
                                               outside=OUTSIDE, width=width))

                parsed = assert_rectangular(self, text, ascii)
                self.assertEqual(display_width(parsed.lines[0]), width)
                self.assertEqual(len(parsed.section("Total")), 3)

    def test_ascii_and_unicode_show_the_same_total_rows(self) -> None:
        for each in (False, True):
            with self.subTest(each=each):
                uni = Parsed(self, frame_text(render_variant(each, False, top=3, outside=OUTSIDE)), False)
                asc = Parsed(self, frame_text(render_variant(each, True, top=3, outside=OUTSIDE)), True)

                self.assertEqual(uni.section("Total"), asc.section("Total"))
                self.assertEqual(len(uni.section("Total")), 4)

    def test_colour_only_adds_escape_codes_with_the_new_rows(self) -> None:
        coloured = render_variant(False, False, outside=OUTSIDE, color=True)
        plain = render_variant(False, False, outside=OUTSIDE, color=False)

        self.assertIn("\x1b", coloured)
        self.assertEqual(strip_ansi(coloured), plain)


class RenderAsciiTests(unittest.TestCase):
    def test_ascii_has_no_drawing_characters_and_only_ascii_text(self) -> None:
        for each in (False, True):
            with self.subTest(each=each):
                text = render_variant(each, True)

                for char in DRAWING + "…":
                    self.assertNotIn(char, text)
                text.encode("ascii")
                self.assertTrue(text.startswith("+-"))
                self.assertRegex(text, r"\[#+\.+\]")

    def test_ascii_has_the_same_facts_as_unicode(self) -> None:
        for each in (False, True):
            with self.subTest(each=each):
                uni = Parsed(self, frame_text(render_variant(each, False)), False)
                asc = Parsed(self, frame_text(render_variant(each, True)), True)

                self.assertEqual(uni.titles, asc.titles)
                self.assertEqual(table_rows(uni), table_rows(asc))
                self.assertEqual(uni.section("Total"), asc.section("Total"))


class RenderWidthTests(unittest.TestCase):
    LONG = "/usr/bin/app " + "--option-value-" * 12

    def rows_with_long_command(self, each: bool):
        procs = sample_processes() + [make_proc(40, name="longcmd", exe="/usr/bin/longcmd",
                                                memory_bytes=700 * MIB, command=self.LONG)]
        if each:
            return sorted(procs, key=lambda p: -p.memory_bytes)
        return importlib.import_module("jzr_tools.core.procs").group_processes(procs)

    def natural(self, each, ascii) -> str:
        rows = self.rows_with_long_command(each)
        return frame_text(render_procs(rows[:3], rows, CLEAN_TOTALS, each=each, ascii=ascii))

    def cut(self, each, ascii, width, color=False) -> str:
        rows = self.rows_with_long_command(each)
        return render_procs(rows[:3], rows, CLEAN_TOTALS, each=each, ascii=ascii, width=width, color=color)

    def test_no_line_is_wider_than_the_width_and_the_frame_is_kept(self) -> None:
        for each, ascii in VARIANTS:
            nat = self.natural(each, ascii)
            natural_width = display_width(nat.splitlines()[0])
            for width in (natural_width - 1, natural_width - 15, 40):
                with self.subTest(each=each, ascii=ascii, width=width):
                    text = frame_text(self.cut(each, ascii, width))

                    parsed = assert_rectangular(self, text, ascii)
                    self.assertEqual(display_width(parsed.lines[0]), width)
                    self.assertEqual(parsed.hostname, "Memory")
                    self.assertEqual(parsed.titles, Parsed(self, nat, ascii).titles)

    def test_cut_lines_end_in_the_marker_before_the_right_border(self) -> None:
        for each, ascii in VARIANTS:
            marker = CHARS[ascii]["cut"]
            nat = Parsed(self, self.natural(each, ascii), ascii)
            width = display_width(nat.lines[0]) - 10
            with self.subTest(each=each, ascii=ascii, width=width):
                cut = Parsed(self, frame_text(self.cut(each, ascii, width)), ascii)
                area = width - 6

                self.assertEqual(len(nat.content), len(cut.content), "lines are cut, never wrapped")
                cut_lines = 0
                for before, after in zip(nat.content, cut.content):
                    if display_width(before) <= area:
                        self.assertEqual(after, before)
                    else:
                        cut_lines += 1
                        self.assertTrue(after.endswith(marker), after)
                        self.assertLessEqual(display_width(after), area)
                        self.assertTrue(before.startswith(after[:-1].rstrip()), (before, after))
                self.assertGreater(cut_lines, 0)

    def test_width_at_or_above_natural_changes_nothing(self) -> None:
        for each, ascii in VARIANTS:
            nat = self.natural(each, ascii)
            natural_width = display_width(nat.splitlines()[0])
            for width in (natural_width, natural_width + 1, 1000):
                with self.subTest(each=each, ascii=ascii, width=width):
                    self.assertEqual(frame_text(self.cut(each, ascii, width)), nat)

    def test_no_width_cuts_nothing(self) -> None:
        for each, ascii in VARIANTS:
            with self.subTest(each=each, ascii=ascii):
                text = self.natural(each, ascii)

                self.assertIn(self.LONG if each else "longcmd", text)
                self.assertNotIn(CHARS[ascii]["cut"], text)

    def test_colour_only_adds_escape_codes_also_when_cut(self) -> None:
        for each, ascii in VARIANTS:
            nat_width = display_width(self.natural(each, ascii).splitlines()[0])
            for width in (None, nat_width - 12):
                with self.subTest(each=each, ascii=ascii, width=width):
                    coloured = self.cut(each, ascii, width, color=True)
                    plain = self.cut(each, ascii, width, color=False)

                    self.assertIn("\x1b", coloured)
                    self.assertNotIn("\x1b", plain)
                    self.assertEqual(strip_ansi(coloured), plain)


# --------------------------------------------------------------------------
# Frame, through the command
# --------------------------------------------------------------------------


class CommandFrameTests(FakedCommand):
    def test_piped_output_is_the_whole_unicode_frame(self) -> None:
        for args in ((), ("--each",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)

                self.assertNotIn("\x1b", result.output)
                parsed = self.parsed(result)
                self.assertEqual(parsed.hostname, "Memory")
                self.assertEqual(parsed.lines[0][0], "╭")
                self.assertNotIn("…", result.output)

    def test_output_equals_render_procs_for_the_same_data(self) -> None:
        result = self.run_cli("--top", "2")

        groups = sample_groups()
        expected = frame_text(render_procs(groups[:2], groups, TOTALS, outside=SAMPLE_OUTSIDE))
        self.assertEqual(result.stdout.rstrip("\n") + "\n", expected)

    def test_titles_follow_top_pattern_and_each(self) -> None:
        cases = [
            ((), ["Applications (4 of 4)", "Total"]),
            (("--top", "2"), ["Applications (2 of 4)", "Total"]),
            (("e", "--top", "1"), ["Applications (1 of 3)", "Total"]),
            (("--each",), ["Processes (7 of 7)", "Total"]),
            (("--each", "-n", "3"), ["Processes (3 of 7)", "Total"]),
            (("--each", "contentproc"), ["Processes (2 of 2)", "Total"]),
        ]
        for args, titles in cases:
            with self.subTest(args=args):
                self.assertEqual(self.parsed(self.run_cli(*args)).titles, titles)

    def test_totals_with_top_show_top_and_all(self) -> None:
        total = self.parsed(self.run_cli("--top", "2")).section("Total")

        self.assertRegex(total[0], r"^Top 2\s+4\s+2\.0 GiB\s+650\.0 KiB$")
        self.assertRegex(total[1], r"^All 4\s+7\s+2\.2 GiB\s+750\.0 KiB$")

    def test_totals_without_top_left_out_start_with_the_all_row_only(self) -> None:
        total = self.parsed(self.run_cli()).section("Total")

        self.assertRegex(total[0], r"^All 4\s+7\s+2\.2 GiB\s+750\.0 KiB$")
        self.assertEqual(len(total), 3)  # All, Outside processes, In use
        self.assertNotIn("Total", "".join(total))
        self.assertNotIn("Shown", "".join(total))

    def test_all_respects_the_pattern(self) -> None:
        total = self.parsed(self.run_cli("e", "--top", "1")).section("Total")

        self.assertRegex(total[0], r"^Top 1\s+3\s+1\.5 GiB\s+600\.0 KiB$")
        self.assertRegex(total[1], r"^All 3\s+6\s+2\.1 GiB\s+680\.0 KiB$")  # tiny does not match

    def test_pattern_that_cuts_nothing_gives_the_all_row_alone(self) -> None:
        (row,) = self.parsed(self.run_cli("contentproc")).section("Total")

        self.assertRegex(row, r"^All 1\s+2\s+536\.0 MiB\s+300\.0 KiB$")

    def test_each_totals_count_rows_not_processes_in_parentheses(self) -> None:
        total = self.parsed(self.run_cli("--each", "-n", "3")).section("Total")

        self.assertRegex(total[0], r"^Top 3\s+1\.9 GiB\s+550\.0 KiB$")
        self.assertRegex(total[1], r"^All 7\s+2\.2 GiB\s+750\.0 KiB$")
        self.assertEqual(len(total), 4)

    # -- Outside processes / In use -------------------------------------

    def test_without_a_pattern_outside_and_in_use_close_the_totals(self) -> None:
        for args in ((), ("--each",), ("--top", "2"), ("--each", "-n", "3")):
            with self.subTest(args=args):
                outside, in_use = self.parsed(self.run_cli(*args)).section("Total")[-2:]

                # 12 GiB in use - 2210.5 MiB in processes; 1.5 GiB swap - 750 KiB
                self.assertRegex(outside, r"^Outside processes\s+9\.8 GiB\s+1\.5 GiB$")
                self.assertRegex(in_use, r"^In use\s+12\.0 GiB\s+1\.5 GiB$")

    def test_outside_counts_every_process_not_just_the_shown_or_matching_ones(self) -> None:
        # --top 1 shows 1.5 GiB of firefox only, yet outside stays 12 GiB - all processes.
        shown_only = self.parsed(self.run_cli("--top", "1")).section("Total")[-2]
        everything = self.parsed(self.run_cli()).section("Total")[-2]

        self.assertEqual(shown_only.split(), everything.split())

    def test_outside_includes_protected_processes_in_the_sum(self) -> None:
        self.snapshot.return_value = [make_proc(1, protected="system", memory_bytes=2 * GIB, swap_bytes=0)]
        self.totals.return_value = dict(TOTALS, used_bytes=5 * GIB, swap_used_bytes=GIB)

        outside = self.parsed(self.run_cli()).section("Total")[-2]

        self.assertRegex(outside, r"^Outside processes\s+3\.0 GiB\s+1\.0 GiB$")

    def test_the_new_rows_have_nothing_under_procs_and_line_up_with_the_columns(self) -> None:
        for args in ((), ("--each",)):
            with self.subTest(args=args):
                parsed = self.parsed(self.run_cli(*args))
                table = size_edges(table_rows(parsed)[0])

                for row in parsed.section("Total")[-2:]:
                    self.assertEqual(size_edges(row), table, row)
                    self.assertNotRegex(ANY_SIZE.sub("", row), r"\d")

    def test_with_a_pattern_neither_row_is_drawn(self) -> None:
        for args in (("fire",), ("fire", "--each"), ("e", "--top", "1"), ("fire", "--top", "1")):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                total = self.parsed(result).section("Total")

                self.assertNotIn("Outside processes", result.output)
                self.assertNotIn("In use", result.output)
                self.assertTrue(all(r.startswith(("Top", "All")) for r in total), total)

    def test_outside_is_clamped_at_zero_when_processes_sum_to_more_than_in_use(self) -> None:
        self.totals.return_value = dict(TOTALS, used_bytes=GIB, swap_used_bytes=100 * KIB)  # < 2.2 GiB, < 750 KiB

        outside, in_use = self.parsed(self.run_cli()).section("Total")[-2:]

        self.assertRegex(outside, r"^Outside processes\s+0(?:\.0)? B\s+0(?:\.0)? B$")
        self.assertRegex(in_use, r"^In use\s+1\.0 GiB\s+100\.0 KiB$")

    def test_a_machine_without_swap_still_gets_both_rows(self) -> None:
        self.totals.return_value = dict(TOTALS, swap_total_bytes=0, swap_used_bytes=0)
        self.snapshot.return_value = [make_proc(5, memory_bytes=GIB, swap_bytes=0)]

        outside, in_use = self.parsed(self.run_cli()).section("Total")[-2:]

        self.assertRegex(outside, r"^Outside processes\s+11\.0 GiB\s+0(?:\.0)? B$")
        self.assertRegex(in_use, r"^In use\s+12\.0 GiB\s+0(?:\.0)? B$")

    def test_the_frame_is_rectangular_in_every_command_variant(self) -> None:
        cases = [(), ("--each",), ("--top", "1"), ("--each", "-n", "2"), ("fire",), ("fire", "--each")]
        for charset in (None, "ascii"):
            for args in cases:
                with self.subTest(args=args, charset=charset):
                    result = self.run_cli(*args, charset=charset)

                    self.assertEqual(result.exit_code, 0, result.output)
                    assert_rectangular(self, result.stdout, ascii=charset is not None)

    def test_memory_section_comes_from_the_system_totals(self) -> None:
        ram, swap = self.parsed(self.run_cli()).host_lines

        self.assertRegex(ram, r"^RAM\s+[█░]{20}\s+\d+%\s+12\.0 / 32\.0 GiB$")
        self.assertRegex(swap, r"^Swap\s+[█░]{20}\s+\d+%\s+1\.5 / 8\.0 GiB$")

    def test_a_machine_without_swap_says_none(self) -> None:
        self.totals.return_value = dict(TOTALS, swap_total_bytes=0, swap_used_bytes=0)

        _, swap = self.parsed(self.run_cli()).host_lines

        self.assertRegex(swap, r"^Swap\s+none\b")

    def test_encoding_without_the_drawing_characters_gets_the_ascii_frame(self) -> None:
        for charset in ("cp1252", "ascii"):
            for args in ((), ("--each",)):
                with self.subTest(charset=charset, args=args):
                    result = self.run_cli(*args, charset=charset)

                    self.assertIsNone(result.exception, repr(result.exception))
                    self.assertEqual(result.exit_code, 0, result.output)
                    assert_rectangular(self, result.stdout, ascii=True)
                    self.assertTrue(result.stdout.startswith("+-"))
                    for char in DRAWING:
                        self.assertNotIn(char, result.stdout)
                    self.assertRegex(result.stdout, r"\[#+\.+\]")
                    self.assertEqual(
                        result.stdout.rstrip("\n") + "\n",
                        frame_text(render_procs(
                            *((sample_singles(),) * 2 if args else (sample_groups(),) * 2),
                            TOTALS, each=bool(args), ascii=True, outside=SAMPLE_OUTSIDE,
                        )),
                    )

    def test_a_name_the_encoding_cannot_write_does_not_crash(self) -> None:
        self.snapshot.return_value = [
            make_proc(1, name="サーバー", exe="/opt/サーバー", user="管理者", command="/opt/サーバー --起動")
        ]
        for charset in ("cp1252", "ascii"):
            for args in ((), ("--each",)):
                with self.subTest(charset=charset, args=args):
                    result = self.run_cli(*args, charset=charset)

                    self.assertIsNone(result.exception, repr(result.exception))
                    self.assertEqual(result.exit_code, 0, result.output)
                    self.assertTrue(result.stdout.startswith("+-"))

    def test_no_match_is_a_bare_line_without_a_frame(self) -> None:
        for args in (("nothing-like-this",), ("--each", "nothing-like-this")):
            for charset in (None, "ascii"):
                with self.subTest(args=args, charset=charset):
                    result = self.run_cli(*args, charset=charset)

                    self.assertEqual(result.exit_code, 0, result.output)
                    self.assertEqual(result.stdout.strip(), "No matching processes.")
                    for char in DRAWING + "+|":
                        self.assertNotIn(char, result.stdout)


class UsageErrorTests(FakedCommand):
    def assert_usage_error(self, result, *needles):
        self.assertEqual(result.exit_code, 2, result.output)
        self.snapshot.assert_not_called()  # usage errors come before any scan
        self.choose.assert_not_called()
        self.confirm.assert_not_called()
        self.end.assert_not_called()
        for needle in needles:
            self.assertIn(needle, result.output)

    def test_kill_with_json(self) -> None:
        self.assert_usage_error(self.run_cli("--kill", "--json", interactive=True), "--json")

    def test_force_without_kill(self) -> None:
        self.assert_usage_error(self.run_cli("--force"), "--force")

    def test_force_with_json_but_no_kill(self) -> None:
        self.assert_usage_error(self.run_cli("--force", "--json"), "--force")

    def test_kill_without_a_terminal(self) -> None:
        # _is_interactive is not patched: CliRunner streams are not terminals.
        result = runner.invoke(app, ["host", "procs", "--kill"])

        self.assert_usage_error(result, "terminal")

    def test_top_below_one(self) -> None:
        for value in ("0", "-3"):
            with self.subTest(top=value):
                self.snapshot.reset_mock()
                self.assert_usage_error(self.run_cli("--top", value))

    def test_top_not_a_number(self) -> None:
        self.assert_usage_error(self.run_cli("--top", "many"))

    def test_unknown_option(self) -> None:
        self.assert_usage_error(self.run_cli("--bogus"))

    def test_two_patterns(self) -> None:
        self.assert_usage_error(self.run_cli("one", "two"))


# --------------------------------------------------------------------------
# --kill flow
# --------------------------------------------------------------------------


class KillFlowTests(FakedCommand):
    interactive = True

    def pids(self, calls):
        return sorted(p.pid for p in calls)

    # -- the list -------------------------------------------------------

    def test_list_offers_the_rows_shown_with_nothing_preticked(self) -> None:
        self.tick()  # nothing

        result = self.run_cli("--kill")

        self.assertEqual(result.exit_code, 0, result.output)
        self.choose.assert_called_once()
        self.assertEqual(len(self.entries), 4)  # four groups
        self.assertFalse(any(e.checked for e in self.entries))
        labels = " ".join(e.label for e in self.entries)
        for name in ("firefox", "editor", "tiny", "systemd"):
            self.assertIn(name, labels)

    def test_list_labels_carry_the_name_and_memory_size(self) -> None:
        self.tick()
        self.run_cli("--kill")

        firefox = next(e for e in self.entries if "firefox" in e.label)
        self.assertIn("1.5 GiB", firefox.label)
        editor = next(e for e in self.entries if "editor" in e.label)
        self.assertIn("512.0 MiB", editor.label)

    def test_list_follows_top_and_pattern_and_each(self) -> None:
        self.tick()
        self.run_cli("--kill", "--top", "2")
        self.assertEqual(len(self.entries), 2)

        self.run_cli("--kill", "--each")
        self.assertEqual(len(self.entries), 7)

        self.run_cli("--kill", "--each", "firefox")
        self.assertEqual(len(self.entries), 3)

    def test_fully_protected_rows_are_disabled_with_the_reason(self) -> None:
        self.tick()
        self.run_cli("--kill")

        by_name = {n: next(e for e in self.entries if n in e.label) for n in ("firefox", "systemd")}
        self.assertEqual(by_name["systemd"].disabled, "system")
        self.assertFalse(by_name["firefox"].disabled)

    def test_protected_single_processes_are_disabled_with_the_reason(self) -> None:
        self.tick()
        self.run_cli("--kill", "--each")

        self.assertEqual([e.disabled for e in self.entries if e.disabled], ["system", "system"])
        self.assertEqual(len(self.entries), 7)

    def test_group_with_some_protected_processes_stays_enabled_and_says_so(self) -> None:
        self.snapshot.return_value = [
            make_proc(1, protected="this terminal"),
            make_proc(2),
            make_proc(3),
        ]
        self.tick()
        self.run_cli("--kill")

        (entry,) = self.entries
        self.assertFalse(entry.disabled)
        self.assertIn("kept", entry.label.lower() + " " + entry.description.lower())
        self.assertIn("1", entry.label + entry.description)

    # -- nothing ticked / cancelled / declined ----------------------------

    def test_nothing_ticked_touches_nothing_and_exits_zero(self) -> None:
        self.tick()

        result = self.run_cli("--kill")

        self.assertEqual(result.exit_code, 0, result.output)
        self.confirm.assert_not_called()
        self.end.assert_not_called()
        self.kill.assert_not_called()

    def test_cancelled_list_touches_nothing_and_exits_one(self) -> None:
        self.choose.return_value = None

        result = self.run_cli("--kill")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("Cancelled", result.output)
        self.confirm.assert_not_called()
        self.end.assert_not_called()
        self.kill.assert_not_called()

    def test_declined_confirmation_touches_nothing_and_exits_zero(self) -> None:
        self.tick("firefox")
        self.confirm.return_value = False

        result = self.run_cli("--kill")

        self.assertEqual(result.exit_code, 0, result.output)
        self.confirm.assert_called_once()
        self.end.assert_not_called()
        self.kill.assert_not_called()

    def test_no_matching_processes_never_opens_the_list(self) -> None:
        result = self.run_cli("--kill", "nothing-like-this")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("No matching processes.", result.output)
        self.choose.assert_not_called()
        self.end.assert_not_called()

    # -- confirmation text ----------------------------------------------

    def test_confirmation_names_the_target_counts_processes_and_defaults_to_no(self) -> None:
        self.tick("firefox")

        self.run_cli("--kill")

        args, kwargs = self.confirm.call_args
        message = args[0] if args else kwargs["message"]
        self.assertIn("firefox", message)
        self.assertRegex(message, r"\b3 processes\b")
        self.assertIn("unsaved", message.lower())
        self.assertFalse(kwargs.get("default", False))

    def test_confirmation_counts_only_the_processes_that_will_be_ended(self) -> None:
        self.snapshot.return_value = [
            make_proc(1, protected="this terminal"),
            make_proc(2),
            make_proc(3),
        ]
        self.tick("app")

        self.run_cli("--kill")

        args, kwargs = self.confirm.call_args
        message = args[0] if args else kwargs["message"]
        self.assertRegex(message, r"\b2 processes\b")

    def test_confirmation_uses_the_singular_for_one_process(self) -> None:
        self.tick("editor")

        self.run_cli("--kill")

        args, kwargs = self.confirm.call_args
        message = args[0] if args else kwargs["message"]
        self.assertRegex(message, r"\b1 process\b(?!es)")
        self.assertNotIn("1 processes", message)

    # -- what is signalled ----------------------------------------------

    def test_exactly_the_processes_of_the_ticked_rows_are_ended(self) -> None:
        self.tick("firefox", "tiny")

        result = self.run_cli("--kill")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(self.end_calls), 1)
        self.assertEqual(self.pids(self.end_calls[0]), [10, 11, 12, 30])
        self.kill.assert_not_called()

    def test_each_mode_ends_only_the_ticked_single_processes(self) -> None:
        prompts = importlib.import_module("jzr_tools.prompts")
        self.choose.side_effect = lambda message, entries, **kw: [
            e.value for e in entries if isinstance(e, prompts.Choice) and "firefox" in e.label
        ][1:2]  # the second firefox row only

        self.run_cli("--kill", "--each")

        self.assertEqual(self.pids(self.end_calls[0]), [11])

    def test_protected_processes_inside_a_ticked_group_are_not_ended(self) -> None:
        self.snapshot.return_value = [
            make_proc(1, protected="this terminal"),
            make_proc(2),
            make_proc(3),
        ]
        self.tick("app")

        self.run_cli("--kill")

        self.assertEqual(self.pids(self.end_calls[0]), [2, 3])

    def test_a_protected_row_is_never_signalled_even_if_the_list_returns_it(self) -> None:
        prompts = importlib.import_module("jzr_tools.prompts")
        self.choose.side_effect = lambda message, entries, **kw: [
            e.value for e in entries if isinstance(e, prompts.Choice)
        ]  # everything, including the disabled rows

        self.run_cli("--kill")

        sent = [p.pid for call in self.end_calls + self.kill_calls for p in call]
        self.assertFalse({1, 2} & set(sent), sent)
        self.assertEqual(sorted(sent), [10, 11, 12, 20, 30])

    def test_ticking_only_protected_rows_signals_nothing(self) -> None:
        self.tick("systemd")

        self.run_cli("--kill")

        sent = [p.pid for call in self.end_calls + self.kill_calls for p in call]
        self.assertEqual(sent, [])

    # -- report ---------------------------------------------------------

    def test_report_says_how_many_ended_and_how_much_memory_they_held(self) -> None:
        self.tick("firefox")

        result = self.run_cli("--kill")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(result.output, r"\b3 processes\b.*\bended\b|\bended\b.*\b3 processes\b")
        self.assertIn("1.5 GiB", result.output.split("ended")[-1] + result.output)

    def test_report_uses_the_singular(self) -> None:
        self.tick("editor")

        result = self.run_cli("--kill")

        self.assertRegex(result.output, r"\b1 process\b(?!es)")
        self.assertIn("512.0 MiB", result.output)

    def test_failures_are_listed_with_their_reason_and_exit_is_one(self) -> None:
        self.tick("firefox")
        tabs = [p for p in sample_processes() if p.pid in (10, 11, 12)]
        self.end_return = end_result(
            ended=[tabs[1], tabs[2]], failed=[(tabs[0], "permission denied")]
        )

        result = self.run_cli("--kill")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("permission denied", result.output)
        self.assertIn("10", result.output)
        self.assertRegex(result.output, r"\b2 processes\b")
        self.kill.assert_not_called()  # failed ones are not survivors

    # -- survivors --------------------------------------------------------

    def survivors_setup(self):
        self.tick("firefox")
        tabs = [p for p in sample_processes() if p.pid in (10, 11, 12)]
        self.end_return = end_result(ended=[tabs[2]], survivors=[tabs[0], tabs[1]])
        return tabs

    def test_survivors_without_force_ask_a_second_confirmation_defaulting_to_no(self) -> None:
        self.survivors_setup()

        self.run_cli("--kill")

        self.assertEqual(self.confirm.call_count, 2)
        args, kwargs = self.confirm.call_args
        message = args[0] if args else kwargs["message"]
        self.assertRegex(message, r"\b2\b")
        self.assertFalse(kwargs.get("default", False))

    def test_survivors_are_force_killed_after_a_yes(self) -> None:
        self.survivors_setup()
        self.confirm.side_effect = [True, True]
        self.kill_return = end_result(ended=[p for p in sample_processes() if p.pid in (10, 11)])

        result = self.run_cli("--kill")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(self.kill_calls), 1)
        self.assertEqual(self.pids(self.kill_calls[0]), [10, 11])
        self.assertRegex(result.output, r"\b3 processes\b")  # 1 politely + 2 forced

    def test_survivors_are_left_running_after_a_no_and_exit_is_one(self) -> None:
        self.survivors_setup()
        self.confirm.side_effect = [True, False]

        result = self.run_cli("--kill")

        self.assertEqual(result.exit_code, 1, result.output)
        self.kill.assert_not_called()
        self.assertRegex(result.output, r"\b2\b")  # how many remain

    def test_force_kills_survivors_without_asking(self) -> None:
        self.survivors_setup()
        self.kill_return = end_result(ended=[p for p in sample_processes() if p.pid in (10, 11)])

        result = self.run_cli("--kill", "--force")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.confirm.call_count, 1)  # only the first confirmation
        self.assertEqual(self.pids(self.kill_calls[0]), [10, 11])

    def test_force_with_nothing_surviving_never_force_kills(self) -> None:
        self.tick("firefox")

        result = self.run_cli("--kill", "--force")

        self.assertEqual(result.exit_code, 0, result.output)
        self.kill.assert_not_called()

    def test_force_does_not_skip_the_first_confirmation(self) -> None:
        self.tick("firefox")
        self.confirm.return_value = False

        self.run_cli("--kill", "--force")

        self.confirm.assert_called_once()
        self.end.assert_not_called()
        self.kill.assert_not_called()

    def test_survivor_of_the_force_kill_exits_one(self) -> None:
        tabs = self.survivors_setup()
        self.kill_return = end_result(survivors=[tabs[0]], ended=[tabs[1]])

        result = self.run_cli("--kill", "--force")

        self.assertEqual(result.exit_code, 1, result.output)

    def test_failure_in_the_force_kill_is_reported_and_exits_one(self) -> None:
        tabs = self.survivors_setup()
        self.kill_return = end_result(ended=[tabs[1]], failed=[(tabs[0], "permission denied")])

        result = self.run_cli("--kill", "--force")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("permission denied", result.output)


# --------------------------------------------------------------------------
# Laziness
# --------------------------------------------------------------------------


class LazyImportTests(unittest.TestCase):
    def test_table_and_json_views_do_not_import_questionary(self) -> None:
        code = textwrap.dedent(
            """
            import sys
            from click.testing import CliRunner
            from jzr_tools.cli import app
            for args in (["host", "procs"], ["host", "procs", "--each", "-n", "3"],
                         ["host", "procs", "--json"]):
                result = CliRunner().invoke(app, args)
                assert result.exit_code == 0, (args, result.output)
            print("questionary" in sys.modules)
            """
        )
        env = {**os.environ, "PYTHONPATH": str(SRC_PATH)}
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
        )

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip().splitlines()[-1], "False")


# --------------------------------------------------------------------------
# Real terminal
# --------------------------------------------------------------------------

CODE = "import sys; from jzr_tools.cli import app; app(sys.argv[1:], prog_name='jzr')"


@unittest.skipIf(pty is None, "needs a POSIX pseudo-terminal")
class RealTerminalTests(unittest.TestCase):
    def spawn(self, args, size=None):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        env["TERM"] = "xterm"
        env.pop("NO_COLOR", None)
        master, slave = pty.openpty()
        if size:  # (rows, columns)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", size[0], size[1], 0, 0))
        proc = subprocess.Popen(
            [sys.executable, "-c", CODE, *args],
            cwd=PROJECT_ROOT, env=env, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
        )
        os.close(slave)
        self.addCleanup(self._reap, proc, master)
        return proc, master

    @staticmethod
    def _reap(proc, master) -> None:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        try:
            os.close(master)
        except OSError:
            pass

    def read_until(self, master, proc, stop, timeout=45.0, seen=""):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode("utf-8", "replace")
                if "\x1b[6n" in text:  # prompt_toolkit asks where the cursor is
                    os.write(master, b"\x1b[1;1R")
                seen += text
                if stop(strip_ansi(seen)):
                    break
            elif proc.poll() is not None:
                break
        return seen

    def settle(self, master, proc, quiet=2.0, timeout=45.0):
        seen, last = "", None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and proc.poll() is None:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                text = data.decode("utf-8", "replace")
                if "\x1b[6n" in text:
                    os.write(master, b"\x1b[1;1R")
                seen += text
                last = time.monotonic()
            elif last is not None and time.monotonic() - last >= quiet:
                break
        return seen

    def test_a_terminal_cuts_the_frame_to_its_width_and_keeps_the_borders(self) -> None:
        proc, master = self.spawn(["host", "procs", "--each", "-n", "6"], size=(40, 60))
        out = self.read_until(master, proc, lambda t: False, timeout=60)
        proc.wait(timeout=30)

        self.assertEqual(proc.returncode, 0, out)
        lines = [l for l in strip_ansi(out).replace("\r", "").split("\n") if l.strip()]
        self.assertGreater(len(lines), 8, out)
        self.assertTrue(all(display_width(l) <= 60 for l in lines), lines)
        self.assertEqual(len({display_width(l) for l in lines}), 1, lines)
        self.assertTrue(lines[0].startswith("╭") and lines[0].endswith("╮"), lines[0])
        self.assertTrue(lines[-1].startswith("╰") and lines[-1].endswith("╯"), lines[-1])
        for line in lines[1:-1]:
            self.assertIn(line[0], "│├", line)
            self.assertIn(line[-1], "│┤", line)
        self.assertTrue(any(l.startswith("│") and l.rstrip("│ ").endswith("…") for l in lines), lines)

    def test_ticking_a_row_and_confirming_ends_the_throwaway_child(self) -> None:
        marker = f"jzr-procs-acceptance-{os.getpid()}-{time.monotonic_ns()}"
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(600)", marker],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(self._kill_child, child)
        self.assertEqual(child.stdout.readline().strip(), "ready")

        # The jzr process carries the marker too; it is protected and disabled,
        # so the pointer starts on the child.
        proc, master = self.spawn(["host", "procs", "--each", "--kill", marker], size=(40, 200))
        # The list labels need not show the command line, so wait until the
        # list has been drawn and the output has gone quiet.
        seen = self.settle(master, proc)
        self.assertIsNone(proc.poll(), strip_ansi(seen))
        self.assertTrue(strip_ansi(seen).strip(), "the list was never drawn")

        os.write(master, b" ")
        time.sleep(0.3)
        os.write(master, b"\r")
        seen = self.read_until(master, proc, lambda t: "unsaved" in t.lower(), seen=seen)
        self.assertIn("unsaved", seen.lower(), strip_ansi(seen))

        os.write(master, b"y")
        time.sleep(0.3)
        if proc.poll() is None:
            os.write(master, b"\r")
        seen = self.read_until(master, proc, lambda t: "ended" in t.lower() and "process" in t.lower(), seen=seen)
        proc.wait(timeout=60)

        self.assertEqual(proc.returncode, 0, strip_ansi(seen))
        self.assertIn("ended", strip_ansi(seen).lower())
        self.assertEqual(child.wait(timeout=30), -15, "the child should have been ended by SIGTERM")
        self.assertFalse(psutil.pid_exists(child.pid))

    @staticmethod
    def _kill_child(child) -> None:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        child.stdout.close()


if __name__ == "__main__":
    unittest.main()
