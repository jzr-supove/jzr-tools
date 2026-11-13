"""``jzr host clean``: find disposable data and remove what the user picks."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sys

import click

from jzr_tools import prompts
from jzr_tools.core import cleanup
from jzr_tools.core.cleanup import Item
from jzr_tools.core.display import human_size, pad

_HEADINGS = {
    "safe": "Safe to remove",
    "review": "Review first",
    "root": "Needs root",
}
_WINDOWS_HEADINGS = {**_HEADINGS, "root": "Needs administrator"}


def _headings() -> dict[str, str]:
    return _WINDOWS_HEADINGS if cleanup._windows() else _HEADINGS
_ERASE_LINE = "\r\x1b[K"


def _is_interactive() -> bool:
    """Return whether there is someone to ask: a terminal on both ends."""
    return sys.stdin.isatty() and sys.stdout.isatty()


@contextmanager
def _status(text: str) -> Iterator[Callable[[str], None]]:
    """Show ``text`` on the terminal while slow work runs, then erase it.

    Yields a function that replaces the text. Nothing is written when stderr
    is not a terminal.
    """
    visible = sys.stderr.isatty()

    def show(text: str) -> None:
        if visible:
            click.echo(f"{_ERASE_LINE}{text}", nl=False, err=True, color=True)

    show(text)
    try:
        yield show
    finally:
        show("")


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _total(items: list[Item]) -> int:
    return sum(item.size_bytes for item in items)


def _wanted_ids(only: tuple[str, ...]) -> list[str]:
    """Split ``--only`` values into ids, rejecting ids no scan could report."""
    ids = [part.strip() for value in only for part in value.split(",") if part.strip()]
    unknown = [item_id for item_id in ids if not cleanup.is_known_id(item_id)]
    if unknown:
        raise click.UsageError(
            f"Unknown item for --only: {', '.join(unknown)}. "
            f"Known: {', '.join(cleanup.KNOWN_IDS)}, cache:<name>."
        )
    return ids


def _plan(item: Item) -> str:
    """Say what removing ``item`` does: ``Runs: ...`` or ``Empties: ...``."""
    return f"{'Runs' if item.command else 'Empties'}: {item.action}"


def _report(items: list[Item], selected: list[Item]) -> list[str]:
    """Write the scan by tier, marking what is selected."""
    id_width = max(len(item.id) for item in items)
    size_width = max(len(human_size(item.size_bytes)) for item in items)
    lines = []
    for tier, heading in _headings().items():
        rows = [item for item in items if item.tier == tier]
        if not rows:
            continue
        lines.append(heading)
        for item in rows:
            mark = "[x]" if item in selected else "[ ]"
            size = human_size(item.size_bytes).rjust(size_width)
            lines.append(f"  {mark} {pad(item.id, id_width)}  {size}  {item.title}")
    lines.append("")
    lines.append(f"Selected: {_count(len(selected), 'item')}, about {human_size(_total(selected))}")
    return lines


def _pick(items: list[Item]) -> list[Item] | None:
    """Let the user tick items; the safe tier starts ticked."""
    title_width = max(len(item.title) for item in items)
    size_width = max(len(human_size(item.size_bytes)) for item in items)
    entries: list[prompts.Choice | prompts.Heading] = []
    for tier, heading in _headings().items():
        rows = [item for item in items if item.tier == tier]
        if rows:
            entries.append(prompts.Heading(heading))
        for item in rows:
            size = human_size(item.size_bytes).rjust(size_width)
            entries.append(
                prompts.Choice(
                    value=item,
                    label=f"{pad(item.title, title_width)}  {size}",
                    checked=tier == "safe",
                    description=f"{item.note} {_plan(item)}",
                )
            )
    picked = prompts.choose("What should be removed?", entries)
    return None if picked is None else [item for item in items if item in picked]


def _free_space() -> int | None:
    try:
        return shutil.disk_usage(Path.home()).free
    except OSError:
        return None


@click.command()
@click.option(
    "-n", "--dry-run", is_flag=True, help="Show what would be removed; remove nothing."
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Do not ask: remove the --only items, or everything that is safe to remove.",
)
@click.option(
    "--only",
    multiple=True,
    metavar="ID[,ID...]",
    help="Select these items instead of showing the list.",
)
@click.option("--json", "as_json", is_flag=True, help="Print what was found as JSON; remove nothing.")
def clean(dry_run: bool, yes: bool, only: tuple[str, ...], as_json: bool) -> None:
    """Find caches and other disposable data, and remove what you pick.

    On a terminal it shows a checkbox list with the safely removable items
    ticked. Nothing is removed before you confirm.
    """
    wanted = _wanted_ids(only)
    if as_json and yes:
        raise click.UsageError("--json only reports; it cannot be combined with --yes.")
    asks = not (yes or dry_run or as_json)
    if asks and not _is_interactive():
        raise click.UsageError(
            "This needs a terminal to ask what to remove. Use --dry-run to look, "
            "or --yes (with --only to choose) to remove without asking."
        )

    with _status("Scanning...") as show:
        if sys.stderr.isatty():
            # Sizing a large cache takes a while; show that it is moving.
            items = cleanup.scan(
                on_progress=lambda done, total: show(f"Scanning... {done} of {total} places checked")
            )
        else:
            items = cleanup.scan()

    if wanted:
        selected = [item for item in items if item.id in wanted]
    else:
        selected = [item for item in items if item.tier == "safe"]

    if as_json:
        document = {
            "items": [
                {
                    "id": item.id,
                    "title": item.title,
                    "tier": item.tier,
                    "size_bytes": item.size_bytes,
                    "note": item.note,
                    "action": item.action,
                    "selected": item in selected,
                }
                for item in items
            ],
            "total_bytes": _total(items),
            "selected_bytes": _total(selected),
        }
        click.echo(json.dumps(document, indent=2))
        return

    if not items:
        click.echo("Nothing to clean.")
        return

    found = {item.id for item in items}
    for item_id in wanted:
        if item_id not in found:
            click.echo(f"{item_id}: nothing to remove")

    if asks and not wanted:
        picked = _pick(items)
        if picked is None:
            click.echo("Cancelled. Nothing was removed.", err=True)
            raise click.exceptions.Exit(1)
        selected = picked
    else:
        for line in _report(items, selected):
            click.echo(line)

    if dry_run:
        for item in selected:
            verb = "would run" if item.command else "would empty"
            click.echo(f"{verb}: {item.action}")
        click.echo("Dry run: nothing was removed.")
        return
    if not selected:
        click.echo("Nothing selected. Nothing was removed.")
        return
    question = f"Remove {_count(len(selected), 'item')}, about {human_size(_total(selected))}?"
    if asks and not prompts.confirm(question, default=False):
        click.echo("Nothing was removed.")
        return

    free_before = _free_space()
    freed, failed = 0, False
    for item in selected:
        with _status(f"Removing {item.title}..."):
            outcome = cleanup.clean(item, ask_password=not yes)
        freed += outcome.freed_bytes
        if outcome.ok:
            line = f"{item.title}: removed {human_size(outcome.freed_bytes)}"
            if outcome.in_use:
                files = "1 file in use was" if outcome.in_use == 1 else f"{outcome.in_use} files in use were"
                line += f"; {files} kept"
            click.echo(line)
        else:
            failed = True
            click.echo(f"{item.title}: failed: {outcome.error}", err=True)

    summary = f"Removed about {human_size(freed)}."
    free_after = _free_space()
    if free_before is not None and free_after is not None:
        summary += (
            f" Free space in the home directory: {human_size(free_before)} before, "
            f"{human_size(free_after)} now."
        )
    click.echo(summary)
    if failed:
        raise click.exceptions.Exit(1)
