"""Offline tests for jzr_bench.report: per task and arm summary and markdown rendering."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench.report import render_markdown, summarize  # noqa: E402

ROW_KEYS = {
    "machine",
    "task",
    "arm",
    "runs",
    "passed",
    "isolation_failures",
    "used_jzr",
    "median_cost_usd",
    "median_cost_passed_usd",
    "cost_vs_baseline",
    "median_input_tokens",
    "median_cache_creation_input_tokens",
    "median_cache_read_input_tokens",
    "median_output_tokens",
    "median_turns",
    "median_duration_s",
    "min_cost_passed_usd",
    "max_cost_passed_usd",
    "cost_minus_hello_usd",
    "minus_hello_vs_baseline",
    "median_written_usd",
    "median_reread_usd",
    "median_output_usd",
}


def rec(
    task: str = "hello",
    arm: str = "baseline",
    *,
    round: int = 1,
    passed: bool = True,
    isolation_ok: bool = True,
    cost: float | None = 1.0,
    input_tokens: int | None = 10,
    cache_creation: int | None = 100,
    cache_read: int | None = 1000,
    output_tokens: int | None = 50,
    turns: int | None = 2,
    duration_ms: int | None = 4000,
    jzr: tuple[bool, bool, bool] = (False, False, False),
) -> dict[str, Any]:
    return {
        "run_id": f"r{round:02d}-{task}-{arm}",
        "task": task,
        "arm": arm,
        "round": round,
        "passed": passed,
        "isolation_ok": isolation_ok,
        "total_cost_usd": cost,
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output_tokens,
        "num_turns": turns,
        "duration_ms": duration_ms,
        "used_jzr_cli": jzr[0],
        "used_jzr_skill": jzr[1],
        "used_jzr_mcp": jzr[2],
    }


def row_of(rows: list[dict], task: str, arm: str) -> dict:
    found = [r for r in rows if r["task"] == task and r["arm"] == arm]
    assert len(found) == 1, (task, arm, rows)
    return found[0]


class SummarizeShapeTest(unittest.TestCase):
    def test_one_row_per_task_and_arm_in_first_seen_order(self) -> None:
        records = [
            rec("hello", "skill", round=1),
            rec("hello", "baseline", round=1),
            rec("gif", "baseline", round=1),
            rec("hello", "skill", round=2),
            rec("gif", "mcp", round=1),
        ]
        rows = summarize(records)
        self.assertEqual(
            [(r["task"], r["arm"]) for r in rows],
            [("hello", "skill"), ("hello", "baseline"), ("gif", "baseline"), ("gif", "mcp")],
        )
        for r in rows:
            self.assertEqual(set(r), ROW_KEYS)

    def test_accepts_any_iterable(self) -> None:
        rows = summarize(r for r in [rec(), rec(round=2)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["runs"], 2)

    def test_empty_input(self) -> None:
        self.assertEqual(summarize([]), [])


class SummarizeCountsTest(unittest.TestCase):
    def test_runs_passed_and_isolation_failures(self) -> None:
        rows = summarize(
            [
                rec(round=1, passed=True),
                rec(round=2, passed=False),
                rec(round=3, passed=False, isolation_ok=False),
                rec(round=4, passed=True),
            ]
        )
        row = row_of(rows, "hello", "baseline")
        self.assertEqual(row["runs"], 4)  # isolation failures are still counted
        self.assertEqual(row["isolation_failures"], 1)
        self.assertEqual(row["passed"], 2)

    def test_used_jzr_counts_runs_using_the_cli_skill_or_mcp(self) -> None:
        rows = summarize(
            [
                rec("gif", "skill", round=1, jzr=(True, True, False)),
                rec("gif", "skill", round=2, jzr=(False, True, False)),
                rec("gif", "skill", round=3, jzr=(False, False, False)),
                rec("gif", "mcp", round=1, jzr=(False, False, True)),
                rec("gif", "mcp", round=2),
            ]
        )
        self.assertEqual(row_of(rows, "gif", "skill")["used_jzr"], 2)
        self.assertEqual(row_of(rows, "gif", "mcp")["used_jzr"], 1)

    def test_used_jzr_is_zero_without_any_use(self) -> None:
        rows = summarize([rec(), rec(round=2)])
        self.assertEqual(rows[0]["used_jzr"], 0)


class SummarizeMediansTest(unittest.TestCase):
    def test_medians_over_isolated_runs(self) -> None:
        rows = summarize(
            [
                rec(
                    round=1,
                    cost=1.0,
                    input_tokens=10,
                    cache_creation=100,
                    cache_read=1000,
                    output_tokens=50,
                    turns=2,
                    duration_ms=3000,
                ),
                rec(
                    round=2,
                    cost=3.0,
                    input_tokens=30,
                    cache_creation=300,
                    cache_read=3000,
                    output_tokens=70,
                    turns=4,
                    duration_ms=9000,
                ),
                rec(
                    round=3,
                    cost=2.0,
                    input_tokens=20,
                    cache_creation=200,
                    cache_read=2000,
                    output_tokens=60,
                    turns=3,
                    duration_ms=5000,
                ),
            ]
        )
        row = rows[0]
        self.assertAlmostEqual(row["median_cost_usd"], 2.0)
        self.assertAlmostEqual(row["median_input_tokens"], 20)
        self.assertAlmostEqual(row["median_cache_creation_input_tokens"], 200)
        self.assertAlmostEqual(row["median_cache_read_input_tokens"], 2000)
        self.assertAlmostEqual(row["median_output_tokens"], 60)
        self.assertAlmostEqual(row["median_turns"], 3)
        self.assertAlmostEqual(row["median_duration_s"], 5.0)  # milliseconds in, seconds out

    def test_even_count_median_is_the_mean_of_the_middle_two(self) -> None:
        rows = summarize([rec(round=1, cost=1.0, duration_ms=1000), rec(round=2, cost=2.0, duration_ms=2000)])
        self.assertAlmostEqual(rows[0]["median_cost_usd"], 1.5)
        self.assertAlmostEqual(rows[0]["median_duration_s"], 1.5)

    def test_isolation_failed_runs_are_excluded_from_every_median(self) -> None:
        rows = summarize(
            [
                rec(
                    round=1,
                    cost=1.0,
                    input_tokens=10,
                    cache_creation=100,
                    cache_read=1000,
                    output_tokens=50,
                    turns=2,
                    duration_ms=2000,
                ),
                rec(
                    round=2,
                    cost=3.0,
                    input_tokens=30,
                    cache_creation=300,
                    cache_read=3000,
                    output_tokens=70,
                    turns=4,
                    duration_ms=6000,
                ),
                rec(
                    round=3,
                    isolation_ok=False,
                    passed=True,
                    cost=100.0,
                    input_tokens=9999,
                    cache_creation=99999,
                    cache_read=999999,
                    output_tokens=9999,
                    turns=99,
                    duration_ms=999000,
                ),
            ]
        )
        row = rows[0]
        self.assertAlmostEqual(row["median_cost_usd"], 2.0)
        self.assertAlmostEqual(row["median_cost_passed_usd"], 2.0)
        self.assertAlmostEqual(row["median_input_tokens"], 20)
        self.assertAlmostEqual(row["median_cache_creation_input_tokens"], 200)
        self.assertAlmostEqual(row["median_cache_read_input_tokens"], 2000)
        self.assertAlmostEqual(row["median_output_tokens"], 60)
        self.assertAlmostEqual(row["median_turns"], 3)
        self.assertAlmostEqual(row["median_duration_s"], 4.0)
        self.assertEqual(row["runs"], 3)
        self.assertEqual(row["isolation_failures"], 1)

    def test_null_values_are_skipped_not_counted_as_zero(self) -> None:
        rows = summarize(
            [
                rec(round=1, cost=4.0, turns=6),
                rec(round=2, cost=None, turns=None, input_tokens=None, duration_ms=None),
                rec(round=3, cost=2.0, turns=2),
            ]
        )
        row = rows[0]
        self.assertAlmostEqual(row["median_cost_usd"], 3.0)
        self.assertAlmostEqual(row["median_turns"], 4.0)
        self.assertAlmostEqual(row["median_input_tokens"], 10)
        self.assertAlmostEqual(row["median_duration_s"], 4.0)

    def test_zero_is_a_real_value(self) -> None:
        rows = summarize([rec(round=1, cost=0.0), rec(round=2, cost=0.0)])
        self.assertEqual(rows[0]["median_cost_usd"], 0.0)
        self.assertIsNotNone(rows[0]["median_cost_usd"])

    def test_empty_sets_give_none(self) -> None:
        rows = summarize(
            [
                rec("hello", "baseline", isolation_ok=False),
                rec(
                    "gif",
                    "baseline",
                    cost=None,
                    input_tokens=None,
                    cache_creation=None,
                    cache_read=None,
                    output_tokens=None,
                    turns=None,
                    duration_ms=None,
                ),
            ]
        )
        for row in rows:
            for key in ROW_KEYS - {"machine", "task", "arm", "runs", "passed", "isolation_failures", "used_jzr"}:
                self.assertIsNone(row[key], (row["task"], key))
        self.assertEqual(row_of(rows, "hello", "baseline")["runs"], 1)

    def test_medians_are_per_task_and_arm(self) -> None:
        rows = summarize(
            [
                rec("hello", "baseline", cost=1.0),
                rec("gif", "baseline", cost=9.0),
                rec("hello", "skill", cost=3.0),
            ]
        )
        self.assertAlmostEqual(row_of(rows, "hello", "baseline")["median_cost_usd"], 1.0)
        self.assertAlmostEqual(row_of(rows, "gif", "baseline")["median_cost_usd"], 9.0)
        self.assertAlmostEqual(row_of(rows, "hello", "skill")["median_cost_usd"], 3.0)


class SummarizePassedCostTest(unittest.TestCase):
    def test_passed_only_median_differs_from_all_run_median(self) -> None:
        rows = summarize(
            [
                rec(round=1, passed=True, cost=2.0),
                rec(round=2, passed=False, cost=0.5),
                rec(round=3, passed=False, cost=0.7),
                rec(round=4, passed=True, cost=4.0),
            ]
        )
        row = rows[0]
        self.assertAlmostEqual(row["median_cost_usd"], (0.7 + 2.0) / 2)
        self.assertAlmostEqual(row["median_cost_passed_usd"], 3.0)

    def test_no_passed_runs_gives_none(self) -> None:
        rows = summarize([rec(round=1, passed=False, cost=1.0), rec(round=2, passed=False, cost=2.0)])
        self.assertIsNone(rows[0]["median_cost_passed_usd"])
        self.assertAlmostEqual(rows[0]["median_cost_usd"], 1.5)

    def test_passed_run_with_null_cost_is_skipped(self) -> None:
        rows = summarize([rec(round=1, passed=True, cost=None), rec(round=2, passed=True, cost=2.0)])
        self.assertAlmostEqual(rows[0]["median_cost_passed_usd"], 2.0)


class CostVsBaselineTest(unittest.TestCase):
    def test_ratio_of_passed_cost_medians_for_the_same_task(self) -> None:
        rows = summarize(
            [
                rec("gif", "baseline", round=1, cost=2.0),
                rec("gif", "baseline", round=2, cost=4.0),  # median 3.0
                rec("gif", "skill", round=1, cost=1.0),
                rec("gif", "skill", round=2, cost=2.0),  # median 1.5
                rec("gif", "mcp", round=1, cost=6.0),
                rec("gif", "mcp", round=2, cost=12.0),  # median 9.0
            ]
        )
        self.assertAlmostEqual(row_of(rows, "gif", "skill")["cost_vs_baseline"], 0.5)
        self.assertAlmostEqual(row_of(rows, "gif", "mcp")["cost_vs_baseline"], 3.0)

    def test_the_baseline_against_itself_is_one(self) -> None:
        rows = summarize([rec("gif", "baseline", cost=2.0), rec("gif", "skill", cost=1.0)])
        self.assertAlmostEqual(row_of(rows, "gif", "baseline")["cost_vs_baseline"], 1.0)

    def test_uses_the_baseline_of_the_same_task_only(self) -> None:
        rows = summarize(
            [
                rec("hello", "baseline", cost=0.5),
                rec("gif", "baseline", cost=4.0),
                rec("hello", "skill", cost=1.0),
                rec("gif", "skill", cost=2.0),
            ]
        )
        self.assertAlmostEqual(row_of(rows, "hello", "skill")["cost_vs_baseline"], 2.0)
        self.assertAlmostEqual(row_of(rows, "gif", "skill")["cost_vs_baseline"], 0.5)

    def test_none_without_a_baseline_for_the_task(self) -> None:
        rows = summarize([rec("gif", "skill", cost=1.0), rec("hello", "baseline", cost=1.0)])
        self.assertIsNone(row_of(rows, "gif", "skill")["cost_vs_baseline"])

    def test_none_when_the_baseline_has_no_passed_run(self) -> None:
        rows = summarize([rec("gif", "baseline", passed=False, cost=1.0), rec("gif", "skill", cost=1.0)])
        self.assertIsNone(row_of(rows, "gif", "skill")["cost_vs_baseline"])

    def test_none_when_the_arm_has_no_passed_run(self) -> None:
        rows = summarize([rec("gif", "baseline", cost=1.0), rec("gif", "skill", passed=False, cost=1.0)])
        self.assertIsNone(row_of(rows, "gif", "skill")["cost_vs_baseline"])

    def test_none_when_the_baseline_median_is_zero(self) -> None:
        rows = summarize([rec("gif", "baseline", cost=0.0), rec("gif", "skill", cost=1.0)])
        self.assertIsNone(row_of(rows, "gif", "skill")["cost_vs_baseline"])

    def test_isolation_failed_baseline_runs_do_not_make_a_baseline(self) -> None:
        rows = summarize([rec("gif", "baseline", isolation_ok=False, cost=1.0), rec("gif", "skill", cost=1.0)])
        self.assertIsNone(row_of(rows, "gif", "skill")["cost_vs_baseline"])


class RenderMarkdownTest(unittest.TestCase):
    NDASH = "–"

    def setUp(self) -> None:
        self.rows = summarize(
            [
                rec("hello", "baseline", cost=0.25),
                rec("hello", "skill", cost=0.5),
                rec("hello", "mcp", cost=0.75, passed=False),
                rec("gif", "baseline", cost=1.25),
                rec("gif", "skill", cost=1.5, isolation_ok=False),
                rec("gif", "mcp", cost=1.75),
            ]
        )
        self.text = render_markdown(self.rows)
        self.lines = self.text.splitlines()

    def table_lines(self) -> list[str]:
        return [ln for ln in self.lines if ln.lstrip().startswith("|")]

    def tables(self) -> list[list[str]]:
        """Contiguous runs of table lines."""
        return [lines for _, lines in self.tables_at()]

    def tables_at(self) -> list[tuple[int, list[str]]]:
        """(index of the first line, lines) of every contiguous run of table lines."""
        groups: list[tuple[int, list[str]]] = []
        previous_was_table = False
        for i, ln in enumerate(self.lines):
            is_table = ln.lstrip().startswith("|")
            if is_table and not previous_was_table:
                groups.append((i, []))
            if is_table:
                groups[-1][1].append(ln)
            previous_was_table = is_table
        return groups

    def heading_index(self, task: str) -> int:
        return next(i for i, ln in enumerate(self.lines) if task in ln and not ln.lstrip().startswith("|"))

    def test_returns_a_string_and_works_with_and_without_meta(self) -> None:
        self.assertIsInstance(self.text, str)
        with_meta = render_markdown(self.rows, {"model": "sonnet", "seed": 7, "rounds": 3})
        self.assertIsInstance(with_meta, str)
        self.assertTrue(with_meta.strip())
        self.assertEqual(
            len([ln for ln in with_meta.splitlines() if ln.lstrip().startswith("|")]), len(self.table_lines())
        )

    def test_one_table_per_task_with_a_separator_row(self) -> None:
        tables = self.tables()
        self.assertEqual(len(tables), 2)
        for table in tables:
            # header, separator, then one row per arm
            self.assertEqual(len(table), 2 + 3)
            self.assertTrue(set(table[1].replace("|", "").replace(":", "").strip()) <= {"-", " "})

    def test_each_task_has_its_own_table_with_one_row_per_arm_in_order(self) -> None:
        tables = self.tables()
        for table in tables:
            arm_rows = table[2:]
            order = [next(a for a in ("baseline", "skill", "mcp") if a in ln) for ln in arm_rows]
            self.assertEqual(order, ["baseline", "skill", "mcp"])
        # the task names appear before their table, in first-seen order
        (start_hello, _), (start_gif, _) = self.tables_at()
        self.assertLess(self.heading_index("hello"), start_hello)
        self.assertLess(start_hello, self.heading_index("gif"))
        self.assertLess(self.heading_index("gif"), start_gif)

    def test_an_old_split_arm_renders_after_the_known_arms(self) -> None:
        # a results folder from before the split arm was dropped: its rows still render, last
        records = []
        for task, base in (("hello", 0.25), ("gif", 1.25)):
            records += [
                rec(task, "split", cost=base + 0.5, jzr=(True, True, False)),  # first in the input on purpose
                rec(task, "mcp", cost=base + 0.4),
                rec(task, "skill", cost=base + 0.3),
                rec(task, "baseline", cost=base),
            ]
        text = render_markdown(summarize(records))
        self.assertNotIn("None", text)
        tables, current = [], []
        for ln in text.splitlines():
            if ln.lstrip().startswith("|"):
                current.append(ln)
            elif current:
                tables.append(current)
                current = []
        if current:
            tables.append(current)
        with_split = [t for t in tables if any("| split " in ln for ln in t)]
        self.assertGreaterEqual(len(with_split), 2)  # at least the two per-task tables
        for table in with_split:
            order = [next(a for a in ("baseline", "skill", "mcp", "split") if f"| {a} " in ln) for ln in table[2:]]
            self.assertEqual(order, ["baseline", "skill", "mcp", "split"])

    def test_values_land_in_the_table_of_their_task(self) -> None:
        hello_table, gif_table = ("\n".join(t) for t in self.tables())
        self.assertIn("0.25", hello_table)
        self.assertNotIn("0.25", gif_table)
        self.assertIn("1.25", gif_table)
        self.assertNotIn("1.25", hello_table)

    def test_none_is_rendered_as_an_en_dash(self) -> None:
        rows = summarize(
            [
                rec("hello", "baseline", cost=0.5),
                rec(
                    "hello",
                    "skill",
                    cost=None,
                    input_tokens=None,
                    cache_creation=None,
                    cache_read=None,
                    output_tokens=None,
                    turns=None,
                    duration_ms=None,
                    passed=False,
                ),
            ]
        )
        text = render_markdown(rows)
        skill_row = next(ln for ln in text.splitlines() if "skill" in ln and ln.lstrip().startswith("|"))
        self.assertIn(self.NDASH, skill_row)
        self.assertNotIn("None", text)

    def test_no_none_text_anywhere_even_for_missing_ratio(self) -> None:
        # gif/skill has no baseline, so cost_vs_baseline is None
        text = render_markdown(summarize([rec("gif", "skill", cost=1.0)]))
        self.assertNotIn("None", text)
        self.assertIn(self.NDASH, text)

    def test_empty_rows_render_without_error(self) -> None:
        self.assertIsInstance(render_markdown([]), str)


if __name__ == "__main__":
    unittest.main()
