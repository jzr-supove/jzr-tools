"""Behavioural tests for core.mcp_setup: the commands and registration helpers.

Nothing here starts `claude`: `run` and `which` are always fakes.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

NEEDS_EXTRA_TEXT = (
    'the MCP server needs the mcp extra: uv tool install "jzr-tools[mcp]" '
    '(or pipx install "jzr-tools[mcp]")'
)
WINDOWS_SERVER = ["C:\\Users\\Ann Lee\\AppData\\Local\\jzr.exe", "agent", "mcp"]


def _setup():
    from jzr_tools.core import mcp_setup

    return mcp_setup


def _mcp_installed() -> bool:
    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True


class FakeRun:
    """Stands in for subprocess.run and decodes like it does.

    The raw bytes are what the child "printed". They are decoded the way
    subprocess.run would for the given keyword arguments: text mode uses the
    given encoding and error handler, and otherwise the bytes are returned.
    Without an explicit encoding the fake falls back to ASCII with strict
    errors, so a caller that relies on the locale code page, or on strict
    decoding, raises UnicodeDecodeError here.
    """

    def __init__(
        self,
        returncode: int = 0,
        out: bytes | str = b"",
        err: bytes | str = b"",
        *,
        raises: Exception | None = None,
    ) -> None:
        self.returncode = returncode
        self.out = out.encode("utf-8") if isinstance(out, str) else out
        self.err = err.encode("utf-8") if isinstance(err, str) else err
        self.raises = raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):  # noqa: ANN001, ANN201
        self.calls.append((list(command), kwargs))
        if self.raises is not None:
            raise self.raises
        piped = bool(kwargs.get("capture_output")) or (
            kwargs.get("stdout") == subprocess.PIPE and kwargs.get("stderr") in (subprocess.PIPE, subprocess.STDOUT)
        )
        if not piped:
            raise AssertionError(f"the output of claude must be captured; got {kwargs!r}")
        merged = kwargs.get("stderr") == subprocess.STDOUT
        out, err = (self.out + self.err, None) if merged else (self.out, self.err)
        text_mode = bool(
            kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding") or kwargs.get("errors")
        )
        if text_mode:
            encoding = kwargs.get("encoding") or "ascii"
            errors = kwargs.get("errors") or "strict"
            out = out.decode(encoding, errors)
            err = None if err is None else err.decode(encoding, errors)
        return types.SimpleNamespace(returncode=self.returncode, stdout=out, stderr=err, args=list(command))


class CommandListTests(unittest.TestCase):
    def test_server_command_appends_agent_mcp_and_leaves_its_input_alone(self) -> None:
        jzr = ["/opt/tools/jzr"]

        self.assertEqual(_setup().server_command(jzr), ["/opt/tools/jzr", "agent", "mcp"])
        self.assertEqual(jzr, ["/opt/tools/jzr"])

    def test_server_command_keeps_every_word_of_a_module_launch(self) -> None:
        self.assertEqual(
            _setup().server_command(["/py/python", "-m", "jzr_tools"]),
            ["/py/python", "-m", "jzr_tools", "agent", "mcp"],
        )

    def test_add_command_registers_for_the_user_with_the_server_after_a_double_dash(self) -> None:
        command = _setup().add_command("/usr/bin/claude", ["/opt/jzr", "agent", "mcp"])

        self.assertEqual(
            command,
            ["/usr/bin/claude", "mcp", "add", "-s", "user", "jzr", "--", "/opt/jzr", "agent", "mcp"],
        )

    def test_add_command_does_not_split_a_path_with_spaces(self) -> None:
        command = _setup().add_command("C:\\Program Files\\claude.exe", WINDOWS_SERVER)

        self.assertEqual(command[0], "C:\\Program Files\\claude.exe")
        self.assertEqual(command[command.index("--") + 1 :], WINDOWS_SERVER)

    def test_remove_command_removes_the_user_level_server_named_jzr(self) -> None:
        command = _setup().remove_command("/usr/bin/claude")

        self.assertEqual(command[:3], ["/usr/bin/claude", "mcp", "remove"])
        self.assertEqual(command.count("jzr"), 1)
        self.assertEqual(command[command.index("-s") + 1], "user")
        self.assertEqual(sorted(command[3:]), sorted(["jzr", "-s", "user"]))


class DesktopTests(unittest.TestCase):
    def test_entry_is_indented_json_that_gives_back_the_same_command_and_arguments(self) -> None:
        text = _setup().desktop_entry(["/opt/jzr", "agent", "mcp"])

        self.assertIn("\n", text)
        self.assertTrue(any(line.startswith("  ") for line in text.splitlines()), text)
        entry = _jzr_entry(json.loads(text))
        self.assertEqual(entry["command"], "/opt/jzr")
        self.assertEqual(entry["args"], ["agent", "mcp"])

    def test_windows_backslashes_survive_the_round_trip_and_are_escaped_in_the_text(self) -> None:
        text = _setup().desktop_entry(WINDOWS_SERVER)

        entry = _jzr_entry(json.loads(text))
        self.assertEqual(entry["command"], WINDOWS_SERVER[0])
        self.assertEqual(entry["args"], ["agent", "mcp"])
        self.assertNotIn("C:\\Users", text)
        self.assertIn("C:\\\\Users\\\\Ann Lee", text)

    def test_entry_for_a_module_launch_keeps_the_extra_words_as_arguments(self) -> None:
        entry = _jzr_entry(json.loads(_setup().desktop_entry(["/py/python", "-m", "jzr_tools", "agent", "mcp"])))

        self.assertEqual(entry["command"], "/py/python")
        self.assertEqual(entry["args"], ["-m", "jzr_tools", "agent", "mcp"])

    def test_path_is_the_claude_desktop_config_of_each_platform(self) -> None:
        setup = _setup()

        windows = setup.desktop_path("win32")
        mac = setup.desktop_path("darwin")
        linux = setup.desktop_path("linux")

        self.assertIn("Claude\\claude_desktop_config.json", windows)
        self.assertIn("APPDATA", windows)
        self.assertIn("Library/Application Support/Claude/claude_desktop_config.json", mac)
        self.assertIn(".config/Claude/claude_desktop_config.json", linux)
        self.assertEqual(len({windows, mac, linux}), 3)


def _jzr_entry(document: dict) -> dict:
    """The server entry, whether or not the text wraps it in mcpServers."""
    if "mcpServers" in document:
        document = document["mcpServers"]
    if "jzr" in document:
        document = document["jzr"]
    return document


class JzrCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(prefix="jzr-mcp-setup-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve()

    @staticmethod
    def no_path(name: str) -> str | None:
        raise AssertionError(f"PATH must not be searched here (looked for {name!r})")

    def test_the_running_jzr_executable_is_used_as_an_absolute_path_when_it_exists(self) -> None:
        exe = self.folder / ("jzr.exe" if os.name == "nt" else "jzr")
        exe.write_bytes(b"")

        command = _setup().jzr_command(str(exe), which=self.no_path)

        self.assertEqual(command, [str(exe)])

    def test_a_relative_argv0_that_exists_becomes_absolute(self) -> None:
        exe = self.folder / ("jzr.exe" if os.name == "nt" else "jzr")
        exe.write_bytes(b"")
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.folder)

        command = _setup().jzr_command(exe.name, which=self.no_path)

        self.assertEqual(len(command), 1)
        self.assertTrue(os.path.isabs(command[0]), command)
        self.assertEqual(Path(command[0]).resolve(), exe)

    def test_argv0_without_the_exe_the_windows_launcher_cuts_off_is_still_the_running_jzr(self) -> None:
        exe = self.folder / "jzr.exe"
        exe.write_bytes(b"")
        other = self.folder / "other" / "jzr.exe"

        command = _setup().jzr_command(str(self.folder / "jzr"), which=lambda name: str(other))

        self.assertEqual(command, [str(exe)])

    def test_a_missing_argv0_falls_back_to_the_jzr_found_on_path(self) -> None:
        found = self.folder / "bin" / "jzr"
        asked: list[str] = []

        def which(name: str) -> str | None:
            asked.append(name)
            return str(found)

        command = _setup().jzr_command(str(self.folder / "nowhere" / "jzr"), which=which)

        self.assertEqual(command, [str(found)])
        self.assertEqual(asked, ["jzr"])

    def test_a_script_that_is_not_jzr_is_not_used_as_the_command(self) -> None:
        script = self.folder / "__main__.py"
        script.write_text("", encoding="utf-8")
        found = self.folder / "bin" / "jzr"

        command = _setup().jzr_command(str(script), which=lambda name: str(found))

        self.assertEqual(command, [str(found)])

    def test_without_jzr_anywhere_it_launches_the_module_with_this_interpreter(self) -> None:
        command = _setup().jzr_command(str(self.folder / "nowhere" / "jzr"), which=lambda name: None)

        self.assertEqual(command, [sys.executable, "-m", "jzr_tools"])


class RegisterTests(unittest.TestCase):
    CLAUDE = "/usr/bin/claude"
    SERVER = ["/opt/jzr", "agent", "mcp"]

    def register(self, run: FakeRun):
        setup = _setup()
        return setup.register(self.CLAUDE, self.SERVER, run=run)

    def test_registration_is_a_frozen_value_with_an_empty_default_detail(self) -> None:
        setup = _setup()
        item = setup.Registration("added")

        self.assertEqual((item.status, item.detail), ("added", ""))
        self.assertEqual(item, setup.Registration("added", ""))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            item.status = "failed"  # type: ignore[misc]

    def test_exit_zero_is_added_and_runs_exactly_the_add_command(self) -> None:
        run = FakeRun(0, out="Added stdio MCP server jzr\n")

        result = self.register(run)

        self.assertEqual((result.status, result.detail), ("added", ""))
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(run.calls[0][0], _setup().add_command(self.CLAUDE, self.SERVER))

    def test_the_child_never_inherits_the_terminal_and_is_not_run_through_a_shell(self) -> None:
        run = FakeRun(0)

        self.register(run)

        kwargs = run.calls[0][1]
        stdin_closed = kwargs.get("stdin") == subprocess.DEVNULL or kwargs.get("input") is not None
        self.assertTrue(stdin_closed, f"stdin must not be inherited: {kwargs!r}")
        self.assertFalse(kwargs.get("shell"), kwargs)

    def test_already_exists_in_stderr_is_exists_even_with_a_failing_exit(self) -> None:
        run = FakeRun(1, err="MCP server jzr already exists in user config\n")

        result = self.register(run)

        self.assertEqual(result.status, "exists")

    def test_already_exists_in_stdout_is_exists_too(self) -> None:
        run = FakeRun(1, out="MCP server jzr already exists in user config\n")

        self.assertEqual(self.register(run).status, "exists")

    def test_any_other_failure_reports_the_last_line_of_the_output(self) -> None:
        run = FakeRun(1, err="first line\nsecond line: cannot write config\n\n")

        result = self.register(run)

        self.assertEqual((result.status, result.detail), ("failed", "second line: cannot write config"))

    def test_a_failure_with_no_output_is_still_failed(self) -> None:
        result = self.register(FakeRun(3))

        self.assertEqual(result.status, "failed")
        self.assertIsInstance(result.detail, str)

    def test_odd_bytes_in_the_output_never_raise_and_do_not_hide_exists(self) -> None:
        run = FakeRun(1, err=b"caf\xe9 \x81\x8d \xff\xfe already exists\n")

        result = self.register(run)

        self.assertEqual(result.status, "exists")

    def test_odd_bytes_in_a_failure_message_still_give_a_failed_result_with_readable_text(self) -> None:
        run = FakeRun(1, err=b"starting\ncaf\xe9 \xff broke\n")

        result = self.register(run)

        self.assertEqual(result.status, "failed")
        self.assertIn("broke", result.detail)
        self.assertNotIn("starting", result.detail)

    def test_an_oserror_is_failed_with_its_message_not_an_exception(self) -> None:
        run = FakeRun(raises=OSError("The system cannot find the file specified"))

        result = self.register(run)

        self.assertEqual(result.status, "failed")
        self.assertIn("cannot find the file", result.detail)


class UnregisterTests(unittest.TestCase):
    CLAUDE = "/usr/bin/claude"

    def unregister(self, run: FakeRun):
        return _setup().unregister(self.CLAUDE, run=run)

    def test_exit_zero_is_removed_and_runs_exactly_the_remove_command(self) -> None:
        run = FakeRun(0, out="Removed MCP server jzr\n")

        result = self.unregister(run)

        self.assertEqual((result.status, result.detail), ("removed", ""))
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(run.calls[0][0], _setup().remove_command(self.CLAUDE))

    def test_the_child_never_inherits_the_terminal_and_its_output_is_captured(self) -> None:
        run = FakeRun(0)

        self.unregister(run)

        kwargs = run.calls[0][1]
        self.assertTrue(kwargs.get("stdin") == subprocess.DEVNULL or kwargs.get("input") is not None, kwargs)

    def test_no_server_found_is_absent(self) -> None:
        for stream in ("out", "err"):
            with self.subTest(stream=stream):
                run = FakeRun(1, **{stream: "No MCP server found with name: jzr\n"})

                self.assertEqual(self.unregister(run).status, "absent")

    def test_the_message_of_claude_2_1_is_absent_too(self) -> None:
        run = FakeRun(1, err='No MCP server named "jzr" in user scope\n')

        self.assertEqual(self.unregister(run).status, "absent")

    def test_another_failure_reports_the_last_output_line(self) -> None:
        run = FakeRun(1, err="warming up\ncannot write config\n")

        result = self.unregister(run)

        self.assertEqual((result.status, result.detail), ("failed", "cannot write config"))

    def test_a_message_with_no_but_not_found_is_failed(self) -> None:
        run = FakeRun(1, err="No permission to write the config\n")

        self.assertEqual(self.unregister(run).status, "failed")

    def test_odd_bytes_never_raise(self) -> None:
        run = FakeRun(1, err=b"\xff\xfe No MCP server found with name: jzr \x81\n")

        self.assertEqual(self.unregister(run).status, "absent")

    def test_an_oserror_is_failed(self) -> None:
        result = self.unregister(FakeRun(raises=OSError("boom")))

        self.assertEqual(result.status, "failed")
        self.assertIn("boom", result.detail)


CODEX = "/usr/local/bin/codex"
CODE = "/usr/local/bin/code"
SERVER = ["/opt/jzr", "agent", "mcp"]
CURSOR_PREFIX = "cursor://anysphere.cursor-deeplink/mcp/install?name=jzr&config="


class CodexCommandTests(unittest.TestCase):
    def test_add_command_puts_the_server_after_a_double_dash(self) -> None:
        self.assertEqual(
            _setup().codex_add_command(CODEX, SERVER),
            [CODEX, "mcp", "add", "jzr", "--", "/opt/jzr", "agent", "mcp"],
        )

    def test_add_command_keeps_a_module_launch_and_a_spaced_path_as_separate_words(self) -> None:
        command = _setup().codex_add_command("C:\\Program Files\\codex.cmd", WINDOWS_SERVER)

        self.assertEqual(command[0], "C:\\Program Files\\codex.cmd")
        self.assertEqual(command[command.index("--") + 1 :], WINDOWS_SERVER)

    def test_remove_command_names_the_server(self) -> None:
        self.assertEqual(_setup().codex_remove_command(CODEX), [CODEX, "mcp", "remove", "jzr"])

    def test_the_builders_leave_their_input_alone(self) -> None:
        server = list(SERVER)

        _setup().codex_add_command(CODEX, server)

        self.assertEqual(server, SERVER)


class VscodeCommandTests(unittest.TestCase):
    def test_add_command_is_one_option_and_one_json_argument(self) -> None:
        command = _setup().vscode_add_command(CODE, SERVER)

        self.assertEqual(len(command), 3, command)
        self.assertEqual(command[:2], [CODE, "--add-mcp"])

    def test_the_json_names_the_server_and_how_to_start_it(self) -> None:
        document = json.loads(_setup().vscode_add_command(CODE, SERVER)[2])

        self.assertEqual(
            document, {"name": "jzr", "type": "stdio", "command": "/opt/jzr", "args": ["agent", "mcp"]}
        )
        self.assertEqual(list(document), ["name", "type", "command", "args"])

    def test_a_windows_path_with_spaces_and_a_module_launch_survive_the_round_trip(self) -> None:
        document = json.loads(_setup().vscode_add_command(CODE, WINDOWS_SERVER)[2])
        self.assertEqual(document["command"], WINDOWS_SERVER[0])
        self.assertEqual(document["args"], ["agent", "mcp"])

        launch = ["/py/python", "-m", "jzr_tools", "agent", "mcp"]
        document = json.loads(_setup().vscode_add_command(CODE, launch)[2])
        self.assertEqual(document["command"], "/py/python")
        self.assertEqual(document["args"], ["-m", "jzr_tools", "agent", "mcp"])


class CursorTests(unittest.TestCase):
    PATHOLOGICAL = ["/opt/my tools/jzr?x>y", "agent", "mcp"]

    def test_entry_gives_back_the_command_and_arguments(self) -> None:
        text = _setup().cursor_entry(SERVER)

        entry = _jzr_entry(json.loads(text))
        self.assertEqual(entry["command"], "/opt/jzr")
        self.assertEqual(entry["args"], ["agent", "mcp"])

    def test_entry_keeps_windows_backslashes_through_the_round_trip(self) -> None:
        entry = _jzr_entry(json.loads(_setup().cursor_entry(WINDOWS_SERVER)))

        self.assertEqual(entry["command"], WINDOWS_SERVER[0])
        self.assertEqual(entry["args"], ["agent", "mcp"])

    def test_the_link_starts_with_the_cursor_install_address_for_jzr(self) -> None:
        link = _setup().cursor_link(SERVER)

        self.assertTrue(link.startswith(CURSOR_PREFIX), link)

    def test_the_link_config_is_standard_base64_of_compact_json_and_is_not_percent_encoded(self) -> None:
        compact = json.dumps(
            {"command": self.PATHOLOGICAL[0], "args": self.PATHOLOGICAL[1:]}, separators=(",", ":")
        ).encode("utf-8")
        standard = base64.b64encode(compact).decode("ascii")
        # The sample must tell the standard alphabet and padding from the URL-safe form.
        self.assertNotEqual(standard, base64.urlsafe_b64encode(compact).decode("ascii"))

        link = _setup().cursor_link(self.PATHOLOGICAL)

        self.assertEqual(link, CURSOR_PREFIX + standard)

    def test_the_link_config_decodes_to_the_command_and_arguments_and_nothing_else(self) -> None:
        link = _setup().cursor_link(WINDOWS_SERVER)

        config = link[len(CURSOR_PREFIX):]
        decoded = base64.b64decode(config, validate=True).decode("utf-8")
        self.assertEqual(decoded, json.dumps({"command": WINDOWS_SERVER[0], "args": ["agent", "mcp"]}, separators=(",", ":")))
        self.assertEqual(list(json.loads(decoded)), ["command", "args"])


class VscodePathTests(unittest.TestCase):
    def test_each_platform_has_its_own_user_mcp_file(self) -> None:
        setup = _setup()

        self.assertEqual(setup.vscode_path("win32"), "%APPDATA%\\Code\\User\\mcp.json")
        self.assertEqual(setup.vscode_path("darwin"), "~/Library/Application Support/Code/User/mcp.json")
        self.assertEqual(setup.vscode_path("linux"), "~/.config/Code/User/mcp.json")

    def test_an_unknown_platform_gets_the_linux_path(self) -> None:
        self.assertEqual(_setup().vscode_path("freebsd14"), "~/.config/Code/User/mcp.json")


class RegisterCodexTests(unittest.TestCase):
    def test_added_in_the_output_is_added_and_exactly_the_add_command_runs(self) -> None:
        run = FakeRun(0, out="Added global MCP server 'jzr'.\n")

        result = _setup().register_codex(CODEX, SERVER, run=run)

        self.assertEqual((result.status, result.detail), ("added", ""))
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(run.calls[0][0], _setup().codex_add_command(CODEX, SERVER))

    def test_the_child_never_inherits_the_terminal_and_is_not_run_through_a_shell(self) -> None:
        run = FakeRun(0, out="Added global MCP server 'jzr'.\n")

        _setup().register_codex(CODEX, SERVER, run=run)

        kwargs = run.calls[0][1]
        self.assertTrue(kwargs.get("stdin") == subprocess.DEVNULL or kwargs.get("input") is not None, kwargs)
        self.assertFalse(kwargs.get("shell"), kwargs)

    def test_a_failing_exit_reports_the_last_output_line(self) -> None:
        run = FakeRun(1, err="loading config\nerror: cannot write config.toml\n")

        result = _setup().register_codex(CODEX, SERVER, run=run)

        self.assertEqual((result.status, result.detail), ("failed", "error: cannot write config.toml"))

    def test_an_exit_zero_without_the_added_line_is_not_a_success(self) -> None:
        run = FakeRun(0, out="something unexpected\n")

        result = _setup().register_codex(CODEX, SERVER, run=run)

        self.assertEqual((result.status, result.detail), ("failed", "something unexpected"))

    def test_odd_bytes_in_the_output_never_raise(self) -> None:
        run = FakeRun(0, out=b"\xff\xfe Added global MCP server 'jzr'. \x81\n")

        self.assertEqual(_setup().register_codex(CODEX, SERVER, run=run).status, "added")

    def test_a_program_that_cannot_be_started_is_failed_with_its_message(self) -> None:
        run = FakeRun(raises=OSError("The system cannot find the file specified"))

        result = _setup().register_codex(CODEX, SERVER, run=run)

        self.assertEqual(result.status, "failed")
        self.assertIn("cannot find the file", result.detail)


class UnregisterCodexTests(unittest.TestCase):
    def test_removed_in_the_output_is_removed_and_exactly_the_remove_command_runs(self) -> None:
        run = FakeRun(0, out="Removed global MCP server 'jzr'.\n")

        result = _setup().unregister_codex(CODEX, run=run)

        self.assertEqual((result.status, result.detail), ("removed", ""))
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(run.calls[0][0], _setup().codex_remove_command(CODEX))

    def test_no_server_named_jzr_is_absent_even_though_codex_exits_zero(self) -> None:
        run = FakeRun(0, out="No MCP server named 'jzr' found.\n")

        result = _setup().unregister_codex(CODEX, run=run)

        self.assertEqual((result.status, result.detail), ("absent", ""))

    def test_the_child_never_inherits_the_terminal(self) -> None:
        run = FakeRun(0, out="Removed global MCP server 'jzr'.\n")

        _setup().unregister_codex(CODEX, run=run)

        kwargs = run.calls[0][1]
        self.assertTrue(kwargs.get("stdin") == subprocess.DEVNULL or kwargs.get("input") is not None, kwargs)
        self.assertFalse(kwargs.get("shell"), kwargs)

    def test_any_other_output_is_failed_with_its_last_line(self) -> None:
        run = FakeRun(1, err="starting\nerror: config is locked\n")

        result = _setup().unregister_codex(CODEX, run=run)

        self.assertEqual((result.status, result.detail), ("failed", "error: config is locked"))

    def test_a_program_that_cannot_be_started_is_failed(self) -> None:
        result = _setup().unregister_codex(CODEX, run=FakeRun(raises=OSError("boom")))

        self.assertEqual(result.status, "failed")
        self.assertIn("boom", result.detail)


class RegisterVscodeTests(unittest.TestCase):
    def test_the_added_mcp_servers_line_is_added_and_exactly_the_add_command_runs(self) -> None:
        run = FakeRun(0, out="Added MCP servers: jzr\n")

        result = _setup().register_vscode(CODE, SERVER, run=run)

        self.assertEqual((result.status, result.detail), ("added", ""))
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(run.calls[0][0], _setup().vscode_add_command(CODE, SERVER))

    def test_the_child_never_inherits_the_terminal_and_is_not_run_through_a_shell(self) -> None:
        run = FakeRun(0, out="Added MCP servers: jzr\n")

        _setup().register_vscode(CODE, SERVER, run=run)

        kwargs = run.calls[0][1]
        self.assertTrue(kwargs.get("stdin") == subprocess.DEVNULL or kwargs.get("input") is not None, kwargs)
        self.assertFalse(kwargs.get("shell"), kwargs)

    def test_bad_json_with_exit_zero_is_failed_with_the_message(self) -> None:
        run = FakeRun(0, out="Invalid JSON: Unexpected token } in JSON at position 12\n")

        result = _setup().register_vscode(CODE, SERVER, run=run)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.detail, "Invalid JSON: Unexpected token } in JSON at position 12")

    def test_no_output_at_all_is_failed_not_added(self) -> None:
        result = _setup().register_vscode(CODE, SERVER, run=FakeRun(0))

        self.assertEqual(result.status, "failed")
        self.assertIsInstance(result.detail, str)

    def test_a_failing_exit_reports_the_last_line(self) -> None:
        run = FakeRun(1, err="cannot open the profile\n")

        result = _setup().register_vscode(CODE, SERVER, run=run)

        self.assertEqual((result.status, result.detail), ("failed", "cannot open the profile"))

    def test_odd_bytes_in_the_output_never_raise(self) -> None:
        run = FakeRun(0, out=b"\xff\xfe Added MCP servers: jzr \x81\n")

        self.assertEqual(_setup().register_vscode(CODE, SERVER, run=run).status, "added")

    def test_a_program_that_cannot_be_started_is_failed(self) -> None:
        result = _setup().register_vscode(CODE, SERVER, run=FakeRun(raises=OSError("no such file")))

        self.assertEqual(result.status, "failed")
        self.assertIn("no such file", result.detail)


class NewHelpersStayLightTests(unittest.TestCase):
    def test_the_builders_import_neither_click_nor_mcp(self) -> None:
        code = (
            "import sys\n"
            "from jzr_tools.core import mcp_setup as m\n"
            "s = ['a', 'b']\n"
            "m.codex_add_command('codex', s)\n"
            "m.codex_remove_command('codex')\n"
            "m.vscode_add_command('code', s)\n"
            "m.cursor_entry(s)\n"
            "m.cursor_link(s)\n"
            "m.vscode_path('win32')\n"
            "bad = sorted(x for x in sys.modules if x.split('.')[0] in ('click', 'mcp', 'mcp_types'))\n"
            "print('@@BAD@@' + ','.join(bad))\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_PATH)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        line = next(item for item in proc.stdout.splitlines() if item.startswith("@@BAD@@"))
        self.assertEqual(line, "@@BAD@@")


class AvailabilityTests(unittest.TestCase):
    def test_the_extra_text_names_the_extra_and_both_install_tools(self) -> None:
        self.assertEqual(_setup().NEEDS_EXTRA, NEEDS_EXTRA_TEXT)

    def test_available_is_false_when_the_package_cannot_be_found(self) -> None:
        with mock.patch.dict(sys.modules, {"mcp": None}):
            self.assertIs(_setup().available(), False)

    @unittest.skipUnless(_mcp_installed(), "the mcp package is not installed")
    def test_available_is_true_when_the_package_is_installed(self) -> None:
        self.assertIs(_setup().available(), True)

    def test_the_module_imports_neither_click_nor_mcp_and_available_does_not_import_mcp(self) -> None:
        code = (
            "import sys\n"
            "from jzr_tools.core import mcp_setup\n"
            "mcp_setup.available()\n"
            "mcp_setup.desktop_entry(['a', 'b'])\n"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('click', 'mcp', 'mcp_types'))\n"
            "print('@@BAD@@' + ','.join(bad))\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_PATH)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        line = next(item for item in proc.stdout.splitlines() if item.startswith("@@BAD@@"))
        self.assertEqual(line, "@@BAD@@")


if __name__ == "__main__":
    unittest.main()
