# jzr env

## jzr env compare

```text
Usage: jzr env compare [OPTIONS] [A] [B]

  Show the keys only in A (default .env.example) or only in B (default .env).

  Exits 1 when the key sets differ, so CI can check that .env has every key of
  .env.example, and 2 when a file cannot be read. Values are shown only with --values.
  Use - for stdin (one side).

Options:
  --values    Also show keys whose values differ, with both values.
  --json      Print the result as JSON.
```

Examples:

- `jzr env compare` - compares .env.example with .env, keys only
- `jzr env compare .env.example .env.test --json` - compares two named files

## jzr env example

```text
Usage: jzr env example [OPTIONS] [FILE]

  Print the keys of FILE (default .env) as KEY= lines for a .env.example.

  Values and comments are left out; each key is listed once, in the order it first
  appears. Use - for stdin.

Options:
  -o, --output FILE  Write the example to this file instead of printing it.
  --overwrite        Replace the -o file if it exists.
  -c, --copy         Copy the result to the clipboard too.
```

Examples:

- `jzr env example` - prints the keys of .env without values
- `jzr env example .env -o .env.example` - writes the file
