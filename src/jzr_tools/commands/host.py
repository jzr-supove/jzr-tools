"""Host CLI commands: what this machine is and how busy it is."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import sys

import click

from jzr_tools.cli import LazyGroup
from jzr_tools.core.display import cut, display_width, human_size, pad, size_pair
from jzr_tools.core.host import GpuInfo, Status, StatusSampler, StorageInfo
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

# Longer keys (a USB drive's mount point, a Windows interface name) are
# shortened so the report still fits an 80-column terminal.
_KEY_LIMIT = 24
# Room for a sensor's temperatures before they wrap onto another row. With a
# 15-column key (a typical interface name) the detail view fits 80 columns.
_FLOW_WIDTH = 55

# CPU usage needs two samples; this is the gap for a reading shown at once.
_FIRST_WINDOW = 0.25
_DEFAULT_INTERVAL = 2.0
_MINIMUM_INTERVAL = 0.2

_HIDE_CURSOR, _SHOW_CURSOR = "\x1b[?25l", "\x1b[?25h"
_ENTER_SCREEN, _LEAVE_SCREEN = "\x1b[?1049h", "\x1b[?1049l"
_HOME, _CLEAR_LINE, _CLEAR_BELOW = "\x1b[H", "\x1b[K", "\x1b[J"


# Other host commands live in their own modules and are loaded only when
# used, so `jzr host status` does not pay for them. The help is repeated here
# for the same reason as in `jzr_tools.cli`.
_COMMANDS: dict[str, tuple[str, str]] = {
    "clean": (
        "jzr_tools.commands.host_clean:clean",
        "Find caches and other disposable data, and remove what you pick.",
    ),
}


@click.group(cls=LazyGroup, lazy_commands=_COMMANDS)
def app() -> None:
    """Inspect and clean up this machine."""


@dataclass(frozen=True)
class _Row:
    """A reading: its key and its value."""

    key: str
    value: str


@dataclass(frozen=True)
class _Usage:
    """A reading drawn as a bar: its key, the percentage and an optional pair."""

    key: str
    percent: float
    pair: str = ""


# A section's lines: readings, or plain text that ignores the key column.
_Line = _Row | _Usage | str


def _cut_middle(text: str, width: int, marker: str) -> str:
    """Shorten ``text`` to ``width`` columns, keeping its start and its end."""
    if display_width(text) <= width:
        return text
    tail_room = (width - len(marker)) // 2
    tail: list[str] = []
    for char in reversed(text):
        tail_room -= display_width(char)
        if tail_room < 0:
            break
        tail.append(char)
    ending = "".join(reversed(tail))
    return cut(text, width - display_width(ending), marker) + ending


def _uptime(seconds: float) -> str:
    minutes = int(seconds) // 60
    days, hours, minutes = minutes // 1440, minutes % 1440 // 60, minutes % 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return "up " + " ".join(parts)


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _temperature(celsius: float) -> str:
    return f"{celsius:.0f}°C"


def _flow(key: str, cells: list[str], per_row: int, gap: str = GAP) -> list[_Line]:
    """Lay ``cells`` out in rows under one key, which only the first row shows."""
    return [
        _Row(key if start == 0 else "", gap.join(cells[start : start + per_row]).rstrip())
        for start in range(0, len(cells), per_row)
    ]


def _shown_once_per_device(storage: tuple[StorageInfo, ...]) -> list[StorageInfo]:
    """Keep each device's shortest mount point, so shared devices appear once."""
    shortest: dict[str, StorageInfo] = {}
    for mount in storage:
        current = shortest.get(mount.device)
        if current is None or len(mount.mount) < len(current.mount):
            shortest[mount.device] = mount
    return [mount for mount in storage if shortest[mount.device] is mount]


def _host_lines(status: Status, paint: Painter, detail: bool) -> list[_Line]:
    host = status.host
    lines: list[_Line] = [
        GAP.join(part for part in (host.os, host.arch, _uptime(host.uptime_seconds)) if part)
    ]
    if detail:
        about = []
        if host.kernel:
            about.append(f"kernel {host.kernel}")
        if host.boot_time:
            about.append(f"booted {_when(host.boot_time)}")
        if about:
            lines.append(paint.dim(GAP.join(about)))
    return lines


def _cpu_lines(status: Status, paint: Painter, detail: bool) -> list[_Line]:
    cpu = status.cpu
    counts = []
    if cpu.physical_cores:
        counts.append(f"{cpu.physical_cores} cores")
    if cpu.logical_cores:
        counts.append(f"{cpu.logical_cores} threads")
    lines: list[_Line] = [
        paint.strong(cpu.model) + (f"{GAP}({', '.join(counts)})" if counts else ""),
        _Usage("Usage", cpu.usage_percent),
    ]
    if cpu.load_average is not None:
        lines.append(_Row("Load", GAP.join(f"{value:.2f}" for value in cpu.load_average)))
    if detail:
        if cpu.frequency_mhz is not None:
            lines.append(_Row("Frequency", f"{cpu.frequency_mhz:.0f} MHz"))
        if cpu.temperature_c is not None:
            lines.append(_Row("Temp", _temperature(cpu.temperature_c)))
        # The figures are right-aligned in their cells, so one space between
        # cells is enough and twenty cores stay on two rows.
        cores = [paint.percent(value) for value in cpu.per_core_percent]
        lines += _flow("Cores", cores, 10, gap=" ")
    return lines


def _memory_lines(status: Status, detail: bool) -> list[_Line]:
    memory = status.memory
    lines: list[_Line] = [
        _Usage("RAM", memory.percent, size_pair(memory.used_bytes, memory.total_bytes))
    ]
    if detail:
        lines.append(_Row("Available", human_size(memory.available_bytes)))
        if memory.swap_total_bytes > 0:
            pair = size_pair(memory.swap_used_bytes, memory.swap_total_bytes)
            lines.append(_Usage("Swap", memory.swap_percent, pair))
        else:
            lines.append(_Row("Swap", "none"))
    return lines


def _storage_lines(status: Status, paint: Painter, detail: bool) -> list[_Line]:
    mounts = list(status.storage) if detail else _shown_once_per_device(status.storage)
    lines: list[_Line] = []
    for mount in mounts:
        pair = size_pair(mount.used_bytes, mount.total_bytes)
        lines.append(_Usage(mount.mount, mount.percent, pair))
        if detail:
            about = (f"free {human_size(mount.free_bytes)}", mount.device, mount.fstype)
            lines.append(_Row("", paint.dim(GAP.join(part for part in about if part))))
    return lines or ["none found"]


def _gpu_lines(gpu: GpuInfo, paint: Painter, detail: bool) -> list[_Line]:
    lines: list[_Line] = [paint.strong(gpu.name)]
    if gpu.utilization_percent is not None:
        lines.append(_Usage("Usage", gpu.utilization_percent))
    used, total = gpu.memory_used_bytes, gpu.memory_total_bytes
    if total:
        if used is not None:
            lines.append(_Usage("Memory", used * 100 / total, size_pair(used, total)))
        else:
            lines.append(_Row("Memory", human_size(total)))
    if gpu.temperature_c is not None:
        lines.append(_Row("Temp", _temperature(gpu.temperature_c)))
    if detail:
        if gpu.driver:
            lines.append(_Row("Driver", gpu.driver))
        if gpu.power_watts is not None:
            lines.append(_Row("Power", f"{gpu.power_watts:.1f} W"))
    return lines


def _all_gpu_lines(status: Status, paint: Painter, detail: bool) -> list[_Line]:
    lines: list[_Line] = []
    for gpu in status.gpus:
        if lines:
            lines.append("")
        lines += _gpu_lines(gpu, paint, detail)
    return lines or ["none detected"]


def _network_lines(status: Status, paint: Painter) -> list[_Line]:
    table = [("State", "Sent", "Received")] + [
        (
            "up" if interface.is_up else "down",
            human_size(interface.bytes_sent),
            human_size(interface.bytes_received),
        )
        for interface in status.network
    ]
    state, sent, received = (max(len(row[column]) for row in table) for column in range(3))

    def cells(row: tuple[str, str, str], address: str) -> str:
        columns = (row[0].ljust(state), row[1].rjust(sent), row[2].rjust(received), address)
        return GAP.join(columns).rstrip()

    lines: list[_Line] = [_Row(paint.dim("Interface"), paint.dim(cells(table[0], "Address")))]
    for interface, row in zip(status.network, table[1:]):
        addresses = list(interface.addresses) or [""]
        lines.append(_Row(interface.name, cells(row, addresses[0])))
        lines += [_Row("", cells(("", "", ""), address)) for address in addresses[1:]]
    return lines


def _temperature_lines(status: Status) -> list[_Line]:
    """One block per sensor chip, as many readings to a row as fit."""
    by_sensor: dict[str, list[str]] = {}
    for reading in status.temperatures:
        by_sensor.setdefault(reading.sensor, []).append(
            f"{reading.label} {_temperature(reading.celsius)}".strip()
        )

    lines: list[_Line] = []
    for sensor, readings in by_sensor.items():
        width = max(display_width(reading) for reading in readings)
        per_row = max(1, (_FLOW_WIDTH + len(GAP)) // (width + len(GAP)))
        lines += _flow(sensor, [pad(reading, width) for reading in readings], per_row)
    return lines


def _battery_lines(status: Status) -> list[_Line]:
    battery = status.battery
    assert battery is not None
    facts = [f"{battery.percent:.0f}%"]
    if battery.plugged is not None:
        facts.append("plugged in" if battery.plugged else "on battery")
    if battery.seconds_left is not None:
        hours, minutes = divmod(battery.seconds_left // 60, 60)
        facts.append(f"{hours}h {minutes}m left")
    return [_Row("Charge", GAP.join(facts))]


def render_status(
    status: Status,
    *,
    detail: bool = False,
    color: bool = False,
    ascii: bool = False,
    width: int | None = None,
) -> str:
    """Return the report as a framed block of text.

    ``color`` adds ANSI colour codes and nothing else. ``ascii`` draws with
    plain characters for output that cannot show box-drawing ones. ``width``
    is the most columns a line may take; lines that need more are cut.
    """
    glyphs = ASCII if ascii else UNICODE
    paint = Painter(color, glyphs)

    sections: list[tuple[str, list[_Line]]] = [
        (status.host.hostname, _host_lines(status, paint, detail)),
        ("CPU", _cpu_lines(status, paint, detail)),
        ("Memory", _memory_lines(status, detail)),
        ("Storage", _storage_lines(status, paint, detail)),
        ("GPU", _all_gpu_lines(status, paint, detail)),
    ]
    if detail and status.network:
        sections.append(("Network", _network_lines(status, paint)))
    if detail and status.temperatures:
        sections.append(("Temperatures", _temperature_lines(status)))
    if detail and status.battery is not None:
        sections.append(("Battery", _battery_lines(status)))

    # One key column and one column of size pairs for the whole report, so
    # every bar and every figure lines up across sections.
    readings = [line for _, lines in sections for line in lines if not isinstance(line, str)]
    key_width = min(_KEY_LIMIT, max(display_width(line.key) for line in readings))
    pair_width = max(
        (len(line.pair) for line in readings if isinstance(line, _Usage)), default=0
    )

    def text(line: _Line) -> str:
        if isinstance(line, str):
            return line
        key = pad(_cut_middle(line.key, key_width, glyphs.cut), key_width)
        if isinstance(line, _Row):
            return f"{key}{GAP}{line.value}"
        pair = f"   {line.pair.rjust(pair_width)}" if line.pair else ""
        return f"{key}{GAP}{paint.usage(line.percent)}{pair}"

    drawn = [(title, [text(line) for line in lines]) for title, lines in sections]
    return draw_frame(drawn, paint, width)


@app.command()
@click.option(
    "-l", "--live", is_flag=True, help="Keep refreshing until interrupted with Ctrl+C."
)
@click.option(
    "-n",
    "--interval",
    type=click.FloatRange(min=_MINIMUM_INTERVAL),
    metavar="SECONDS",
    default=None,
    help=f"Seconds between --live readings.  [default: {_DEFAULT_INTERVAL:g}]",
)
@click.option(
    "-d", "--detail", is_flag=True, help="Add per-core, swap, mount, network and sensor detail."
)
@click.option(
    "--json", "as_json", is_flag=True, help="Print everything as JSON instead of text."
)
def status(live: bool, interval: float | None, detail: bool, as_json: bool) -> None:
    """Show host, CPU, memory, storage and GPU status.

    Works on Windows, Linux and macOS. Values the system does not expose are
    left out.
    """
    if interval is not None and not live:
        raise click.UsageError("--interval only applies with --live.")

    sampler = StatusSampler()
    on_terminal = sys.stdout.isatty()
    in_place = live and not as_json and on_terminal
    color = use_color()
    plain = needs_ascii()

    def text(reading: Status, *, fit_height: bool = False) -> str:
        # One column is left free: writing in the last one and then clearing
        # to the end of the line erases that character on some terminals.
        size = terminal_size()
        report = render_status(
            reading,
            detail=detail,
            color=color,
            ascii=plain,
            width=size.columns - 1 if size else None,
        )
        if size and fit_height:
            # A report taller than the screen would scroll on every redraw.
            report = "".join(report.splitlines(keepends=True)[: max(1, size.lines - 1)])
        return writable(report)

    reading = sampler.sample(_FIRST_WINDOW)

    if not live:
        if as_json:
            click.echo(json.dumps(reading.to_dict(), indent=2))
        else:
            click.echo(text(reading), nl=False, color=color)
        return

    def emit(reading: Status, first: bool) -> None:
        if as_json:
            click.echo(json.dumps(reading.to_dict(), separators=(",", ":")))
        elif in_place:
            # Rewriting each line and clearing what is left avoids the flicker
            # of clearing the whole screen first.
            frame = "".join(
                f"{line}{_CLEAR_LINE}\n"
                for line in text(reading, fit_height=True).splitlines()
            )
            click.echo(f"{_HOME}{frame}{_CLEAR_BELOW}", nl=False, color=True)
        else:
            click.echo(("" if first else "\n") + text(reading), nl=False)

    if in_place:
        click.echo(_ENTER_SCREEN + _HIDE_CURSOR, nl=False, color=True)
    try:
        emit(reading, first=True)
        while True:
            reading = sampler.sample(interval or _DEFAULT_INTERVAL)
            emit(reading, first=False)
    except KeyboardInterrupt:
        pass
    finally:
        if in_place:
            # Back on the normal screen, leave the last reading behind.
            click.echo(_LEAVE_SCREEN + _SHOW_CURSOR, nl=False, color=True)
            click.echo(text(reading), nl=False, color=color)
