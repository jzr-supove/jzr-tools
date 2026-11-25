"""Tell AI clients how to start jzr's MCP server.

Claude Code, Codex and VS Code are changed through their own command lines.
The settings files of Cursor and Claude Desktop are never edited; the entry to
add is only shown.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import base64
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

SERVER_NAME = "jzr"
NEEDS_EXTRA = (
    'the MCP server needs the mcp extra: uv tool install "jzr-tools[mcp]" '
    '(or pipx install "jzr-tools[mcp]")'
)


@dataclass(frozen=True)
class Registration:
    """``status`` is added, exists, removed, absent or failed (with the reason)."""

    status: str
    detail: str = ""


def available() -> bool:
    """Whether the optional ``mcp`` package is installed. Does not import it."""
    return importlib.util.find_spec("mcp") is not None


def jzr_command(argv0: str, *, which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """How another program starts this jzr: an absolute path, whatever its PATH is."""
    running = Path(argv0)
    if running.name.lower() in ("jzr", "jzr.exe"):
        # The launcher on Windows cuts .exe off sys.argv[0].
        for path in (running, running.with_name("jzr.exe")):
            if path.is_file():
                return [str(path.absolute())]
    found = which("jzr")
    if found:
        return [str(Path(found).absolute())]
    return [sys.executable, "-m", "jzr_tools"]


def server_command(jzr: list[str]) -> list[str]:
    return [*jzr, "agent", "mcp"]


def add_command(claude: str, server: list[str]) -> list[str]:
    return [claude, "mcp", "add", "-s", "user", SERVER_NAME, "--", *server]


def remove_command(claude: str) -> list[str]:
    return [claude, "mcp", "remove", "-s", "user", SERVER_NAME]


def desktop_entry(server: list[str]) -> str:
    """What Claude Desktop's settings file needs, as JSON."""
    entry = {"command": server[0], "args": server[1:]}
    return json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2, ensure_ascii=False)


def desktop_path(platform: str) -> str:
    """Where Claude Desktop's settings file usually is, for showing."""
    if platform == "win32":
        return r"%APPDATA%\Claude\claude_desktop_config.json"
    if platform == "darwin":
        return "~/Library/Application Support/Claude/claude_desktop_config.json"
    return "~/.config/Claude/claude_desktop_config.json"


def codex_add_command(codex: str, server: list[str]) -> list[str]:
    return [codex, "mcp", "add", SERVER_NAME, "--", *server]


def codex_remove_command(codex: str) -> list[str]:
    return [codex, "mcp", "remove", SERVER_NAME]


def vscode_add_command(code: str, server: list[str]) -> list[str]:
    """``code --add-mcp`` with the server as one JSON argument."""
    entry = {"name": SERVER_NAME, "type": "stdio", "command": server[0], "args": server[1:]}
    return [code, "--add-mcp", json.dumps(entry, ensure_ascii=False)]


def vscode_path(platform: str) -> str:
    """Where VS Code keeps the user's MCP servers, for showing."""
    if platform == "win32":
        return r"%APPDATA%\Code\User\mcp.json"
    if platform == "darwin":
        return "~/Library/Application Support/Code/User/mcp.json"
    return "~/.config/Code/User/mcp.json"


def cursor_entry(server: list[str]) -> str:
    """What ``~/.cursor/mcp.json`` needs, as JSON."""
    return desktop_entry(server)


def cursor_link(server: list[str]) -> str:
    """Cursor's install link: the command as base64 of compact JSON."""
    config = json.dumps({"command": server[0], "args": server[1:]}, separators=(",", ":"), ensure_ascii=False)
    encoded = base64.b64encode(config.encode("utf-8")).decode("ascii")
    return f"cursor://anysphere.cursor-deeplink/mcp/install?name={SERVER_NAME}&config={encoded}"


def register(
    claude: str, server: list[str], *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> Registration:
    """Add the server to Claude Code, for all of the user's projects."""
    code, output = _run(add_command(claude, server), run)
    if code == 0:
        return Registration("added")
    if code is not None and "already exists" in output.lower():
        return Registration("exists")
    return Registration("failed", _last_line(output))


def unregister(
    claude: str, *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> Registration:
    """Take the server out of Claude Code again."""
    code, output = _run(remove_command(claude), run)
    if code == 0:
        return Registration("removed")
    # The wording differs between versions of claude.
    said = output.lower()
    if code is not None and ("no mcp server" in said or ("no " in said and "found" in said)):
        return Registration("absent")
    return Registration("failed", _last_line(output))


def register_codex(
    codex: str, server: list[str], *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> Registration:
    """Add the server to Codex. Codex replaces an entry of the same name."""
    code, output = _run(codex_add_command(codex, server), run)
    if code == 0 and "added" in output.lower():
        return Registration("added")
    return Registration("failed", _last_line(output))


def unregister_codex(
    codex: str, *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> Registration:
    """Take the server out of Codex; Codex exits 0 when there is none."""
    code, output = _run(codex_remove_command(codex), run)
    said = output.lower()
    if code == 0 and "no mcp server" in said:
        return Registration("absent")
    if code == 0 and "removed" in said:
        return Registration("removed")
    return Registration("failed", _last_line(output))


def register_vscode(
    code: str, server: list[str], *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> Registration:
    """Add the server to VS Code's user profile. VS Code exits 0 even on bad input."""
    exit_code, output = _run(vscode_add_command(code, server), run)
    if exit_code is not None and "added mcp servers" in output.lower():
        return Registration("added")
    return Registration("failed", _last_line(output))


def _run(
    command: list[str], run: Callable[..., subprocess.CompletedProcess]
) -> tuple[int | None, str]:
    """The exit code and all output, errors last; no code when the program cannot be started.

    The two streams are read apart: merged, a program's buffered output can
    arrive after its error message and hide it.
    """
    try:
        done = run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        return None, error.strerror or str(error)
    out, err = done.stdout or "", done.stderr or ""
    return done.returncode, out + ("\n" if out and err and not out.endswith("\n") else "") + err


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else "no reason given"
