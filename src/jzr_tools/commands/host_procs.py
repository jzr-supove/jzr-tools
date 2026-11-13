"""``jzr host procs``: what uses memory, and ending what the user picks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import sys

import click

from jzr_tools import prompts
from jzr_tools.core import procs as core
from jzr_tools.core.display import human_size, pad, size_pair
from jzr_tools.core.procs import Group, Proc
from jzr_tools.frame import (
    ASCII,
    GAP,
    UNICODE,
    Painter,
    draw_frame,
    needs_ascii,
    terminal_size,
    use_color,
    writable,
)

Row = Group | Proc


def _is_interactive() -> bool:
    """Return whether there is someone to ask: a terminal on both ends."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _count(number: int, noun: str) -> str:
    plural = noun + ("es" if noun.endswith("s") else "s")
    return f"{number} {noun if number == 1 else plural}"


def _matches(process: Proc, pattern: str) -> bool:
    pattern = pattern.casefold()
    return pattern in process.name.casefold() or pattern in process.command.casefold()


def _cells(row: Row) -> list[str]:
    """One table row; the last cell is free text and is not padded."""
    memory, swap = human_size(row.memory_bytes), human_size(row.swap_bytes)
    reason = f"({row.protected})" if row.protected else ""
    if isinstance(row, Group):
        user = " ".join(part for part in (row.user, reason) if part)
        return [row.name, str(len(row.processes)), memory, swap, user]
    command = " ".join(part for part in (row.command, reason) if part)
    return [str(row.pid), row.name, memory, swap, row.user, command]


def _sums(rows: Sequence[Row]) -> dict[str, int]:
    """Add up rows: how many, how many processes, and what they hold."""
    return {
        "rows": len(rows),
        "processes": sum(len(row.processes) if isinstance(row, Group) else 1 for row in rows),
        "memory_bytes": sum(row.memory_bytes for row in rows),
        "swap_bytes": sum(row.swap_bytes for row in rows),
    }


def _outside(processes: Sequence[Proc], machine: Mapping[str, int]) -> dict[str, int]:
    """What is in use on the machine but belongs to none of ``processes``.

    That is the kernel's own memory, shared memory nobody has mapped, and
    cache the system does not count as free. ``processes`` must be all of
    them for the figure to mean that.
    """
    memory = sum(process.memory_bytes for process in processes)
    swap = sum(process.swap_bytes for process in processes)
    return {
        "memory_bytes": max(0, machine["used_bytes"] - memory),
        "swap_bytes": max(0, machine["swap_used_bytes"] - swap),
    }


def _memory_rows(totals: Mapping[str, int], paint: Painter) -> list[str]:
    """The machine's RAM and swap as usage bars."""
    ram = size_pair(totals["used_bytes"], totals["total_bytes"])
    has_swap = totals["swap_total_bytes"] > 0
    swap = size_pair(totals["swap_used_bytes"], totals["swap_total_bytes"]) if has_swap else ""
    width = max(len(ram), len(swap))

    def bar(used: int, total: int, pair: str) -> str:
        percent = used * 100 / total if total else 0.0
        return f"{paint.usage(percent)}   {pair.rjust(width)}"

    rows = [f"RAM {GAP}{bar(totals['used_bytes'], totals['total_bytes'], ram)}"]
    if has_swap:
        rows.append(f"Swap{GAP}{bar(totals['swap_used_bytes'], totals['swap_total_bytes'], swap)}")
    else:
        rows.append(f"Swap{GAP}none")
    return rows


def render_procs(
    rows: Sequence[Row],
    matching: Sequence[Row],
    totals: Mapping[str, int],
    *,
    outside: Mapping[str, int] | None = None,
    each: bool = False,
    color: bool = False,
    ascii: bool = False,
    width: int | None = None,
) -> str:
    """Return the report as a framed block of text.

    ``rows`` are shown, ``matching`` is every row before the limit, and
    ``totals`` is the machine's memory and swap. ``outside`` is what is in use
    but in no process; when given, it and the machine's totals are added
    under the sums. ``color``, ``ascii`` and ``width`` mean what they mean
    for the status report.
    """
    paint = Painter(color, ASCII if ascii else UNICODE)

    if each:
        header = ["PID", "Name", "Memory", "Swap", "User", "Command"]
        numeric = {0, 2, 3}
    else:
        header = ["Application", "Procs", "Memory", "Swap", "User"]
        numeric = {1, 2, 3}

    # label, processes, memory, swap; rows without a process count leave it empty
    summary: list[tuple[str, str, int, int]] = []
    counted = [(f"All {len(matching)}", matching)]
    if len(rows) < len(matching):
        # Something was left out, so the table's own sum is worth a row.
        counted.insert(0, (f"Top {len(rows)}", rows))
    for label, members in counted:
        sums = _sums(members)
        summary.append((label, str(sums["processes"]), sums["memory_bytes"], sums["swap_bytes"]))
    if outside is not None:
        # These two make the sums add up to the bars at the top.
        summary.append(("Outside processes", "", outside["memory_bytes"], outside["swap_bytes"]))
        summary.append(("In use", "", totals["used_bytes"], totals["swap_used_bytes"]))
    figures = [(human_size(memory), human_size(swap)) for _, _, memory, swap in summary]

    table = [header, *(_cells(row) for row in rows)]
    last = len(header) - 1
    widths = [max(len(line[column]) for line in table) for column in range(last)]
    widths[2] = max(widths[2], *(len(memory) for memory, _ in figures))
    widths[3] = max(widths[3], *(len(swap) for _, swap in figures))
    label_width = max(len(label) for label, *_ in summary)
    if each:
        # No column counts processes here; the label spans PID and name.
        widths[1] = max(widths[1], label_width - widths[0] - len(GAP))
        label_width = widths[0] + len(GAP) + widths[1]
        counts = [""] * len(summary)
    else:
        widths[0] = label_width = max(widths[0], label_width)
        widths[1] = max(widths[1], *(len(count) for _, count, *_ in summary))
        counts = [GAP + count.rjust(widths[1]) for _, count, *_ in summary]
    summary_lines = [
        f"{pad(label, label_width)}{count}{GAP}{memory.rjust(widths[2])}{GAP}{swap.rjust(widths[3])}"
        for (label, *_), count, (memory, swap) in zip(summary, counts, figures)
    ]

    lines = [
        GAP.join(
            [pad(line[column], widths[column], right=column in numeric) for column in range(last)]
            + [line[last]]
        ).rstrip()
        for line in table
    ]
    kind = "Processes" if each else "Applications"
    sections = [
        ("Memory", _memory_rows(totals, paint)),
        (f"{kind} ({len(rows)} of {len(matching)})", [paint.dim(lines[0]), *lines[1:]]),
        ("Total", summary_lines),
    ]
    return draw_frame(sections, paint, width)


def _label(row: Row) -> str:
    """A picker entry: what it is, how much it holds, and what is kept."""
    if isinstance(row, Proc):
        return f"{row.name}  PID {row.pid}  {human_size(row.memory_bytes)}"
    label = f"{row.name}  {_count(len(row.processes), 'process')}  {human_size(row.memory_bytes)}"
    kept = len(row.processes) - len(row.endable)
    if kept and not row.protected:
        label += f"  ({kept} kept)"
    return label


def _document(rows: list[Row]) -> list[dict[str, object]]:
    document: list[dict[str, object]] = []
    for row in rows:
        entry: dict[str, object]
        if isinstance(row, Group):
            entry = {"name": row.name, "user": row.user, "count": len(row.processes)}
            entry |= {"memory_bytes": row.memory_bytes, "swap_bytes": row.swap_bytes}
            entry |= {"pids": list(row.pids)}
        else:
            entry = {"pid": row.pid, "name": row.name, "user": row.user}
            entry |= {"memory_bytes": row.memory_bytes, "swap_bytes": row.swap_bytes}
            entry |= {"command": row.command}
        entry["protected"] = row.protected or None
        document.append(entry)
    return document


def _describe(process: Proc) -> str:
    return f"{process.name} (PID {process.pid})"


def _end(rows: list[Row], force: bool) -> None:
    """Let the user tick rows, then end their processes."""
    entries = [
        prompts.Choice(value=row, label=_label(row), disabled=row.protected) for row in rows
    ]
    picked = prompts.choose("Which should be ended?", entries)
    if picked is None:
        click.echo("Cancelled. Nothing was ended.", err=True)
        raise click.exceptions.Exit(1)

    chosen = [row for row in rows if row in picked]
    targets: list[Proc] = []
    for row in chosen:
        if isinstance(row, Group):
            targets += row.endable
        elif not row.protected:
            targets.append(row)
    if not targets:
        click.echo("Nothing selected. Nothing was ended.")
        return

    names = ", ".join(dict.fromkeys(row.name for row in chosen))
    question = f"End {names} ({_count(len(targets), 'process')})? Unsaved work in them is lost."
    if not prompts.confirm(question, default=False):
        click.echo("Nothing was ended.")
        return

    result = core.end_processes(targets)
    ended, failed, survivors = list(result.ended), list(result.failed), list(result.survivors)
    if survivors:
        waiting = _count(len(survivors), "process")
        verb = "is" if len(survivors) == 1 else "are"
        if force or prompts.confirm(f"{waiting} {verb} still running. Force kill?", default=False):
            forced = core.kill_processes(survivors)
            ended += forced.ended
            failed += forced.failed
            survivors = list(forced.survivors)

    held = sum(process.memory_bytes for process in ended)
    click.echo(f"{_count(len(ended), 'process')} ended, about {human_size(held)} of memory.")
    for process, reason in failed:
        click.echo(f"Could not end {_describe(process)}: {reason}", err=True)
    if survivors:
        names = ", ".join(_describe(process) for process in survivors)
        click.echo(f"{_count(len(survivors), 'process')} still running: {names}", err=True)
    if failed or survivors:
        raise click.exceptions.Exit(1)


@click.command()
@click.argument("pattern", required=False)
@click.option(
    "-n",
    "--top",
    type=click.IntRange(min=1),
    default=15,
    show_default=True,
    help="How many rows to show.",
)
@click.option("--each", is_flag=True, help="List single processes instead of applications.")
@click.option(
    "-k", "--kill", is_flag=True, help="Tick rows in a list, confirm, and end their processes."
)
@click.option(
    "--force",
    is_flag=True,
    help="With --kill: force-kill what is still running after 5 seconds, without asking.",
)
@click.option("--json", "as_json", is_flag=True, help="Print the rows as JSON.")
def procs(
    pattern: str | None, top: int, each: bool, kill: bool, force: bool, as_json: bool
) -> None:
    """Show what uses the most memory, grouped by application.

    PATTERN keeps the rows whose name or command line contains it. With
    --kill, the processes you tick are asked to exit; nothing is ticked for
    you, and system processes and this terminal cannot be ticked.
    """
    if force and not kill:
        raise click.UsageError("--force only applies with --kill.")
    if kill and as_json:
        raise click.UsageError("--kill cannot be combined with --json.")
    if kill and not _is_interactive():
        raise click.UsageError("--kill needs a terminal to ask which processes to end.")

    processes = core.snapshot()
    if pattern:
        processes = [process for process in processes if _matches(process, pattern)]
    matching: list[Row]
    if each:
        matching = sorted(processes, key=lambda process: (-process.memory_bytes, process.pid))
    else:
        matching = list(core.group_processes(processes))
    rows = matching[:top]

    machine: Mapping[str, int] = {}
    outside = None
    if rows or as_json:
        machine = core.memory_totals()
        if not pattern:
            # Only the whole list can be set against the machine's figures.
            outside = _outside(processes, machine)

    if as_json:
        document = {
            "memory": machine,
            "rows": _document(rows),
            "totals": {"shown": _sums(rows), "all": _sums(matching), "outside": outside},
        }
        click.echo(json.dumps(document, indent=2))
        return
    if not rows:
        click.echo("No matching processes.")
        return
    if kill:
        _end(rows, force)
        return

    size, color = terminal_size(), use_color()
    report = render_procs(
        rows,
        matching,
        machine,
        outside=outside,
        each=each,
        color=color,
        ascii=needs_ascii(),
        # One column is left free, as for the status report.
        width=size.columns - 1 if size else None,
    )
    click.echo(writable(report), nl=False, color=color)
