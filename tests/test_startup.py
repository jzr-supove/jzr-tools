"""Acceptance tests for the lazy-loading, Typer-free CLI startup refactor."""

from __future__ import annotations

import importlib
import json
import os
import pkgutil
import re
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

LAZY_NAMES = {"image", "text", "port", "killport"}
BOX_CHARS = ("╭", "│", "╰", "─")

_RUNNER = """
import json, sys
from jzr_tools.cli import app
code = 0
try:
    app({args!r}, prog_name="jzr")
except SystemExit as exit_:
    code = exit_.code
print("\\n@@EXIT@@" + str(code))
print("@@MODULES@@" + ",".join(sorted(sys.modules)))
"""


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_PATH)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class Observed:
    def __init__(self, args: list[str]) -> None:
        proc = subprocess.run(
            [sys.executable, "-c", _RUNNER.format(args=args)],
            cwd=PROJECT_ROOT,
            env=_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.stderr = proc.stderr
        self.returncode = proc.returncode
        self.exit_code: str | None = None
        self.modules: set[str] = set()
        out_lines = []
        for line in proc.stdout.splitlines():
            if line.startswith("@@EXIT@@"):
                self.exit_code = line[len("@@EXIT@@"):]
            elif line.startswith("@@MODULES@@"):
                self.modules = set(filter(None, line[len("@@MODULES@@"):].split(",")))
            else:
                out_lines.append(line)
        self.stdout = "\n".join(out_lines)

    def loaded(self, name: str) -> bool:
        return name in self.modules

    def describe(self) -> str:
        return (
            f"exit={self.exit_code} rc={self.returncode}\nstdout={self.stdout!r}\n"
            f"stderr={self.stderr[-500:]!r}"
        )


class _ObservedTestCase(unittest.TestCase):
    def run_cli(self, *args: str) -> Observed:
        return Observed(list(args))

    def assert_ok(self, obs: Observed) -> None:
        self.assertEqual(obs.exit_code, "0", obs.describe())

    def assert_not_loaded(self, obs: Observed, names: list[str]) -> None:
        for name in names:
            self.assertFalse(obs.loaded(name), f"{name} was imported\n{obs.describe()}")

    def assert_no_prefix_loaded(self, obs: Observed, prefixes: list[str]) -> None:
        bad = sorted(m for m in obs.modules if any(m.startswith(p) for p in prefixes))
        self.assertEqual(bad, [], f"unexpected modules loaded: {bad}")


class NoTyperTests(unittest.TestCase):
    def test_package_import_pulls_in_neither_typer_nor_rich(self) -> None:
        code = (
            "import importlib, json, pkgutil, sys\n"
            "import jzr_tools\n"
            "for m in pkgutil.walk_packages(jzr_tools.__path__, 'jzr_tools.'):\n"
            "    if m.name.endswith('__main__'):\n"
            "        continue\n"
            "    importlib.import_module(m.name)\n"
            "print('@@MODULES@@' + ','.join(sorted(sys.modules)))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            env=_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        line = next(l for l in proc.stdout.splitlines() if l.startswith("@@MODULES@@"))
        modules = set(line[len("@@MODULES@@"):].split(","))
        for banned in ("typer", "rich"):
            offenders = sorted(m for m in modules if m == banned or m.startswith(banned + "."))
            self.assertEqual(offenders, [], f"{banned} imported: {offenders[:5]}")

    def test_pyproject_dependencies_drop_typer_and_keep_click_and_psutil(self) -> None:
        data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        deps = data["project"]["dependencies"]

        def name_of(spec: str) -> str:
            for i, ch in enumerate(spec):
                if ch in "<>=!~[ ;(":
                    return spec[:i].strip().lower()
            return spec.strip().lower()

        names = [name_of(d) for d in deps]
        self.assertNotIn("typer", names)
        self.assertIn("click", names)
        self.assertIn("psutil", names)

    def test_app_is_a_click_group(self) -> None:
        import click

        sys.path.insert(0, str(SRC_PATH))
        try:
            cli = importlib.import_module("jzr_tools.cli")
        finally:
            sys.path.remove(str(SRC_PATH))
        self.assertIsInstance(cli.app, click.Group)


class RootCallsAreLightTests(_ObservedTestCase):
    def _check(self, *args: str) -> None:
        obs = self.run_cli(*args)
        self.assert_ok(obs)
        self.assert_not_loaded(obs, ["psutil", "typer", "rich"])
        self.assert_no_prefix_loaded(obs, ["jzr_tools.commands.", "jzr_tools.core."])

    def test_dash_dash_version_loads_nothing_command_specific(self) -> None:
        self._check("--version")

    def test_dash_dash_help_loads_nothing_command_specific(self) -> None:
        self._check("--help")

    def test_version_command_loads_nothing_command_specific(self) -> None:
        self._check("version")


class CommandLoadsOnlyItsOwnTests(_ObservedTestCase):
    def test_text_slug_does_not_load_network_or_image_code(self) -> None:
        obs = self.run_cli("text", "slug", "Hello World")
        self.assert_ok(obs)
        self.assertIn("hello-world", obs.stdout)
        self.assert_not_loaded(
            obs,
            [
                "psutil",
                "typer",
                "rich",
                "jzr_tools.commands.network",
                "jzr_tools.commands.image",
                "jzr_tools.core.network",
                "jzr_tools.core.images",
                "jzr_tools.core.windows_network",
            ],
        )

    def test_image_formats_does_not_load_network_or_text(self) -> None:
        obs = self.run_cli("image", "formats")
        self.assert_ok(obs)
        self.assert_not_loaded(
            obs, ["psutil", "jzr_tools.commands.network", "jzr_tools.commands.text"]
        )

    def test_killport_help_does_not_load_image_code(self) -> None:
        obs = self.run_cli("killport", "--help")
        self.assert_ok(obs)
        self.assert_not_loaded(obs, ["jzr_tools.commands.image", "jzr_tools.core.images"])

    def test_port_help_does_not_load_image_code(self) -> None:
        obs = self.run_cli("port", "-h")
        self.assert_ok(obs)
        self.assert_not_loaded(obs, ["jzr_tools.commands.image", "jzr_tools.core.images"])


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import click

        sys.path.insert(0, str(SRC_PATH))
        try:
            cls.cli = importlib.import_module("jzr_tools.cli")
        finally:
            sys.path.remove(str(SRC_PATH))
        cls.click = click
        cls.app = cls.cli.app

    def _lazy(self) -> dict:
        lazy = getattr(self.app, "lazy_commands", None)
        self.assertIsInstance(lazy, dict, "app.lazy_commands must be a dict")
        return lazy

    def test_lazy_commands_keys_are_exactly_the_four_commands(self) -> None:
        self.assertEqual(set(self._lazy()), LAZY_NAMES)

    def test_lazy_commands_values_are_import_path_and_help_tuples(self) -> None:
        for name, value in self._lazy().items():
            with self.subTest(name=name):
                self.assertIsInstance(value, tuple)
                self.assertEqual(len(value), 2)
                import_path, help_text = value
                self.assertIsInstance(import_path, str)
                module_name, sep, attr = import_path.partition(":")
                self.assertEqual(sep, ":", import_path)
                self.assertTrue(module_name and attr, import_path)
                self.assertIsInstance(help_text, str)
                self.assertTrue(help_text.strip())

    def test_list_commands_has_lazy_commands_and_version(self) -> None:
        self._lazy()
        ctx = self.click.Context(self.app)
        listed = set(self.app.list_commands(ctx))
        self.assertTrue((LAZY_NAMES | {"version"}) <= listed, listed)

    def test_get_command_resolves_every_lazy_command_and_help_matches(self) -> None:
        ctx = self.click.Context(self.app)
        for name, (_path, help_text) in self._lazy().items():
            with self.subTest(name=name):
                command = self.app.get_command(ctx, name)
                self.assertIsInstance(command, self.click.Command)
                self.assertEqual(help_text, command.get_short_help_str(limit=300))

    def test_get_command_unknown_returns_none(self) -> None:
        ctx = self.click.Context(self.app)
        self.assertIsNone(self.app.get_command(ctx, "nope"))

    def test_lazy_commands_point_at_the_new_modules(self) -> None:
        paths = {name: value[0] for name, value in self._lazy().items()}
        self.assertEqual(
            paths,
            {
                "image": "jzr_tools.commands.image:app",
                "text": "jzr_tools.commands.text:app",
                "port": "jzr_tools.commands.network:app",
                "killport": "jzr_tools.commands.network:killport",
            },
        )


class RootHelpTests(_ObservedTestCase):
    def test_root_help_lists_all_commands_with_their_help(self) -> None:
        obs = self.run_cli("--help")
        self.assert_ok(obs)
        for name in LAZY_NAMES | {"version"}:
            self.assertIn(name, obs.stdout)
        self.assertIn("Text utilities.", obs.stdout)
        commands_section = obs.stdout.split("Commands:", 1)[1]
        for removed in ("convert", "png2jpg", "fs", "system"):
            self.assertIsNone(
                re.search(rf"^  {removed}\s", commands_section, flags=re.MULTILINE),
                f"{removed} is still listed",
            )


class ShortHelpFlagTests(_ObservedTestCase):
    def test_dash_h_works_everywhere_dash_dash_help_does(self) -> None:
        cases = [
            ["-h"],
            ["text", "-h"],
            ["text", "slug", "-h"],
            ["image", "-h"],
            ["image", "convert", "-h"],
            ["image", "png2jpg", "-h"],
            ["port", "-h"],
            ["port", "kill", "-h"],
            ["port", "show", "-h"],
            ["killport", "-h"],
        ]
        for args in cases:
            with self.subTest(args=args):
                obs = self.run_cli(*args)
                self.assert_ok(obs)
                self.assertIn("Usage:", obs.stdout)


class PlainErrorOutputTests(_ObservedTestCase):
    def test_usage_error_is_plain_text_with_exit_code_2(self) -> None:
        obs = self.run_cli("text", "slug")
        self.assertEqual(obs.exit_code, "2", obs.describe())
        self.assertIn("Error:", obs.stderr)
        for ch in BOX_CHARS:
            self.assertNotIn(ch, obs.stderr)


if __name__ == "__main__":
    unittest.main()
