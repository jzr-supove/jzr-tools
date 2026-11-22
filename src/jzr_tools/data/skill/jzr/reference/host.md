# jzr host

## jzr host status

```text
Usage: jzr host status [OPTIONS]

  Show host, CPU, memory, storage and GPU status.

  Works on Windows, Linux and macOS. Values the system does not expose are left out.

Options:
  -l, --live              Keep refreshing until interrupted with Ctrl+C.
  -n, --interval SECONDS  Seconds between --live readings.  [default: 2]  [x>=0.2]
  -d, --detail            Add per-core, swap, mount, network and sensor detail.
  --json                  Print everything as JSON instead of text.
```

Examples:

- `jzr host status` - shows CPU, memory, storage and GPU
- `jzr host status --json` - prints everything as JSON

## jzr host clean

```text
Usage: jzr host clean [OPTIONS]

  Find caches and other disposable data, and remove what you pick.

  On a terminal it shows a checkbox list with the safely removable items ticked. Nothing
  is removed before you confirm.

Options:
  -n, --dry-run      Show what would be removed; remove nothing.
  -y, --yes          Do not ask: remove the --only items, or everything that is safe to
                     remove.
  --only ID[,ID...]  Select these items instead of showing the list.
  --json             Print what was found as JSON; remove nothing.
```

Examples:

- `jzr host clean --dry-run` - shows what could be removed
- `jzr host clean --json` - prints what was found, removes nothing

## jzr host procs

```text
Usage: jzr host procs [OPTIONS] [PATTERN]

  Show what uses the most memory, grouped by application.

  PATTERN keeps the rows whose name or command line contains it. With --kill, the
  processes you tick are asked to exit; nothing is ticked for you, and system processes
  and this terminal cannot be ticked.

Options:
  -n, --top INTEGER RANGE  How many rows to show.  [default: 15; x>=1]
  --each                   List single processes instead of applications.
  -k, --kill               Tick rows in a list, confirm, and end their processes.
  --force                  With --kill: force-kill what is still running after 5
                           seconds, without asking.
  --json                   Print the rows as JSON.
```

Examples:

- `jzr host procs -n 5` - shows the five biggest applications
- `jzr host procs chrome --each --json` - lists matching processes as JSON
