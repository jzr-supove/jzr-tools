# jzr bind

## jzr bind add

```text
Usage: jzr bind add [OPTIONS] NAME [COMMAND]...

  Bind NAME to COMMAND, so that `jzr NAME` runs it.

  COMMAND is everything after NAME, written out or as one quoted string. It is run
  directly, without a shell: no pipes, `&&` or variables. Write {1}, {2}, ... where the
  words given when it runs belong, and {*} for the rest of them; without {*}, the rest
  go on the end.

Options:
  -f, --force  Replace the binding if NAME is already bound.
```

Examples:

- `jzr bind add serve python -m http.server 8000` - makes jzr serve run that command
- `jzr bind add show "git show {1} --stat"` - puts the first argument in place of {1}

## jzr bind list

```text
Usage: jzr bind list [OPTIONS]

  List the bindings.

Options:
  --json      Print the bindings as JSON.
```

Examples:

- `jzr bind list --json` - prints the bindings as JSON

## jzr bind path

```text
Usage: jzr bind path [OPTIONS]

  Print where the bindings are stored.
```

Examples:

- `jzr bind path` - prints where they are stored

## jzr bind remove

```text
Usage: jzr bind remove [OPTIONS] NAME...

  Remove bindings.
```

Examples:

- `jzr bind remove serve` - removes one binding

## jzr bind run

```text
Usage: jzr bind run [OPTIONS] NAME [ARGS]...

  Run the binding NAME, with ARGS added to its command.

  The same as `jzr NAME`, and it also reaches a binding whose name a built-in command
  has taken.
```

Examples:

- `jzr bind run serve` - runs it, even when a command has the same name
