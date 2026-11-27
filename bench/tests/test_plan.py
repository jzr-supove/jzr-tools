"""Offline tests for jzr_bench.plan: interleaved, seeded run plan."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench import arms as arms_module  # noqa: E402
from jzr_bench.arms import ARMS as ALL_ARMS  # noqa: E402
from jzr_bench.plan import PlannedRun, plan_runs  # noqa: E402

TASKS = ["hello", "gif"]
# The pilot design: 2 tasks x 3 arms x 3 rounds = 18 runs.
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

    def test_plan_with_every_arm_has_the_three_arms_once_per_task_per_round(self) -> None:
        plan = plan_runs(TASKS, ALL_ARMS, 2, 99)
        self.assertEqual(len(plan), 2 * 3 * 2)
        for rnd in (1, 2):
            for task in TASKS:
                arms = [p.arm for p in plan if p.round == rnd and p.task == task]
                self.assertCountEqual(arms, ["baseline", "skill", "mcp"])
        self.assertNotIn("r01-hello-split", {p.run_id for p in plan})

    def test_planned_run_fields(self) -> None:
        run = PlannedRun(run_id="r01-hello-baseline", round=1, task="hello", arm="baseline")
        self.assertEqual((run.round, run.task, run.arm), (1, "hello", "baseline"))


class OldPlanUnchangedTest(unittest.TestCase):
    """Without bare arms the plan is exactly the one the harness made before the bare machine existed.

    The ids below were taken from the plan_runs of 2026-11-27 (before round 3), so they pin both the ids and the
    shuffle.
    """

    OLD_1234 = [
        *("r01-hello-mcp", "r01-hello-skill", "r01-hello-baseline", "r01-gif-mcp", "r01-gif-skill", "r01-gif-baseline"),
        *("r02-hello-baseline", "r02-hello-skill", "r02-hello-mcp", "r02-gif-mcp", "r02-gif-skill", "r02-gif-baseline"),
        *("r03-hello-baseline", "r03-hello-skill", "r03-hello-mcp", "r03-gif-skill", "r03-gif-baseline", "r03-gif-mcp"),
    ]
    OLD_0 = [
        *("r01-gif-baseline", "r01-gif-skill", "r01-compress-skill", "r01-compress-baseline"),
        *("r02-gif-baseline", "r02-gif-skill", "r02-compress-skill", "r02-compress-baseline"),
    ]

    def test_the_old_call_gives_the_old_ids_in_the_old_order(self) -> None:
        self.assertEqual([p.run_id for p in plan_runs(TASKS, ARMS, 3, 1234)], self.OLD_1234)
        self.assertEqual([p.run_id for p in plan_runs(["gif", "compress"], ["baseline", "skill"], 2, 0)], self.OLD_0)

    def test_empty_bare_arms_give_exactly_that_plan(self) -> None:
        for bare_arms in ((), []):
            with self.subTest(bare_arms=bare_arms):
                plan = plan_runs(TASKS, ARMS, 3, 1234, bare_arms=bare_arms)
                self.assertEqual([p.run_id for p in plan], self.OLD_1234)
                self.assertEqual(plan, plan_runs(TASKS, ARMS, 3, 1234))

    def test_old_runs_are_on_the_full_machine(self) -> None:
        self.assertEqual({p.machine for p in plan_runs(TASKS, ARMS, 3, 1234)}, {"full"})
        self.assertEqual(PlannedRun("r01-hello-baseline", 1, "hello", "baseline").machine, "full")

    def test_machines_are_full_and_bare(self) -> None:
        self.assertEqual(tuple(arms_module.MACHINES), ("full", "bare"))


class BarePlanTest(unittest.TestCase):
    BARE = ["baseline", "skill"]

    def setUp(self) -> None:
        self.plan = plan_runs(TASKS, ARMS, 3, 1234, bare_arms=self.BARE)

    def pairs(self, plan: list[PlannedRun], rnd: int, task: str) -> list[tuple[str, str]]:
        return [(p.machine, p.arm) for p in plan if p.round == rnd and p.task == task]

    def test_size_is_every_machine_arm_pair_per_round_and_task(self) -> None:
        self.assertEqual(len(self.plan), 2 * (3 + 2) * 3)

    def test_every_pair_once_per_round_and_task(self) -> None:
        expected = sorted([("full", a) for a in ARMS] + [("bare", a) for a in self.BARE])
        for rnd in (1, 2, 3):
            for task in TASKS:
                self.assertEqual(sorted(self.pairs(self.plan, rnd, task)), expected, (rnd, task))

    def test_bare_ids_end_in_bare_and_full_ids_are_unchanged(self) -> None:
        for p in self.plan:
            suffix = "-bare" if p.machine == "bare" else ""
            self.assertEqual(p.run_id, f"r{p.round:02d}-{p.task}-{p.arm}{suffix}")
        self.assertIn("r01-hello-skill-bare", {p.run_id for p in self.plan})
        self.assertIn("r01-hello-skill", {p.run_id for p in self.plan})
        self.assertNotIn("r01-hello-mcp-bare", {p.run_id for p in self.plan})

    def test_run_ids_are_unique(self) -> None:
        self.assertEqual(len({p.run_id for p in self.plan}), len(self.plan))

    def test_rounds_and_tasks_stay_in_blocks_in_the_given_order(self) -> None:
        keys = [(p.round, TASKS.index(p.task)) for p in self.plan]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual([p.round for p in self.plan], [1] * 10 + [2] * 10 + [3] * 10)

    def test_same_inputs_same_plan(self) -> None:
        self.assertEqual(self.plan, plan_runs(TASKS, ARMS, 3, 1234, bare_arms=self.BARE))
        self.assertEqual(self.plan, plan_runs(TASKS, ARMS, 3, 1234, bare_arms=tuple(self.BARE)))

    def test_the_seed_drives_the_order(self) -> None:
        orders = {tuple(p.run_id for p in plan_runs(TASKS, ARMS, 3, seed, bare_arms=self.BARE)) for seed in range(20)}
        self.assertGreater(len(orders), 1)

    def test_machines_are_shuffled_together_not_one_after_the_other(self) -> None:
        # With 5 pairs, "all full first" or "all bare first" in every one of 24 blocks has probability ~ 1e-20.
        plan = plan_runs(TASKS, ARMS, 12, 7, bare_arms=self.BARE)
        full_first = bare_first = 0
        interleaved = 0
        for rnd in range(1, 13):
            for task in TASKS:
                machines = [m for m, _ in self.pairs(plan, rnd, task)]
                full_first += machines[0] == "full"
                bare_first += machines[0] == "bare"
                interleaved += machines != sorted(machines, key=("full", "bare").index)
        self.assertGreater(full_first, 0)
        self.assertGreater(bare_first, 0)
        self.assertGreater(interleaved, 0, "no block mixes the machines")

    def test_the_order_is_not_one_fixed_order(self) -> None:
        plan = plan_runs(TASKS, ARMS, 12, 7, bare_arms=self.BARE)
        orders = {tuple(self.pairs(plan, rnd, task)) for rnd in range(1, 13) for task in TASKS}
        self.assertGreater(len(orders), 1)

    def test_arms_empty_with_bare_arms_plans_only_bare_runs(self) -> None:
        plan = plan_runs(["gif", "hello"], [], 2, 5, bare_arms=self.BARE)
        self.assertEqual(len(plan), 2 * 2 * 2)
        self.assertEqual({p.machine for p in plan}, {"bare"})
        self.assertTrue(all(p.run_id.endswith("-bare") for p in plan))
        for rnd in (1, 2):
            for task in ("gif", "hello"):
                self.assertCountEqual([p.arm for p in plan if (p.round, p.task) == (rnd, task)], self.BARE)
        self.assertEqual([p.task for p in plan[:4]], ["gif", "gif", "hello", "hello"])

    def test_a_bare_arm_the_full_machine_does_not_run_still_plans(self) -> None:
        plan = plan_runs(["gif"], ["baseline"], 1, 3, bare_arms=["mcp"])
        self.assertCountEqual([(p.machine, p.arm) for p in plan], [("full", "baseline"), ("bare", "mcp")])
        self.assertCountEqual([p.run_id for p in plan], ["r01-gif-baseline", "r01-gif-mcp-bare"])

    def test_planned_run_carries_its_machine(self) -> None:
        run = PlannedRun(run_id="r01-hello-baseline-bare", round=1, task="hello", arm="baseline", machine="bare")
        self.assertEqual((run.machine, run.arm), ("bare", "baseline"))
        for p in self.plan:
            self.assertIn(p.machine, arms_module.MACHINES)

    def test_the_acceptance_plan_has_135_runs(self) -> None:
        tasks = ["hello", "gif", "compress", "videosize", "tags", "pdfsplit", "batch", "xlsxjson", "rename"]
        plan = plan_runs(tasks, ["baseline", "skill", "mcp"], 3, 0, bare_arms=["baseline", "skill"])
        self.assertEqual(len(plan), 135)


if __name__ == "__main__":
    unittest.main()
