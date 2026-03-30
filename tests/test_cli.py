"""CLI behavior tests."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from typer.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core.filesystem import list_entries
from jzr_tools.core.system import resolve_executable
from jzr_tools.core.text import slugify

runner = CliRunner()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"


class CliTests(unittest.TestCase):
    def test_root_help_lists_registered_groups(self) -> None:
        result = runner.invoke(app, ["--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("text", result.stdout)
        self.assertIn("fs", result.stdout)
        self.assertIn("system", result.stdout)

    def test_version_flag_prints_package_version(self) -> None:
        result = runner.invoke(app, ["--version"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "0.1.0")

    def test_slug_command_uses_text_core(self) -> None:
        result = runner.invoke(app, ["text", "slug", "Hello, World!"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "hello-world")
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_fs_ls_lists_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "alpha.txt").write_text("a", encoding="utf-8")
            (tmp_path / "beta").mkdir()

            result = runner.invoke(app, ["fs", "ls", str(tmp_path)])

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.stdout.splitlines(), ["beta/", "alpha.txt"])
            self.assertEqual(list_entries(tmp_path), ["beta/", "alpha.txt"])

    def test_system_which_finds_python(self) -> None:
        result = runner.invoke(app, ["system", "which", sys.executable])

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(Path(result.stdout.strip()).exists())
        self.assertEqual(resolve_executable(sys.executable), result.stdout.strip())

    def test_module_entrypoint_runs(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_PATH)

        result = subprocess.run(
            [sys.executable, "-m", "jzr_tools", "--version"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "0.1.0")


if __name__ == "__main__":
    unittest.main()
