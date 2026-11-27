"""The twelve-task registry, the held-out rule and the default task list of the command line."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench import tasks as tasks_module  # noqa: E402
from jzr_bench.cli import build_parser, main  # noqa: E402
from jzr_bench.tasks import HELD_OUT, TASKS, get_task  # noqa: E402
from jzr_bench.tasks.base import Task  # noqa: E402

OLD = ["hello", "gif", "heic", "pdf", "compress", "xlsx"]
NEW = ["videosize", "tags", "pdfsplit", "batch", "xlsxjson", "rename"]  # round 3, in the order of the spec
ALL = {*OLD, *NEW}
HELD = {"compress", "xlsx", *NEW}


def task_names(value: str | list[str]) -> list[str]:
    """--tasks is a comma-separated string or a list; either way, the names."""
    if isinstance(value, str):
        return [name for name in value.split(",") if name]
    return list(value)


class RegistryTest(unittest.TestCase):
    def test_registry_holds_the_twelve_tasks(self) -> None:
        self.assertEqual(set(TASKS), ALL)

    def test_the_new_tasks_come_after_the_existing_ones_in_the_spec_order(self) -> None:
        # the order matters: the plan, the default --tasks and the tasks command follow it
        self.assertEqual(list(TASKS), [*OLD, *NEW])

    def test_every_entry_is_a_task_named_after_its_key(self) -> None:
        for name, task in TASKS.items():
            with self.subTest(task=name):
                self.assertIsInstance(task, Task)
                self.assertEqual(task.name, name)

    def test_get_task_returns_each_registered_task(self) -> None:
        for name in ALL:
            with self.subTest(task=name):
                self.assertIs(get_task(name), TASKS[name])

    def test_get_task_unknown_names_the_new_tasks_too(self) -> None:
        with self.assertRaises(KeyError) as ctx:
            get_task("does-not-exist")
        message = str(ctx.exception)
        for name in ALL:
            self.assertIn(name, message)

    def test_no_prompt_mentions_jzr(self) -> None:
        for name, task in TASKS.items():
            with self.subTest(task=name):
                self.assertNotIn("jzr", task.prompt.lower())
                self.assertTrue(task.prompt.strip())

    def test_prompts_are_all_different(self) -> None:
        self.assertEqual(len({t.prompt for t in TASKS.values()}), len(TASKS))

    def test_each_new_task_lives_in_its_own_module(self) -> None:
        for name in ("heic", "pdf", "compress", "xlsx", *NEW):
            with self.subTest(task=name):
                module = getattr(tasks_module, name)
                self.assertIs(module.TASK, TASKS[name])


class HeldOutTest(unittest.TestCase):
    def test_held_out_tasks_are_compress_xlsx_and_the_six_of_round_3(self) -> None:
        self.assertEqual(set(HELD_OUT), HELD)

    def test_the_pilot_tasks_are_not_held_out(self) -> None:
        self.assertFalse(HELD_OUT & {"hello", "gif", "heic", "pdf"})

    def test_held_out_is_immutable(self) -> None:
        self.assertIsInstance(HELD_OUT, frozenset)

    def test_every_held_out_task_exists(self) -> None:
        self.assertLessEqual(set(HELD_OUT), set(TASKS))


class CliTasksTest(unittest.TestCase):
    def test_default_tasks_exclude_the_held_out_ones(self) -> None:
        names = task_names(build_parser().parse_args(["run"]).tasks)
        self.assertEqual(set(names), ALL - HELD)
        self.assertEqual(names, ["hello", "gif", "heic", "pdf"])
        self.assertEqual(len(names), len(set(names)))

    def test_default_tasks_still_include_the_pilot_tasks(self) -> None:
        names = task_names(build_parser().parse_args(["run"]).tasks)
        self.assertIn("hello", names)
        self.assertIn("gif", names)

    def test_naming_a_held_out_task_is_accepted(self) -> None:
        for given in ("compress", "xlsx", "compress,xlsx", "gif,compress", *NEW, ",".join(NEW), "gif,videosize,rename"):
            with self.subTest(tasks=given):
                args = build_parser().parse_args(["run", "--tasks", given])
                self.assertEqual(task_names(args.tasks), given.split(","))

    def test_a_held_out_task_named_is_found_in_the_registry(self) -> None:
        for name in task_names(
            build_parser().parse_args(["run", "--tasks", ",".join(["compress", "xlsx", *NEW])]).tasks
        ):
            self.assertIn(name, TASKS)

    def test_the_tasks_command_lists_all_twelve_and_marks_the_held_out_ones(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["tasks"]), 0)
        lines = out.getvalue().splitlines()
        for name in ALL:
            with self.subTest(task=name):
                matching = [line for line in lines if line.startswith(f"{name}:") or line.startswith(f"{name} ")]
                self.assertEqual(len(matching), 1, lines)
                self.assertIn(TASKS[name].prompt, matching[0])
                marked = "held" in matching[0].lower()
                self.assertEqual(marked, name in HELD_OUT, matching[0])


if __name__ == "__main__":
    unittest.main()
