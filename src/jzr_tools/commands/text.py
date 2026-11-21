"""Text-related CLI commands."""

from datetime import date
import os
from pathlib import Path
import sys
from typing import NoReturn

import click

from jzr_tools.core.system import copy_to_clipboard
from jzr_tools.core.text import slugify


@click.group()
def app() -> None:
    """Text utilities."""


def _read_text(text: str | None) -> str:
    if text is not None:
        return text

    if sys.stdin.isatty():
        click.echo(
            "Paste text, then finish with Ctrl+Z and Enter on Windows or Ctrl+D on macOS/Linux.",
            err=True,
        )

    return sys.stdin.read()


@app.command()
@click.argument("text", required=False)
@click.option(
    "--separator",
    "-s",
    default="-",
    show_default=True,
    help="Separator to put between words.",
)
@click.option(
    "--preserve-case",
    is_flag=True,
    help="Keep the original letter casing instead of lowercasing.",
)
@click.option(
    "--date",
    "--include-date",
    "include_date",
    is_flag=True,
    help="Prefix the result with today's ISO date and an underscore.",
)
@click.option(
    "--copy",
    "-c",
    is_flag=True,
    help="Copy the result to the system clipboard.",
)
def slug(
    text: str | None, separator: str, preserve_case: bool, include_date: bool, copy: bool
) -> None:
    """Convert text into a URL-safe slug, lowercase kebab-case by default.

    camelCase and PascalCase words are split (myVarName becomes my-var-name).
    Omit TEXT to read pasted or piped stdin.
    """
    result = slugify(_read_text(text), separator=separator, lowercase=not preserve_case)
    if not result:
        # Checked before the date is added, so the output is never a bare prefix.
        click.echo("Nothing to convert: the text has no letters or digits.", err=True)
        raise click.exceptions.Exit(1)

    if include_date:
        result = f"{date.today().isoformat()}_{result}"

    if copy:
        try:
            copy_to_clipboard(result)
        except RuntimeError as exc:
            click.echo(str(exc), err=True)
            raise click.exceptions.Exit(1) from exc

    click.echo(result)


def _copy(result: str) -> None:
    try:
        copy_to_clipboard(result)
    except RuntimeError as exc:
        click.echo(str(exc), err=True)
        raise click.exceptions.Exit(1) from exc


def _fail(message: str) -> NoReturn:
    click.echo(message, err=True)
    raise click.exceptions.Exit(1)


def _stdin_bytes() -> bytes:
    stream = click.get_binary_stream("stdin")
    try:
        terminal = stream.isatty()
    except (AttributeError, ValueError):
        terminal = False
    if terminal:
        click.echo(
            "Paste text, then finish with Ctrl+Z and Enter on Windows or Ctrl+D on macOS/Linux.",
            err=True,
        )
    return stream.read()


@app.command("base64")
@click.argument("text", required=False)
@click.option("-d", "--decode", is_flag=True, help="Decode instead of encode.")
@click.option(
    "-f",
    "--file",
    "file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read the input from this file (its bytes, when encoding).",
)
@click.option("--url-safe", is_flag=True, help="Use the - and _ alphabet, padding optional.")
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the decoded bytes to this file.",
)
@click.option("--overwrite", is_flag=True, help="Replace the -o file if it exists.")
@click.option("--copy", "-c", is_flag=True, help="Copy the result to the clipboard too.")
def base64_command(
    text: str | None,
    decode: bool,
    file: Path | None,
    url_safe: bool,
    output: Path | None,
    overwrite: bool,
    copy: bool,
) -> None:
    """Encode text or a file as Base64, or decode it with -d.

    Without TEXT or --file, reads stdin; when encoding, its bytes are used
    exactly, so a trailing newline is encoded too.
    """
    from jzr_tools.core import encoding

    if text is not None and file is not None:
        raise click.UsageError("Give TEXT or --file, not both.")
    if output is not None and not decode:
        raise click.UsageError("-o writes decoded bytes; it only applies with --decode.")
    if output is not None and copy:
        raise click.UsageError("--copy and -o do not combine.")

    if not decode:
        if text is not None and text != "-":
            data = text.encode("utf-8")
        elif file is not None:
            data = _read_file(file)
        else:
            data = _stdin_bytes()
        result = encoding.b64_encode(data, url_safe=url_safe)
        if copy:
            _copy(result)
        click.echo(result)
        return

    if file is not None:
        source = _read_file(file).decode("ascii", errors="replace")
    elif text is not None and text != "-":
        source = text
    else:
        source = _stdin_bytes().decode("ascii", errors="replace")
    try:
        data = encoding.b64_decode(source, url_safe=url_safe)
    except encoding.EncodingError as error:
        _fail(str(error))

    if output is not None:
        _write_bytes(output, data, overwrite=overwrite)
        noun = "byte" if len(data) == 1 else "bytes"
        click.echo(f"Wrote {len(data):,} {noun} to {output}")
        return
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        _fail("The decoded data is not text; write it to a file with -o FILE.")
    if copy:
        _copy(decoded)
    click.echo(decoded, nl=not decoded.endswith("\n"))


def _read_file(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        _fail(f"cannot read {path}: {error.strerror or error}")


def _write_bytes(target: Path, data: bytes, *, overwrite: bool) -> None:
    """Write ``data`` through a temp name in the same folder, then rename."""
    import os
    import tempfile

    if target.exists() and not overwrite:
        _fail(f"{target} exists; add --overwrite to replace it.")
    folder = target.parent if str(target.parent) else Path(".")
    try:
        handle, temporary = tempfile.mkstemp(dir=folder, prefix=f".{target.name}-", suffix=".tmp")
    except OSError as error:
        _fail(f"cannot write {target}: {error.strerror or error}")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, target)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        _fail(f"cannot write {target}: {error.strerror or error}")


_PARSE_LABELS = 10


@app.command("url")
@click.argument("text", required=False)
@click.option("-d", "--decode", is_flag=True, help="Decode %XX escapes instead of encoding.")
@click.option("--form", is_flag=True, help="Use + for spaces, as HTML forms and query strings do.")
@click.option("--parse", is_flag=True, help="Show the parts of a URL instead.")
@click.option("--json", "as_json", is_flag=True, help="With --parse: print the parts as JSON.")
@click.option("--copy", "-c", is_flag=True, help="Copy the result to the clipboard too.")
def url_command(
    text: str | None, decode: bool, form: bool, parse: bool, as_json: bool, copy: bool
) -> None:
    """Percent-encode text for a URL, decode it with -d, or --parse a URL.

    Encoding works like JavaScript's encodeURIComponent. Without TEXT, reads
    stdin and drops one trailing line break.
    """
    from jzr_tools.core import encoding

    if parse and (decode or form or copy):
        raise click.UsageError(
            "--parse shows the parts of a URL; it does not combine with -d, --form or --copy."
        )
    if as_json and not parse:
        raise click.UsageError("--json only applies with --parse.")

    if text is not None and text != "-":
        source = text
    else:
        source = _read_text(None)
        if source.endswith("\r\n"):
            source = source[:-2]
        elif source.endswith("\n"):
            source = source[:-1]

    try:
        if parse:
            parsed = encoding.parse_url(source)
        elif decode:
            result = encoding.url_decode(source, form=form)
        else:
            result = encoding.url_encode(source, form=form)
    except encoding.EncodingError as error:
        _fail(str(error))

    if parse:
        _show_url(parsed, as_json=as_json)
        return
    if copy:
        _copy(result)
    click.echo(result)


def _show_url(parsed, *, as_json: bool) -> None:  # noqa: ANN001 - core.encoding.ParsedUrl
    import json

    user = parsed.user
    if as_json:
        document = {
            "scheme": parsed.scheme,
            "host": parsed.host,
            "port": parsed.port,
            "user": user,
            "password": "***" if parsed.has_password else None,
            "path": parsed.path,
            "fragment": parsed.fragment,
            "query": [{"name": name, "value": value} for name, value in parsed.query],
        }
        click.echo(json.dumps(document, indent=2, ensure_ascii=False))
        return

    if user is not None and parsed.has_password:
        user = f"{user}:***"
    rows = [
        ("Scheme", parsed.scheme),
        ("Host", parsed.host),
        ("Port", None if parsed.port is None else str(parsed.port)),
        ("User", user),
        ("Path", parsed.path or None),
        ("Fragment", parsed.fragment),
    ]
    for name, value in rows:
        if value is not None:
            click.echo(f"{name:<{_PARSE_LABELS}}{value}")
    for number, (name, value) in enumerate(parsed.query):
        prefix = "Query" if number == 0 else ""
        line = f"{prefix:<{_PARSE_LABELS}}{name or '(no name)'} = {value}"
        click.echo(line.rstrip())


# The old name of the same command, kept working but not listed.
app.add_command(click.Command(
    "kebab",
    callback=slug.callback,
    params=slug.params,
    help=slug.help,
    hidden=True,
))


_DIFF_STYLES = {
    "removed": {"fg": "red"},
    "added": {"fg": "green"},
    "hunk": {"fg": "cyan"},
}
_DIFF_MARKS = {"context": " ", "removed": "-", "added": "+"}


@app.command("diff")
@click.argument("old")
@click.argument("new")
@click.option(
    "-U",
    "--unified",
    "context",
    type=click.IntRange(min=0),
    default=3,
    show_default=True,
    metavar="N",
    help="Lines of context around each change.",
)
@click.option(
    "-w", "--ignore-space", is_flag=True, help="Ignore spaces and tabs at the start and end of lines."
)
@click.option("--strict-eol", is_flag=True, help="Treat CRLF and LF line endings as different.")
@click.option(
    "--color",
    type=click.Choice(["auto", "always", "never"]),
    default="auto",
    show_default=True,
    help="Colour the output (auto: only on a terminal, and not when NO_COLOR is set).",
)
def diff_command(
    old: str, new: str, context: int, ignore_space: bool, strict_eol: bool, color: str
) -> None:
    """Show how text file NEW differs from OLD, as a unified diff.

    Exits 0 when they are the same, 1 when they differ and 2 when a file
    cannot be compared. CRLF and LF count as the same unless --strict-eol is
    given. Use - for stdin on one side.
    """
    if old == "-" and new == "-":
        raise click.UsageError("Only one of OLD and NEW can be - (stdin).")

    from jzr_tools.core import textdiff

    texts: list[str] = []
    errors: list[str] = []
    for name in (old, new):
        try:
            texts.append(_diff_input(name, textdiff.decode))
        except _DiffInputError as error:
            errors.append(str(error))
    if errors:
        for line in errors:
            click.echo(line, err=True)
        raise click.exceptions.Exit(2)

    old_lines, new_lines = (textdiff.split_lines(text) for text in texts)
    options = {"context": context, "ignore_space": ignore_space}
    rows = textdiff.diff_rows(old_lines, new_lines, strict_eol=strict_eol, **options)
    if not rows:
        if not strict_eol and textdiff.diff_rows(old_lines, new_lines, strict_eol=True, **options):
            click.echo(
                f"{old} and {new} differ only in line endings; add --strict-eol to compare them.",
                err=True,
            )
        return

    _diff_lf_output()
    coloured = color == "always" or (
        color == "auto" and not os.environ.get("NO_COLOR") and _stdout_is_terminal()
    )
    out = [_styled(f"--- {old}", coloured, bold=True), _styled(f"+++ {new}", coloured, bold=True)]
    for row in rows:
        if row.kind == "hunk":
            out.append(_styled(row.text, coloured, **_DIFF_STYLES["hunk"]))
        elif row.kind == "note":
            out.append(row.text)
        else:
            line = _DIFF_MARKS[row.kind] + row.text
            style = _DIFF_STYLES.get(row.kind)
            if not coloured or style is None:
                out.append(line)
            elif row.span is None:
                out.append(click.style(line, **style))
            else:
                start, end = row.span[0] + 1, row.span[1] + 1
                out.append(
                    click.style(line[:start], **style)
                    + click.style(line[start:end], underline=True, **style)
                    + click.style(line[end:], **style)
                )
    click.echo("\n".join(out), color=True if coloured else None)
    raise click.exceptions.Exit(1)


class _DiffInputError(Exception):
    """One side cannot be compared; the message is the whole stderr line."""


def _diff_input(name: str, decode) -> str:  # noqa: ANN001 - core.textdiff.decode
    if name == "-":
        data = click.get_binary_stream("stdin").read()
        label = "<stdin>"
    else:
        label = name
        path = Path(name)
        if path.is_dir():
            raise _DiffInputError(f"{label}: is a folder")
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise _DiffInputError(f"{label}: not found") from None
        except OSError as error:
            raise _DiffInputError(f"{label}: cannot read: {error.strerror or error}") from None
    text = decode(data)
    if text is None:
        raise _DiffInputError(f"{label} looks binary; not compared")
    return text


def _styled(text: str, coloured: bool, **style: object) -> str:
    return click.style(text, **style) if coloured else text


def _stdout_is_terminal() -> bool:
    stream = click.get_text_stream("stdout")
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def _diff_lf_output() -> None:
    """End lines with LF on Windows too, so a saved diff is a valid patch."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(newline="\n")
        except (OSError, ValueError):
            pass
