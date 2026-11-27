"""The order of runs: round by round, every arm of every machine once per task, shuffled per round."""

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
    machine: str = "full"


def plan_runs(
    tasks: Sequence[str], arms: Sequence[str], rounds: int, seed: int, bare_arms: Sequence[str] = ()
) -> list[PlannedRun]:
    """`arms` run on the full machine, `bare_arms` on the bare one; a bare run's id ends in -bare."""
    plan: list[PlannedRun] = []
    for rnd in range(1, rounds + 1):
        for task in tasks:
            order = [("full", arm) for arm in arms] + [("bare", arm) for arm in bare_arms]
            random.Random(f"{seed}:{rnd}:{task}").shuffle(order)
            for machine, arm in order:
                run_id = f"r{rnd:02d}-{task}-{arm}" + ("-bare" if machine == "bare" else "")
                plan.append(PlannedRun(run_id, rnd, task, arm, machine))
    return plan
