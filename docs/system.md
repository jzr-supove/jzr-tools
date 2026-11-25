# Ports and your machine

[← jzr-tools](../README.md) · [Install](install.md) · [Images](images.md) · [Video and audio](video-audio.md) · [PDF](pdf.md) · [Text and data](text-and-data.md) · [Files](files.md) · [Ports and host](system.md) · [Shortcuts](shortcuts.md) · [Completion](completion.md) · [AI agents](ai-agents.md)

## Ports

See what is using a port, then free it:

```bash
jzr port show              # every listening TCP port and its process
jzr port show --udp        # ...and bound UDP ports too
jzr port show 3000 8080    # every socket on these ports
jzr port show --json       # for scripts
jzr port kill 3000
jzr killport 3000 8080     # shortcut for `jzr port kill`, several ports at once
```

Without a port, `jzr port show` prints a table sorted by port. A process that
listens on the same port over IPv4 and IPv6 gets one row:

```text
PORT  PROTO  PID    NAME         ADDRESS
135   TCP    1700   svchost.exe  0.0.0.0, [::]
445   TCP    4      System       0.0.0.0, [::]
3000  TCP    18472  node.exe     127.0.0.1
```

With ports, it prints one line per socket with the PID, name, protocol, local
address and state. `--json` prints one array of sockets in both cases, each
with `port`, `protocol`, `address`, `pid` (`null` when unknown), `name` and
`status` (`null` for UDP). `port show` changes nothing.

`jzr port kill` force-kills all visible processes bound to the exact local port, for TCP and UDP
over IPv4 and IPv6. Works on Windows, Linux, and macOS using `psutil`, installed
with the toolkit. On Unix this sends SIGKILL; on Windows it uses TerminateProcess.
Remote connections to the port are not targeted, and unrelated child processes
are not killed. There is no confirmation prompt.

Ports must be between 1 and 65535. Give several ports to free them in one call;
a process holding more than one of them is killed once and reported for each.
The command prints each killed PID and returns success if no process or Windows
port exclusion is found; a failure on one port does not stop the others. Permission errors or processes that do not exit
produce a nonzero exit code, while other matching processes are still attempted.
Processes that restart automatically may reclaim the port.

Use an Administrator terminal on Windows or `sudo jzr killport 3000` on Unix
when permissions prevent inspection or termination. macOS requires root for
system-wide socket inspection; Linux may omit sockets without sufficient
permissions (see the [psutil documentation](https://psutil.readthedocs.io/stable/#psutil.net_connections)).

### Windows excluded ports (EACCES)

Windows can reserve a port even when no process is listening. `jzr killport`
checks active TCP/UDP exclusion ranges for IPv4 and IPv6, prints matching ranges,
and returns a nonzero exit code when the port is excluded. This explains cases
such as Next.js reporting `listen EACCES: permission denied 0.0.0.0:3000` while
no port owner is visible.

Choose an unreserved port, or try your HNS recovery workflow from an
**Administrator terminal**:

```powershell
jzr killport 3000 --restart-hns
```

The flag stops and starts the Host Network Service only when an exclusion is
detected. This can interrupt Docker, containers, and WSL networking. The command
rechecks all exclusion ranges and tests local binds afterward; it fails if the
port is still unavailable. With several excluded ports, HNS is restarted once. A restart is not guaranteed to clear a reservation,
and Windows may reserve the port again later. Administered exclusions (`*`) are
reported without restarting HNS. The command does not delete reservations or
change dynamic port ranges. Linux and macOS reject `--restart-hns`.

## Host status

See what the machine is and how busy it is:

```bash
jzr host status                 # host, CPU, memory, storage, GPU
jzr host status --detail        # plus per-core usage, swap, every mount, network, sensors
jzr host status --live          # keep refreshing until Ctrl+C
jzr host status --live -n 0.5   # refresh every half second
jzr host status --json          # everything, for scripts
```

```text
╭─ fedora ──────────────────────────────────────────────────────╮
│  Fedora Linux 42 (Workstation Edition)  x86_64  up 4h 30m     │
├─ CPU ─────────────────────────────────────────────────────────┤
│  12th Gen Intel(R) Core(TM) i7-12700  (12 cores, 20 threads)  │
│  Usage      ████░░░░░░░░░░░░░░░░   19%                        │
│  Load       1.94  2.13  2.32                                  │
├─ Memory ──────────────────────────────────────────────────────┤
│  RAM        ██████████████░░░░░░   70%     21.8 / 31.1 GiB    │
├─ Storage ─────────────────────────────────────────────────────┤
│  /          ████████████████░░░░   81%   363.5 / 451.6 GiB    │
│  /boot      ██████████░░░░░░░░░░   48%   430.6 / 973.4 MiB    │
├─ GPU ─────────────────────────────────────────────────────────┤
│  NVIDIA GeForce RTX 4080                                      │
│  Usage      ██████░░░░░░░░░░░░░░   31%                        │
│  Memory     █████░░░░░░░░░░░░░░░   25%      4.0 / 16.0 GiB    │
│  Temp       63°C                                              │
│                                                               │
│  Intel UHD Graphics 770                                       │
╰───────────────────────────────────────────────────────────────╯
```

Works on Windows, Linux and macOS using `psutil`, installed with the toolkit.
Anything a system does not expose is left out of the text and is `null` in
JSON; the command still exits `0`.

Options: `-l/--live`, `-n/--interval SECONDS` (default 2, minimum 0.2, only
with `--live`), `-d/--detail`, `--json`.

Behavior worth knowing:

- CPU usage is measured over a quarter of a second for a single reading, and
  over the whole interval for each `--live` refresh.
- Memory "used" is total minus available, so cache the system can give back
  does not count as used.
- Storage lists each device once, at its shortest mount point; `--detail`
  lists every mount with its device, filesystem and free space. Loop devices
  and snap images are never listed.
- GPUs: NVIDIA cards are read through `nvidia-smi` on every system (usage,
  memory, temperature, and in `--detail` driver and power). On Linux, AMD
  cards report the same through the kernel, and Intel cards are listed by
  name. On Windows and macOS other adapters are listed by name only, because
  neither system offers a simple way to read their usage.
- Load average is not shown on Windows, which has none. Temperatures come
  from the kernel's sensors and are only available on Linux.
- `--json` always carries everything, with or without `--detail`. Sizes are
  bytes, percentages run 0-100, and times are ISO 8601. With `--live` it
  prints one compact JSON document per line.
- `--live` redraws in place on a terminal and leaves the last reading on
  screen when you stop it. Piped to a file, readings are appended one after
  another.
- Colour is used only on a terminal, and never when `NO_COLOR` is set.
- The frame is drawn with box characters. Where the output cannot show them
  (a redirected stream on Windows, an ASCII locale) it falls back to `+`, `-`,
  `|` and `[####....]` bars.
- On a terminal the report never exceeds the window: lines that do not fit
  are cut with `…`, and `--live` also stops at the window's height. Piped
  output is never cut.

Linux and Windows 11 (with an NVIDIA card) have been run on real hardware. The
macOS code path, AMD GPU readings and battery reporting are covered by tests
on recorded sample output only.

## Host clean

Find what can be deleted to get disk space back, tick what should go, confirm:

```bash
jzr host clean                    # scan, pick from a checkbox list, confirm, remove
jzr host clean --dry-run          # only look: what was found and what would be done
jzr host clean --yes              # no questions: remove everything that is safe to remove
jzr host clean --only uv,pip      # just these items (asks once; add --yes to skip that)
jzr host clean --json             # the scan, for scripts; removes nothing
```

```text
? What should be removed? (Use arrow keys to move, <space> to select, <a> to toggle, <i> to invert)
   Safe to remove
 » ● uv cache                         24.1 GiB
   ● Docker build cache               19.9 GiB
   ● pip cache                        15.6 GiB
   Review first
   ○ Docker images no container uses  35.3 GiB
   ○ Trash                             4.2 GiB
  Description: Packages are downloaded and built again when needed. Runs: uv cache clean
? Remove 3 items, about 59.6 GiB? (y/N)
```

What it looks for on Linux:

| Tier | Items | Ticked by default |
|------|-------|-------------------|
| Safe to remove | Docker build cache; uv, pip, npm, Cargo, Yarn, pnpm, Go build, node-gyp, Mesa shader and thumbnail caches | yes |
| Review first | Trash; stopped Docker containers; Docker images no container uses; any other folder of 100 MiB or more in `~/.cache` (listed as `cache:<name>`) | no |
| Needs root | systemd journal beyond the newest 500 MiB; the dnf or apt package cache | no |

And on Windows, mostly under `%LOCALAPPDATA%`:

| Tier | Items | Ticked by default |
|------|-------|-------------------|
| Safe to remove | Docker build cache; uv, pip, npm, Cargo, Yarn, pnpm, Go build and node-gyp caches; the graphics drivers' shader caches (NVIDIA, AMD, Intel, Direct3D) | yes |
| Review first | Temp files (`%TEMP%`, `SquirrelTemp`); crash dumps; the Recycle Bin; Docker containers and images; other large folders in `~/.cache` | no |
| Needs administrator | `C:\Windows\Temp`; Windows Update downloads | no |

The ids for `--only` are the same everywhere (`jzr host clean --only x` with an
id the other system has prints `x: nothing to remove`). `GOCACHE`,
`YARN_CACHE_FOLDER`, `UV_CACHE_DIR`, `PIP_CACHE_DIR`, `npm_config_cache` and
`CARGO_HOME` move a cache on either system.

Behavior worth knowing:

- Nothing is removed before you confirm, and `--dry-run` and `--json` never
  remove anything. `--yes` on its own removes the safe tier only; anything
  else has to be ticked or named with `--only`.
- It only looks in known places. It does not hunt for "temporary-looking"
  files, and it never offers Docker volumes, project folders or `/tmp`.
- Where a tool has its own clean-up command it is used (`uv cache clean`,
  `npm cache clean --force`, `docker builder prune --all --force`,
  `journalctl --vacuum-size=500M`, `dnf clean all`); otherwise the cache
  folder is emptied and kept. `--dry-run` shows which.
- Root items run through `sudo`, which asks for your password itself. With
  `--yes` they run as `sudo -n` and fail instead of asking.
- On Windows, administrator items are offered in an elevated terminal, or
  when Windows sudo is turned on; then jzr runs itself through `sudo` for
  them and measures again afterwards. With `--yes` and no elevation they fail
  instead of raising a prompt nobody answers. `C:\Windows\Temp` cannot be
  read without elevation, so it shows up only in an elevated terminal.
- On Windows, files a program still has open cannot be deleted; they are left
  in place and counted (`Temp files: removed 8.0 GiB; 31 files in use were
  kept`), and the item still counts as cleaned. Read-only files are removed.
- The Recycle Bin is sized and emptied through the Windows shell, on every
  drive at once.
- Sizes are estimates. The uv cache shares files with your environments, so
  less may come back than shown; the last line reports the free space before
  and after.
- Symbolic links (and Windows junctions) are never followed, and a link
  inside `~/.cache` is never offered as a cache.
- On Windows a hard-linked file counts once per link: Windows does not report
  link counts while listing a folder, and asking per file made sizing the uv
  cache ten times slower.
- A scan of a large cache takes a while (about 25 seconds here for 68 GiB).
- Without a terminal it refuses to guess: pass `--dry-run`, `--json` or
  `--yes`.
- Exit code `1` means something could not be removed (the rest still was) or
  the list was cancelled.

Removal has been run against throwaway folders only, on Linux and Windows;
the Docker, `sudo`, journal, dnf and apt commands, the Recycle Bin and
Windows sudo are covered by tests with them faked and have not been run for
real. The Windows scan has been run on a real machine.

## Host procs

See what is using memory, by application, and optionally end some of it:

```bash
jzr host procs                  # the 15 biggest applications
jzr host procs chrome           # only rows whose name or command line contains "chrome"
jzr host procs --each -n 30     # single processes, with PID and command line
jzr host procs --kill           # tick rows, confirm, and end them
jzr host procs --kill --force   # ...and force-kill whatever ignores the request
jzr host procs --json           # for scripts
```

```text
╭─ Memory ───────────────────────────────────────────────╮
│  RAM   ██████████████░░░░░░   72%   22.3 / 31.1 GiB    │
│  Swap  ████████████████████  100%     8.0 / 8.0 GiB    │
├─ Applications (5 of 166) ──────────────────────────────┤
│  Application         Procs    Memory       Swap  User  │
│  qemu-system-x86_64      1   3.4 GiB   36.1 MiB  sam   │
│  pycharm                 1   2.9 GiB  263.3 MiB  sam   │
│  msedge                 29   2.4 GiB  577.4 MiB  sam   │
│  node                   40   2.0 GiB    2.0 GiB  sam   │
│  claude                  4   1.1 GiB   86.9 MiB  sam   │
├─ Total ────────────────────────────────────────────────┤
│  Top 5                  75  11.8 GiB    2.9 GiB        │
│  All 166               354  17.1 GiB    6.7 GiB        │
│  Outside processes           5.2 GiB    1.3 GiB        │
│  In use                     22.3 GiB    8.0 GiB        │
╰────────────────────────────────────────────────────────╯
```

Behavior worth knowing:

- A row is every process of one user running the same program, so a browser
  is one line. All scripts run by the same interpreter also share a line; use
  `--each` to tell them apart.
- Memory and swap are each process's fair share of what it uses (pages shared
  by several processes are split between them), so the numbers add up. For
  other users' processes only the plain resident size is readable.
- The `Total` section adds up the Procs, Memory and Swap columns. `Top N` is
  the rows in the table; `All M` is everything that matched, including what
  `--top` left out (only `All M` appears when nothing was left out).
- `Outside processes` is what the machine has in use that belongs to no
  process: the kernel's own memory, shared memory, and cache it does not
  count as free. `All` plus `Outside processes` gives `In use`, the same
  figures as the bars at the top. Both rows are left out when you filter with
  a pattern, because the sums then cover only what matched.
- The report uses the same frame as `jzr host status`: plain `+ - |`
  characters where the output cannot show boxes, and cut to the window's
  width on a terminal. A protected row ends with the reason, such as
  `(system)` or `(this terminal)`.
- `--kill` ticks nothing for you. Processes are first asked to exit; after 5
  seconds it asks whether to force-kill what is left (`--force` skips that
  question). Only the processes listed are signalled, not their children.
- It will not end this terminal (the `jzr` process and its parents), other
  users' processes, or the session itself: `systemd`, the desktop shell, the
  display server, the sound and message services, `sshd`. A group that
  contains one of these can still be ticked; the protected ones are kept.
- `--kill` needs a terminal. For scripted killing use `pkill`.
- Exit code `1` means a process could not be ended or is still running.

On Windows:

- "Asked to exit" means each chosen program's windows are told to close, as
  clicking their X would, so it can save or ask you first. A program without
  a window cannot be asked, so it goes straight to the force-kill question.
  Force-killing is `TerminateProcess`.
- Windows keeps no per-process share of the page file, so the table has no
  Swap column and `--json` gives `null` for it. The Swap bar at the top is
  the page file.
- Protected as `(system)`: services and anything owned by `NT AUTHORITY`, and
  the shell and log-on processes (`explorer.exe`, `dwm.exe`, `csrss.exe`,
  `winlogon.exe`, `sihost.exe`, `svchost.exe` and the like).

Tried on Linux and Windows 11; ending processes only on throwaway child
processes.
