"""`jzr completion`: set up shell tab completion, or print its script."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import sys

import click
from click.shell_completion import CompletionItem, ShellComplete, add_completion_class, get_completion_class

# The same fixed names `jzr_tools.cli.main` passes to Click.
PROG_NAME = "jzr"
COMPLETE_VAR = "_JZR_COMPLETE"
SHELLS = ("bash", "zsh", "fish", "powershell")

# Native completer for Windows PowerShell 5.1 and PowerShell 7. It passes the
# command line up to the cursor in COMP_WORDS, as typed, and turns the
# "type<TAB>value<TAB>help" lines back into completions. (Windows PowerShell
# 5.1 does not call native completers for a bare `-` or `--`.) When
# jzr asks for a file or directory, nothing is returned, so PowerShell falls
# back to its own path completion.
_SOURCE_POWERSHELL = """\
Register-ArgumentCompleter -Native -CommandName %(prog_name)s, %(prog_name)s.exe -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $line = $commandAst.Extent.Text
    $end = [Math]::Min($line.Length, $cursorPosition - $commandAst.Extent.StartOffset)
    if ($end -ge 0) { $line = $line.Substring(0, $end) }
    # A space at the end means a new word; PowerShell trims it from the AST.
    if ($cursorPosition -gt $commandAst.Extent.EndOffset) { $line += ' ' }
    $env:%(complete_var)s = 'powershell_complete'
    $env:COMP_WORDS = $line
    $env:COMP_CWORD = ''
    try {
        $lines = & %(prog_name)s 2>$null
    } finally {
        Remove-Item Env:%(complete_var)s, Env:COMP_WORDS, Env:COMP_CWORD -ErrorAction SilentlyContinue
    }
    foreach ($line in $lines) {
        $kind, $value, $help = $line -split "`t", 3
        if ($kind -ne 'plain' -or -not $value) { continue }
        $text = $value
        if ($value -match '\\s') { $text = "'" + ($value -replace "'", "''") + "'" }
        if (-not $help) { $help = $value }
        $type = 'ParameterValue'
        if ($value.StartsWith('-')) { $type = 'ParameterName' }
        [System.Management.Automation.CompletionResult]::new($text, $value, $type, $help)
    }
}
"""


def _split_words(line: str) -> list[str]:
    """Split a command line like a shell, keeping backslashes in Windows paths.

    An unclosed quote, as in a word still being typed, ends the last word.
    """
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    words: list[str] = []
    try:
        for word in lexer:
            words.append(word)
    except ValueError:
        words.append(lexer.token)
    return words


class PowerShellComplete(ShellComplete):
    """Click's bash protocol in, one tab-separated line per completion out."""

    name = "powershell"
    source_template = _SOURCE_POWERSHELL

    def get_completion_args(self) -> tuple[list[str], str]:
        line = os.environ.get("COMP_WORDS", "")
        words = _split_words(line)
        cword = os.environ.get("COMP_CWORD", "")
        if cword:
            index = int(cword)
        else:
            # The script sends the raw line up to the cursor: a trailing space
            # starts a new word.
            index = len(words) if not line or line[-1].isspace() else len(words) - 1
        args = words[1:index]
        incomplete = words[index] if index < len(words) else ""
        return args, incomplete

    def format_completion(self, item: CompletionItem) -> str:
        help_text = " ".join((item.help or "").split())
        return f"{item.type}\t{item.value}\t{help_text}"


# Importing this module is what makes `_JZR_COMPLETE=powershell_*` work.
add_completion_class(PowerShellComplete)


def script_source(shell: str) -> str:
    """The completion script for ``shell``, as `jzr completion SHELL` prints it."""
    from jzr_tools.cli import app as root

    completer = get_completion_class(shell)
    script = completer(root, {}, PROG_NAME, COMPLETE_VAR)
    # ShellComplete.source() for bash also runs bash to check its version;
    # the script itself does not need that.
    return completer.source_template % script.source_vars()


@click.group()
def app() -> None:
    """Set up shell completion, or print its script.

    \b
    Set it up for the shell you are typing in:
      jzr completion install

    \b
    Or load the script from your shell's start-up file yourself:
      bash        echo 'eval "$(jzr completion bash)"' >> ~/.bashrc
      zsh         echo 'eval "$(jzr completion zsh)"' >> ~/.zshrc
      fish        jzr completion fish > ~/.config/fish/completions/jzr.fish
      powershell  Add-Content $PROFILE 'jzr completion powershell | Out-String | Invoke-Expression'

    Open a new shell afterwards. Your own `jzr bind` shortcuts are not
    completed.
    """


def _print_command(shell: str) -> click.Command:
    def run() -> None:
        # Bytes, so the line endings stay "\n" on Windows too: bash cannot
        # read a script with "\r\n".
        click.echo(script_source(shell).encode("utf-8"), nl=False)

    return click.Command(shell, callback=run, help=f"Print the {shell} completion script.")


for _shell in SHELLS:
    app.add_command(_print_command(_shell))


def _shell_argument(command):  # noqa: ANN001, ANN202
    return click.argument("shell", metavar="[SHELL]", required=False, type=click.Choice(SHELLS))(command)


def _dry_run_option(command):  # noqa: ANN001, ANN202
    return click.option("--dry-run", is_flag=True, help="Show what would change; change nothing.")(command)


def _shown(path: Path, home: Path) -> str:
    """``path`` with the home folder written as ``~``."""
    try:
        return "~" + os.sep + str(path.relative_to(home))
    except ValueError:
        return str(path)


def _targets(shell: str | None) -> list:
    """The shells to work on: one, or every PowerShell when PowerShell is named."""
    from jzr_tools.core import completion as core

    if shell is None:
        return [core.detect_shell(core.parent_processes(), environ=os.environ, platform=sys.platform)]
    if shell != "powershell":
        return [core.named_shell(shell)]
    # Named PowerShell: the one being typed in, if it is one, else all of them.
    try:
        found = core.detect_shell(core.parent_processes(), environ=os.environ, platform=sys.platform)
    except core.CompletionError:
        found = None
    if found is not None and found.name == "powershell":
        return [found]
    everyone = core.powershells_on_path(platform=sys.platform)
    if not everyone:
        raise core.CompletionError("no PowerShell found on PATH")
    return [core.Shell("powershell", exe, "named") for exe in everyone]


def _shell_line(target) -> str:  # noqa: ANN001
    if target.name == "powershell" and target.exe:
        exe = target.exe.replace("\\", "/").rsplit("/", 1)[-1]
        return f"Shell: powershell ({exe}, {target.source})"
    return f"Shell: {target.name} ({target.source})"


# What each kind of change is called: (done, dry run).
_SCRIPT_WORDS = {"write": ("Wrote", "Would write"), "update": ("Updated", "Would update"),
                 "delete": ("Deleted", "Would delete")}
_STARTUP_WORDS = {
    "add": ("Added completion to", "Would add completion to"),
    "replace": ("Updated completion in", "Would update completion in"),
    "remove": ("Removed completion from", "Would remove completion from"),
}


def _describe(change, home: Path, dry_run: bool, installing: bool) -> str:  # noqa: ANN001
    if change.kind == "script":
        words = _SCRIPT_WORDS[change.action]
    elif not installing:
        words = _STARTUP_WORDS["remove"]
    else:
        words = _STARTUP_WORDS["replace" if change.replaced else "add"]
    return f"{words[1] if dry_run else words[0]} {_shown(change.path, home)}"


def _one(core, target, home: Path, dry_run: bool, installing: bool) -> None:  # noqa: ANN001
    """Install or remove completion for one shell."""
    from jzr_tools import __version__

    profile = policy = None
    if target.name == "powershell":
        info = core.powershell_info(target.exe or "pwsh")
        profile, policy = info.profile, info.policy
    if installing:
        changes = core.plan_install(
            target.name, script_source(target.name), __version__,
            environ=os.environ, platform=sys.platform, profile=profile,
        )
    else:
        changes = core.plan_remove(target.name, environ=os.environ, platform=sys.platform, profile=profile)

    todo = [change for change in changes if change.action in ("write", "update", "delete")]
    if not todo:
        if installing:
            where = next((c.path for c in changes if c.kind == "startup"), changes[0].path)
            click.echo(f"Completion for {target.name} is already set up ({_shown(where, home)}).")
        else:
            click.echo(f"Completion for {target.name} is not set up.")
        return

    if not dry_run:
        core.apply(changes)
    for change in todo:
        click.echo(_describe(change, home, dry_run, installing))
    if not installing:
        return
    for note in core.install_notes(target.name, environ=os.environ, platform=sys.platform):
        click.echo(note)
    if not dry_run:
        click.echo(f"Open a new terminal to use it. Undo: jzr completion remove {target.name}")
    bash = None
    # On Windows a bare `bash` may be the WSL launcher, so only a bash that
    # was found among the parent processes is asked for its version there.
    if target.name == "bash" and (target.exe or sys.platform != "win32"):
        bash = core.bash_version(target.exe)
    for warning in core.install_warnings(target.name, environ=os.environ, policy=policy, bash=bash):
        click.echo(f"warning: {warning}", err=True)


def _run(shell: str | None, dry_run: bool, *, installing: bool) -> None:
    from jzr_tools.core import completion as core

    try:
        targets = _targets(shell)
    except core.CompletionError as error:
        click.echo(f"error: {error}", err=True)
        raise SystemExit(1) from None
    home = core.home_dir(os.environ)
    failed = False
    for index, target in enumerate(targets):
        if index:
            click.echo()
        click.echo(_shell_line(target))
        try:
            _one(core, target, home, dry_run, installing)
        except core.CompletionError as error:
            click.echo(f"error: {error}", err=True)
            failed = True
    if failed:
        raise SystemExit(1)


@app.command()
@_shell_argument
@_dry_run_option
def install(shell: str | None, dry_run: bool) -> None:
    """Set up completion for SHELL, or for the shell you are typing in.

    SHELL is bash, zsh, fish or powershell. The script is written to jzr's
    data folder (fish: its completions folder), and one marked block in the
    shell's start-up file loads it. Running it again changes nothing.
    """
    _run(shell, dry_run, installing=True)


@app.command()
@_shell_argument
@_dry_run_option
def remove(shell: str | None, dry_run: bool) -> None:
    """Undo `jzr completion install` for SHELL, or for the current shell."""
    _run(shell, dry_run, installing=False)


# The name first asked for, kept working but not listed.
app.add_command(click.Command(
    "setup",
    callback=install.callback,
    params=install.params,
    help=install.help,
    hidden=True,
))
