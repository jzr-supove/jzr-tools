"""Offline tests for jzr_bench.plan: interleaved, seeded run plan."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench.arms import ARMS as ALL_ARMS  # noqa: E402
from jzr_bench.plan import PlannedRun, plan_runs  # noqa: E402

TASKS = ["hello", "gif"]
# The pilot design: 2 tasks x 3 arms x 3 rounds = 18 runs. Not ARMS, which also has the split arm.
ARMS = ["baseline", "skill", "mcp"]


class PlanRunsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = plan_runs(TASKS, ARMS, 3, 1234)

    def test_pilot_size_is_18(self) -> None:
        self.assertEqual(len(self.plan), 2 * 3 * 3)
        self.assertTrue(all(isinstance(p, PlannedRun) for p in self.plan))

    def test_run_id_format(self) -> None:
        for p in self.plan:
            self.assertEqual(p.run_id, f"r{p.round:02d}-{p.task}-{p.arm}")
        self.assertTrue(all(re.fullmatch(r"r0[123]-(hello|gif)-(baseline|skill|mcp)", p.run_id) for p in self.plan))

    def test_run_ids_are_unique(self) -> None:
        self.assertEqual(len({p.run_id for p in self.plan}), 18)

    def test_rounds_are_one_based_and_round_major(self) -> None:
        self.assertEqual([p.round for p in self.plan], [1] * 6 + [2] * 6 + [3] * 6)

    def test_tasks_in_the_given_order_within_a_round(self) -> None:
        for r in range(3):
            block = self.plan[r * 6 : (r + 1) * 6]
            self.assertEqual([p.task for p in block], ["hello"] * 3 + ["gif"] * 3)

    def test_task_order_follows_the_argument(self) -> None:
        plan = plan_runs(["gif", "hello"], ARMS, 1, 1)
        self.assertEqual([p.task for p in plan], ["gif"] * 3 + ["hello"] * 3)

    def test_every_arm_once_per_task_per_round(self) -> None:
        for rnd in (1, 2, 3):
            for task in TASKS:
                arms = [p.arm for p in self.plan if p.round == rnd and p.task == task]
                self.assertEqual(sorted(arms), sorted(ARMS), (rnd, task))

    def test_same_inputs_same_plan(self) -> None:
        again = plan_runs(TASKS, ARMS, 3, 1234)
        self.assertEqual(self.plan, again)
        self.assertEqual([p.run_id for p in self.plan], [p.run_id for p in again])

    def test_different_seeds_give_different_shuffles(self) -> None:
        # 6 orderings per (round, task) and 6 blocks: identical plans across 20 seeds would be a fluke
        # of probability ~ 6**-120, so this only passes if the seed really drives the order.
        orders = {tuple(p.arm for p in plan_runs(TASKS, ARMS, 3, seed)) for seed in range(20)}
        self.assertGreater(len(orders), 1)

    def test_arm_order_is_shuffled_not_fixed(self) -> None:
        # Across many rounds and both tasks the arm order must not be one fixed order.
        plan = plan_runs(TASKS, ARMS, 12, 7)
        orders = {
            tuple(p.arm for p in plan if p.round == rnd and p.task == task) for rnd in range(1, 13) for task in TASKS
        }
        self.assertGreater(len(orders), 1)

    def test_all_arm_orders_stay_permutations_for_many_seeds(self) -> None:
        for seed in range(25):
            plan = plan_runs(TASKS, ARMS, 2, seed)
            for rnd in (1, 2):
                for task in TASKS:
                    arms = [p.arm for p in plan if p.round == rnd and p.task == task]
                    self.assertEqual(sorted(arms), sorted(ARMS), (seed, rnd, task))

    def test_zero_rounds_is_an_empty_plan(self) -> None:
        self.assertEqual(plan_runs(TASKS, ARMS, 0, 1), [])

    def test_subset_of_arms_and_tasks(self) -> None:
        plan = plan_runs(["hello"], ["baseline", "mcp"], 2, 5)
        self.assertEqual(len(plan), 4)
        self.assertEqual({p.arm for p in plan}, {"baseline", "mcp"})
        self.assertEqual({p.task for p in plan}, {"hello"})
        self.assertEqual([p.round for p in plan], [1, 1, 2, 2])

    def test_plan_with_every_arm_includes_split_once_per_task_per_round(self) -> None:
        plan = plan_runs(TASKS, ALL_ARMS, 2, 99)
        self.assertEqual(len(plan), 2 * len(ALL_ARMS) * 2)
        for rnd in (1, 2):
            for task in TASKS:
                arms = [p.arm for p in plan if p.round == rnd and p.task == task]
                self.assertCountEqual(arms, ["baseline", "skill", "mcp", "split"])
        self.assertIn("r01-hello-split", {p.run_id for p in plan})

    def test_planned_run_fields(self) -> None:
        run = PlannedRun(run_id="r01-hello-baseline", round=1, task="hello", arm="baseline")
        self.assertEqual((run.round, run.task, run.arm), (1, "hello", "baseline"))


if __name__ == "__main__":
    unittest.main()
