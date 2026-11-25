# jzr top-level commands

## jzr uuid

```text
Usage: jzr uuid [OPTIONS]

  Generate UUIDs (v4, or v7 with --v7).

  Version 7 IDs made in one run sort in the order they were made.

Options:
  -n, --count INTEGER  How many to make.  [default: 1]
  --v7                 Version 7: time-ordered, sorts by creation.
  --upper              Upper-case letters.
  --no-hyphens         Leave out the hyphens.
  -c, --copy           Also copy the result to the clipboard.
```

Examples:

- `jzr uuid` - prints one random UUID
- `jzr uuid --v7 -n 5` - prints five that sort by time

## jzr password

```text
Usage: jzr password [OPTIONS]

  Generate passwords or passphrases.

  Passwords use upper- and lower-case letters, digits and symbols, with at least one of
  each type chosen. -w makes a passphrase from the EFF Long Wordlist. On a terminal the
  strength is shown on stderr; --copy puts the result on the clipboard without printing
  it.

Options:
  -l, --length INTEGER  Characters.  [default: 20]
  --no-upper            No upper-case letters.
  --no-lower            No lower-case letters.
  --no-digits           No digits.
  --no-symbols          No symbols.
  --no-similar          Leave out look-alikes: I l 1 O 0 o.
  -w, --words INTEGER   Make a passphrase of this many words (3-20).
  --separator TEXT      Between passphrase words.  [default: -]
  -n, --count INTEGER   How many to make.  [default: 1]
  -c, --copy            Copy to the clipboard instead of printing.
```

Examples:

- `jzr password -l 24` - prints a 24 character password
- `jzr password -w 5` - prints a five word passphrase

## jzr hash

```text
Usage: jzr hash [OPTIONS] [FILES]...

  Hash files or text (SHA-256 by default).

  Prints HEX  NAME per file, as sha256sum does; without FILES (or with -) hashes stdin.
  --check reads such a list, with names relative to the list's own folder.

Options:
  -t, --text TEXT                 Hash this text (UTF-8) instead of files.
  -a, --algorithm [md5|sha1|sha256|sha384|sha512|sha3-256|sha3-512|blake2b]
                                  Which hash to compute.  [default: sha256]
  --expect HEX                    Say OK or MISMATCH for one input.
  -c, --check SUMFILE             Check the files listed in a sha256sum-style file.
  --json                          Print the results as JSON.
```

Examples:

- `jzr hash setup.exe` - prints the SHA-256
- `jzr hash setup.exe -a md5 --expect d41d8cd98f00b204e9800998ecf8427e` - says OK or MISMATCH
- `jzr hash -c SHA256SUMS` - checks every listed file
- `jzr hash -t "hello" --json` - hashes text and prints JSON

## jzr timestamp

```text
Usage: jzr timestamp [OPTIONS] [VALUE]

  Convert between Unix time and dates.

  VALUE is Unix seconds or milliseconds (told apart by size unless --unit says), or an
  ISO 8601 date such as 2026-11-21T09:40 (local time unless it has an offset or --tz is
  given). Without VALUE: now.

Options:
  --unit [s|ms]      Read VALUE as seconds or milliseconds (default: guessed from its
                     size).
  --tz ZONE          Show this IANA zone, e.g. Europe/Berlin.
  --utc              Show UTC; the same as --tz UTC.
  --format STRFTIME  Print only this strftime format.
  --json             Print the result as JSON.
```

Examples:

- `jzr timestamp` - shows the time now
- `jzr timestamp 1763715600 --tz Europe/Berlin` - shows a Unix time in a zone
- `jzr timestamp 2026-11-21T09:40 --utc --json` - converts a date to Unix time

## jzr jwt

```text
Usage: jzr jwt [OPTIONS] [TOKEN]

  Decode a JWT and check its times and HMAC signature.

  TOKEN may start with "Authorization: Bearer"; without TOKEN (or with -) it is read
  from stdin. Decoding never proves a token is genuine: give the secret with --secret-
  env or --secret-file (never on the command line) to check an HMAC signature.

Options:
  --secret-env NAME   Check an HS256/384/512 signature with the secret in this
                      environment variable.
  --secret-file FILE  Check the signature with the secret in this file (one final line
                      break is dropped).
  --require-valid     Exit 1 when the token is expired, not yet valid, or its signature
                      is invalid.
  --json              Print the result as JSON.
```

Examples:

- `jzr jwt - --json` - decodes a token from stdin
- `jzr jwt - --secret-env JWT_SECRET --require-valid` - checks the signature and times
