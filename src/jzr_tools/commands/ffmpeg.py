"""ffmpeg CLI commands.

* ``jzr ffmpeg install`` downloads ffmpeg and ffprobe into jzr's own folder.
* ``jzr ffmpeg status`` shows which ffmpeg and ffprobe jzr would use.
* ``jzr ffmpeg remove`` deletes jzr's copy.

The installer module is imported inside the commands, so asking for help
imports neither it nor the network stack.
"""

from __future__ import annotations

import click


@click.group()
def app() -> None:
    """Install and check the ffmpeg jzr uses.

    `jzr ffmpeg install` downloads ffmpeg and ffprobe into jzr's own folder:
    no admin rights, and PATH is left alone. JZR_FFMPEG and JZR_FFPROBE win
    over that copy when set; without either, ffmpeg is taken from PATH.
    """


def _stderr_is_terminal() -> bool:
    import sys

    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


class _ProgressLine:
    """One updating download line on stderr, shown only on a terminal."""

    def __init__(self) -> None:
        self.enabled = _stderr_is_terminal()
        self.shown = False

    def __call__(self, done: int, total: int | None) -> None:
        if not self.enabled:
            return
        from jzr_tools.core.media import format_size

        if total:
            text = f"downloading {format_size(done)} of {format_size(total)} ({done * 100 // total}%)"
        else:
            text = f"downloading {format_size(done)}"
        click.echo(f"\r{text:<60}", nl=False, err=True)
        self.shown = True

    def close(self) -> None:
        if self.shown:
            click.echo(err=True)


def install_ffmpeg(*, assume_yes: bool, offer: bool = False) -> bool:
    """Ask, download and install; return whether jzr's copy is now in place.

    Every outcome prints its own message. ``offer`` is for a command that
    found no ffmpeg: the closing line goes to stderr so its output stays its
    own, and a "no" prints nothing, leaving the caller to say what is missing.
    """
    import platform

    from jzr_tools.core import ffmpeg_install
    from jzr_tools.core.media import format_size

    system = platform.system()
    source = ffmpeg_install.pick_source(system, platform.machine())
    if source is None:
        click.echo(
            f"jzr knows no ffmpeg build for this machine ({system} {platform.machine()}). "
            f"Install it yourself: {ffmpeg_install.manual_route(system)}, "
            "or set JZR_FFMPEG and JZR_FFPROBE.",
            err=True,
        )
        return False

    destination = ffmpeg_install.install_dir()
    try:
        release = ffmpeg_install.resolve(source, fetch=ffmpeg_install.urlopen_fetch)
        if ffmpeg_install.up_to_date(release, ffmpeg_install.installed()):
            click.echo(
                f"ffmpeg {release.version} in {destination} is already up to date.",
                err=offer,
            )
            return True

        if not assume_yes:
            size = format_size(release.size) if release.size is not None else "size unknown"
            question = (
                f"Download ffmpeg {release.version} ({size}) from {source.host} "
                f"into {destination}?"
            )
            if not click.confirm(question, default=True, err=True):
                if not offer:
                    click.echo("Nothing was installed.", err=True)
                return False

        progress = _ProgressLine()
        try:
            ffmpeg_install.install(
                release, fetch=ffmpeg_install.urlopen_fetch, on_progress=progress
            )
        finally:
            progress.close()
    except ffmpeg_install.InstallError as error:
        click.echo(str(error), err=True)
        return False
    except click.Abort:
        click.echo("Nothing was installed.", err=True)
        return False

    click.echo(f"installed ffmpeg {release.version} into {destination}", err=offer)
    return True


@app.command(name="install")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Download without asking first.")
def install_command(assume_yes: bool) -> None:
    """Download ffmpeg and ffprobe into jzr's own folder.

    The download is checked against the SHA-256 checksum its source
    publishes and the new copy is run once before it replaces an older one.
    Run it again to update.
    """
    if not install_ffmpeg(assume_yes=assume_yes):
        raise click.exceptions.Exit(1)


@app.command()
def status() -> None:
    """Show which ffmpeg and ffprobe jzr uses and where they come from."""
    from jzr_tools.core import ffmpeg, ffmpeg_install, media

    for name in ("ffmpeg", "ffprobe"):
        try:
            tool = ffmpeg.locate_tool(name)
        except media.MissingToolError as error:
            click.echo(f"{name}: not found")
            click.echo(str(error), err=True)
            continue
        click.echo(f"{name}: {tool.path} ({ffmpeg.version(tool)}, from {tool.origin})")

    copy = ffmpeg_install.installed()
    if copy is None:
        click.echo(f"jzr copy: none (installs into {ffmpeg_install.install_dir()})")
    else:
        click.echo(f"jzr copy: ffmpeg {copy.version} in {copy.directory}")


@app.command()
def remove() -> None:
    """Delete the ffmpeg that `jzr ffmpeg install` downloaded."""
    from jzr_tools.core import ffmpeg_install

    directory = ffmpeg_install.install_dir()
    try:
        removed = ffmpeg_install.remove()
    except ffmpeg_install.InstallError as error:
        click.echo(str(error), err=True)
        raise click.exceptions.Exit(1) from error
    if removed:
        click.echo(f"removed {directory}")
    else:
        click.echo(f"jzr has no ffmpeg in {directory}")
