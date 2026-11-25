# Install

[← jzr-tools](../README.md) · [Install](install.md) · [Images](images.md) · [Video and audio](video-audio.md) · [PDF](pdf.md) · [Text and data](text-and-data.md) · [Files](files.md) · [Ports and host](system.md) · [Shortcuts](shortcuts.md) · [Completion](completion.md) · [AI agents](ai-agents.md)


No Python needed. The installer sets up [uv](https://docs.astral.sh/uv/),
which downloads a suitable Python by itself, and then installs the `jzr`
command.

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/jzr-supove/jzr-tools/main/install.ps1 | iex"
```

macOS and Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/jzr-supove/jzr-tools/main/install.sh | sh
```

Open a new terminal afterwards and run `jzr --help`. Run the same line again
to upgrade.

If you already have uv or pipx:

```bash
uv tool install jzr-tools      # upgrade: uv tool upgrade jzr-tools
pipx install jzr-tools         # needs Python 3.11 or newer; upgrade: pipx upgrade jzr-tools
```

To try it without installing anything: `uvx --from jzr-tools jzr --help`.

The video and audio commands use ffmpeg. If it is not on your `PATH`, run
`jzr ffmpeg install` once to download it into jzr's own folder.

## Install from source

For global usage with `pipx`, from a clone of the repository:

```bash
pipx install .
jzr --help
```

For development with `uv`:

```bash
uv sync
uv run jzr --help
uv run python -m unittest discover -s tests
```

You can also run the package directly:

```bash
python -m jzr_tools --help
```
