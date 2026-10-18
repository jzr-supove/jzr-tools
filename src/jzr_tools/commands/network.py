"""Network-related CLI commands."""

from typing import Annotated

import typer

from jzr_tools.core.network import kill_port
from jzr_tools.core.windows_network import (
    PortExclusion, check_port_bindings, get_port_exclusions, is_windows,
    restart_hns as restart_hns_service,
)


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


def killport(
    port: Annotated[
        int,
        typer.Argument(min=1, max=65535, help="Local TCP/UDP port to free."),
    ],
    restart_hns: Annotated[
        bool,
        typer.Option(
            "--restart-hns",
            help="Windows: restart HNS if the port is excluded (Administrator required; interrupts container/WSL networking).",
        ),
    ] = False,
) -> None:
    """Force-kill processes using a local port (Windows, Linux, and macOS)."""
    try:
        if restart_hns and not is_windows():
            raise RuntimeError("--restart-hns is only available on Windows.")
        result = kill_port(port)
    except (RuntimeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    for pid in result.killed:
        typer.echo(f"Killed PID {pid} using port {port}.")
    for error in result.errors:
        typer.echo(error, err=True)
    if result.errors:
        raise typer.Exit(code=1)

    try:
        exclusions = get_port_exclusions(port)
        if exclusions:
            for description in _describe_exclusions(exclusions):
                typer.echo(f"Port {port} is excluded for {description}.", err=True)
            if any(exclusion.administered for exclusion in exclusions):
                raise RuntimeError(
                    "This includes an administered exclusion. Choose another port or review the "
                    "reservation with its administrator; HNS recovery is not applied."
                )
            if not restart_hns:
                raise RuntimeError(
                    "Killing processes cannot remove a Windows port exclusion. Choose another port, "
                    f"or run 'jzr killport {port} --restart-hns' in an Administrator terminal. "
                    "Restarting HNS can interrupt Docker, containers, and WSL networking."
                )
            typer.echo("Restarting HNS; Docker, containers, and WSL networking may be interrupted.")
            restart_hns_service()
            remaining = get_port_exclusions(port)
            if remaining:
                ranges = ", ".join(_describe_exclusions(remaining))
                raise RuntimeError(
                    f"HNS restarted, but port {port} is still excluded: {ranges}. "
                    "Choose another port; restarting HNS is not a guaranteed fix."
                )
            check_port_bindings(port)
            typer.echo(f"HNS restarted; port {port} passed local TCP/UDP bind checks.")
            return
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if not result.killed:
        typer.echo(f"No visible processes using local port {port}.")
