"""Install shell completion: find the shell, write the script, edit start-up files.

The completion script itself is made by the Click layer and passed in as
text; this module only decides where it goes and how a start-up file loads
it. bash, zsh and PowerShell get the script in jzr's data folder plus a
marked block in their start-up file; fish loads its completions folder on
its own, so only the script is written there.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile

from jzr_tools.core.paths import data_dir

SHELLS = ("bash", "zsh", "fish", "powershell")
MARK_START = "# >>> jzr completion >>>"
MARK_END = "# <<< jzr completion <<<"

# Processes between the shell and jzr that are not the shell.
_LAUNCHERS = frozenset({"python", "python3", "pythonw", "py", "uv", "uvx", "pipx", "jzr"})
_VERSIONED_PYTHON = re.compile(r"python3\.\d+")
_SCRIPT_NAMES = {"bash": "jzr.bash", "zsh": "jzr.zsh", "powershell": "jzr.ps1"}
_SUPPORTED = "supported: " + ", ".join(SHELLS)
_UNDETECTED = "could not tell which shell this is; name it: jzr completion install SHELL"
_ZSH_COMPINIT = "(( $+functions[compdef] )) || { autoload -Uz compinit && compinit; }"
_BOM = "\ufeff"
_QUIET_POLICIES = ("Restricted", "AllSigned")


class CompletionError(Exception):
    """A user-facing failure; the message is the whole explanation."""


@dataclass(frozen=True)
class Ancestor:
    """One parent process: its name and, when readable, its executable."""

    name: str
    exe: str = ""


@dataclass(frozen=True)
class Shell:
    """The shell to set up and how it was found."""

    name: str
    exe: str | None
    source: str


@dataclass(frozen=True)
class PowerShellInfo:
    """What a PowerShell reports about itself."""

    profile: Path
    policy: str


@dataclass(frozen=True)
class Change:
    """One file install or remove would touch, and its new bytes."""

    path: Path
    action: str
    kind: str
    content: bytes | None = None
    replaced: bool = False


def parent_processes(limit: int = 10) -> list[Ancestor]:
    """Return this process's ancestors, nearest first, as far as they can be read."""
    import psutil

    found: list[Ancestor] = []
    try:
        process = psutil.Process().parent()
        while process is not None and len(found) < limit:
            try:
                name = process.name()
            except psutil.Error:
                break
            try:
                exe = process.exe()
            except psutil.Error:
                exe = ""
            found.append(Ancestor(name, exe))
            process = process.parent()
    except psutil.Error:
        pass
    return found


def _normalise(name: str) -> str:
    name = name.strip().lower()
    return name[: -len(".exe")] if name.endswith(".exe") else name


def _is_launcher(name: str) -> bool:
    return name in _LAUNCHERS or bool(_VERSIONED_PYTHON.fullmatch(name))


def _shell_from_name(name: str, exe: str | None, source: str) -> Shell:
    if name in ("pwsh", "powershell"):
        return Shell("powershell", exe, source)
    if name in ("bash", "zsh", "fish"):
        return Shell(name, exe if name == "bash" else None, source)
    if name == "cmd":
        raise CompletionError(
            "cmd.exe has no programmable completion; use PowerShell, "
            "or name a shell: jzr completion install SHELL"
        )
    raise CompletionError(f"{name} is not supported; {_SUPPORTED}")


def detect_shell(ancestors: Sequence[Ancestor], *, environ: Mapping[str, str], platform: str) -> Shell:
    """Pick the shell from the nearest ancestor that is not a launcher."""
    for ancestor in ancestors:
        name = _normalise(ancestor.name)
        if not name or _is_launcher(name):
            continue
        return _shell_from_name(name, ancestor.exe or ancestor.name, "parent process")
    login = environ.get("SHELL", "")
    if platform != "win32" and login:
        return _shell_from_name(_normalise(os.path.basename(login)), None, "from $SHELL")
    raise CompletionError(_UNDETECTED)


def named_shell(name: str) -> Shell:
    """The shell the user named on the command line."""
    return Shell(name, None, "named")


def home_dir(environ: Mapping[str, str]) -> Path:
    """The user's home: $HOME when set (Git Bash sets it on Windows too)."""
    return Path(environ["HOME"]) if environ.get("HOME") else Path.home()


def script_file(shell: str, *, environ: Mapping[str, str], platform: str) -> Path:
    """Where the completion script for ``shell`` is written."""
    if shell == "fish":
        configured = environ.get("XDG_CONFIG_HOME", "")
        base = Path(configured) if os.path.isabs(configured) else home_dir(environ) / ".config"
        return base / "fish" / "completions" / "jzr.fish"
    return data_dir(environ, platform) / "completion" / _SCRIPT_NAMES[shell]


def startup_file(shell: str, *, environ: Mapping[str, str], profile: Path | None = None) -> Path | None:
    """The start-up file that loads the script; fish has none."""
    if shell == "bash":
        return home_dir(environ) / ".bashrc"
    if shell == "zsh":
        zdotdir = environ.get("ZDOTDIR")
        return (Path(zdotdir) if zdotdir else home_dir(environ)) / ".zshrc"
    if shell == "powershell":
        if profile is None:
            raise ValueError("PowerShell needs its profile path")
        return profile
    return None


def header(shell: str, version: str) -> str:
    """The first line of every script file jzr writes."""
    return (
        f"# jzr completion for {shell}, written by jzr {version}; "
        "re-run 'jzr completion install' after upgrading"
    )


def block_lines(shell: str, script: Path, *, platform: str) -> list[str]:
    """The lines inside the marked block that load ``script``."""
    if shell == "powershell":
        quoted = str(script).replace("'", "''")
        return [f"if (Test-Path '{quoted}') {{ . '{quoted}' }}"]
    # Git Bash reads C:/Users/... but not C:\Users\...
    path = script.as_posix() if platform == "win32" else str(script)
    load = f'[ -f "{path}" ] && . "{path}"'
    return [_ZSH_COMPINIT, load] if shell == "zsh" else [load]


def _line_ending(text: str, default: str) -> str:
    match = re.search(r"\r?\n", text)
    return match.group(0) if match else default


def _find_block(lines: list[str]) -> tuple[int, int] | None:
    """Index of the start marker line and of the end marker line, if both are there."""
    for start, line in enumerate(lines):
        if line.rstrip("\r\n") == MARK_START:
            for end in range(start + 1, len(lines)):
                if lines[end].rstrip("\r\n") == MARK_END:
                    return start, end
            return None
    return None


def add_block(text: str, lines: Sequence[str], *, newline: str = "\n") -> str:
    """Put the marked block in ``text``: in place of an old one, else at the end."""
    bom = _BOM if text.startswith(_BOM) else ""
    body = text[len(bom):]
    eol = _line_ending(body, newline)
    block = eol.join([MARK_START, *lines, MARK_END]) + eol
    rows = body.splitlines(keepends=True)
    found = _find_block(rows)
    if found is not None:
        start, end = found
        after = rows[end + 1 :]
        return bom + "".join(rows[:start]) + block + "".join(after)
    if body and not body.endswith(("\n", "\r")):
        body += eol
    return bom + body + block


def remove_block(text: str) -> str:
    """Drop the marked block from ``text``; text without one is returned as is."""
    rows = text.splitlines(keepends=True)
    found = _find_block([row.removeprefix(_BOM) for row in rows])
    if found is None:
        return text
    start, end = found
    bom = _BOM if start == 0 and text.startswith(_BOM) else ""
    return bom + "".join(rows[:start]) + "".join(rows[end + 1 :])


def powershell_info(exe: str, *, timeout: float = 10.0) -> PowerShellInfo:
    """Ask ``exe`` for its all-hosts profile path and its execution policy."""
    import subprocess

    expression = "$PROFILE.CurrentUserAllHosts; (Get-ExecutionPolicy).ToString()"
    command = [exe, "-NoProfile", "-NoLogo", "-NonInteractive", "-Command", expression]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise CompletionError(f"{exe} did not answer within {timeout:g} s") from None
    except OSError as error:
        raise CompletionError(f"could not run {exe}: {error.strerror or error}") from None
    lines = [line.strip() for line in (done.stdout or "").splitlines() if line.strip()]
    if done.returncode != 0 or len(lines) < 2:
        raise CompletionError(f"{exe} did not report its profile and execution policy")
    return PowerShellInfo(Path(lines[0]), lines[1])


def powershells_on_path(*, platform: str) -> list[str]:
    """Every PowerShell on PATH: pwsh, and Windows PowerShell on Windows."""
    import shutil

    names = ["pwsh", "powershell"] if platform == "win32" else ["pwsh"]
    return [found for found in (shutil.which(name) for name in names) if found]


def bash_version(exe: str | None) -> tuple[int, int] | None:
    """Major and minor version of ``exe`` (or bash on PATH), or None."""
    import subprocess

    try:
        done = subprocess.run(
            [exe or "bash", "--version"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    first = (done.stdout or "").splitlines()[:1]
    match = re.search(r"bash, version (\d+)\.(\d+)", first[0]) if first else None
    return (int(match.group(1)), int(match.group(2))) if match else None


def _read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CompletionError(f"cannot read {path}: {error.strerror or error}") from None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise CompletionError(f"{path} is not UTF-8 text; left it unchanged") from None


def _file_change(path: Path, kind: str, content: bytes, *, replaced: bool = False) -> Change:
    try:
        current = path.read_bytes()
    except FileNotFoundError:
        return Change(path, "write", kind, content, replaced)
    except OSError as error:
        raise CompletionError(f"cannot read {path}: {error.strerror or error}") from None
    if current == content:
        return Change(path, "unchanged", kind, None, replaced)
    return Change(path, "update", kind, content, replaced)


def plan_install(
    shell: str,
    source: str,
    version: str,
    *,
    environ: Mapping[str, str],
    platform: str,
    profile: Path | None = None,
) -> list[Change]:
    """The changes that install completion for ``shell``; nothing is written."""
    script = script_file(shell, environ=environ, platform=platform)
    script_text = header(shell, version) + "\n" + source
    changes = [_file_change(script, "script", script_text.encode("utf-8"))]
    startup = startup_file(shell, environ=environ, profile=profile)
    if startup is None:
        return changes
    current = _read_text(startup)
    newline = "\r\n" if shell == "powershell" and platform == "win32" else "\n"
    lines = block_lines(shell, script, platform=platform)
    text = add_block(current or "", lines, newline=newline)
    had_block = current is not None and _find_block(current.removeprefix(_BOM).splitlines(keepends=True)) is not None
    if current == text:
        changes.append(Change(startup, "unchanged", "startup", None, had_block))
    else:
        action = "write" if current is None else "update"
        changes.append(Change(startup, action, "startup", text.encode("utf-8"), had_block))
    return changes


def plan_remove(
    shell: str,
    *,
    environ: Mapping[str, str],
    platform: str,
    profile: Path | None = None,
) -> list[Change]:
    """The changes that remove jzr's completion for ``shell``; nothing is written."""
    script = script_file(shell, environ=environ, platform=platform)
    if shell == "fish":
        text = _read_text(script)
        if text is None:
            return [Change(script, "absent", "script")]
        if not text.startswith("# jzr completion for fish"):
            raise CompletionError(f"{script} is not jzr's; left it")
        return [Change(script, "delete", "script")]
    changes = [Change(script, "delete" if script.exists() else "absent", "script")]
    startup = startup_file(shell, environ=environ, profile=profile)
    current = _read_text(startup)
    if current is None:
        changes.append(Change(startup, "absent", "startup"))
    else:
        text = remove_block(current)
        if text == current:
            changes.append(Change(startup, "unchanged", "startup"))
        else:
            changes.append(Change(startup, "update", "startup", text.encode("utf-8"), True))
    return changes


def _write(path: Path, content: bytes) -> None:
    # Through a symlink to its target, so the link itself stays a link.
    target = Path(os.path.realpath(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".jzr-tmp-", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(content)
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def apply(changes: Sequence[Change]) -> None:
    """Write, update or delete the files in ``changes``, in order."""
    created: list[Path] = []
    for change in changes:
        try:
            if change.action in ("write", "update"):
                _write(change.path, change.content or b"")
                if change.action == "write" and change.kind == "script":
                    created.append(change.path)
            elif change.action == "delete":
                change.path.unlink(missing_ok=True)
        except OSError as error:
            for path in created:
                try:
                    path.unlink()
                except OSError:
                    pass
            raise CompletionError(f"cannot write {change.path}: {error.strerror or error}") from None


_GIT_BASH_NOTE = (
    "Note: the next Git Bash window will say it found ~/.bashrc but no "
    "~/.bash_profile and create one that loads ~/.bashrc. That is expected."
)


def install_notes(shell: str, *, environ: Mapping[str, str], platform: str) -> list[str]:
    """Things the user will see after installing that are expected, not problems."""
    if shell != "bash" or platform != "win32":
        return []
    home = home_dir(environ)
    # Git for Windows' /etc/profile.d/bash_profile.sh warns and writes a
    # ~/.bash_profile when a login shell finds ~/.bashrc and none of these.
    if any((home / name).exists() for name in (".bash_profile", ".bash_login", ".profile")):
        return []
    return [_GIT_BASH_NOTE]


def install_warnings(
    shell: str,
    *,
    environ: Mapping[str, str],
    policy: str | None = None,
    bash: tuple[int, int] | None = None,
) -> list[str]:
    """Things that will keep completion from loading, which jzr does not fix."""
    found: list[str] = []
    if shell == "powershell" and policy in _QUIET_POLICIES:
        found.append(
            f"PowerShell will not run the profile while the execution policy is {policy}. "
            "To allow it for your user: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"
        )
    if shell == "bash":
        if bash is not None and bash < (4, 4):
            found.append(
                f"bash {bash[0]}.{bash[1]} is too old for jzr completion (4.4 or newer is needed)"
            )
        profile = home_dir(environ) / ".bash_profile"
        try:
            text = profile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = None
        if text is not None and ".bashrc" not in text:
            found.append(
                "~/.bash_profile does not load ~/.bashrc, so login shells will not get "
                "completion; add: [ -f ~/.bashrc ] && . ~/.bashrc"
            )
    return found
