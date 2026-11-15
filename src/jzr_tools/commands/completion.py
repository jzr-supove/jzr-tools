"""`jzr completion`: print the script that gives a shell tab completion."""

from __future__ import annotations

import os
import shlex

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


@click.command()
@click.argument("shell", metavar="SHELL", type=click.Choice(SHELLS))
def app(shell: str) -> None:
    """Print a shell completion script.

    SHELL is bash, zsh, fish or powershell; the script goes to stdout.

    \b
    Load it from your shell's start-up file:
      bash        echo 'eval "$(jzr completion bash)"' >> ~/.bashrc
      zsh         echo 'eval "$(jzr completion zsh)"' >> ~/.zshrc
      fish        jzr completion fish > ~/.config/fish/completions/jzr.fish
      powershell  Add-Content $PROFILE 'jzr completion powershell | Out-String | Invoke-Expression'

    Open a new shell afterwards. Your own `jzr bind` shortcuts are not
    completed.
    """
    from jzr_tools.cli import app as root

    completer = get_completion_class(shell)
    script = completer(root, {}, PROG_NAME, COMPLETE_VAR)
    # ShellComplete.source() for bash also runs bash to check its version;
    # the script itself does not need that. Written as bytes so the line
    # endings stay "\n" on Windows too: bash cannot read a script with "\r\n".
    source = completer.source_template % script.source_vars()
    click.echo(source.encode("utf-8"), nl=False)
