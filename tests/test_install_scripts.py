"""The one-line installers: install.ps1 (Windows) and install.sh (macOS, Linux).

Both put uv on the machine when it is missing and then install or upgrade
jzr-tools with `uv tool install --upgrade`. The tests run them against fake
`uv` and `curl` commands on PATH, so nothing is downloaded.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALL_PS1 = ROOT / "install.ps1"
INSTALL_SH = ROOT / "install.sh"
README = ROOT / "README.md"
RAW = "https://raw.githubusercontent.com/jzr-supove/jzr-tools/main"

FAKE_UV_SH = """#!/bin/sh
echo "uv $*" >> "$JZR_TEST_LOG"
exit "${FAKE_UV_EXIT:-0}"
"""
FAKE_UV_CMD = """@echo off
echo uv %*>> "%JZR_TEST_LOG%"
if defined FAKE_UV_EXIT exit /b %FAKE_UV_EXIT%
exit /b 0
"""
# Stands in for curl: logs its arguments and prints a "uv installer" that
# drops the fake uv into ~/.local/bin, where the real installer puts uv.
FAKE_CURL_SH = """#!/bin/sh
echo "curl $*" >> "$JZR_TEST_LOG"
cat <<'INSTALLER'
mkdir -p "$HOME/.local/bin"
cp "$FAKE_UV" "$HOME/.local/bin/uv"
chmod +x "$HOME/.local/bin/uv"
INSTALLER
"""


def _without_uv(path: str) -> str:
    """PATH with every directory that holds a real uv left out."""
    keep = []
    for entry in path.split(os.pathsep):
        if not entry:
            continue
        if any((Path(entry) / name).exists() for name in ("uv", "uv.exe")):
            continue
        keep.append(entry)
    return os.pathsep.join(keep)


def _write(path: Path, text: str, *, executable: bool = False) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n" if path.suffix != ".cmd" else "\r\n")
    if executable:
        path.chmod(0o755)
    return path


class _Sandbox(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="jzr-install-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "calls.log"
        self.log.touch()
        self.fake_uv = _write(self.tmp / "fake-uv", FAKE_UV_SH, executable=True)

    def env(self, *, uv_on_path: bool) -> dict[str, str]:
        env = dict(os.environ)
        for name in ("XDG_BIN_HOME", "UV_INSTALL_DIR", "CARGO_HOME", "VIRTUAL_ENV"):
            env.pop(name, None)
        if uv_on_path:
            _write(self.bin / "uv", FAKE_UV_SH, executable=True)
            if sys.platform == "win32":
                _write(self.bin / "uv.cmd", FAKE_UV_CMD)
        env["PATH"] = os.pathsep.join([str(self.bin), _without_uv(env.get("PATH", ""))])
        env["HOME"] = str(self.home)
        env["JZR_TEST_LOG"] = str(self.log)
        env["FAKE_UV"] = str(self.fake_uv)
        return env

    def calls(self) -> list[str]:
        return [line.strip() for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]


class FilesTests(unittest.TestCase):
    def test_both_installers_exist(self) -> None:
        self.assertTrue(INSTALL_PS1.is_file(), INSTALL_PS1)
        self.assertTrue(INSTALL_SH.is_file(), INSTALL_SH)

    def test_installers_use_the_official_uv_installers(self) -> None:
        self.assertIn("https://astral.sh/uv/install.ps1", INSTALL_PS1.read_text(encoding="utf-8"))
        self.assertIn("https://astral.sh/uv/install.sh", INSTALL_SH.read_text(encoding="utf-8"))

    def test_install_sh_has_lf_line_endings(self) -> None:
        # A CRLF shell script fails under `curl ... | sh` on Linux and macOS.
        self.assertNotIn(b"\r\n", INSTALL_SH.read_bytes())

    def test_installers_ship_in_the_sdist(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"/install.ps1"', pyproject)
        self.assertIn('"/install.sh"', pyproject)


@unittest.skipUnless(shutil.which("sh"), "needs sh")
class InstallShTests(_Sandbox):
    def run_sh(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [shutil.which("sh"), str(INSTALL_SH)],
            env=env, capture_output=True, text=True, timeout=60,
        )

    def test_syntax(self) -> None:
        result = subprocess.run([shutil.which("sh"), "-n", str(INSTALL_SH)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_with_uv_installs_jzr_tools_and_downloads_nothing(self) -> None:
        _write(self.bin / "curl", FAKE_CURL_SH, executable=True)
        result = self.run_sh(self.env(uv_on_path=True))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertIn("uv tool install --upgrade jzr-tools", calls)
        self.assertIn("uv tool update-shell", calls)
        self.assertFalse([c for c in calls if c.startswith("curl")], calls)
        self.assertIn("jzr --help", result.stdout)

    def test_without_uv_installs_uv_first_then_jzr_tools(self) -> None:
        _write(self.bin / "curl", FAKE_CURL_SH, executable=True)
        result = self.run_sh(self.env(uv_on_path=False))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertTrue(calls and calls[0].startswith("curl "), calls)
        self.assertIn("https://astral.sh/uv/install.sh", calls[0])
        self.assertIn("uv tool install --upgrade jzr-tools", calls)
        self.assertTrue((self.home / ".local/bin/uv").exists())

    def test_a_failing_uv_fails_the_script(self) -> None:
        env = self.env(uv_on_path=True)
        env["FAKE_UV_EXIT"] = "3"
        result = self.run_sh(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("jzr-tools was not installed", result.stderr)


def _powershells() -> list[str]:
    found = []
    for name in ("pwsh", "powershell"):
        path = shutil.which(name)
        if path:
            found.append(path)
    return found


@unittest.skipUnless(_powershells(), "needs PowerShell")
class InstallPs1Tests(_Sandbox):
    def run_iex(self, shell: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        # The README runs it as `irm <url> | iex`: the script runs inside the
        # caller's session, so it must not end that session.
        command = (
            f"Get-Content -Raw -LiteralPath '{INSTALL_PS1}' | Invoke-Expression; "
            "Write-Output 'session-still-open'"
        )
        return subprocess.run(
            [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            env=env, capture_output=True, text=True, timeout=120,
        )

    def test_parses_without_errors(self) -> None:
        for shell in _powershells():
            with self.subTest(shell=shell):
                command = (
                    "$errors = $null; "
                    f"[void][System.Management.Automation.Language.Parser]::ParseFile('{INSTALL_PS1}', [ref]$null, [ref]$errors); "
                    "if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 }"
                )
                result = subprocess.run(
                    [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_with_uv_installs_jzr_tools_and_leaves_the_session_open(self) -> None:
        for shell in _powershells():
            with self.subTest(shell=shell):
                self.log.write_text("", encoding="utf-8")
                result = self.run_iex(shell, self.env(uv_on_path=True))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = self.calls()
                self.assertIn("uv tool install --upgrade jzr-tools", calls)
                self.assertIn("uv tool update-shell", calls)
                self.assertIn("jzr --help", result.stdout)
                self.assertIn("session-still-open", result.stdout)

    def test_a_failing_uv_reports_an_error(self) -> None:
        for shell in _powershells():
            with self.subTest(shell=shell):
                env = self.env(uv_on_path=True)
                env["FAKE_UV_EXIT"] = "3"
                result = self.run_iex(shell, env)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("jzr-tools was not installed", result.stdout + result.stderr)


class ReadmeTests(unittest.TestCase):
    def setUp(self) -> None:
        text = README.read_text(encoding="utf-8")
        start = text.index("\n## Install")
        end = text.index("\n## ", start + 1)
        self.install = text[start:end]

    def test_one_line_installers_come_first(self) -> None:
        ps1 = self.install.index(f"{RAW}/install.ps1")
        sh = self.install.index(f"{RAW}/install.sh")
        pipx = self.install.index("pipx install jzr-tools")
        self.assertLess(ps1, pipx)
        self.assertLess(sh, pipx)

    def test_uv_route_is_shown_before_pipx(self) -> None:
        self.assertLess(
            self.install.index("uv tool install jzr-tools"),
            self.install.index("pipx install jzr-tools"),
        )

    def test_says_python_is_not_needed_up_front(self) -> None:
        self.assertIn("No Python", self.install)


if __name__ == "__main__":
    unittest.main()
