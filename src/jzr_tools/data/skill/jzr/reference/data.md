# jzr data

## jzr data json

```text
Usage: jzr data json [OPTIONS] [FILES]...

  Pretty-print, minify or check JSON from FILES or stdin.

  Only whitespace changes: numbers, string escapes and duplicate keys stay exactly as
  written. With several FILES each is printed in turn; a file that fails is named on
  stderr and the others still run. Use - for stdin.

Options:
  --indent N   Indent with N spaces, 0 to 8 (default 2).  [0<=x<=8]
  --tab        Indent with tabs.
  --minify     Leave out all whitespace.
  --sort-keys  Sort the members of every object by key.
  --check      Only check that the input is valid; print nothing.
  -w, --write  Rewrite the FILES in place.
  --python     Read a Python literal (a repr) and print it as JSON.
```

Examples:

- `jzr data json config.json` - pretty-prints to stdout
- `jzr data json --check a.json b.json` - only checks the files
- `jzr data json config.json --sort-keys -w` - rewrites the file in place
