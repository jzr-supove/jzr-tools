# jzr port

## jzr port kill

```text
Usage: jzr port kill [OPTIONS] PORT...

  Force-kill processes using a local port (Windows, Linux, and macOS).

  PORT is a local TCP/UDP port to free; give several to free them all.

Options:
  --restart-hns  Windows: restart HNS if a port is excluded (Administrator required;
                 interrupts container/WSL networking).
```

Examples:

- `jzr port kill 3000 8080` - ends the processes on both ports

## jzr port show

```text
Usage: jzr port show [OPTIONS] [PORT]...

  Show which processes are using local ports.

  With no PORT, list every listening TCP port, and bound UDP ports with --udp. With one
  or more PORTs, show every socket bound to each of them. Nothing is killed.

Options:
  --udp       Without PORT: also list bound UDP sockets.
  --json      Print one JSON array of sockets.
```

Examples:

- `jzr port show` - lists every listening TCP port
- `jzr port show 3000 --json` - shows who uses port 3000
