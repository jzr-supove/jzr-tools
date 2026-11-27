# Text, data and developer tools

[← jzr-tools](../README.md) · [Install](install.md) · [Images](images.md) · [Video and audio](video-audio.md) · [PDF](pdf.md) · [Text and data](text-and-data.md) · [Files](files.md) · [Ports and host](system.md) · [Shortcuts](shortcuts.md) · [Completion](completion.md) · [AI agents](ai-agents.md)

## Text

Turn text into a lowercase kebab-case slug:

```bash
jzr text slug "ApiSix deployment Notes"         # api-six-deployment-notes
jzr text slug "ApiSix deployment Notes" --date  # 2026-11-15_api-six-deployment-notes
jzr text slug "ApiSix deployment Notes" -c      # ...and copy it to the clipboard
jzr text slug "myVarName" -s _                  # my_var_name
jzr text slug "Read Me" --preserve-case         # Read-Me
```

Omit the text argument to paste or pipe multiline content. Runs of spaces,
newlines, and punctuation collapse into one separator, and camelCase or
PascalCase words are split (`HTTPServer` becomes `http-server`). The date
prefix is always followed by `_`. `jzr text kebab` is the same command under
its old name.

The result is ASCII. Accented Latin letters lose their accents, Russian and
Uzbek Cyrillic are transliterated (`Привет мир` becomes `privet-mir`), and
letters such as `ß` and `ø` are spelled out. Other scripts are dropped; if
nothing is left, the command exits `1` instead of printing an empty result.

### Base64

```bash
jzr text base64 "hello"                 # aGVsbG8=
jzr text base64 -d aGVsbG8=             # hello
jzr text base64 -f logo.png             # a file's bytes
jzr text base64 -d -o logo.png < logo.b64
jzr text base64 --url-safe "a?b"        # YT9i, the - and _ alphabet, no padding
```

Without text or `--file`, it reads stdin, and when encoding it uses the bytes
exactly: `echo hi | jzr text base64` gives `aGkK`, the newline included, as
`base64` does (PowerShell pipes end lines with `\r\n`, giving `aGkNCg==`;
pass the text as an argument to leave the line break out). Decoding ignores spaces and line breaks but refuses anything
else that is off (a wrong character, wrong padding, a length no data has)
rather than guessing. Decoded bytes that are not text are not printed; write
them with `-o FILE` (`--overwrite` to replace a file). `-c` also copies the
result.

### URLs

```bash
jzr text url "a b&c=d"                  # a%20b%26c%3Dd
jzr text url -d "a%20b%26c%3Dd"         # a b&c=d
jzr text url --form "a b"               # a+b, as in query strings and forms
jzr text url --parse "https://bob:secret@example.com:8443/a?q=1&q=2#top"
```

Encoding works like JavaScript's `encodeURIComponent`. A `%` that is not
followed by two hex digits, or bytes that are not UTF-8, is an error on
decoding, not replaced. Piped input loses one trailing line break, so
`echo x | jzr text url` gives `x`.

`--parse` prints the scheme, host, port, user, path, fragment and each query
parameter on its own line, in order, with duplicates and empty names kept
and values decoded. A password in the URL shows as `***`. `--json` prints the
same for scripts.

### Diff

```bash
jzr text diff old.txt new.txt          # unified diff, 3 lines of context
jzr text diff -U 0 a.conf b.conf       # no context lines
jzr text diff -w a.py b.py             # ignore spaces at the start and end of lines
jzr text diff old.txt new.txt > change.patch
```

- The output is a unified diff, as `diff -u` writes it, so `patch` and
  `git apply` read it. It exits 0 when the files are the same (and prints
  nothing), 1 when they differ and 2 when one cannot be compared, as `diff`
  does.
- On a terminal, removed lines are red and added lines green. A line that
  was edited rather than replaced is shown right above its new version, with
  the changed characters underlined. `--color always|never` decides instead;
  `NO_COLOR` turns the automatic colour off.
- CRLF and LF line endings count as the same; when they are the only
  difference, jzr says so on stderr. `--strict-eol` compares them.
- A missing newline at the end of a file shows as
  `\ No newline at end of file`. Files with NUL bytes or that are not UTF-8
  are not compared. Use `-` to read one side from stdin.

## Timestamps

```bash
jzr timestamp                           # now
jzr timestamp 1795236000                # Unix seconds
jzr timestamp 1795236000000             # milliseconds, told apart by size
jzr timestamp 2026-11-21T09:40          # a local date and time
jzr timestamp 2026-11-21T09:40 --tz Europe/Berlin
jzr timestamp 1795236000 --format "%d %B %Y"
```

```
Unix seconds  1795236000
Unix ms       1795236000000
UTC           2026-11-21T04:40:00Z
Local         2026-11-21T09:40:00+05:00 (Asia/Tashkent)
Relative      2 hours ago
```

- A number of at least 100,000,000,000 is read as milliseconds, anything
  smaller as seconds; `--unit s` or `--unit ms` decides instead. Fractions
  are kept (`1.125` is 1125 ms), and negative numbers work as they are
  (`jzr timestamp -86400`).
- Dates are ISO 8601: `2026-11-21`, `2026-11-21T09:40`, with seconds,
  fractions, `Z` or an offset such as `+05:00`. Without an offset the time is
  local, or in the `--tz` zone. A local time that the clocks skip or show
  twice when they change is an error that says so, instead of being moved.
- `--tz` takes IANA names (`Europe/Berlin`, `America/New_York`); `--utc` is
  `--tz UTC`. On Windows the local zone is shown with the name Windows gives
  it, such as `West Asia Standard Time`.
- `--format` uses Python's `strftime`. Platform extras such as `%-d` work on
  Linux and macOS but not on Windows.
- `--json` prints `seconds`, `milliseconds`, `utc`, `local` and `zone`.

## JSON

```bash
jzr data json response.json              # pretty-print with 2 spaces
curl -s https://example.com/api | jzr data json
jzr data json --minify --sort-keys a.json
jzr data json --check *.json             # exit 1 and say where, if any is invalid
jzr data json --write config.json        # rewrite the file in place
python -c "print({'a': True})" | jzr data json --python
```

- Only whitespace changes. Numbers stay as written (`1.10`, `1e400` and
  long integers are not rounded), string escapes such as `é` are not
  decoded, and duplicate keys are kept in their order.
- `--indent N` (0 to 8, default 2), `--tab` or `--minify` set the layout;
  `--sort-keys` sorts every object by key.
- An error names the input, line, column and what was expected, for example
  `bad.json: invalid JSON at line 2 column 7: expected a value`. With several
  files the others still run; the exit code is 1 if any failed.
- `--write` prints `FILE: formatted` or `FILE: already formatted` and leaves
  an already formatted file untouched. The file keeps its line endings, its
  byte order mark and whether it ends with a newline.
- `--python` reads a Python literal, such as a printed dict with `True`,
  `None` and single quotes, without running it, and prints it as JSON.
- Output always ends lines with LF, on Windows too.

## Converting data

```bash
jzr data convert config.yaml --to json        # print to stdout
jzr data convert users.json -o users.csv      # the format from the extension
jzr data convert rows.csv --types --to xlsx   # rows.xlsx beside rows.csv
jzr data convert report.xlsx --to json --sheet Sales --header-row 3
curl -s https://example.com/api | jzr data convert --from json --to yaml
```

```text
wrote users.csv (2 rows, 64 B)
note: arrays were written as JSON text
```

- Formats: JSON, JSON Lines (`.jsonl`, `.ndjson`), YAML, CSV (`.tsv` is
  tab-separated), TOML and Excel (`.xlsx`, `.xlsm`). They come from the file
  names, or from `--from` and `--to`; stdin needs `--from`. Old `.xls`,
  `.ods` and `.xlsb` files are refused: save them as `.xlsx` first.
- Text goes to stdout unless `-o` names a file. Excel output goes to `-o`, or
  to `<name>.xlsx` beside the input. An existing file is only replaced with
  `--overwrite`, and never when it is the input itself.
- Anything that changes on the way is named in a `note:` on stderr: duplicate
  keys (the last value is kept), keys that were not text, dates that became
  text, YAML comments and anchors, empty cells, arrays written as text, and
  cells that a spreadsheet might run as a formula (they are kept as written).
- CSV and Excel: the first row holds the column names, and nested objects
  become dotted columns (`user.name`); `--nest` turns them back into objects.
  CSV cells stay text unless `--types` reads numbers, `true`/`false` and empty
  cells. `--no-header` reads every row as an array.
- YAML is read with the YAML 1.2 rules: `no` and `on` stay text, `0777` is a
  number, dates stay text, and one document per input.
- Excel: every visible sheet is read (`--sheet` picks sheets, also hidden
  ones), merged cells are filled in, and a formula gives its saved value. Text
  longer than a cell allows is cut and numbers with more than 15 digits are
  written as text, both with a note.
- Errors name the input and where the problem is, such as `bad.json: invalid
  JSON at line 2 column 7: expecting value`, exit 1 and write nothing.

## JWTs

```bash
jzr jwt eyJhbGciOiJIUzI1NiIs...
jzr jwt "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
jzr jwt --secret-env JWT_SECRET eyJhbGciOiJIUzI1NiIs...
jzr jwt --require-valid --json < token.txt
```

```
Header
{
  "alg": "HS256",
  "typ": "JWT"
}

Payload
{
  "sub": "123",
  "exp": 1795348800
}

exp  2026-11-22T12:00:00Z (in 1 day)   not expired
iat  not present
nbf  not present
```

- Decoding only shows what the token says; it proves nothing. Without a
  secret, jzr says on stderr that the signature was not checked.
- To check an HS256, HS384 or HS512 signature, give the secret with
  `--secret-env NAME` or `--secret-file FILE` (one final line break is
  dropped), never on the command line. jzr then prints `signature valid`
  or `signature INVALID`; an invalid signature exits 1. Tokens signed with
  `none`, RSA or EC keys, or with `crit` header parameters, are not checked.
- `exp` counts as expired from the second it names. `--require-valid` exits
  1 when the token is expired, not yet valid (`nbf`) or has an invalid
  signature, and says which.
- `--json` prints the header, payload, each time claim and the signature
  result. The token and the secret never appear in error messages.

## .env files

```bash
jzr env example                         # print the keys of .env as KEY= lines
jzr env example -o .env.example         # write them to a file
jzr env compare                         # .env.example against .env
jzr env compare .env.staging .env.production --values
```

```
Only in .env.example (2):
  API_KEY
  DB_URL
Only in .env (1):
  DEBUG
```

- `example` keeps each key once, in the order it first appears, and leaves
  out every value and comment, so the result is safe to commit. `-o FILE`
  writes it (`--overwrite` to replace a file); it refuses to write over the
  file it reads. `-c` copies it too.
- `compare` exits 1 when the key sets differ, so CI can check that `.env`
  has every key of `.env.example`; it exits 2 when a file cannot be read.
  `--values` also lists shared keys whose values differ, with both values;
  without it no value is ever printed. `--json` prints the result for
  scripts.
- The files are read as most loaders read them: `export KEY=value`, `#`
  comments, single or double quotes that may span lines. Values are not
  expanded. A line that cannot be read stops the command with
  `FILE:LINE: what is wrong`, without showing its value.

## Generators

```bash
jzr uuid                       # 5cdd86e0-af4b-4c87-9cee-5ea81baaa2b5
jzr uuid --v7 -n 3             # time-ordered: they sort in the order made
jzr password                   # 20 characters: letters, digits and symbols
jzr password -l 12 --no-symbols --no-similar
jzr password -w 6              # bobcat-rotten-distrust-chamber-spore-cinch
jzr password -c                # copy to the clipboard without printing it
jzr random int                 # 1 to 100
jzr random int -10 10 -n 5 --unique
jzr random pick tea coffee water
jzr random shuffle < names.txt
```

`jzr uuid` makes version 4 UUIDs; `--v7` makes version 7 ones, which start
with the time, so a batch made in one run sorts in creation order.
`--upper`, `--no-hyphens`, `-n` and `-c` (copy as well) change the output.

`jzr password` always uses at least one character of each type it is given
(`--no-upper`, `--no-lower`, `--no-digits`, `--no-symbols` take one out;
`--no-similar` leaves out `I l 1 O 0 o`). `-w N` makes a passphrase of N words
(3-20) from the EFF Long Wordlist, joined by `-` (`--separator`). On a
terminal it also says how strong the result is (`20 characters, about 129
bits`). `-c` puts it on the clipboard instead of the screen, so it does not
stay in your terminal's history.

`jzr random int [MIN MAX]` includes both ends; `--unique` never repeats a
number, even across a huge range. `jzr random pick` and `jzr random shuffle`
take items as arguments, or one per line on stdin; `pick -n 3` picks three
different ones, `--repeat` allows repeats.

Every random value comes from the operating system's secure source
(Python's `secrets`), with no bias towards any value.

## Hashes

```bash
jzr hash file.iso                          # sha256, printed like sha256sum
jzr hash *.zip > SHA256SUMS                # a list sha256sum -c can read
jzr hash -c SHA256SUMS                     # file.zip: OK / FAILED / missing
jzr hash file.iso --expect 9c2c9d42...     # OK or MISMATCH (exit 1)
jzr hash -t "some text" -a md5             # hash text instead of a file
```

`-a` chooses `md5`, `sha1`, `sha256` (default), `sha384`, `sha512`,
`sha3-256`, `sha3-512` or `blake2b`. Without files, jzr hashes stdin. The
lines end in LF on every system, so a list written on Windows works with
`sha256sum -c` too. `--check` finds the files next to the list, whatever
folder you run it from, and takes the algorithm from the digest length (or
`-a`). `--json` prints the results for scripts.
