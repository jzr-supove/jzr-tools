# jzr file

## jzr file rename

```text
Usage: jzr file rename [OPTIONS] [SOURCES]...

  Rename many files at once; shows the plan unless --apply is given.

  Files keep their folder. Names that would clash get -2, -3, ...; a name taken by
  another file is skipped; nothing is ever overwritten.

Options:
  -d, --dir DIRECTORY             Folder to scan when no SOURCES are listed.  [default:
                                  .]
  -p, --pattern TEXT              Regex filter on the path relative to --dir (the file
                                  name for SOURCES).
  -r, --recursive                 Scan subfolders too.
  --hidden                        Include hidden files and folders in a scan.
  --replace OLD NEW               Replace text in the name (repeatable).
  --regex PATTERN REPL            Regex replace in the name; \1 and \g<name> work
                                  (repeatable).
  -i, --ignore-case               Make --replace ignore case.
  --case [lower|upper|title|slug|snake]
                                  Change the case; slug and snake also make the name
                                  ASCII.
  --prefix TEXT                   Add text before the name.
  --suffix TEXT                   Add text after the name, before the extension.
  --template TEXT                 Build the name from fields, e.g. "{date}-{n:02}".
  --ext EXT                       New extension; --ext "" removes it.
  --lower-ext                     Lower-case the extension.
  --start INTEGER                 First {n}.  [default: 1]
  --sort [name|mtime|size]        Order of files for {n}.  [default: listed order, or
                                  name for a scan]
  --reverse                       Reverse the order.
  -y, --apply                     Rename; without it only the plan is shown.
  -v, --verbose                   Also list files whose name stays.
  --json                          Print the plan or result as JSON.

  Name steps run in this order on the name without its extension: --replace, --regex,
  --case, --prefix/--suffix, --template. Template fields: {name} {n} {parent} {date}
  {ext}; {n:03} pads the number.

  Example: jzr file rename -d photos --template "{date}-{n:03}" --lower-ext -y
```

Examples:

- `jzr file rename -d photos --template "{date}-{n:03}"` - shows the plan, renames nothing
- `jzr file rename -d photos --template "{date}-{n:03}" -y` - applies that plan
- `jzr file rename draft-1.txt draft-2.txt --replace draft final -y` - replaces text in two names
- `jzr file rename -d docs -r --case slug --lower-ext --json` - prints the plan as JSON
