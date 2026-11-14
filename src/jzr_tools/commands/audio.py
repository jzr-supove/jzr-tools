"""Audio CLI commands.

* ``jzr audio info FILE...`` describes audio files, tags included.
* ``jzr audio formats`` lists the targets and what the ffmpeg build can do.
"""

from __future__ import annotations

import click

from jzr_tools.commands._media import formats_command, info_command
from jzr_tools.core.media import AUDIO_FORMATS


@click.group()
def app() -> None:
    """Work with audio files.

    Needs ffmpeg and ffprobe: set JZR_FFMPEG and JZR_FFPROBE to use a
    particular copy, otherwise they are taken from PATH.
    """


app.add_command(
    info_command(
        tags=True,
        help_text=(
            "Show the format, duration, streams and tags of audio files.\n\n"
            "--json prints one array with the same facts and every tag."
        ),
    )
)
app.add_command(
    formats_command(
        targets=AUDIO_FORMATS,
        video=False,
        help_text="Show the target formats, the ffmpeg in use and its audio encoders.",
    )
)
