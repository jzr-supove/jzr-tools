---
name: jzr-pdf
description: Run the jzr command line for PDF files. Merge, split, reorder, rotate or remove pages; pull out text or images; show page counts and sizes. Use it instead of pypdf scripts or Ghostscript.
---

# jzr-pdf

Part of jzr, a command-line tool for everyday file and machine jobs. If `jzr` is not found, run it as `uvx --from jzr-tools jzr <command>` (needs uv), or ask the user to install it (`uv tool install jzr-tools`).

## Commands

Each line is a command with its own arguments and options, then what it does. Run `jzr <command> -h` only when an option's meaning is not clear; `reference/<group>.md` has worked examples.

### pdf
- `jzr pdf compress [SOURCES]... --level [lossless|recommended|strong] --strip` - Make PDF files smaller, mostly by shrinking their images.
- `jzr pdf from-images PICTURES... --paper [a4|letter|original] --orientation [auto|portrait|landscape] --margin MM --smaller` - Make a PDF with one page per picture, in the order given.
- `jzr pdf images FILE --to [jpg|png] --dpi N --quality N --pages PAGES` - Render the pages of a PDF as pictures.
- `jzr pdf info FILES... --json` - Show the pages, sizes, metadata and features of PDF files.
- `jzr pdf merge [FILES]...` - Join PDF files into one, in the order given.
- `jzr pdf organize FILE --order PAGES --delete PAGES --rotate [PAGES:]ANGLE` - Reorder, rotate or remove the pages of a PDF.
- `jzr pdf split FILE --every N --pages GROUPS` - Split a PDF into several: every page, every N pages, or groups.
- `jzr pdf text FILE --markdown --pages PAGES --page-breaks` - Print the text of a PDF, or convert it to Markdown.

## Rules

- Sources stay as they are.
- Every result is printed as `source -> target (...)` with what was written, such as its size, dimensions or page count: read that line instead of checking the file again.
- An output that already exists is skipped. Pass `--overwrite` only when the user wants it replaced.
- `-o FILE` names the output of one source. `--outdir DIR` collects the outputs of many.
- PAGES are page numbers from 1 and ranges: `1-3,5`, `7-` (to the end), `2-last`; `last` is the last page, so there is no need to count pages first. `jzr pdf split --pages` separates its groups with `;`.
- With no SOURCES, a command takes every matching file in `--dir` (default: the current folder); `-r` adds subfolders and `-p REGEX` filters.
- Files named as SOURCES need no dry run. Preview a scan with `--dry-run` only when the folder may hold files the user did not mean.
- Use `--json` where a command has it, to read results by program.
- Exit codes: 0 all went well, 1 something failed, 2 wrong usage. One bad file does not stop the others.
- jzr does not change what is on a page (text, pictures, forms). Use another tool for that.
- Do not guess options: an unknown one exits 2.
