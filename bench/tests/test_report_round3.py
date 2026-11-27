"""Offline tests for what round 3 adds to jzr_bench.report: everything per machine (full and bare).

A record without a `machine` is a full-machine record. Rows, the "All tasks" sums and the per-task tables of one
machine never borrow a baseline, a `hello` or a task from the other.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import usage_fixtures as uf  # noqa: E402
from jzr_bench.cli import main  # noqa: E402
from jzr_bench.report import overall, render_markdown, summarize  # noqa: E402
from jzr_bench.runner import combined_summary, write_summary  # noqa: E402

SIMPLE = {"input": 1.0, "cache_write": 2.0, "cache_read": 0.5, "output": 4.0}
SIMPLE_PRICES = {uf.MODEL: SIMPLE}
UNSET: Any = object()


def run(
    task: str,
    arm: str,
    cost: float | None,
    *,
    machine: Any = UNSET,
    k: int | None = None,
    round: int = 1,
    passed: bool = True,
    isolation_ok: bool = True,
) -> dict[str, Any]:
    """A run record. `machine` left out gives an old record without the key; "bare" adds the id's -bare suffix."""
    per_model = {}
    if k is not None:
        tokens = {"input": k, "cache_write": 2 * k, "cache_read": 4 * k, "output": k}
        per_model = {uf.MODEL: (tokens, SIMPLE)}
    record = uf.usage_record(
        per_model, task=task, arm=arm, round=round, passed=passed, isolation_ok=isolation_ok, cost=cost
    )
    if machine is not UNSET:
        record["machine"] = machine
        if machine == "bare":
            record["run_id"] += "-bare"
    return record


def row_of(rows: list[dict], machine: str, task: str, arm: str) -> dict:
    found = [r for r in rows if r.get("machine") == machine and r["task"] == task and r["arm"] == arm]
    assert len(found) == 1, (machine, task, arm, rows)
    return found[0]


def by_arm(rows: list[dict]) -> dict[str, dict]:
    result = {r["arm"]: r for r in rows}
    assert len(result) == len(rows), rows
    return result


def headings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("#") and not line.startswith("# ")]


def section(text: str, heading: str) -> list[str]:
    """The lines under an exact heading, up to the next heading."""
    lines = text.splitlines()
    start = lines.index(heading)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    return lines[start + 1 : end]


def table(text: str, heading: str) -> list[list[str]]:
    """The cells of the first table under the heading: header row first, separator row dropped."""
    rows = [ln for ln in section(text, heading) if ln.lstrip().startswith("|")]
    cells = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in rows]
    return [cells[0], *cells[2:]]


def cell(text: str, heading: str, arm: str, column: str) -> str:
    rows = table(text, heading)
    index = rows[0].index(column)
    return next(r for r in rows[1:] if r[0] == arm)[index]


def two_machines() -> list[dict[str, Any]]:
    """hello and gif on both machines. Full has baseline, skill and mcp; bare has baseline and skill.

    cost:      hello  gif                    k (usage size):  hello  gif
    full  b     1.0   4.0                     full  b           1     1
          s     1.5   3.0                           s           1     2
          m     2.0   5.0                           m           1     3
    bare  b     2.0   8.0                     bare  b           1     2
          s     2.5   2.0                           s           1     4
    """
    full = {"baseline": (1.0, 4.0), "skill": (1.5, 3.0), "mcp": (2.0, 5.0)}
    full_k = {"baseline": (1, 1), "skill": (1, 2), "mcp": (1, 3)}
    bare = {"baseline": (2.0, 8.0), "skill": (2.5, 2.0)}
    bare_k = {"baseline": (1, 2), "skill": (1, 4)}
    records = []
    for machine, costs, sizes in (("full", full, full_k), ("bare", bare, bare_k)):
        for task_index, task in enumerate(("hello", "gif")):
            for arm, task_costs in costs.items():
                records.append(run(task, arm, task_costs[task_index], machine=machine, k=sizes[arm][task_index]))
    return records


class SummarizeMachineTest(unittest.TestCase):
    def test_rows_carry_their_machine(self) -> None:
        rows = summarize(two_machines(), prices=SIMPLE_PRICES)
        self.assertEqual({r["machine"] for r in rows}, {"full", "bare"})
        self.assertEqual(len(rows), 2 * 3 + 2 * 2)
        self.assertEqual(sorted((r["task"], r["arm"]) for r in rows if r["machine"] == "bare"),
                         [("gif", "baseline"), ("gif", "skill"), ("hello", "baseline"), ("hello", "skill")])  # fmt: skip

    def test_a_record_without_machine_is_a_full_record(self) -> None:
        rows = summarize([run("gif", "baseline", 1.0), run("gif", "skill", 2.0)], prices={})
        self.assertEqual([r["machine"] for r in rows], ["full", "full"])

    def test_old_and_explicit_full_records_are_one_group(self) -> None:
        rows = summarize(
            [
                run("gif", "baseline", 1.0, round=1),
                run("gif", "baseline", 3.0, round=2, machine="full"),
                run("gif", "baseline", 5.0, round=3, machine=UNSET),
            ],
            prices={},
        )
        [row] = rows
        self.assertEqual((row["machine"], row["runs"]), ("full", 3))
        self.assertAlmostEqual(row["median_cost_passed_usd"], 3.0)

    def test_the_same_task_and_arm_on_two_machines_are_two_rows_with_their_own_numbers(self) -> None:
        rows = summarize(
            [
                run("gif", "skill", 1.0, machine="full", round=1),
                run("gif", "skill", 3.0, machine="full", round=2),
                run("gif", "skill", 10.0, machine="bare", round=1),
                run("gif", "skill", 20.0, machine="bare", round=2, passed=False),
            ],
            prices={},
        )
        full, bare = row_of(rows, "full", "gif", "skill"), row_of(rows, "bare", "gif", "skill")
        self.assertEqual((full["runs"], full["passed"]), (2, 2))
        self.assertEqual((bare["runs"], bare["passed"]), (2, 1))
        self.assertAlmostEqual(full["median_cost_passed_usd"], 2.0)
        self.assertAlmostEqual(bare["median_cost_passed_usd"], 10.0)
        self.assertAlmostEqual(bare["median_cost_usd"], 15.0)
        self.assertAlmostEqual(bare["max_cost_passed_usd"], 10.0)

    def test_the_ratio_to_the_baseline_uses_the_baseline_of_the_same_machine(self) -> None:
        rows = summarize(two_machines(), prices=SIMPLE_PRICES)
        self.assertAlmostEqual(row_of(rows, "full", "gif", "skill")["cost_vs_baseline"], 3.0 / 4.0)
        self.assertAlmostEqual(row_of(rows, "bare", "gif", "skill")["cost_vs_baseline"], 2.0 / 8.0)
        self.assertAlmostEqual(row_of(rows, "bare", "gif", "baseline")["cost_vs_baseline"], 1.0)

    def test_a_machine_without_a_baseline_borrows_none(self) -> None:
        records = [
            run("gif", "baseline", 4.0, machine="full"),
            run("gif", "skill", 3.0, machine="full"),
            run("gif", "skill", 2.0, machine="bare"),  # no bare baseline
        ]
        rows = summarize(records, prices={})
        self.assertAlmostEqual(row_of(rows, "full", "gif", "skill")["cost_vs_baseline"], 0.75)
        self.assertIsNone(row_of(rows, "bare", "gif", "skill")["cost_vs_baseline"])
        self.assertIsNone(row_of(rows, "bare", "gif", "skill")["minus_hello_vs_baseline"])

    def test_the_other_way_round_too(self) -> None:
        records = [
            run("gif", "baseline", 4.0, machine="bare"),
            run("gif", "skill", 3.0, machine="full"),  # no full baseline
            run("gif", "skill", 2.0, machine="bare"),
        ]
        rows = summarize(records, prices={})
        self.assertIsNone(row_of(rows, "full", "gif", "skill")["cost_vs_baseline"])
        self.assertAlmostEqual(row_of(rows, "bare", "gif", "skill")["cost_vs_baseline"], 0.5)

    def test_minus_hello_uses_the_hello_of_the_same_machine(self) -> None:
        rows = summarize(two_machines(), prices=SIMPLE_PRICES)
        self.assertAlmostEqual(row_of(rows, "full", "gif", "skill")["cost_minus_hello_usd"], 3.0 - 1.5)
        self.assertAlmostEqual(row_of(rows, "bare", "gif", "skill")["cost_minus_hello_usd"], 2.0 - 2.5)
        self.assertAlmostEqual(row_of(rows, "bare", "gif", "baseline")["cost_minus_hello_usd"], 8.0 - 2.0)
        self.assertAlmostEqual(
            row_of(rows, "bare", "gif", "skill")["minus_hello_vs_baseline"], (2.0 - 2.5) / (8.0 - 2.0)
        )
        self.assertIsNone(row_of(rows, "bare", "hello", "skill")["cost_minus_hello_usd"])

    def test_a_machine_without_a_hello_does_not_take_the_other_ones(self) -> None:
        records = [
            run("hello", "baseline", 1.0, machine="full"),
            run("hello", "skill", 1.5, machine="full"),
            run("gif", "baseline", 4.0, machine="full"),
            run("gif", "skill", 3.0, machine="full"),
            run("gif", "baseline", 8.0, machine="bare"),  # bare ran no hello
            run("gif", "skill", 2.0, machine="bare"),
        ]
        rows = summarize(records, prices={})
        self.assertAlmostEqual(row_of(rows, "full", "gif", "skill")["cost_minus_hello_usd"], 1.5)
        for arm in ("baseline", "skill"):
            self.assertIsNone(row_of(rows, "bare", "gif", arm)["cost_minus_hello_usd"], arm)
            self.assertIsNone(row_of(rows, "bare", "gif", arm)["minus_hello_vs_baseline"], arm)

    def test_the_other_columns_of_a_machine_are_its_own(self) -> None:
        rows = summarize(two_machines(), prices=SIMPLE_PRICES)
        # k=1 and k=2 usage on the two machines: written 5k, re-read 2k, output 4k dollars
        self.assertAlmostEqual(row_of(rows, "full", "gif", "baseline")["median_written_usd"], 5.0)
        self.assertAlmostEqual(row_of(rows, "bare", "gif", "baseline")["median_written_usd"], 10.0)
        self.assertEqual(row_of(rows, "full", "gif", "skill")["median_input_tokens"], 2)
        self.assertEqual(row_of(rows, "bare", "gif", "skill")["median_input_tokens"], 4)

    def test_isolation_failures_are_counted_per_machine(self) -> None:
        rows = summarize(
            [
                run("gif", "skill", 1.0, machine="full"),
                run("gif", "skill", 1.0, machine="bare", isolation_ok=False),
            ],
            prices={},
        )
        self.assertEqual(row_of(rows, "full", "gif", "skill")["isolation_failures"], 0)
        self.assertEqual(row_of(rows, "bare", "gif", "skill")["isolation_failures"], 1)


class OverallMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = summarize(two_machines(), prices=SIMPLE_PRICES)

    def test_the_default_is_the_full_machine(self) -> None:
        default = overall(self.rows)
        self.assertEqual({r["arm"] for r in default}, {"baseline", "skill", "mcp"})
        self.assertEqual(default, overall(self.rows, machine="full"))
        full_only = [r for r in self.rows if r["machine"] == "full"]
        self.assertEqual(default, overall(full_only))

    def test_the_bare_machine_sums_its_own_arms_and_tasks(self) -> None:
        bare = by_arm(overall(self.rows, machine="bare"))
        self.assertEqual(set(bare), {"baseline", "skill"})
        self.assertEqual(bare["baseline"]["tasks"], ["gif"])
        self.assertAlmostEqual(bare["baseline"]["cost_usd"], 8.0)
        self.assertAlmostEqual(bare["skill"]["cost_usd"], 2.0)
        self.assertAlmostEqual(bare["skill"]["cost_vs_baseline"], 0.25)
        self.assertAlmostEqual(bare["skill"]["minus_hello_usd"], 2.0 - 2.5)
        self.assertAlmostEqual(bare["skill"]["minus_hello_vs_baseline"], (2.0 - 2.5) / (8.0 - 2.0))
        self.assertAlmostEqual(bare["skill"]["unused_session_usd"], 2.5 - 2.0)

    def test_the_full_machine_is_not_changed_by_bare_runs(self) -> None:
        full = by_arm(overall(self.rows))
        self.assertAlmostEqual(full["skill"]["cost_usd"], 3.0)
        self.assertAlmostEqual(full["mcp"]["cost_usd"], 5.0)
        self.assertAlmostEqual(full["skill"]["unused_session_usd"], 1.5 - 1.0)
        self.assertAlmostEqual(full["skill"]["cost_vs_baseline"], 0.75)

    def test_empty_for_a_machine_without_rows(self) -> None:
        full_only = summarize([r for r in two_machines() if r.get("machine") == "full"], prices=SIMPLE_PRICES)
        self.assertEqual(overall(full_only, machine="bare"), [])
        bare_only = summarize([r for r in two_machines() if r.get("machine") == "bare"], prices=SIMPLE_PRICES)
        self.assertEqual(overall(bare_only), [])
        self.assertNotEqual(overall(bare_only, machine="bare"), [])

    def test_a_task_counts_when_every_arm_of_that_machine_passed_it(self) -> None:
        # full has a failing mcp on xlsx, bare has no mcp at all: xlsx is summed for bare only
        records = [
            *two_machines(),
            run("xlsx", "baseline", 6.0, machine="full"),
            run("xlsx", "skill", 4.0, machine="full"),
            run("xlsx", "mcp", 8.0, machine="full", passed=False),
            run("xlsx", "baseline", 7.0, machine="bare"),
            run("xlsx", "skill", 5.0, machine="bare"),
        ]
        rows = summarize(records, prices=SIMPLE_PRICES)
        self.assertEqual(by_arm(overall(rows))["skill"]["tasks"], ["gif"])
        self.assertEqual(by_arm(overall(rows, machine="bare"))["skill"]["tasks"], ["gif", "xlsx"])
        self.assertAlmostEqual(by_arm(overall(rows, machine="bare"))["baseline"]["cost_usd"], 15.0)

    def test_a_baseline_of_the_other_machine_is_no_baseline(self) -> None:
        # the bare machine has no baseline arm: its rows have no ratio, though the full machine has one
        records = [
            run("gif", "baseline", 4.0, machine="full"),
            run("gif", "skill", 3.0, machine="full"),
            run("gif", "skill", 2.0, machine="bare"),
            run("gif", "mcp", 2.5, machine="bare"),
        ]
        bare = by_arm(overall(summarize(records, prices={}), machine="bare"))
        self.assertAlmostEqual(bare["skill"]["cost_usd"], 2.0)
        self.assertIsNone(bare["skill"]["cost_vs_baseline"])
        self.assertIsNone(bare["skill"]["unused_session_usd"])

    def test_hello_is_not_borrowed_across_machines(self) -> None:
        records = [
            run("hello", "baseline", 1.0, machine="full"),
            run("hello", "skill", 1.5, machine="full"),
            run("gif", "baseline", 4.0, machine="full"),
            run("gif", "skill", 3.0, machine="full"),
            run("gif", "baseline", 8.0, machine="bare"),
            run("gif", "skill", 2.0, machine="bare"),
        ]
        bare = by_arm(overall(summarize(records, prices={}), machine="bare"))
        for arm in ("baseline", "skill"):
            self.assertIsNone(bare[arm]["minus_hello_usd"], arm)
            self.assertIsNone(bare[arm]["unused_session_usd"], arm)
            self.assertIsNone(bare[arm]["minus_hello_vs_baseline"], arm)
        self.assertAlmostEqual(bare["skill"]["cost_usd"], 2.0)

    def test_overall_does_not_change_the_rows(self) -> None:
        before = json.dumps(self.rows, sort_keys=True)
        overall(self.rows)
        overall(self.rows, machine="bare")
        self.assertEqual(json.dumps(self.rows, sort_keys=True), before)


class RenderMachineTest(unittest.TestCase):
    def render(self, records: list[dict[str, Any]] | None = None, meta: dict | None = None) -> str:
        rows = summarize(records or two_machines(), prices=SIMPLE_PRICES)
        return render_markdown(rows, meta, SIMPLE_PRICES)

    def test_sections_come_in_the_documented_order(self) -> None:
        self.assertEqual(
            headings(self.render()),
            [
                "## All tasks",
                "## All tasks, bare machine",
                "## hello",
                "## hello, bare machine",
                "## gif",
                "## gif, bare machine",
            ],
        )

    def test_a_task_with_runs_on_one_machine_only_has_one_table(self) -> None:
        records = [
            *two_machines(),
            run("xlsx", "baseline", 6.0, machine="full"),
            run("xlsx", "skill", 4.0, machine="full"),
        ]
        found = headings(self.render(records))
        self.assertIn("## xlsx", found)
        self.assertNotIn("## xlsx, bare machine", found)
        self.assertEqual(found[-1], "## xlsx")

    def test_a_task_only_on_the_bare_machine_has_only_the_bare_table(self) -> None:
        records = [
            *two_machines(),
            run("heic", "baseline", 6.0, machine="bare"),
            run("heic", "skill", 4.0, machine="bare"),
        ]
        found = headings(self.render(records))
        self.assertIn("## heic, bare machine", found)
        self.assertNotIn("## heic", found)

    def test_a_machine_without_runs_prints_nothing(self) -> None:
        full_only = self.render([r for r in two_machines() if r["machine"] == "full"])
        self.assertEqual(headings(full_only), ["## All tasks", "## hello", "## gif"])
        self.assertNotIn("bare machine", full_only)

    def test_a_summary_of_bare_runs_only_has_no_full_sections(self) -> None:
        bare_only = self.render([r for r in two_machines() if r["machine"] == "bare"])
        self.assertEqual(
            headings(bare_only), ["## All tasks, bare machine", "## hello, bare machine", "## gif, bare machine"]
        )
        self.assertNotIn("## All tasks\n", bare_only)

    def test_the_all_tasks_table_of_the_bare_machine_has_the_bare_arms_and_their_sums(self) -> None:
        text = self.render()
        rows = table(text, "## All tasks, bare machine")
        self.assertEqual([r[0] for r in rows[1:]], ["baseline", "skill"])
        self.assertEqual(rows[0][:3], ["arm", "cost", "vs baseline"])
        self.assertEqual(cell(text, "## All tasks, bare machine", "skill", "cost"), "$2.0000")
        self.assertEqual(cell(text, "## All tasks, bare machine", "skill", "vs baseline"), "0.25x")
        self.assertEqual(cell(text, "## All tasks, bare machine", "baseline", "cost"), "$8.0000")

    def test_the_all_tasks_table_of_the_full_machine_is_the_full_one(self) -> None:
        text = self.render()
        rows = table(text, "## All tasks")
        self.assertEqual([r[0] for r in rows[1:]], ["baseline", "skill", "mcp"])
        self.assertEqual(cell(text, "## All tasks", "skill", "cost"), "$3.0000")
        self.assertEqual(cell(text, "## All tasks", "skill", "vs baseline"), "0.75x")
        self.assertEqual(cell(text, "## All tasks", "mcp", "cost"), "$5.0000")

    def test_the_per_task_tables_hold_the_arms_of_their_machine(self) -> None:
        text = self.render()
        self.assertEqual([r[0] for r in table(text, "## gif")[1:]], ["baseline", "skill", "mcp"])
        self.assertEqual([r[0] for r in table(text, "## gif, bare machine")[1:]], ["baseline", "skill"])
        for heading, expected in (("## gif", "$3.0000"), ("## gif, bare machine", "$2.0000")):
            self.assertEqual(cell(text, heading, "skill", "cost (passed)"), expected, heading)

    def test_the_bare_tables_compare_with_the_bare_baseline(self) -> None:
        text = self.render()
        self.assertEqual(cell(text, "## gif", "skill", "vs baseline"), "0.75x")
        self.assertEqual(cell(text, "## gif, bare machine", "skill", "vs baseline"), "0.25x")
        self.assertEqual(cell(text, "## gif, bare machine", "skill", "minus hello"), "-$0.5000")
        self.assertEqual(cell(text, "## gif", "skill", "minus hello"), "$1.5000")

    def test_a_bare_table_does_not_borrow_the_full_baseline(self) -> None:
        records = [
            run("gif", "baseline", 4.0, machine="full"),
            run("gif", "skill", 3.0, machine="full"),
            run("gif", "skill", 2.0, machine="bare"),
        ]
        text = self.render(records)
        self.assertEqual(cell(text, "## gif", "skill", "vs baseline"), "0.75x")
        self.assertEqual(cell(text, "## gif, bare machine", "skill", "vs baseline"), "–")
        self.assertEqual([r[0] for r in table(text, "## gif, bare machine")[1:]], ["skill"])
        self.assertNotIn("None", text)

    def test_the_bare_all_tasks_table_does_not_borrow_hello_from_the_full_machine(self) -> None:
        records = [
            run("hello", "baseline", 1.0, machine="full"),
            run("hello", "skill", 1.5, machine="full"),
            run("gif", "baseline", 4.0, machine="full"),
            run("gif", "skill", 3.0, machine="full"),
            run("gif", "baseline", 8.0, machine="bare"),
            run("gif", "skill", 2.0, machine="bare"),
        ]
        text = self.render(records)
        self.assertEqual(cell(text, "## All tasks", "skill", "unused, per session"), "$0.5000")
        self.assertEqual(cell(text, "## All tasks, bare machine", "skill", "unused, per session"), "–")
        self.assertEqual(cell(text, "## All tasks, bare machine", "skill", "minus hello"), "–")
        self.assertNotIn("## hello, bare machine", headings(text))

    def test_old_records_render_under_the_plain_headings(self) -> None:
        old = [{k: v for k, v in r.items() if k != "machine"} for r in two_machines() if r["machine"] == "full"]
        self.assertEqual(headings(self.render(old)), ["## All tasks", "## hello", "## gif"])
        explicit = self.render([r for r in two_machines() if r["machine"] == "full"])
        self.assertEqual(self.render(old), explicit)

    def test_the_bare_arms_of_the_meta_are_shown(self) -> None:
        text = self.render(meta={"bare_arms": ["baseline", "skill"], "model": "sonnet"})
        lines = [ln for ln in text.splitlines() if re.search(r"bare[ _]arms", ln, re.IGNORECASE)]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("baseline, skill", lines[0])
        self.assertIn("model: sonnet", text)

    def test_no_bare_arms_line_when_there_are_none(self) -> None:
        for meta in ({"bare_arms": [], "model": "sonnet"}, {"model": "sonnet"}, None):
            with self.subTest(meta=meta):
                text = self.render([r for r in two_machines() if r["machine"] == "full"], meta=meta)
                self.assertNotRegex(text, r"(?i)bare[ _]arms")

    def test_no_none_in_the_text(self) -> None:
        self.assertNotIn("None", self.render(meta={"bare_arms": ["baseline"]}))


OLD_SUMMARY = (
    "# Benchmark summary\n"
    "\n"
    "- model: sonnet\n"
    "- rounds: 2\n"
    "- private tmp: true\n"
    "\n"
    "Medians over runs whose isolation check passed. Costs are Claude Code's `total_cost_usd`;\n"
    '"cost (passed)" counts only runs whose result was correct, "range" is its lowest and highest\n'
    'run, and "vs baseline" compares it with the baseline arm of the same task. "minus hello" is\n'
    "that cost less the same arm's `hello` cost: what the job cost on top of starting a session.\n"
    '"written $" (uncached input and cache writes), "re-read $" (cache reads) and "output $" split\n'
    "the cost of the passed runs by kind of token. Token columns are per run.\n"
    "\n"
    "Prices fitted from these runs, in USD per million tokens:\n"
    "\n"
    "- claude-sonnet-5-5: input 1e+06, cache write 2e+06, cache read 5e+05, output 4e+06\n"
    "\n"
    "## All tasks\n"
    "\n"
    "Sums of the per-task values over gif: the tasks other than `hello`\n"
    'that every arm passed at least once. "unused, per session" is the arm\'s `hello` cost less the\n'
    "baseline's: what the arm adds to a session that never uses jzr.\n"
    "\n"
    "| arm | cost | vs baseline | minus hello | minus hello vs baseline | written $ | re-read $ | output $ | unused, per session |\n"
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    "| baseline | $4.5000 | 1.00x | $3.5000 | 1.00x | $5.0000 | $2.0000 | $4.0000 | $0.0000 |\n"
    "| skill | $3.0000 | 0.67x | $1.5000 | 0.43x | $10.0000 | $4.0000 | $8.0000 | $0.5000 |\n"
    "\n"
    "## hello\n"
    "\n"
    "| arm | passed / runs | used jzr | isolation fails | cost (passed) | range | vs baseline | minus hello | minus hello vs baseline | cost (all) | written $ | re-read $ | output $ | input | cache write | cache read | output | turns | time (s) |\n"
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    "| baseline | 1 / 1 | 0 | 0 | $1.0000 | $1.0000–$1.0000 | 1.00x | – | – | $1.0000 | $5.0000 | $2.0000 | $4.0000 | 1 | 2 | 4 | 1 | 2 | 4.0 |\n"
    "| skill | 1 / 1 | 0 | 0 | $1.5000 | $1.5000–$1.5000 | 1.50x | – | – | $1.5000 | $5.0000 | $2.0000 | $4.0000 | 1 | 2 | 4 | 1 | 2 | 4.0 |\n"
    "\n"
    "## gif\n"
    "\n"
    "| arm | passed / runs | used jzr | isolation fails | cost (passed) | range | vs baseline | minus hello | minus hello vs baseline | cost (all) | written $ | re-read $ | output $ | input | cache write | cache read | output | turns | time (s) |\n"
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    "| baseline | 2 / 2 | 0 | 0 | $4.5000 | $4.0000–$5.0000 | 1.00x | $3.5000 | 1.00x | $4.5000 | $5.0000 | $2.0000 | $4.0000 | 1 | 2 | 4 | 1 | 2 | 4.0 |\n"
    "| skill | 1 / 2 | 0 | 0 | $3.0000 | $3.0000–$3.0000 | 0.67x | $1.5000 | 0.43x | $6.0000 | $10.0000 | $4.0000 | $8.0000 | 2 | 4 | 8 | 2 | 2 | 4.0 |\n"
    ""
)


class OldSummaryUnchangedTest(unittest.TestCase):
    """A summary of records without a machine is byte for byte what the harness wrote before round 3."""

    def records(self) -> list[dict[str, Any]]:
        return [
            run("hello", "baseline", 1.0, k=1),
            run("hello", "skill", 1.5, k=1),
            run("gif", "baseline", 4.0, k=1),
            run("gif", "baseline", 5.0, k=1, round=2),
            run("gif", "skill", 3.0, k=2),
            run("gif", "skill", 9.0, k=2, round=2, passed=False),
        ]

    def test_the_text_is_the_old_text(self) -> None:
        text = render_markdown(
            summarize(self.records(), prices=SIMPLE_PRICES),
            {"model": "sonnet", "rounds": 2, "private_tmp": True},
            SIMPLE_PRICES,
        )
        self.assertEqual(text, OLD_SUMMARY)

    def test_records_marked_full_give_the_same_text(self) -> None:
        marked = [dict(r, machine="full") for r in self.records()]
        text = render_markdown(
            summarize(marked, prices=SIMPLE_PRICES),
            {"model": "sonnet", "rounds": 2, "private_tmp": True},
            SIMPLE_PRICES,
        )
        self.assertEqual(text, OLD_SUMMARY)

    def test_the_old_text_has_no_machine_in_it(self) -> None:
        self.assertNotIn("machine", OLD_SUMMARY)
        self.assertNotIn("bare", OLD_SUMMARY.lower())


class ResultsFolderMachineTest(unittest.TestCase):
    """summary.md of a results folder with bare runs, through write_summary and the report command."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def folder(self, name: str, records: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> Path:
        path = self.root / name
        path.mkdir()
        path.joinpath("runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        if meta is not None:
            path.joinpath("meta.json").write_text(json.dumps(meta))
        return path

    def test_write_summary_splits_by_machine_and_shows_the_bare_arms(self) -> None:
        folder = self.folder("a", two_machines(), {"bare_arms": ["baseline", "skill"], "model": "sonnet"})
        text = write_summary(folder).read_text()
        self.assertEqual(
            headings(text),
            [
                "## All tasks",
                "## All tasks, bare machine",
                "## hello",
                "## hello, bare machine",
                "## gif",
                "## gif, bare machine",
            ],
        )
        self.assertRegex(text, r"(?i)bare[ _]arms: baseline, skill")

    def test_the_report_command_prints_and_writes_the_same(self) -> None:
        folder = self.folder("a", two_machines(), {"bare_arms": ["baseline", "skill"]})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["report", str(folder)]), 0)
        written = (folder / "summary.md").read_text()
        self.assertIn("## gif, bare machine", written)
        self.assertIn("## gif, bare machine", out.getvalue())

    def test_a_folder_of_old_records_reports_as_before(self) -> None:
        old = [{k: v for k, v in r.items() if k != "machine"} for r in two_machines() if r["machine"] == "full"]
        folder = self.folder("old", old)
        text = write_summary(folder).read_text()
        self.assertEqual(headings(text), ["## All tasks", "## hello", "## gif"])
        self.assertNotIn("bare", text.lower())

    def test_several_folders_keep_the_machines_of_the_runs_they_take(self) -> None:
        old_run = [run("gif", "baseline", 9.0, machine="full"), run("gif", "skill", 9.0, machine="full")]
        a = self.folder("20261126T000000Z", old_run)
        b = self.folder("20261127T000000Z", two_machines())
        text = combined_summary([a, b])
        self.assertIn("## gif, bare machine", headings(text))
        self.assertIn("## hello, bare machine", headings(text))
        self.assertEqual(cell(text, "## gif", "skill", "cost (passed)"), "$3.0000")


if __name__ == "__main__":
    unittest.main()
