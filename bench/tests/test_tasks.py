"""Offline tests for jzr_bench.tasks: registry, Task.setup, and the hello task."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench.tasks import TASKS, get_task  # noqa: E402
from jzr_bench.tasks.base import CheckResult, Task  # noqa: E402


class TmpDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def new_dir(self, name: str) -> Path:
        d = self.tmp / name
        d.mkdir()
        return d


class RegistryTest(unittest.TestCase):
    def test_registry_holds_hello_and_gif(self) -> None:
        self.assertIn("hello", TASKS)
        self.assertIn("gif", TASKS)
        for name, task in TASKS.items():
            self.assertIsInstance(task, Task)
            self.assertEqual(task.name, name)

    def test_get_task_returns_the_registered_task(self) -> None:
        self.assertIs(get_task("hello"), TASKS["hello"])
        self.assertIs(get_task("gif"), TASKS["gif"])

    def test_get_task_unknown_raises_key_error_naming_the_known_tasks(self) -> None:
        with self.assertRaises(KeyError) as ctx:
            get_task("does-not-exist")
        message = str(ctx.exception)
        self.assertIn("hello", message)
        self.assertIn("gif", message)

    def test_no_prompt_mentions_jzr(self) -> None:
        for name, task in TASKS.items():
            with self.subTest(task=name):
                self.assertNotIn("jzr", task.prompt.lower())
                self.assertTrue(task.prompt.strip())


class TaskSetupTest(TmpDirCase):
    def make_task(self, calls: list[Path]) -> Task:
        def prepare(fixture_dir: Path) -> None:
            calls.append(fixture_dir)
            (fixture_dir / "data.txt").write_text("fixture data\n")
            (fixture_dir / "other.bin").write_bytes(b"\x00\x01\x02")

        return Task(
            name="custom",
            prompt="do the thing",
            prepare_fixtures=prepare,
            check=lambda work, fixtures, text: CheckResult(True, []),
        )

    def test_setup_copies_every_fixture_file_into_the_work_dir(self) -> None:
        calls: list[Path] = []
        task = self.make_task(calls)
        fixtures, work = self.new_dir("fixtures"), self.new_dir("work")
        task.prepare_fixtures(fixtures)
        calls.clear()
        task.setup(work, fixtures)
        self.assertEqual((work / "data.txt").read_text(), "fixture data\n")
        self.assertEqual((work / "other.bin").read_bytes(), b"\x00\x01\x02")
        self.assertEqual(calls, [], "fixtures already prepared must not be regenerated")

    def test_setup_prepares_the_fixtures_when_the_fixture_dir_is_empty(self) -> None:
        calls: list[Path] = []
        task = self.make_task(calls)
        fixtures, work = self.new_dir("fixtures"), self.new_dir("work")
        task.setup(work, fixtures)
        self.assertEqual(calls, [fixtures])
        self.assertEqual((work / "data.txt").read_text(), "fixture data\n")
        self.assertTrue((fixtures / "data.txt").exists())

    def test_setup_copies_are_independent_of_the_fixture_folder(self) -> None:
        task = self.make_task([])
        fixtures, work_a, work_b = self.new_dir("fixtures"), self.new_dir("wa"), self.new_dir("wb")
        task.setup(work_a, fixtures)
        task.setup(work_b, fixtures)
        (work_a / "data.txt").write_text("modified by the agent\n")
        self.assertEqual((fixtures / "data.txt").read_text(), "fixture data\n")
        self.assertEqual((work_b / "data.txt").read_text(), "fixture data\n")

    def test_check_result_fields(self) -> None:
        result = CheckResult(passed=False, details=["why"])
        self.assertFalse(result.passed)
        self.assertEqual(result.details, ["why"])


class HelloTaskTest(TmpDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = get_task("hello")
        self.work, self.fixtures = self.new_dir("work"), self.new_dir("fixtures")

    def check(self, text: str) -> CheckResult:
        return self.task.check(self.work, self.fixtures, text)

    def test_prompt_is_the_one_in_the_spec(self) -> None:
        self.assertEqual(self.task.prompt, "Reply with the single word: ok")

    def test_passes_when_the_answer_contains_ok(self) -> None:
        for text in ("ok", "OK", "Ok.", "  ok\n", "\nOK \n"):
            with self.subTest(text=text):
                result = self.check(text)
                self.assertIsInstance(result, CheckResult)
                self.assertTrue(result.passed, result.details)

    def test_fails_with_a_reason_otherwise(self) -> None:
        for text in ("", "   \n", "no", "Hello there", "I could not do that."):
            with self.subTest(text=text):
                result = self.check(text)
                self.assertFalse(result.passed)
                self.assertTrue(result.details)
                self.assertTrue(all(isinstance(d, str) and d for d in result.details))

    def test_needs_no_fixtures(self) -> None:
        self.task.prepare_fixtures(self.fixtures)
        self.task.setup(self.work, self.fixtures)
        self.assertEqual(list(self.fixtures.iterdir()), [])
        self.assertEqual(list(self.work.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
