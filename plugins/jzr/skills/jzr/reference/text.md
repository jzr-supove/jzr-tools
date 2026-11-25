# jzr text

## jzr text base64

```text
Usage: jzr text base64 [OPTIONS] [TEXT]

  Encode text or a file as Base64, or decode it with -d.

  Without TEXT or --file, reads stdin; when encoding, its bytes are used exactly, so a
  trailing newline is encoded too.

Options:
  -d, --decode       Decode instead of encode.
  -f, --file FILE    Read the input from this file (its bytes, when encoding).
  --url-safe         Use the - and _ alphabet, padding optional.
  -o, --output FILE  Write the decoded bytes to this file.
  --overwrite        Replace the -o file if it exists.
  -c, --copy         Copy the result to the clipboard too.
```

Examples:

- `jzr text base64 "hello"` - encodes text
- `jzr text base64 -d aGVsbG8=` - decodes it
- `jzr text base64 -f logo.png` - encodes a file

## jzr text diff

```text
Usage: jzr text diff [OPTIONS] OLD NEW

  Show how text file NEW differs from OLD, as a unified diff.

  Exits 0 when they are the same, 1 when they differ and 2 when a file cannot be
  compared. CRLF and LF count as the same unless --strict-eol is given. Use - for stdin
  on one side.

Options:
  -U, --unified N              Lines of context around each change.  [default: 3; x>=0]
  -w, --ignore-space           Ignore spaces and tabs at the start and end of lines.
  --strict-eol                 Treat CRLF and LF line endings as different.
  --color [auto|always|never]  Colour the output (auto: only on a terminal, and not when
                               NO_COLOR is set).  [default: auto]
```

Examples:

- `jzr text diff old.txt new.txt` - exits 1 when they differ
- `jzr text diff old.txt new.txt -U 0 -w` - leaves out context and indentation changes

## jzr text slug

```text
Usage: jzr text slug [OPTIONS] [TEXT]

  Convert text into a URL-safe slug, lowercase kebab-case by default.

  camelCase and PascalCase words are split (myVarName becomes my-var-name). Omit TEXT to
  read pasted or piped stdin.

Options:
  -s, --separator TEXT    Separator to put between words.  [default: -]
  --preserve-case         Keep the original letter casing instead of lowercasing.
  --date, --include-date  Prefix the result with today's ISO date and an underscore.
  -c, --copy              Copy the result to the system clipboard.
```

Examples:

- `jzr text slug "Hello World"` - prints hello-world
- `jzr text slug "Hello World" -s _ --date` - uses underscores, with the date in front

## jzr text url

```text
Usage: jzr text url [OPTIONS] [TEXT]

  Percent-encode text for a URL, decode it with -d, or --parse a URL.

  Encoding works like JavaScript's encodeURIComponent. Without TEXT, reads stdin and
  drops one trailing line break.

Options:
  -d, --decode  Decode %XX escapes instead of encoding.
  --form        Use + for spaces, as HTML forms and query strings do.
  --parse       Show the parts of a URL instead.
  --json        With --parse: print the parts as JSON.
  -c, --copy    Copy the result to the clipboard too.
```

Examples:

- `jzr text url "a b&c"` - prints a%20b%26c
- `jzr text url --parse "https://example.com/p?x=1" --json` - prints the parts as JSON
