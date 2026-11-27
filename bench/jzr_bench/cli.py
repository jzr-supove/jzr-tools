"""Command line: run, report, tasks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .arms import ARMS
from .tasks import HELD_OUT, TASKS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench/run.py", description="Claude Code with and without jzr.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run tasks in every arm and write a results folder")
    run.add_argument(
        "--tasks",
        default=",".join(name for name in TASKS if name not in HELD_OUT),
        help="comma-separated (default: %(default)s; held out: " + ",".join(sorted(HELD_OUT)) + ")",
    )
    run.add_argument("--arms", default=",".join(ARMS), help="comma-separated (default: %(default)s)")
    run.add_argument("--runs", type=int, default=3, help="rounds: runs per task and arm (default: %(default)s)")
    run.add_argument("--model", default="sonnet", help="passed to claude --model (default: %(default)s)")
    run.add_argument("--max-turns", type=int, default=30, help="default: %(default)s")
    run.add_argument("--timeout", type=float, default=600, help="seconds per run (default: %(default)s)")
    run.add_argument("--max-cost-usd", type=float, default=5.0, help="stop before the next run once reached")
    run.add_argument("--seed", type=int, default=0, help="seeds the arm order of each round")
    run.add_argument("--out", help="results folder (default: bench/results/<UTC time>)")
    run.add_argument("--sandbox-dir", help="where run sandboxes go (default: the system temp folder)")
    run.add_argument("--claude", help="the claude executable (default: claude on PATH)")
    run.add_argument("--keep-sandboxes", action="store_true", help="keep each run's sandbox folder")
    run.add_argument("--dry-run", action="store_true", help="show the plan and the first command, run nothing")

    report = sub.add_parser("report", help="rebuild summary.md of a results folder")
    report.add_argument("folder")

    sub.add_parser("tasks", help="list the tasks")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        from .runner import run_all

        return run_all(args)
    if args.command == "report":
        from .runner import write_summary

        folder = Path(args.folder)
        if not (folder / "runs.jsonl").is_file():
            print(f"error: {folder / 'runs.jsonl'} not found", file=sys.stderr)
            return 2
        print(write_summary(folder).read_text())
        return 0
    for task in TASKS.values():
        held = " [held out]" if task.name in HELD_OUT else ""
        print(f"{task.name}: {task.prompt}{held}")
    return 0
