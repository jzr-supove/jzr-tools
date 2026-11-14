"""Video CLI commands.

* ``jzr video info FILE...`` describes video files.
* ``jzr video formats`` lists the targets and what the ffmpeg build can do.
"""

from __future__ import annotations

import click

from jzr_tools.commands._media import formats_command, info_command
from jzr_tools.core.media import VIDEO_TARGETS


@click.group()
def app() -> None:
    """Work with video files.

    Needs ffmpeg and ffprobe: set JZR_FFMPEG and JZR_FFPROBE to use a
    particular copy, otherwise they are taken from PATH.
    """


app.add_command(
    info_command(
        tags=False,
        help_text=(
            "Show the container, duration, size and streams of video files.\n\n"
            "Rotation is given in degrees clockwise. --json prints one array "
            "with the same facts."
        ),
    )
)
app.add_command(
    formats_command(
        targets=VIDEO_TARGETS,
        video=True,
        help_text="Show the target formats, the ffmpeg in use and its encoders.",
    )
)
