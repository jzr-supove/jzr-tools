"""Commands that work on files as files: `jzr file rename`."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import click

from jzr_tools.commands._batch import compile_pattern

if TYPE_CHECKING:
    from jzr_tools.core.rename import RenameItem

# Kept here so `--help` does not import the core module.
_CASES = ("lower", "upper", "title", "slug", "snake")
_CASE_ALIASES = ("kebab",)


class _CaseChoice(click.Choice):
    """The listed cases, plus aliases that are accepted but not shown."""

    def convert(self, value: object, param: click.Parameter | None, ctx: click.Context | None):
        if isinstance(value, str) and value.lower() in _CASE_ALIASES:
            return value.lower()
        return super().convert(value, param, ctx)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def app() -> None:
    """Rename and organise files."""


@app.command(
    epilog=(
        "Name steps run in this order on the name without its extension: "
        "--replace, --regex, --case, --prefix/--suffix, --template. "
        "Template fields: {name} {n} {parent} {date} {ext}; {n:03} pads the number.\n\n"
        "Example: jzr file rename -d photos --template \"{date}-{n:03}\" --lower-ext -y"
    )
)
@click.argument("sources", nargs=-1, type=click.Path(path_type=Path))
@click.option(
    "-d",
    "--dir",
    "directory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Folder to scan when no SOURCES are listed.  [default: .]",
)
@click.option(
    "-p",
    "--pattern",
    default=None,
    help="Regex filter on the path relative to --dir (the file name for SOURCES).",
)
@click.option("-r", "--recursive", is_flag=True, help="Scan subfolders too.")
@click.option("--hidden", is_flag=True, help="Include hidden files and folders in a scan.")
@click.option(
    "--replace",
    nargs=2,
    multiple=True,
    metavar="OLD NEW",
    help="Replace text in the name (repeatable).",
)
@click.option(
    "--regex",
    nargs=2,
    multiple=True,
    metavar="PATTERN REPL",
    help="Regex replace in the name; \\1 and \\g<name> work (repeatable).",
)
@click.option("-i", "--ignore-case", is_flag=True, help="Make --replace ignore case.")
@click.option(
    "--case",
    type=_CaseChoice(_CASES, case_sensitive=False),
    default=None,
    help="Change the case; slug and snake also make the name ASCII.",
)
@click.option("--prefix", default=None, help="Add text before the name.")
@click.option("--suffix", default=None, help="Add text after the name, before the extension.")
@click.option("--template", default=None, help="Build the name from fields, e.g. \"{date}-{n:02}\".")
@click.option("--ext", default=None, metavar="EXT", help='New extension; --ext "" removes it.')
@click.option("--lower-ext", is_flag=True, help="Lower-case the extension.")
@click.option("--start", type=int, default=1, show_default=True, help="First {n}.")
@click.option(
    "--sort",
    type=click.Choice(("name", "mtime", "size"), case_sensitive=False),
    default=None,
    help="Order of files for {n}.  [default: listed order, or name for a scan]",
)
@click.option("--reverse", is_flag=True, help="Reverse the order.")
@click.option("-y", "--apply", "apply", is_flag=True, help="Rename; without it only the plan is shown.")
@click.option("--dry-run", is_flag=True, hidden=True)
@click.option("-v", "--verbose", is_flag=True, help="Also list files whose name stays.")
@click.option("--json", "as_json", is_flag=True, help="Print the plan or result as JSON.")
def rename(
    sources: tuple[Path, ...],
    directory: Path | None,
    pattern: str | None,
    recursive: bool,
    hidden: bool,
    replace: tuple[tuple[str, str], ...],
    regex: tuple[tuple[str, str], ...],
    ignore_case: bool,
    case: str | None,
    prefix: str | None,
    suffix: str | None,
    template: str | None,
    ext: str | None,
    lower_ext: bool,
    start: int,
    sort: str | None,
    reverse: bool,
    apply: bool,
    dry_run: bool,
    verbose: bool,
    as_json: bool,
) -> None:
    """Rename many files at once; shows the plan unless --apply is given.

    Files keep their folder. Names that would clash get -2, -3, ...; a name
    taken by another file is skipped; nothing is ever overwritten.
    """
    from jzr_tools.core import rename as core

    if apply and dry_run:
        raise click.UsageError("--apply and --dry-run cannot be used together.")
    options = core.RenameOptions(
        replace=tuple(replace),
        regex=tuple(regex),
        ignore_case=ignore_case,
        case=case.lower() if case else None,
        prefix=prefix or "",
        suffix=suffix or "",
        template=template,
        ext=ext,
        lower_ext=lower_ext,
        start=start,
    )
    # An option given with empty text (`--prefix ""`) still counts as given.
    if not options.changes_anything() and prefix is None and suffix is None:
        raise click.UsageError(
            "Nothing to change: give --replace, --regex, --case, --prefix, --suffix, "
            "--template, --ext or --lower-ext."
        )
    if sources and (directory is not None or recursive or hidden):
        raise click.UsageError(
            "--dir, --recursive and --hidden only apply when no SOURCES are listed."
        )
    try:
        core.check_options(options)
    except core.RenameError as error:
        raise click.UsageError(str(error)) from error
    regex_filter = compile_pattern(pattern)

    if sources:
        selected = list(sources)
        if regex_filter is not None:
            selected = [path for path in selected if regex_filter.search(path.name)]
        selected = core.order_sources(selected, sort=sort, reverse=reverse)
    else:
        root = directory if directory is not None else Path(".")
        selected = core.find_files(root, pattern=regex_filter, recursive=recursive, hidden=hidden)
        if sort is not None or reverse:
            selected = core.order_sources(selected, sort=sort, reverse=reverse)

    if not selected:
        click.echo("No matching files.", err=True)
        raise click.exceptions.Exit(1)

    items = core.plan_renames(selected, options)
    interrupted = False
    if apply:
        try:
            items = core.apply_renames(items)
        except core.RenameInterrupted as stopped:
            items = stopped.items
            interrupted = True

    counts = Counter(item.status for item in items)
    if as_json:
        _print_json(items)
    else:
        for item in items:
            _report(item, verbose=verbose)
        if interrupted:
            renamed = counts["renamed"]
            click.echo(
                f"Interrupted: renamed {renamed} {_files(renamed)}; the rest keep their names.",
                err=True,
            )
        else:
            click.echo(_summary(counts, applied=apply))

    if interrupted:
        raise click.exceptions.Exit(130)
    if counts["failed"]:
        raise click.exceptions.Exit(1)


def _files(count: int) -> str:
    return "file" if count == 1 else "files"


def _report(item: RenameItem, *, verbose: bool) -> None:
    if item.status in ("rename", "renamed"):
        note = f" ({item.detail})" if item.detail else ""
        click.echo(f"{item.source} -> {item.target.name}{note}")
    elif item.status == "skipped":
        click.echo(f"skip {item.source}: {item.detail}", err=True)
    elif item.status == "failed":
        click.echo(f"fail {item.source}: {item.detail}", err=True)
    elif verbose:
        click.echo(f"same {item.source}")


def _summary(counts: Counter[str], *, applied: bool) -> str:
    if applied:
        moved = counts["renamed"]
        head = f"Renamed {moved} {_files(moved)}"
    else:
        moved = counts["rename"]
        head = f"{moved} {_files(moved)} would be renamed"
    parts = [head]
    for status in ("skipped", "failed", "unchanged"):
        if counts[status]:
            parts.append(f"{counts[status]} {status}")
    line = ", ".join(parts) + "."
    if not applied and moved:
        line += " Run again with --apply to rename."
    return line


def _print_json(items: Sequence[RenameItem]) -> None:
    import json

    document = [
        {
            "source": str(item.source),
            "target": str(item.target),
            "status": item.status,
            "detail": item.detail,
        }
        for item in items
    ]
    click.echo(json.dumps(document, ensure_ascii=False, indent=2))
