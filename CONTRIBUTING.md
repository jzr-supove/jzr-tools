# Contributing to jzr-tools

Thanks for helping. Bug reports, fixes and new tools are all welcome. For a
new tool or a change in behaviour, open an issue first so we can agree on the
command name and what it does before you write it.

## Set up

You need [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer.

```bash
git clone https://github.com/jzr-supove/jzr-tools.git
cd jzr-tools
uv sync
uv run jzr --help
uv run python -m unittest discover -s tests
```

The tests use `unittest`, not pytest. Tests that need ffmpeg and ffprobe
skip themselves when those are not on your `PATH`; install ffmpeg to run
them too. The MCP server's own tests need `uv sync --extra mcp` and are
skipped without it. CI runs the suite on Windows, Linux and macOS with Python 3.11 to
3.14, so a change that works on one platform only shows up there.

## How the code is laid out

- `src/jzr_tools/cli.py`: the root `jzr` command and the `_COMMANDS`
  registry.
- `src/jzr_tools/commands/<group>.py`: the Click layer only. It parses
  options, calls into `core/`, prints results and sets the exit code.
- `src/jzr_tools/core/`: the logic, as plain Python functions. Modules here
  must not import Click, so the same functions can be reused outside the
  CLI and tested without it.
- `tests/`: one or more `test_<area>.py` files per area.

## Command shape

- Commands read `jzr <thing> <action>`, grouped by what they work on
  (`jzr image convert`, `jzr port kill`), two levels at most. A tool is
  top-level only when it has no sibling actions.
- Commands that take many files follow `jzr image convert`: one result line
  per file, `--outdir`, `--overwrite` and `--dry-run`, one bad file fails on
  its own, exit code 1 if any file failed, and outputs written under a
  temporary name and renamed when done.
- Messages are plain and specific. Expected errors (a missing file, a bad
  option) print a short message and exit non-zero, never a traceback.

## Adding a tool

To an existing group: add a Click command to
`src/jzr_tools/commands/<group>.py` and put its logic in a module under
`src/jzr_tools/core/`. Add a row to the command table in `README.md` and
describe the command in the matching page under `docs/` (`docs/images.md`,
`docs/pdf.md` and so on). A new group gets its own page, linked from the
Documentation table in `README.md`.

A new group takes three steps:

1. Create `src/jzr_tools/commands/<group>.py` with a Click group named `app`.
   Its docstring is the short help shown by `jzr --help`.
2. Register it with one line in `_COMMANDS` in `src/jzr_tools/cli.py`:

   ```python
   "pdf": ("jzr_tools.commands.pdf:app", "Work with PDF files."),
   ```

   The help text there must equal the group's own short help. It is repeated
   so that `jzr --help` can list commands without importing them.
3. Add the group's name to `LAZY_NAMES` in `tests/test_startup.py`.

Every new command is also described to AI agents, and tests fail until it is:

1. Add examples to `src/jzr_tools/commands/_skill_examples.py`.
2. In `src/jzr_tools/commands/_mcp_tools.py`, put it in `EXPOSED` (the MCP
   server offers it) or in `NOT_EXPOSED` with the reason. Every option that is
   offered needs help text.
3. Run `uv run python -m jzr_tools.commands._skill_text` to update the files
   under `src/jzr_tools/data/skill/`; never edit its `reference/` files by hand.
   Run it again whenever a command's help changes.

## Keep start-up fast

Every `jzr` call pays for the modules it imports, so `cli.py` imports nothing
command-specific and a command module is loaded only when that command runs.
Inside a command, import heavy libraries (Pillow, mutagen, psutil and the
like) in the function that needs them, not at the top of the module.

`tests/test_startup.py` checks this: it runs `jzr --help`, `jzr --version`
and individual commands in a fresh interpreter and fails if they load modules
they should not, or if a help text in `_COMMANDS` differs from the command's
own. When you add a group or a heavy dependency, add a case there that shows
what your command does and does not load.

## Tests

- Write tests for the behaviour you add or change, in `tests/test_<area>.py`.
  Test `core/` functions directly, and the command through
  `click.testing.CliRunner`.
- Tests that need an external program skip themselves when it is missing,
  for example `@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not
  on PATH")` with `HAVE_FFMPEG` from `tests/media_fixtures.py`. Tests that
  run the real ffmpeg live in `tests/test_*_real.py`.
- Use temporary directories for files a test creates, and make sure tests pass
  on Windows, Linux and macOS (paths, line endings, file locking).

## Pull requests

- Run the full suite before you open a pull request.
- Commit subjects follow [Conventional Commits](https://www.conventionalcommits.org/),
  with the group as the scope: `feat(video): add video gif`,
  `fix(image): keep EXIF orientation`.
- Keep a pull request to one tool or one fix where you can.

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE), as section 5 of the license describes.
