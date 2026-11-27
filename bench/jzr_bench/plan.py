"""The order of runs: round by round, every arm once per task, arms shuffled per round."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class PlannedRun:
    run_id: str
    round: int
    task: str
    arm: str


def plan_runs(tasks: Sequence[str], arms: Sequence[str], rounds: int, seed: int) -> list[PlannedRun]:
    plan: list[PlannedRun] = []
    for rnd in range(1, rounds + 1):
        for task in tasks:
            order = list(arms)
            random.Random(f"{seed}:{rnd}:{task}").shuffle(order)
            plan.extend(PlannedRun(f"r{rnd:02d}-{task}-{arm}", rnd, task, arm) for arm in order)
    return plan
