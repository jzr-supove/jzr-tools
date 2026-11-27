"""Running the repository's own jzr in a task's work folder, for the "jzr's output passes the check" tests.

Not a test module: the unittest discovery pattern is test*.py.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JZR = REPO / ".venv" / "bin" / "jzr"
HAVE_JZR = os.access(JZR, os.X_OK)
NEEDS_JZR = f"the repository's jzr ({JZR}) is not installed"


def run_jzr(args: Sequence[str], cwd: Path, *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    """Run jzr with args in cwd; raise AssertionError with its output when it exits with an error."""
    result = subprocess.run(
        [str(JZR), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"jzr {' '.join(args)} exited with {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result
