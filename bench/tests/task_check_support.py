"""Shared helpers for the task check tests (heic, pdf, compress, xlsx).

Not a test module: the unittest discovery pattern is test*.py. Every test module that uses it puts
bench/tests on sys.path first, like the existing ones do for stream_fixtures.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jzr_bench.tasks.base import CheckResult, Task


def snapshot(folder: Path) -> dict[str, bytes]:
    """Name -> content of every file under folder."""
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


class CheckCase(unittest.TestCase):
    """One work folder per test, set up from the class's task and fixtures with Task.setup."""

    task: Task
    fixtures: Path
    final_text = "Done."

    def setUp(self) -> None:
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        self.work = Path(self._work.name)
        self.task.setup(self.work, self.fixtures)

    def check(self, final_text: str | None = None) -> CheckResult:
        result = self.task.check(
            self.work,
            self.fixtures,
            self.final_text if final_text is None else final_text,
        )
        self.assertIsInstance(result, CheckResult)
        return result

    def assert_passes(self) -> CheckResult:
        result = self.check()
        self.assertTrue(result.passed, result.details)
        return result

    def assert_fails_with(self, result: CheckResult, *keywords: str, reasons: int | None = None) -> None:
        self.assertFalse(result.passed, "the check passed but should have failed")
        self.assertTrue(result.details, "a failed check must say why")
        joined = " ".join(result.details).lower()
        self.assertTrue(
            any(k.lower() in joined for k in keywords),
            f"expected one of {keywords} in the reasons, got {result.details}",
        )
        if reasons is not None:
            self.assertEqual(len(result.details), reasons, result.details)

    def assert_check_leaves_work_folder_alone(self) -> None:
        before = snapshot(self.work)
        self.check()
        self.assertEqual(before, snapshot(self.work))
