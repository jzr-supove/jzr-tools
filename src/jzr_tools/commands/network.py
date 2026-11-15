"""Network-related CLI commands."""

import json

import click

from jzr_tools.core.network import PortOwner, kill_ports, list_listening, list_port_owners
from jzr_tools.core.windows_network import (
    PortExclusion, check_port_bindings, get_port_exclusions, is_windows,
    restart_hns as restart_hns_service,
)

_PORT = click.IntRange(1, 65535)


def _describe_exclusions(exclusions: list[PortExclusion]) -> list[str]:
    groups: dict[tuple[str, int, int, bool], set[str]] = {}
    for exclusion in exclusions:
        key = (exclusion.protocol, exclusion.start, exclusion.end, exclusion.administered)
        groups.setdefault(key, set()).add(exclusion.family)

    descriptions = []
    for (protocol, start, end, administered), families in groups.items():
        names = " and ".join({"ipv4": "IPv4", "ipv6": "IPv6"}[family] for family in sorted(families))
        suffix = "; administered" if administered else ""
        descriptions.append(f"{protocol.upper()} on {names} (range {start}-{end}{suffix})")
    return descriptions


def _fail(message: str) -> None:
    click.echo(message, err=True)
    raise click.exceptions.Exit(1)


def _host(address: str) -> str:
    return f"[{address}]" if ":" in address else address


def _owner_json(owner: PortOwner) -> dict[str, object]:
    return {
        "port": owner.port,
        "protocol": owner.protocol,
        "address": owner.address,
        "pid": owner.pid,
        "name": owner.name,
        # psutil reports UDP sockets as "NONE": they have no state.
        "status": None if owner.status in ("", "NONE") else owner.status,
    }


def _echo_table(owners: list[PortOwner]) -> None:
    """One row per port, protocol and PID; that process's addresses share it."""
    hosts: dict[tuple[int, str, int | None], list[str]] = {}
    names: dict[tuple[int, str, int | None], str] = {}
    for owner in owners:
        key = (owner.port, owner.protocol, owner.pid)
        row = hosts.setdefault(key, [])
        host = _host(owner.address)
        if host not in row:
            row.append(host)
        names[key] = owner.name

    table = [("PORT", "PROTO", "PID", "NAME", "ADDRESS")]
    for (port, protocol, pid), row in hosts.items():
        pid_text = "?" if pid is None else str(pid)
        name = names[(port, protocol, pid)]
        table.append((str(port), protocol.upper(), pid_text, name, ", ".join(row)))
    widths = [max(len(row[column]) for row in table) for column in range(4)]
    for row in table:
        cells = [cell.ljust(width) for cell, width in zip(row, widths)]
        click.echo("  ".join([*cells, row[4]]))


def _owner_line(owner: PortOwner) -> str:
    pid = "?" if owner.pid is None else owner.pid
    line = f"PID {pid} ({owner.name}) {owner.protocol.upper()} {_host(owner.address)}:{owner.port}"
    if owner.status and owner.status != "NONE":
        line += f" {owner.status}"
    return line


@click.group()
def app() -> None:
    """Inspect and free local ports."""


@app.command()
@click.argument("ports", metavar="[PORT]...", nargs=-1, type=_PORT)
@click.option("--udp", is_flag=True, help="Without PORT: also list bound UDP sockets.")
@click.option("--json", "as_json", is_flag=True, help="Print one JSON array of sockets.")
def show(ports: tuple[int, ...], udp: bool, as_json: bool) -> None:
    """Show which processes are using local ports.

    With no PORT, list every listening TCP port, and bound UDP ports with
    --udp. With one or more PORTs, show every socket bound to each of them.
    Nothing is killed.
    """
    if ports and udp:
        raise click.UsageError("--udp applies only without PORT; a PORT already covers UDP.")

    try:
        if ports:
            found = {port: list_port_owners(port) for port in dict.fromkeys(ports)}
        else:
            found = {0: list_listening(udp=udp)}
    except (RuntimeError, ValueError) as exc:
        _fail(str(exc))

    if as_json:
        listed = [owner for owners in found.values() for owner in owners]
        click.echo(json.dumps([_owner_json(owner) for owner in listed], indent=2))
        return

    if not ports:
        if found[0]:
            _echo_table(found[0])
        else:
            click.echo("No listening ports found.")
        return

    for port, owners in found.items():
        if not owners:
            click.echo(f"No visible processes using local port {port}.")
        for owner in owners:
            click.echo(_owner_line(owner))


# Registered twice on purpose: `jzr port kill` is the canonical path and
# `jzr killport` is the one shortcut the CLI keeps.
@app.command("kill")
@click.argument("ports", metavar="PORT...", nargs=-1, required=True, type=_PORT)
@click.option(
    "--restart-hns",
    is_flag=True,
    help="Windows: restart HNS if a port is excluded (Administrator required; interrupts container/WSL networking).",
)
def killport(ports: tuple[int, ...], restart_hns: bool) -> None:
    """Force-kill processes using a local port (Windows, Linux, and macOS).

    PORT is a local TCP/UDP port to free; give several to free them all.
    """
    try:
        if restart_hns and not is_windows():
            raise RuntimeError("--restart-hns is only available on Windows.")
        results = kill_ports(ports)
        exclusions = {port: get_port_exclusions(port) for port in results}
    except (RuntimeError, ValueError) as exc:
        _fail(str(exc))

    failed = False
    for port, result in results.items():
        for pid in result.killed:
            click.echo(f"Killed PID {pid} using port {port}.")
        for error in result.errors:
            click.echo(error, err=True)
        failed = failed or bool(result.errors)
        for description in _describe_exclusions(exclusions[port]):
            click.echo(f"Port {port} is excluded for {description}.", err=True)
        if not result.killed and not result.errors and not exclusions[port]:
            click.echo(f"No visible processes using local port {port}.")

    excluded = [port for port, found in exclusions.items() if found]
    if excluded:
        try:
            _recover(excluded, exclusions, restart_hns)
        except RuntimeError as exc:
            _fail(str(exc))
    if failed:
        raise click.exceptions.Exit(1)


def _recover(excluded: list[int], exclusions: dict[int, list[PortExclusion]], restart_hns: bool) -> None:
    """Restart HNS once for all excluded ports, or say why it is not done."""
    if any(exclusion.administered for port in excluded for exclusion in exclusions[port]):
        raise RuntimeError(
            "This includes an administered exclusion. Choose another port or review the "
            "reservation with its administrator; HNS recovery is not applied."
        )
    if not restart_hns:
        listed = " ".join(str(port) for port in excluded)
        raise RuntimeError(
            "Killing processes cannot remove a Windows port exclusion. Choose another port, "
            f"or run 'jzr killport {listed} --restart-hns' in an Administrator terminal. "
            "Restarting HNS can interrupt Docker, containers, and WSL networking."
        )
    click.echo("Restarting HNS; Docker, containers, and WSL networking may be interrupted.")
    restart_hns_service()
    still = []
    for port in excluded:
        remaining = get_port_exclusions(port)
        if remaining:
            still.append(f"port {port} is still excluded: " + ", ".join(_describe_exclusions(remaining)))
    if still:
        raise RuntimeError(
            f"HNS restarted, but {'; '.join(still)}. "
            "Choose another port; restarting HNS is not a guaranteed fix."
        )
    for port in excluded:
        check_port_bindings(port)
        click.echo(f"HNS restarted; port {port} passed local TCP/UDP bind checks.")
