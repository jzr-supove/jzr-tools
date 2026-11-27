---
name: {name}
description: {description}
---

# {name}

Part of jzr, a command-line tool for everyday file and machine jobs. If `jzr` is not found, run it as `uvx --from jzr-tools jzr <command>` (needs uv), or ask the user to install it (`uv tool install jzr-tools`).

## Commands

Each line is a command with its own arguments and options, then what it does. Run `jzr <command> -h` only when an option's meaning is not clear; `reference/<group>.md` has worked examples.

{commands}

## Rules

[media] - Sources stay as they are. Each command writes a new file beside its source (`a_compressed.jpg`, `a_1600x1200.jpg`, `a_trim.mp4`).
[pdf] - Sources stay as they are.
[media pdf] - Every result is printed as `source -> target (...)` with what was written, such as its size, dimensions or page count: read that line instead of checking the file again.
[media pdf] - An output that already exists is skipped. Pass `--overwrite` only when the user wants it replaced.
[media pdf] - `-o FILE` names the output of one source. `--outdir DIR` collects the outputs of many.
[pdf] - PAGES are page numbers from 1 and ranges: `1-3,5`, `7-` (to the end), `2-last`; `last` is the last page, so there is no need to count pages first. `jzr pdf split --pages` separates its groups with `;`.
[media pdf data] - With no SOURCES, a command takes every matching file in `--dir` (default: the current folder); `-r` adds subfolders and `-p REGEX` filters.
[media pdf] - Files named as SOURCES need no dry run. Preview a scan with `--dry-run` only when the folder may hold files the user did not mean.
[data] - `jzr data convert` reads CSV cells as text; `--types` makes numbers, true/false and empty cells real values, which Excel and JSON output need for real numbers.
[data] - `jzr data convert` prints text formats to stdout; `-o FILE` writes a file instead. Excel output is always a file, `-o FILE` or `<name>.xlsx` beside the source: never redirect it with `>`. Its `wrote ...` line counts the rows and the number and text cells, so there is no need to convert back to check.
[data] - `jzr file rename` only shows its plan; `-y` applies it and renames in place. `jzr data json -w` rewrites its files in place.
[media] - `jzr audio tags` with an editing option changes the file in place.
- Use `--json` where a command has it, to read results by program.
- Exit codes: 0 all went well, 1 something failed, 2 wrong usage. One bad file does not stop the others.
[data] - `jzr text diff` and `jzr env compare` exit 1 when they find a difference, 2 when a file cannot be read.
[data] - Quote regexes and rename templates.
[data] - Never put a secret on the command line: `jzr jwt` takes it with `--secret-env` or `--secret-file`.
[media] - Video commands and most audio commands need ffmpeg; `jzr ffmpeg status` says whether there is one. `jzr ffmpeg install` downloads it and `jzr ffmpeg remove` deletes it: suggest those to the user instead of running them.
[system] - `jzr port kill` ends processes without asking. Run `jzr port show PORT` first and tell the user what will end.
[system] - `jzr host clean` and `jzr host procs --kill` ask on a terminal and then delete or end what the user picks: suggest them instead of running them. `jzr host clean --dry-run` and `jzr host procs` without `--kill` only show.
[system] - Ask before `jzr bind add`, `jzr bind remove`, `jzr completion install` or `jzr completion remove`: they change the user's own setup.
[media] - jzr does not crop, rotate, join, watermark or draw on images or video. Use another tool for those.
[pdf] - jzr does not change what is on a page (text, pictures, forms). Use another tool for that.
[data] - jzr does not edit spreadsheets. Use another tool for that.
- Do not guess options: an unknown one exits 2.
