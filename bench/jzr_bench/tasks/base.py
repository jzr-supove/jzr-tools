"""What a benchmark task is: a prompt, its fixtures and a pass/fail check."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    details: list[str]


@dataclass(frozen=True)
class Task:
    name: str
    prompt: str
    prepare_fixtures: Callable[[Path], None]
    check: Callable[[Path, Path, str], CheckResult]

    def setup(self, work_dir: Path, fixture_dir: Path) -> None:
        """Copy the task's fixtures into work_dir, making them first if fixture_dir is empty."""
        fixture_dir.mkdir(parents=True, exist_ok=True)
        if not any(fixture_dir.iterdir()):
            self.prepare_fixtures(fixture_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        for item in sorted(fixture_dir.iterdir()):
            if item.is_dir():
                shutil.copytree(item, work_dir / item.name)
            else:
                shutil.copy2(item, work_dir / item.name)


def sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sources_unchanged(work_dir: Path, fixture_dir: Path, names: list[str]) -> list[str]:
    """One problem line for each source that is missing or differs from its fixture."""
    problems = []
    for name in names:
        path = work_dir / name
        if not path.is_file():
            problems.append(f"{name} is missing: the original must be kept unchanged")
        elif sha256(path) != sha256(fixture_dir / name):
            problems.append(f"{name} was changed: its SHA-256 differs from the original")
    return problems
