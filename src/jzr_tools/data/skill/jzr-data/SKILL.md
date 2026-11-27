---
name: jzr-data
description: Run the jzr command line for data, text and file jobs. Convert JSON, YAML, CSV, TOML and Excel data; rename files; hash files; encode, diff or slug text; read JWTs; convert timestamps; make UUIDs or passwords.
---

# jzr-data

Part of jzr, a command-line tool for everyday file and machine jobs. If `jzr` is not found, run it as `uvx --from jzr-tools jzr <command>` (needs uv), or ask the user to install it (`uv tool install jzr-tools`).

## Commands

Each line is a command with its own arguments and options, then what it does. Run `jzr <command> -h` only when an option's meaning is not clear; `reference/<group>.md` has worked examples.

### file
- `jzr file rename [SOURCES]... --hidden --replace OLD NEW --regex PATTERN REPL --ignore-case --case [lower|upper|title|slug|snake] --prefix TEXT --suffix TEXT --template TEXT --ext EXT --lower-ext --start N --sort [name|mtime|size] --reverse --apply --verbose --json` - Rename many files at once; shows the plan unless --apply is given.
### text
- `jzr text base64 [TEXT] --decode --file FILE --url-safe --copy` - Encode text or a file as Base64, or decode it with -d.
- `jzr text diff OLD NEW --unified N --ignore-space --strict-eol --color [auto|always|never]` - Show how text file NEW differs from OLD, as a unified diff.
- `jzr text slug [TEXT] --separator TEXT --preserve-case --include-date --copy` - Convert text into a URL-safe slug, lowercase kebab-case by default.
- `jzr text url [TEXT] --decode --form --parse --json --copy` - Percent-encode text for a URL, decode it with -d, or --parse a URL.
### tools
- `jzr uuid --count N --v7 --upper --no-hyphens --copy` - Generate UUIDs (v4, or v7 with --v7).
- `jzr password --length N --no-upper --no-lower --no-digits --no-symbols --no-similar --words N --separator TEXT --count N --copy` - Generate passwords or passphrases.
- `jzr hash [FILES]... --text TEXT --algorithm [md5|sha1|sha256|sha384|sha512|sha3-256|sha3-512|blake2b] --expect HEX --check SUMFILE --json` - Hash files or text (SHA-256 by default).
- `jzr timestamp [VALUE] --unit [s|ms] --tz ZONE --utc --format STRFTIME --json` - Convert between Unix time and dates.
- `jzr jwt [TOKEN] --secret-env NAME --secret-file FILE --require-valid --json` - Decode a JWT and check its times and HMAC signature.
### random
- `jzr random int [MIN MAX] --count N --unique` - Random whole numbers from MIN to MAX, both included (default 1 100).
- `jzr random pick [ITEMS]... --count N --repeat` - Pick random ITEMS; without ITEMS, the lines of stdin.
- `jzr random shuffle [ITEMS]...` - Print ITEMS in random order; without ITEMS, the lines of stdin.
### data
- `jzr data convert [FILE] --from [json|jsonl|yaml|csv|toml|xlsx] --to [json|jsonl|yaml|csv|toml|xlsx] --delimiter [,|;|tab||] --no-header --header-row N --types --nest --sheet NAME --indent N --minify` - Convert data between JSON, YAML, CSV, TOML and Excel.
- `jzr data json [FILES]... --indent N --tab --minify --sort-keys --check --write --python` - Pretty-print, minify or check JSON from FILES or stdin.
### env
- `jzr env compare [A] [B] --values --json` - Show the keys only in A (default .env.example) or only in B (default .env).
- `jzr env example [FILE] --copy` - Print the keys of FILE (default .env) as KEY= lines for a .env.example.

## Rules

- With no SOURCES, a command takes every matching file in `--dir` (default: the current folder); `-r` adds subfolders and `-p REGEX` filters.
- `jzr data convert` reads CSV cells as text; `--types` makes numbers, true/false and empty cells real values, which Excel and JSON output need for real numbers.
- `jzr data convert` prints text formats to stdout; `-o FILE` writes a file instead. Excel output is always a file, `-o FILE` or `<name>.xlsx` beside the source: never redirect it with `>`. Its `wrote ...` line counts the rows and the number and text cells, so there is no need to convert back to check.
- `jzr file rename` only shows its plan; `-y` applies it and renames in place. `jzr data json -w` rewrites its files in place.
- Use `--json` where a command has it, to read results by program.
- Exit codes: 0 all went well, 1 something failed, 2 wrong usage. One bad file does not stop the others.
- `jzr text diff` and `jzr env compare` exit 1 when they find a difference, 2 when a file cannot be read.
- Quote regexes and rename templates.
- Never put a secret on the command line: `jzr jwt` takes it with `--secret-env` or `--secret-file`.
- jzr does not edit spreadsheets. Use another tool for that.
- Do not guess options: an unknown one exits 2.
