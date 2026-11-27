"""Offline tests for what round 2 adds to jzr_bench.report: range, minus hello, the written / re-read /
output split, the all-tasks table and the new markdown."""

from __future__ import annotations

import re
import statistics
import sys
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import usage_fixtures as uf  # noqa: E402
from jzr_bench.report import overall, render_markdown, summarize  # noqa: E402

NDASH = "–"

# Prices that make the split easy to read: a run with k tokens-units (k input, 2k cache write, 4k cache
# read, k output) has written = 5k, re-read = 2k and output = 4k dollars.
SIMPLE = {"input": 1.0, "cache_write": 2.0, "cache_read": 0.5, "output": 4.0}
SIMPLE_PRICES = {uf.MODEL: SIMPLE}

OVERALL_KEYS = {
    "arm",
    "tasks",
    "cost_usd",
    "cost_vs_baseline",
    "minus_hello_usd",
    "minus_hello_vs_baseline",
    "written_usd",
    "reread_usd",
    "output_usd",
    "unused_session_usd",
}

TASK_COLUMNS = [
    "arm",
    "passed / runs",
    "used jzr",
    "isolation fails",
    "cost (passed)",
    "range",
    "vs baseline",
    "minus hello",
    "minus hello vs baseline",
    "cost (all)",
    "written $",
    "re-read $",
    "output $",
    "input",
    "cache write",
    "cache read",
    "output",
    "turns",
    "time (s)",
]


def run(
    task: str,
    arm: str,
    cost: float | None,
    *,
    k: int | None = None,
    round: int = 1,
    passed: bool = True,
    isolation_ok: bool = True,
) -> dict[str, Any]:
    """A run record costing `cost`; with `k` it also has usage that SIMPLE prices split into 5k / 2k / 4k."""
    per_model = {}
    if k is not None:
        tokens = {"input": k, "cache_write": 2 * k, "cache_read": 4 * k, "output": k}
        per_model = {uf.MODEL: (tokens, SIMPLE)}
    return uf.usage_record(
        per_model, task=task, arm=arm, round=round, passed=passed, isolation_ok=isolation_ok, cost=cost
    )


def row_of(rows: list[dict], task: str, arm: str) -> dict:
    found = [r for r in rows if r["task"] == task and r["arm"] == arm]
    assert len(found) == 1, (task, arm, rows)
    return found[0]


def by_arm(rows: list[dict]) -> dict[str, dict]:
    result = {r["arm"]: r for r in rows}
    assert len(result) == len(rows), rows
    return result


class RangeTest(unittest.TestCase):
    def test_lowest_and_highest_cost_of_passed_isolated_runs(self) -> None:
        rows = summarize(
            [
                run("gif", "baseline", 0.0666, round=1),
                run("gif", "baseline", 0.0439, round=2),
                run("gif", "baseline", 0.05, round=3),
                run("gif", "baseline", 0.0001, round=4, passed=False),  # failed: left out
                run("gif", "baseline", 9.0, round=5, passed=False),
                run("gif", "baseline", 0.0002, round=6, isolation_ok=False),  # not isolated: left out
                run("gif", "baseline", 8.0, round=7, isolation_ok=False),
                run("gif", "baseline", None, round=8),  # no cost: skipped
            ]
        )
        row = row_of(rows, "gif", "baseline")
        self.assertAlmostEqual(row["min_cost_passed_usd"], 0.0439)
        self.assertAlmostEqual(row["max_cost_passed_usd"], 0.0666)
        self.assertAlmostEqual(row["median_cost_passed_usd"], 0.05)

    def test_one_passed_run_is_both_ends(self) -> None:
        row = summarize([run("gif", "baseline", 0.07)])[0]
        self.assertAlmostEqual(row["min_cost_passed_usd"], 0.07)
        self.assertAlmostEqual(row["max_cost_passed_usd"], 0.07)

    def test_none_without_a_passed_isolated_run_with_a_cost(self) -> None:
        rows = summarize(
            [
                run("a", "baseline", 1.0, passed=False),
                run("b", "baseline", 1.0, isolation_ok=False),
                run("c", "baseline", None),
            ]
        )
        for row in rows:
            self.assertIsNone(row["min_cost_passed_usd"], row["task"])
            self.assertIsNone(row["max_cost_passed_usd"], row["task"])

    def test_range_is_per_task_and_arm(self) -> None:
        rows = summarize(
            [
                run("gif", "baseline", 1.0),
                run("gif", "baseline", 3.0, round=2),
                run("gif", "skill", 7.0),
                run("xlsx", "baseline", 20.0),
            ]
        )
        self.assertEqual(row_of(rows, "gif", "baseline")["max_cost_passed_usd"], 3.0)
        self.assertEqual(row_of(rows, "gif", "skill")["min_cost_passed_usd"], 7.0)
        self.assertEqual(row_of(rows, "xlsx", "baseline")["min_cost_passed_usd"], 20.0)


class MinusHelloTest(unittest.TestCase):
    def test_the_median_cost_minus_the_same_arms_hello_median(self) -> None:
        rows = summarize(
            [
                run("hello", "baseline", 1.0),
                run("hello", "skill", 1.5),
                run("gif", "baseline", 4.0),
                run("gif", "skill", 3.0),
            ]
        )
        self.assertAlmostEqual(row_of(rows, "gif", "baseline")["cost_minus_hello_usd"], 3.0)
        self.assertAlmostEqual(row_of(rows, "gif", "skill")["cost_minus_hello_usd"], 1.5)  # 3.0 - 1.5, not 3.0 - 1.0

    def test_uses_passed_medians_on_both_sides(self) -> None:
        rows = summarize(
            [
                run("hello", "baseline", 1.0, round=1),
                run("hello", "baseline", 3.0, round=2),  # median 2.0
                run("hello", "baseline", 100.0, round=3, passed=False),
                run("hello", "baseline", 100.0, round=4, isolation_ok=False),
                run("gif", "baseline", 10.0, round=1),
                run("gif", "baseline", 14.0, round=2),  # median 12.0
                run("gif", "baseline", 0.5, round=3, passed=False),
            ]
        )
        self.assertAlmostEqual(row_of(rows, "gif", "baseline")["cost_minus_hello_usd"], 10.0)

    def test_ratio_to_the_baselines_minus_hello(self) -> None:
        rows = summarize(
            [
                run("hello", "baseline", 1.0),
                run("hello", "skill", 1.5),
                run("hello", "mcp", 2.0),
                run("gif", "baseline", 4.0),  # 3.0 over hello
                run("gif", "skill", 3.0),  # 1.5 over hello
                run("gif", "mcp", 8.0),  # 6.0 over hello
            ]
        )
        self.assertAlmostEqual(row_of(rows, "gif", "baseline")["minus_hello_vs_baseline"], 1.0)
        self.assertAlmostEqual(row_of(rows, "gif", "skill")["minus_hello_vs_baseline"], 0.5)
        self.assertAlmostEqual(row_of(rows, "gif", "mcp")["minus_hello_vs_baseline"], 2.0)

    def test_none_for_the_hello_task_itself(self) -> None:
        rows = summarize([run("hello", "baseline", 1.0), run("hello", "skill", 1.5)])
        for row in rows:
            self.assertIsNone(row["cost_minus_hello_usd"])
            self.assertIsNone(row["minus_hello_vs_baseline"])

    def test_none_when_the_summary_has_no_hello_at_all(self) -> None:
        rows = summarize([run("gif", "baseline", 4.0), run("gif", "skill", 3.0)])
        for row in rows:
            self.assertIsNone(row["cost_minus_hello_usd"])
            self.assertIsNone(row["minus_hello_vs_baseline"])

    def test_none_for_an_arm_whose_hello_has_no_passed_run(self) -> None:
        rows = summarize(
            [
                run("hello", "baseline", 1.0),
                run("hello", "skill", 1.5, passed=False),
                run("hello", "mcp", 2.0, isolation_ok=False),
                run("gif", "baseline", 4.0),
                run("gif", "skill", 3.0),
                run("gif", "mcp", 5.0),
                run("gif", "split", 5.0),  # no hello row for this arm at all
            ]
        )
        self.assertAlmostEqual(row_of(rows, "gif", "baseline")["cost_minus_hello_usd"], 3.0)
        for arm in ("skill", "mcp", "split"):
            with self.subTest(arm=arm):
                self.assertIsNone(row_of(rows, "gif", arm)["cost_minus_hello_usd"])
                self.assertIsNone(row_of(rows, "gif", arm)["minus_hello_vs_baseline"])

    def test_none_when_the_task_row_has_no_passed_cost(self) -> None:
        rows = summarize([run("hello", "baseline", 1.0), run("gif", "baseline", 4.0, passed=False)])
        self.assertIsNone(row_of(rows, "gif", "baseline")["cost_minus_hello_usd"])

    def test_ratio_none_when_the_baselines_minus_hello_is_zero(self) -> None:
        rows = summarize(
            [
                run("hello", "baseline", 1.0),
                run("hello", "skill", 1.0),
                run("gif", "baseline", 1.0),  # exactly hello: nothing on top
                run("gif", "skill", 3.0),
            ]
        )
        self.assertAlmostEqual(row_of(rows, "gif", "baseline")["cost_minus_hello_usd"], 0.0)
        self.assertAlmostEqual(row_of(rows, "gif", "skill")["cost_minus_hello_usd"], 2.0)
        self.assertIsNone(row_of(rows, "gif", "skill")["minus_hello_vs_baseline"])
        self.assertIsNone(row_of(rows, "gif", "baseline")["minus_hello_vs_baseline"])

    def test_ratio_none_when_the_baselines_minus_hello_is_negative(self) -> None:
        rows = summarize(
            [
                run("hello", "baseline", 2.0),
                run("hello", "skill", 1.0),
                run("gif", "baseline", 1.5),  # cheaper than its own hello
                run("gif", "skill", 3.0),
            ]
        )
        self.assertAlmostEqual(row_of(rows, "gif", "baseline")["cost_minus_hello_usd"], -0.5)
        self.assertIsNone(row_of(rows, "gif", "skill")["minus_hello_vs_baseline"])

    def test_an_arm_below_its_hello_gets_a_negative_ratio(self) -> None:
        rows = summarize(
            [
                run("hello", "baseline", 1.0),
                run("hello", "skill", 1.0),
                run("gif", "baseline", 4.0),  # +3.0
                run("gif", "skill", 0.5),  # -0.5
            ]
        )
        self.assertAlmostEqual(row_of(rows, "gif", "skill")["minus_hello_vs_baseline"], -0.5 / 3.0)

    def test_ratio_none_without_a_baseline_or_a_baseline_hello(self) -> None:
        rows = summarize(
            [
                run("hello", "skill", 1.0),
                run("gif", "skill", 3.0),  # no baseline for the task
                run("xlsx", "skill", 3.0),
                run("xlsx", "baseline", 5.0),  # baseline has no hello
            ]
        )
        self.assertAlmostEqual(row_of(rows, "gif", "skill")["cost_minus_hello_usd"], 2.0)
        self.assertIsNone(row_of(rows, "gif", "skill")["minus_hello_vs_baseline"])
        self.assertAlmostEqual(row_of(rows, "xlsx", "skill")["cost_minus_hello_usd"], 2.0)
        self.assertIsNone(row_of(rows, "xlsx", "skill")["minus_hello_vs_baseline"])

    def test_the_ratio_uses_the_baseline_of_the_same_task(self) -> None:
        rows = summarize(
            [
                run("hello", "baseline", 1.0),
                run("hello", "skill", 1.0),
                run("gif", "baseline", 3.0),  # +2
                run("gif", "skill", 2.0),  # +1
                run("xlsx", "baseline", 9.0),  # +8
                run("xlsx", "skill", 5.0),  # +4
            ]
        )
        self.assertAlmostEqual(row_of(rows, "gif", "skill")["minus_hello_vs_baseline"], 0.5)
        self.assertAlmostEqual(row_of(rows, "xlsx", "skill")["minus_hello_vs_baseline"], 0.5)

    def test_does_not_change_the_plain_cost_ratio(self) -> None:
        rows = summarize(
            [
                run("hello", "baseline", 1.0),
                run("hello", "skill", 1.0),
                run("gif", "baseline", 4.0),
                run("gif", "skill", 2.0),
            ]
        )
        self.assertAlmostEqual(row_of(rows, "gif", "skill")["cost_vs_baseline"], 0.5)


class SplitMediansTest(unittest.TestCase):
    def test_medians_of_written_reread_and_output_with_given_prices(self) -> None:
        rows = summarize(
            [
                run("gif", "baseline", 1.0, k=1, round=1),
                run("gif", "baseline", 1.0, k=3, round=2),  # medians over k=1,3 -> k=2
                run("gif", "baseline", 1.0, k=1000, round=3, passed=False),  # failed: left out
                run("gif", "baseline", 1.0, k=500, round=4, isolation_ok=False),  # not isolated: left out
            ],
            prices=SIMPLE_PRICES,
        )
        row = row_of(rows, "gif", "baseline")
        self.assertAlmostEqual(row["median_written_usd"], 10.0)
        self.assertAlmostEqual(row["median_reread_usd"], 4.0)
        self.assertAlmostEqual(row["median_output_usd"], 8.0)

    def test_given_prices_win_over_a_fit(self) -> None:
        # these records would fit PRICES_1H exactly; the explicit prices (twice as high) must be used
        records = uf.fit_records(task="gif", arm="baseline")
        doubled = {uf.MODEL: {kind: 2 * price for kind, price in uf.PRICES_1H.items()}}
        explicit = row_of(summarize(records, prices=doubled), "gif", "baseline")
        fitted = row_of(summarize(records), "gif", "baseline")
        self.assertAlmostEqual(explicit["median_output_usd"], 2 * fitted["median_output_usd"], places=9)

    def test_prices_fitted_from_the_records_when_none_are_given(self) -> None:
        records = uf.fit_records(task="gif", arm="baseline")
        row = row_of(summarize(records), "gif", "baseline")
        p = uf.PRICES_1H
        written = [r[0] * p["input"] + r[1] * p["cache_write_1h"] for r in uf.ROWS_4]
        reread = [r[2] * p["cache_read"] for r in uf.ROWS_4]
        output = [r[3] * p["output"] for r in uf.ROWS_4]
        self.assertAlmostEqual(row["median_written_usd"], statistics.median(written), places=9)
        self.assertAlmostEqual(row["median_reread_usd"], statistics.median(reread), places=9)
        self.assertAlmostEqual(row["median_output_usd"], statistics.median(output), places=9)

    def test_the_fit_uses_the_runs_of_every_task_and_arm(self) -> None:
        # three rows per task is not enough for a fit; all six together are
        records = [
            *uf.fit_records(task="gif", arm="baseline", rows=uf.ROWS_4[:3]),
            *uf.fit_records(task="xlsx", arm="skill", rows=uf.ROWS_4[3:]),
        ]
        rows = summarize(records)
        for row in rows:
            self.assertIsNotNone(row["median_written_usd"], row["task"])
            self.assertIsNotNone(row["median_output_usd"], row["task"])

    def test_the_fit_also_uses_failed_and_non_isolated_runs(self) -> None:
        records = uf.fit_records(task="gif", arm="baseline", rows=uf.ROWS_4[:2])
        records += uf.fit_records(task="gif", arm="baseline", rows=uf.ROWS_4[2:3], passed=False)
        records += uf.fit_records(task="gif", arm="baseline", rows=uf.ROWS_4[3:4], isolation_ok=False)
        row = row_of(summarize(records), "gif", "baseline")
        # only the two passed isolated runs are medianed, but the four runs together gave the prices
        p = uf.PRICES_1H
        expected = statistics.median(r[3] * p["output"] for r in uf.ROWS_4[:2])
        self.assertAlmostEqual(row["median_output_usd"], expected, places=9)

    def test_none_when_the_fit_is_not_unique(self) -> None:
        records = uf.fit_records(task="gif", arm="baseline", rows=[uf.ROWS_4[0]] * 5)
        row = summarize(records)[0]
        for key in ("median_written_usd", "median_reread_usd", "median_output_usd"):
            self.assertIsNone(row[key], key)
        self.assertIsNotNone(row["median_cost_passed_usd"])  # the plain costs are untouched

    def test_none_when_the_fit_is_not_exact(self) -> None:
        rows6 = [*uf.ROWS_4, (4, 2500, 7000, 40), (9, 900, 9000, 900)]
        records = uf.fit_records(task="gif", arm="baseline", rows=rows6)
        self.assertIsNotNone(summarize(records)[0]["median_written_usd"])  # exact data: fine
        records[3]["model_usage"][uf.MODEL]["costUSD"] += 0.001
        row = summarize(records)[0]
        for key in ("median_written_usd", "median_reread_usd", "median_output_usd"):
            self.assertIsNone(row[key], key)
        # explicit prices do not need the fit
        explicit = summarize(records, prices={uf.MODEL: uf.PRICES_1H})[0]
        self.assertIsNotNone(explicit["median_written_usd"])

    def test_none_without_usage_or_with_empty_prices(self) -> None:
        records = [run("gif", "baseline", 1.0), run("gif", "baseline", 2.0, round=2)]
        row = summarize(records)[0]
        self.assertIsNone(row["median_written_usd"])
        self.assertIsNone(row["median_reread_usd"])
        self.assertIsNone(row["median_output_usd"])
        with_usage = [run("gif", "baseline", 1.0, k=1), run("gif", "baseline", 2.0, k=2, round=2)]
        row = summarize(with_usage, prices={})[0]
        self.assertIsNone(row["median_written_usd"])
        self.assertIsNone(row["median_reread_usd"])
        self.assertIsNone(row["median_output_usd"])

    def test_none_when_every_passed_run_used_a_model_without_prices(self) -> None:
        records = [run("gif", "baseline", 1.0, k=1), run("gif", "baseline", 2.0, k=2, round=2)]
        row = summarize(records, prices={uf.OTHER_MODEL: SIMPLE})[0]
        self.assertIsNone(row["median_written_usd"])

    def test_none_when_no_run_passed(self) -> None:
        row = summarize([run("gif", "baseline", 1.0, k=1, passed=False)], prices=SIMPLE_PRICES)[0]
        self.assertIsNone(row["median_written_usd"])
        self.assertIsNone(row["median_reread_usd"])
        self.assertIsNone(row["median_output_usd"])

    def test_prices_do_not_change_the_other_columns(self) -> None:
        records = [run("gif", "baseline", 1.0, k=1), run("gif", "baseline", 3.0, k=2, round=2)]
        plain = summarize(records, prices={})[0]
        priced = summarize(records, prices=SIMPLE_PRICES)[0]
        for key in plain:
            if key not in ("median_written_usd", "median_reread_usd", "median_output_usd"):
                self.assertEqual(plain[key], priced[key], key)


def three_by_three(overrides: dict[tuple[str, str], list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    """hello, gif, xlsx in baseline, skill and mcp. Costs and usage sizes `k` as in the comments.

    cost:  hello b 1.0  s 1.5  m 2.0     gif b 4.0 s 3.0 m 5.0      xlsx b 6.0 s 4.0 m 8.0
    k:     hello 1 each                  gif b 1 s 2 m 3            xlsx b 10 s 20 m 30
    `overrides` maps (task, arm) to the records that replace that cell.
    """
    costs = {
        "hello": {"baseline": 1.0, "skill": 1.5, "mcp": 2.0},
        "gif": {"baseline": 4.0, "skill": 3.0, "mcp": 5.0},
        "xlsx": {"baseline": 6.0, "skill": 4.0, "mcp": 8.0},
    }
    sizes = {
        "hello": {"baseline": 1, "skill": 1, "mcp": 1},
        "gif": {"baseline": 1, "skill": 2, "mcp": 3},
        "xlsx": {"baseline": 10, "skill": 20, "mcp": 30},
    }
    overrides = overrides or {}
    records: list[dict[str, Any]] = []
    for task, arms in costs.items():
        for arm, cost in arms.items():
            if (task, arm) in overrides:
                records += overrides[(task, arm)]
            else:
                records.append(run(task, arm, cost, k=sizes[task][arm]))
    return records


class OverallTest(unittest.TestCase):
    def overall_by_arm(self, records: list[dict], **kwargs: Any) -> dict[str, dict]:
        return by_arm(overall(summarize(records, prices=SIMPLE_PRICES), **kwargs))

    def test_one_row_per_arm_with_the_documented_keys(self) -> None:
        result = overall(summarize(three_by_three(), prices=SIMPLE_PRICES))
        self.assertEqual({r["arm"] for r in result}, {"baseline", "skill", "mcp"})
        self.assertEqual(len(result), 3)
        for row in result:
            self.assertEqual(set(row), OVERALL_KEYS)

    def test_sums_the_median_passed_cost_of_every_task_but_hello(self) -> None:
        result = self.overall_by_arm(three_by_three())
        for arm, expected in {"baseline": 10.0, "skill": 7.0, "mcp": 13.0}.items():
            self.assertAlmostEqual(result[arm]["cost_usd"], expected, msg=arm)
            self.assertEqual(sorted(result[arm]["tasks"]), ["gif", "xlsx"], arm)

    def test_an_old_split_arm_is_a_row_after_the_known_arms(self) -> None:
        records = [
            run("gif", arm, cost) for arm, cost in (("split", 6.0), ("mcp", 5.0), ("skill", 3.0), ("baseline", 4.0))
        ]
        result = overall(summarize(records, prices=SIMPLE_PRICES))
        self.assertEqual([r["arm"] for r in result], ["baseline", "skill", "mcp", "split"])
        self.assertAlmostEqual(result[-1]["cost_usd"], 6.0)

    def test_ratio_to_the_baselines_sum(self) -> None:
        result = self.overall_by_arm(three_by_three())
        self.assertAlmostEqual(result["baseline"]["cost_vs_baseline"], 1.0)
        self.assertAlmostEqual(result["skill"]["cost_vs_baseline"], 0.7)
        self.assertAlmostEqual(result["mcp"]["cost_vs_baseline"], 1.3)

    def test_minus_hello_sums_and_ratio(self) -> None:
        result = self.overall_by_arm(three_by_three())
        # baseline (4-1)+(6-1)=8, skill (3-1.5)+(4-1.5)=4, mcp (5-2)+(8-2)=9
        self.assertAlmostEqual(result["baseline"]["minus_hello_usd"], 8.0)
        self.assertAlmostEqual(result["skill"]["minus_hello_usd"], 4.0)
        self.assertAlmostEqual(result["mcp"]["minus_hello_usd"], 9.0)
        self.assertAlmostEqual(result["baseline"]["minus_hello_vs_baseline"], 1.0)
        self.assertAlmostEqual(result["skill"]["minus_hello_vs_baseline"], 0.5)
        self.assertAlmostEqual(result["mcp"]["minus_hello_vs_baseline"], 1.125)

    def test_sums_of_the_three_parts(self) -> None:
        result = self.overall_by_arm(three_by_three())
        # written 5k, re-read 2k, output 4k summed over gif and xlsx: k is 1+10, 2+20 and 3+30
        for arm, k in {"baseline": 11, "skill": 22, "mcp": 33}.items():
            self.assertAlmostEqual(result[arm]["written_usd"], 5.0 * k, msg=arm)
            self.assertAlmostEqual(result[arm]["reread_usd"], 2.0 * k, msg=arm)
            self.assertAlmostEqual(result[arm]["output_usd"], 4.0 * k, msg=arm)

    def test_unused_session_is_the_arms_hello_minus_the_baselines(self) -> None:
        result = self.overall_by_arm(three_by_three())
        self.assertAlmostEqual(result["baseline"]["unused_session_usd"], 0.0)
        self.assertAlmostEqual(result["skill"]["unused_session_usd"], 0.5)
        self.assertAlmostEqual(result["mcp"]["unused_session_usd"], 1.0)

    def test_only_passed_isolated_runs_count(self) -> None:
        extra = [
            run("gif", "skill", 99.0, k=1000, round=2, passed=False),
            run("gif", "skill", 98.0, k=1000, round=3, isolation_ok=False),
            run("hello", "skill", 97.0, k=1000, round=2, passed=False),
        ]
        records = three_by_three()
        records += extra
        result = self.overall_by_arm(records)
        self.assertAlmostEqual(result["skill"]["cost_usd"], 7.0)
        self.assertAlmostEqual(result["skill"]["written_usd"], 110.0)
        self.assertAlmostEqual(result["skill"]["unused_session_usd"], 0.5)

    def test_hello_is_never_summed_even_when_every_arm_passed_it(self) -> None:
        result = self.overall_by_arm(three_by_three())
        for row in result.values():
            self.assertNotIn("hello", row["tasks"])

    def test_a_task_is_dropped_for_all_arms_when_one_arm_has_no_passed_run(self) -> None:
        records = three_by_three({("xlsx", "skill"): [run("xlsx", "skill", 4.0, k=20, passed=False)]})
        records += [
            run("pdf", "baseline", 1.0, k=1),
            run("pdf", "mcp", 1.0, k=1),
            run("pdf", "skill", 1.0, k=1, passed=False),
        ]
        result = self.overall_by_arm(records)
        for arm, expected in {"baseline": 4.0, "skill": 3.0, "mcp": 5.0}.items():  # gif only
            self.assertEqual(result[arm]["tasks"], ["gif"], arm)
            self.assertAlmostEqual(result[arm]["cost_usd"], expected, msg=arm)
        self.assertAlmostEqual(result["mcp"]["cost_vs_baseline"], 1.25)

    def test_a_task_whose_only_passed_runs_were_not_isolated_is_dropped(self) -> None:
        records = three_by_three({("gif", "mcp"): [run("gif", "mcp", 5.0, k=3, isolation_ok=False)]})
        result = self.overall_by_arm(records)
        for row in result.values():
            self.assertEqual(row["tasks"], ["xlsx"])
        self.assertAlmostEqual(result["mcp"]["cost_usd"], 8.0)

    def test_sums_are_none_when_one_of_their_parts_is(self) -> None:
        # xlsx of the mcp arm has no usage: its parts are unknown, its cost is not
        records = three_by_three({("xlsx", "mcp"): [run("xlsx", "mcp", 8.0)]})
        result = self.overall_by_arm(records)
        for key in ("written_usd", "reread_usd", "output_usd"):
            self.assertIsNone(result["mcp"][key], key)
            self.assertIsNotNone(result["skill"][key], key)
            self.assertIsNotNone(result["baseline"][key], key)
        self.assertAlmostEqual(result["mcp"]["cost_usd"], 13.0)
        self.assertAlmostEqual(result["mcp"]["minus_hello_usd"], 9.0)

    def test_no_prices_no_parts_but_the_costs_stay(self) -> None:
        result = by_arm(overall(summarize(three_by_three(), prices={})))
        for row in result.values():
            for key in ("written_usd", "reread_usd", "output_usd"):
                self.assertIsNone(row[key], key)
            self.assertIsNotNone(row["cost_usd"])
            self.assertIsNotNone(row["minus_hello_usd"])

    def test_minus_hello_is_none_for_an_arm_without_a_passed_hello(self) -> None:
        records = three_by_three({("hello", "mcp"): [run("hello", "mcp", 2.0, k=1, passed=False)]})
        result = self.overall_by_arm(records)
        self.assertIsNone(result["mcp"]["minus_hello_usd"])
        self.assertIsNone(result["mcp"]["minus_hello_vs_baseline"])
        self.assertIsNone(result["mcp"]["unused_session_usd"])
        self.assertAlmostEqual(result["mcp"]["cost_usd"], 13.0)
        self.assertAlmostEqual(result["skill"]["minus_hello_usd"], 4.0)  # the others are unaffected
        self.assertAlmostEqual(result["skill"]["unused_session_usd"], 0.5)

    def test_without_any_hello_the_hello_based_sums_are_none(self) -> None:
        records = [r for r in three_by_three() if r["task"] != "hello"]
        result = self.overall_by_arm(records)
        for row in result.values():
            self.assertIsNone(row["minus_hello_usd"])
            self.assertIsNone(row["minus_hello_vs_baseline"])
            self.assertIsNone(row["unused_session_usd"])
            self.assertIsNotNone(row["cost_usd"])

    def test_unused_session_is_none_without_a_baseline_hello(self) -> None:
        records = [r for r in three_by_three() if not (r["task"] == "hello" and r["arm"] == "baseline")]
        result = self.overall_by_arm(records)
        self.assertIsNone(result["skill"]["unused_session_usd"])

    def test_minus_hello_ratio_none_when_the_baselines_sum_is_not_positive(self) -> None:
        records = three_by_three(
            {
                ("gif", "baseline"): [run("gif", "baseline", 1.0, k=1)],  # = hello: +0
                ("xlsx", "baseline"): [run("xlsx", "baseline", 1.0, k=10)],  # = hello: +0
            }
        )
        result = self.overall_by_arm(records)
        self.assertAlmostEqual(result["baseline"]["minus_hello_usd"], 0.0)
        self.assertIsNone(result["skill"]["minus_hello_vs_baseline"])
        self.assertIsNone(result["baseline"]["minus_hello_vs_baseline"])

    def test_empty_when_nothing_qualifies(self) -> None:
        self.assertEqual(overall([]), [])
        only_hello = summarize([run("hello", "baseline", 1.0), run("hello", "skill", 1.5)])
        self.assertEqual(overall(only_hello), [])
        # every task has an arm without a passed run
        records = [
            run("gif", "baseline", 1.0),
            run("gif", "skill", 1.0, passed=False),
            run("xlsx", "baseline", 1.0, passed=False),
            run("xlsx", "skill", 1.0),
        ]
        self.assertEqual(overall(summarize(records)), [])

    def test_does_not_change_the_rows_it_is_given(self) -> None:
        rows = summarize(three_by_three(), prices=SIMPLE_PRICES)
        before = [dict(r) for r in rows]
        overall(rows)
        self.assertEqual(rows, before)


class RenderTablesTest(unittest.TestCase):
    """The per-task tables."""

    def setUp(self) -> None:
        records = [
            run("hello", "baseline", 0.01),
            run("hello", "skill", 0.01),
            run("hello", "mcp", 0.01),
            run("gif", "baseline", 0.04, k=2, round=1),
            run("gif", "baseline", 0.06, k=2, round=2),
            run("gif", "skill", 0.0439, round=1),
            run("gif", "skill", 0.0666, round=2),
            run("gif", "mcp", 0.5, passed=False),
        ]
        self.rows = summarize(records, prices=SIMPLE_PRICES)
        self.text = render_markdown(self.rows, prices=SIMPLE_PRICES)
        self.lines = self.text.splitlines()

    def heading(self, task: str) -> int:
        return next(i for i, ln in enumerate(self.lines) if re.match(rf"^#+\s+{re.escape(task)}\b", ln))

    def table(self, task: str) -> list[list[str]]:
        """The cells of the first table after the task's heading, header row first, separator row dropped."""
        start = self.heading(task)
        lines: list[str] = []
        for ln in self.lines[start + 1 :]:
            if ln.lstrip().startswith("|"):
                lines.append(ln)
            elif lines:
                break
        cells = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
        return [cells[0], *cells[2:]]

    def cell(self, task: str, arm: str, column: str) -> str:
        table = self.table(task)
        index = table[0].index(column)
        row = next(r for r in table[1:] if r[0] == arm)
        return row[index]

    def test_columns_in_the_documented_order(self) -> None:
        for task in ("hello", "gif"):
            with self.subTest(task=task):
                self.assertEqual(self.table(task)[0], TASK_COLUMNS)

    def test_every_row_has_a_cell_per_column(self) -> None:
        for task in ("hello", "gif"):
            for row in self.table(task):
                self.assertEqual(len(row), len(TASK_COLUMNS), row)

    def test_range_is_low_en_dash_high(self) -> None:
        self.assertEqual(self.cell("gif", "skill", "range"), f"$0.0439{NDASH}$0.0666")
        self.assertEqual(self.cell("gif", "baseline", "range"), f"$0.0400{NDASH}$0.0600")
        self.assertNotIn("-$", self.cell("gif", "skill", "range"))  # an en dash, not a hyphen

    def test_range_is_an_en_dash_without_a_passed_run(self) -> None:
        self.assertEqual(self.cell("gif", "mcp", "range"), NDASH)

    def test_minus_hello_cells(self) -> None:
        self.assertEqual(self.cell("gif", "baseline", "minus hello"), "$0.0400")  # 0.05 - 0.01
        self.assertEqual(self.cell("gif", "baseline", "minus hello vs baseline"), "1.00x")
        self.assertEqual(self.cell("gif", "skill", "minus hello vs baseline"), "1.13x")  # 0.04525 / 0.04
        self.assertEqual(self.cell("gif", "mcp", "minus hello"), NDASH)
        self.assertEqual(self.cell("gif", "mcp", "minus hello vs baseline"), NDASH)

    def test_minus_hello_is_a_dash_for_hello_itself(self) -> None:
        for arm in ("baseline", "skill", "mcp"):
            self.assertEqual(self.cell("hello", arm, "minus hello"), NDASH, arm)
            self.assertEqual(self.cell("hello", arm, "minus hello vs baseline"), NDASH, arm)

    def test_the_three_parts(self) -> None:
        self.assertEqual(self.cell("gif", "baseline", "written $"), "$10.0000")  # k=2: 5k
        self.assertEqual(self.cell("gif", "baseline", "re-read $"), "$4.0000")
        self.assertEqual(self.cell("gif", "baseline", "output $"), "$8.0000")

    def test_the_parts_are_dashes_without_a_split(self) -> None:
        for column in ("written $", "re-read $", "output $"):
            self.assertEqual(self.cell("gif", "skill", column), NDASH, column)

    def test_the_old_columns_keep_their_values(self) -> None:
        self.assertEqual(self.cell("gif", "baseline", "cost (passed)"), "$0.0500")
        self.assertEqual(self.cell("gif", "baseline", "passed / runs"), "2 / 2")
        self.assertEqual(self.cell("gif", "mcp", "passed / runs"), "0 / 1")
        self.assertEqual(self.cell("gif", "mcp", "cost (all)"), "$0.5000")

    def test_no_none_in_the_text(self) -> None:
        self.assertNotIn("None", self.text)


class RenderAllTasksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = summarize(three_by_three(), prices=SIMPLE_PRICES)
        self.text = render_markdown(self.rows, prices=SIMPLE_PRICES)
        self.lines = self.text.splitlines()

    def section(self) -> list[str]:
        """Lines from the all-tasks heading to the next heading."""
        start = next(i for i, ln in enumerate(self.lines) if ln.startswith("#") and "All tasks" in ln)
        end = next((i for i in range(start + 1, len(self.lines)) if self.lines[i].startswith("#")), len(self.lines))
        return self.lines[start:end]

    def arm_cells(self, arm: str) -> list[str]:
        for ln in self.section():
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if ln.lstrip().startswith("|") and cells[0] == arm:
                return cells
        raise AssertionError(f"no row for {arm} in the All tasks section")

    def test_the_section_comes_before_every_per_task_table(self) -> None:
        start = next(i for i, ln in enumerate(self.lines) if ln.startswith("#") and "All tasks" in ln)
        for task in ("hello", "gif", "xlsx"):
            heading = next(i for i, ln in enumerate(self.lines) if re.match(rf"^#+\s+{task}\b", ln))
            self.assertLess(start, heading, task)

    def test_the_section_names_the_summed_tasks(self) -> None:
        body = "\n".join(self.section())
        self.assertIn("gif", body)
        self.assertIn("xlsx", body)

    def test_one_row_per_arm(self) -> None:
        for arm in ("baseline", "skill", "mcp"):
            self.arm_cells(arm)
        rows = [ln for ln in self.section() if ln.lstrip().startswith("|")]
        self.assertEqual(len(rows), 2 + 3)  # header, separator, three arms

    def test_values_of_the_skill_row(self) -> None:
        cells = self.arm_cells("skill")
        for expected in ("$7.0000", "0.70x", "$4.0000", "0.50x", "$110.0000", "$44.0000", "$88.0000", "$0.5000"):
            self.assertIn(expected, cells)

    def test_values_of_the_baseline_row(self) -> None:
        cells = self.arm_cells("baseline")
        for expected in ("$10.0000", "1.00x", "$8.0000", "$55.0000", "$22.0000", "$44.0000", "$0.0000"):
            self.assertIn(expected, cells)

    def test_unknown_sums_are_dashes(self) -> None:
        records = three_by_three({("xlsx", "mcp"): [run("xlsx", "mcp", 8.0)]})
        text = render_markdown(summarize(records, prices=SIMPLE_PRICES), prices=SIMPLE_PRICES)
        self.lines = text.splitlines()
        cells = self.arm_cells("mcp")
        self.assertIn("$13.0000", cells)
        self.assertGreaterEqual(cells.count(NDASH), 3)  # written, re-read and output
        self.assertNotIn("None", text)

    def test_absent_when_no_task_qualifies(self) -> None:
        only_hello = summarize([run("hello", "baseline", 1.0), run("hello", "skill", 1.5)])
        text = render_markdown(only_hello)
        self.assertNotIn("All tasks", text)
        self.assertEqual(overall(only_hello), [])
        self.assertNotIn("All tasks", render_markdown([]))

    def test_present_exactly_when_overall_is_not_empty(self) -> None:
        rows = summarize([run("gif", "baseline", 1.0), run("gif", "skill", 1.0, passed=False)])
        self.assertEqual(overall(rows), [])
        self.assertNotIn("All tasks", render_markdown(rows))
        self.assertIn("All tasks", self.text)


class RenderPricesAndMetaTest(unittest.TestCase):
    PRICES = {
        uf.MODEL: {"input": 3.25e-6, "cache_write": 3.75e-6, "cache_read": 0.35e-6, "output": 15.5e-6},
    }

    def setUp(self) -> None:
        self.rows = summarize([run("hello", "baseline", 1.0), run("hello", "skill", 1.5)])

    @staticmethod
    def numbers(text: str) -> set[float]:
        return {float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)}

    def test_prices_are_listed_per_million_tokens(self) -> None:
        plain = render_markdown(self.rows)
        text = render_markdown(self.rows, prices=self.PRICES)
        self.assertIn(uf.MODEL, text)
        self.assertRegex(text.lower(), r"million|per m\b|/\s*m\b|mtok|\b1m\b")
        new_numbers = self.numbers(text) - self.numbers(plain)
        for per_million in (3.25, 3.75, 0.35, 15.5):
            self.assertIn(per_million, new_numbers)
        for per_token in ("e-06", "e-6"):
            self.assertNotIn(per_token, text)

    def test_every_fitted_kind_and_model_is_listed(self) -> None:
        prices = {
            uf.MODEL: {"input": 2e-6, "cache_write_1h": 4.5e-6, "cache_read": 0.15e-6, "output": 10.5e-6},
            uf.OTHER_MODEL: {
                "input": 1.1e-6,
                "cache_write_5m": 1.4e-6,
                "cache_write_1h": 2.2e-6,
                "cache_read": 0.12e-6,
                "output": 5.5e-6,
            },
        }
        text = render_markdown(self.rows, prices=prices)
        self.assertIn(uf.MODEL, text)
        self.assertIn(uf.OTHER_MODEL, text)
        found = self.numbers(text) - self.numbers(render_markdown(self.rows))
        for per_million in (4.5, 0.15, 10.5, 1.1, 1.4, 2.2, 0.12, 5.5):
            self.assertIn(per_million, found)

    def test_an_empty_dict_says_the_split_was_left_out(self) -> None:
        text = render_markdown(self.rows, prices={})
        self.assertIn("left out", text.lower())
        self.assertNotIn("left out", render_markdown(self.rows).lower())

    def test_prices_none_says_nothing_about_prices(self) -> None:
        text = render_markdown(self.rows, prices=None)
        self.assertNotIn("left out", text.lower())
        self.assertNotIn("million", text.lower())
        self.assertNotIn(uf.MODEL, text)
        self.assertEqual(text, render_markdown(self.rows))

    def test_real_prices_do_not_say_left_out(self) -> None:
        self.assertNotIn("left out", render_markdown(self.rows, prices=self.PRICES).lower())

    def test_sources_print_one_line_per_task_with_its_folder(self) -> None:
        meta = {"sources": {"gif": "20261126T131420Z", "hello": "20261127T070331Z"}}
        lines = render_markdown(self.rows, meta).splitlines()
        gif = [ln for ln in lines if "20261126T131420Z" in ln]
        hello = [ln for ln in lines if "20261127T070331Z" in ln]
        self.assertEqual(len(gif), 1)
        self.assertEqual(len(hello), 1)
        self.assertIn("gif", gif[0])
        self.assertNotIn("20261127T070331Z", gif[0])
        self.assertIn("hello", hello[0])
        self.assertNotEqual(gif[0], hello[0])

    def test_no_sources_no_folder_lines(self) -> None:
        text = render_markdown(self.rows, {"model": "sonnet"})
        self.assertNotIn("sources", text.lower())

    def test_private_tmp_is_shown(self) -> None:
        shown = {}
        for value in (True, False):
            text = render_markdown(self.rows, {"private_tmp": value})
            lines = [ln for ln in text.splitlines() if re.search(r"private[ _]tmp", ln, re.IGNORECASE)]
            self.assertEqual(len(lines), 1, text)
            shown[value] = lines[0].lower()
        self.assertIn("true", shown[True])
        self.assertNotIn("false", shown[True])
        self.assertIn("false", shown[False])
        self.assertNotIn("true", shown[False])

    def test_no_private_tmp_line_when_meta_has_none(self) -> None:
        text = render_markdown(self.rows, {"model": "sonnet"})
        self.assertNotRegex(text, r"(?i)private[ _]tmp")

    def test_old_meta_keys_are_still_shown(self) -> None:
        text = render_markdown(self.rows, {"model": "sonnet", "seed": 7, "rounds": 3, "private_tmp": True})
        self.assertIn("model: sonnet", text)
        self.assertIn("seed: 7", text)
        self.assertIn("rounds: 3", text)


if __name__ == "__main__":
    unittest.main()
