"""Overhead probe: needs no tool, so its cost is what each arm adds to every request."""

from __future__ import annotations

from pathlib import Path

from .base import CheckResult, Task


def _prepare(fixture_dir: Path) -> None:
    """No fixtures."""


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    answer = final_text.strip().lower()
    if "ok" in answer:
        return CheckResult(True, ["the answer contains ok"])
    return CheckResult(False, [f"the answer does not contain ok: {final_text.strip()[:200]!r}"])


TASK = Task(name="hello", prompt="Reply with the single word: ok", prepare_fixtures=_prepare, check=_check)
