# jzr data

## jzr data convert

```text
Usage: jzr data convert [OPTIONS] [FILE]

  Convert data between JSON, YAML, CSV, TOML and Excel.

  The formats come from the file names, or from --from and --to. Text goes to stdout
  unless -o names a file; Excel output goes to -o, or to <name>.xlsx beside FILE.
  Anything lost on the way is named in a note on stderr.

Options:
  -f, --from [json|jsonl|yaml|csv|toml|xlsx]
                                  Format of the input; needed for stdin.
  -t, --to [json|jsonl|yaml|csv|toml|xlsx]
                                  Format to write; or give -o a file name with an
                                  extension.
  -o, --output FILE               Write to this file instead of stdout.
  --delimiter [,|;|tab||]         CSV delimiter: , ; tab or | (default , and tab for
                                  .tsv).
  --no-header                     CSV and Excel: the first row is data; rows become
                                  arrays.
  --header-row N                  Excel: the row with the column names (default: the
                                  first row with data).  [x>=1]
  --types                         CSV: read numbers, true/false and empty cells instead
                                  of text.
  --nest                          CSV and Excel: turn dotted column names into nested
                                  objects.
  --sheet NAME                    Excel: read this sheet; repeat for more (default:
                                  every visible sheet).
  --indent N                      JSON: indent with N spaces, 0 to 8 (default 2).
                                  [0<=x<=8]
  --minify                        JSON: leave out all whitespace.
  --overwrite                     Replace an existing output file.
```

Examples:

- `jzr data convert config.yaml --to json` - prints the YAML as JSON
- `jzr data convert users.json -o users.csv` - nested keys become dotted columns
- `jzr data convert report.xlsx --to json --sheet Sales` - reads one sheet
- `jzr data convert rows.csv --types --to xlsx` - numbers stay numbers; writes rows.xlsx

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
