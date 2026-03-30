# jzr-tools

`jzr-tools` is a personal cross-platform CLI toolkit packaged as a Python module
with an executable entrypoint.

## Install

For global usage with `pipx`:

```bash
pipx install .
jzr --help
```

For development with `uv`:

```bash
uv sync
uv run jzr --help
python -m unittest discover -s tests
```

You can also run the package directly:

```bash
python -m jzr_tools --help
```

## Layout

- `src/jzr_tools/cli.py`: root Typer app and command registration
- `src/jzr_tools/commands/`: CLI-facing command modules
- `src/jzr_tools/core/`: plain Python business logic
- `tests/`: CLI and core behavior checks

## Included v1 Commands

- `jzr version`: print the installed toolkit version
- `jzr text slug "Some Text"`: convert text into a slug
- `jzr fs ls [PATH]`: list directory entries
- `jzr system which COMMAND`: resolve an executable on `PATH`
