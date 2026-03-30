"""System-related CLI commands."""

import typer

from jzr_tools.core.system import resolve_executable

app = typer.Typer(help="System utilities.")


@app.command()
def which(command: str) -> None:
    """Resolve a command on PATH."""
    resolved = resolve_executable(command)
    if resolved is None:
        typer.echo(f"Command not found: {command}", err=True)
        raise typer.Exit(code=1)

    typer.echo(resolved)
