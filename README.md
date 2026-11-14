# jzr-tools

`jzr-tools` is a personal cross-platform CLI toolkit packaged as a Python module
with an executable entrypoint.

## Install

For global usage with `pipx`:

```bash
pipx install .
jzr --help
```

For development with `uv`:

```bash
uv sync
uv run jzr --help
uv run python -m unittest discover -s tests
```

You can also run the package directly:

```bash
python -m jzr_tools --help
```

## Layout

- `src/jzr_tools/cli.py`: root Click group and the command registry. Commands
  are imported only when invoked, so each `jzr` call loads just what it needs;
  a new command is added by listing it in `_COMMANDS`
- `src/jzr_tools/commands/`: CLI-facing command modules
- `src/jzr_tools/core/`: plain Python business logic
- `tests/`: CLI and core behavior checks

## Commands

Commands follow one shape, `jzr <thing> <action>`, grouped by what they work
on. Run `jzr -h`, or `-h` after any group or command, for the details.

| Command | What it does |
|---------|--------------|
| `jzr image convert` | Convert images between formats, one file or a whole directory |
| `jzr image <from>2<to>` | Shorthand for a format pair, e.g. `jzr image png2jpg` |
| `jzr image formats` | List supported extensions and the conversion engines available |
| `jzr video info` | Show the container, duration, size and streams of video files |
| `jzr video formats` | List video targets, the ffmpeg in use and which encoders it has |
| `jzr audio convert` | Convert audio files to mp3, m4a, opus, ogg, flac or wav |
| `jzr audio <from>2<to>` | Shorthand for a format pair, e.g. `jzr audio wav2mp3` |
| `jzr audio info` | Show the format, streams and tags of audio files |
| `jzr audio formats` | List audio targets, the ffmpeg in use and which encoders it has |
| `jzr text slug` | Turn text into a URL-safe slug |
| `jzr text kebab` | Turn text into lowercase kebab-case, optionally dated and copied |
| `jzr port show` | Show which processes are using a local port |
| `jzr port kill` | Force-kill the processes using a local port |
| `jzr killport` | Shortcut for `jzr port kill` |
| `jzr host status` | Show host, CPU, memory, storage and GPU status |
| `jzr host clean` | Find caches and other disposable data, and remove what you pick |
| `jzr host procs` | Show what uses the most memory; optionally end what you pick |
| `jzr bind add` | Save a long command under a short name, run as `jzr <name>` |
| `jzr bind list` | List your shortcuts |
| `jzr bind remove` | Remove shortcuts |
| `jzr version` | Print the installed toolkit version |

## Image Conversion

`jzr image` converts with Pillow, which is installed with the toolkit, so it
needs nothing else on your machine. HEIC and AVIF are included. Pass
`--backend magick` to use ImageMagick from `PATH` instead.

Name the files, or leave them out to convert a directory:

```bash
jzr image convert photo.png --to jpg -q 90          # one file
jzr image convert photo.png -o small/photo.webp     # one file, named output
jzr image convert a.png b.webp --to avif            # several files
jzr image convert --to jpg                          # every image in this directory
jzr image convert --from heic --to jpg -d ~/Photos -r
jzr image convert --to webp --pattern "^IMG_" -q 85 # only names matching a regex
```

When the source format is fixed, the pair shorthand is shorter. It is the same
command as `image convert --from <from> --to <to>`:

```bash
jzr image png2jpg photo.png -q 90
jzr image png2jpg photo                # the extension may be left off
jzr image heic2jpg -d ~/Photos -r      # every .heic under a directory
```

Options: `-t/--to`, `-f/--from`, `-q/--quality` (1-100), `-d/--dir`,
`-p/--pattern`, `-r/--recursive`, `-o/--output`, `--outdir`, `-b/--backend` (`auto`,
`pillow`, `magick`), `--overwrite`, `--dry-run`.

Behavior worth knowing:

- Without `--from`, a directory scan picks up every supported image that is
  not already in the target format.
- With a known source format, a listed file with a different extension is
  rejected rather than converted.
- `--dir` and `--recursive` only apply when no files are listed; combining
  them with listed files is an error.
- `--pattern` is a regular expression matched against each candidate's path
  relative to the search directory (just the file name unless `--recursive`
  is used), or against the file name of each listed file.
- `-o/--output FILE` names the output of a single listed file; its extension
  sets the target format, so `--to` can be left out. For several files use
  `--outdir`.
- Outputs land beside their source unless `--outdir` is given. When a
  directory is scanned with `--recursive`, its subdirectories are recreated
  under `--outdir`; explicitly listed sources are written flat.
- No output is written twice in one run. Sources that would share a name
  (`logo.png` and `logo.webp` both becoming `logo.jpg`) get `-2`, `-3`, ...
  appended, in sorted source order.
- Existing outputs are skipped unless `--overwrite` is passed.
- An animated source (GIF, WebP) stays animated when the target is `gif`,
  `webp` or `avif`, with its frame timings. Any other target gets the first
  frame.
- Transparent areas become white in formats that cannot hold transparency
  (`jpg`, `bmp`, `ppm`).
- Photos are written upright: a rotation stored in EXIF is applied to the
  pixels. Other EXIF data and the colour profile are kept where the target
  format can store them.
- `-q/--quality` is 1-100, higher is better, and applies to the lossy formats
  (`jpg`, `webp`, `avif`, `heic`). Without it they are written at 90. The
  other formats are lossless; passing `-q` for them prints a note on stderr
  and converts anyway.
- An output is written under a temporary name and renamed when it is complete,
  so a failed conversion never leaves a broken file behind.
- A failed file does not stop the batch; the run exits `1` and reports which
  files failed.
- `--dry-run` lists what would be converted and writes nothing.

With `--backend magick`:

- `--dry-run` prints the exact ImageMagick command for each file.
- For `png` and `tiff`, `-q` tunes compression rather than being ignored.
- An output whose path contains `%` is written under a temporary
  `jzr-tmp-...` name and then renamed, because ImageMagick would read the `%`
  as a numbered-sequence escape; `--dry-run` prints a note saying so.
- Some ImageMagick builds cannot write every format (HEIC on Windows, for
  one). Such a conversion is reported as failed instead of leaving a file in
  another format under the requested name.

`jzr image formats` lists the supported extensions, the Pillow versions in
use, and whether ImageMagick was found on `PATH`.

## Video and Audio

`jzr video` and `jzr audio` use ffmpeg and ffprobe. They are taken from
`PATH`; set `JZR_FFMPEG` and `JZR_FFPROBE` to the programs' paths to use a
particular copy instead. When neither is found the command says so and exits
with code 1. To install ffmpeg:

```bash
winget install Gyan.FFmpeg     # Windows
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Debian, Ubuntu
```

`info` describes files: container, duration, size, overall bitrate, the
video stream (codec, resolution, frame rate, pixel format), each audio
stream, and rotation (in degrees clockwise) and HDR transfer when present.
`audio info` adds title, artist, album, year, track and whether there is
cover art. `--json` prints one array with the same facts as numbers and
every tag. A file that is not media is reported on its own line and the rest
are still shown; the exit code is then 1.

```bash
jzr video info clip.mov
jzr video info *.mp4 --json
jzr audio info song.mp3
```

`jzr video formats` and `jzr audio formats` list the target formats, which
ffmpeg and ffprobe are used, their versions and where they were found, and
which of the encoders jzr uses this ffmpeg build has. Include their output
when reporting a problem.

### Audio conversion

`jzr audio convert` turns audio files into `mp3`, `m4a`, `opus`, `ogg`,
`flac` or `wav`. It picks its sources and outputs the way `jzr image convert`
does:

```bash
jzr audio convert song.wav --to mp3               # one file
jzr audio convert song.wav -o out/song.mp3        # named output; --to not needed
jzr audio convert *.flac --to m4a --outdir m4a
jzr audio convert --to mp3 -d ~/Music -r          # every audio file under a directory
jzr audio wav2mp3 song                            # pair shorthand; extension optional
```

Options: `-t/--to`, `-f/--from`, `-b/--bitrate` (kb/s, e.g. `128k`),
`-d/--dir`, `-p/--pattern`, `-r/--recursive`, `-o/--output`, `--outdir`,
`--overwrite`, `--dry-run`.

- When the target can hold the source's codec, the audio is copied, with no
  quality loss and almost instantly (AAC from an `.mp4` or `.m4a` into
  `.m4a`, MP3 into `.mp3` under `--outdir`). Otherwise it is encoded. The
  result line says which: `song.wav -> song.mp3 (audio encoded to mp3)`.
- Encoding defaults: MP3 VBR about 190 kb/s, AAC 192 kb/s, Opus 128 kb/s,
  Vorbis quality 5, FLAC level 5, WAV PCM. FLAC and WAV keep 24-bit sources
  at 24 bits and write 16 bits otherwise. `--bitrate` sets the rate of the
  lossy targets and forces an encode; for `flac` and `wav` it prints a note
  and is ignored.
- Tags are carried over. Cover art is carried into `mp3`, `m4a` and `flac`;
  the other formats get none, and the result line says it was dropped.
- Scanning a directory picks audio files only (`mp3 m4a aac opus ogg oga
  flac wav aif aiff wma mka`) that are not already in the target format.
  A listed video file is accepted too, and its first audio track is
  converted; `jzr video` is the place for video work.
- A file with no audio track, or one ffmpeg cannot read, fails on its own;
  the rest still convert and the exit code is 1.
- `--dry-run` prints the ffmpeg command for each file and writes nothing.
- Output is written under a temporary `jzr-tmp-...` name and renamed when
  ffmpeg succeeds, so a failure or Ctrl+C leaves no partial file and never
  damages an existing one. On a terminal a progress line is shown on stderr.

## Ports

See what is using a port, then free it:

```bash
jzr port show 3000
jzr port kill 3000
jzr killport 3000      # shortcut for `jzr port kill`
```

`jzr port show` lists each process with its PID, name, protocol and local
address, and changes nothing.

`jzr port kill` force-kills all visible processes bound to the exact local port, for TCP and UDP
over IPv4 and IPv6. Works on Windows, Linux, and macOS using `psutil`, installed
with the toolkit. On Unix this sends SIGKILL; on Windows it uses TerminateProcess.
Remote connections to the port are not targeted, and unrelated child processes
are not killed. There is no confirmation prompt.

Ports must be between 1 and 65535. The command prints each killed PID and returns
success if no process or Windows port exclusion is found. Permission errors or processes that do not exit
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
port is still unavailable. A restart is not guaranteed to clear a reservation,
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

## Bind

Save a long command under a short name, then run it as `jzr <name>`:

```bash
jzr bind add tunnel ngrok http 5173 --url https://example.ngrok-free.dev
jzr tunnel                     # runs the command
jzr tunnel --log stdout        # anything after the name is added to it
jzr bind list
jzr bind remove tunnel
```

The command can also be given as one quoted string; both forms store the same
thing:

```bash
jzr bind add tunnel "ngrok http 5173 --url https://example.ngrok-free.dev"
```

Other commands: `jzr bind add --force NAME ...` replaces an existing shortcut,
`jzr bind run NAME` is the long way to run one, `jzr bind path` prints where
they are stored, and `jzr bind list --json` is for scripts.

Behavior worth knowing:

- The command runs directly, without a shell, so it behaves the same on
  Linux, macOS and Windows. The price: no pipes, `&&`, `$VARIABLES` or `~`.
  One shortcut is one command.
- Everything after the name belongs to the command, including `--options`,
  both when binding and when running. `--force` therefore goes before the
  name, and `jzr tunnel --help` shows ngrok's help, not jzr's.
- Built-in commands always win. `jzr bind add host ...` is refused, and if a
  later version of jzr adds a command with the name of one of your
  shortcuts, `jzr bind list` marks it and `jzr bind run NAME` still reaches
  it.
- Names start with a letter or digit and may contain letters, digits, `.`,
  `_` and `-`.
- The exit code is the command's own. Ctrl+C goes to the command, and jzr
  waits for it to finish. A program that is not installed gives exit `127`.
- Shortcuts live in `bindings.json` in jzr's config folder: `~/.config/jzr`
  (or `$XDG_CONFIG_HOME/jzr`), `%APPDATA%\jzr` on Windows, or wherever
  `JZR_CONFIG_DIR` points. It is plain JSON that you can edit or copy to
  another machine. It is only readable by you, but anything in it, tokens
  included, is stored as plain text.
- A bindings file that is damaged is reported and never overwritten.
- `jzr -h` does not list your shortcuts; `jzr bind list` does.

On Windows:

- What you type reaches the command as typed: `$HOME`, `%PATH%`, `~` and
  `*.txt` are not expanded by jzr (other jzr commands do expand wildcards,
  since cmd and PowerShell leave that to the program).
- One quoted string is split by Windows rules: double quotes group words and
  backslashes in paths are kept; single quotes are ordinary characters.
  `jzr bind list` writes commands back the same way.
- Programs that are `.cmd` or `.bat` files, such as `npm`, `pnpm` and `yarn`,
  have to run through cmd.exe. jzr quotes every argument so `&`, `|`, `<` and
  `>` stay literal, and refuses to run one with `%`, `"`, `!` or a line
  break in it, which cmd.exe would act on whatever the quoting.

Tried on Linux and Windows 11.

## Text

Convert text into lowercase kebab-case:

```bash
jzr text kebab "ApiSix deployment Notes"
```

Prefix the result with today's ISO date:

```bash
jzr text kebab "ApiSix deployment Notes" --date
```

Copy the result to the system clipboard:

```bash
jzr text kebab "ApiSix deployment Notes" -c
```

Omit the text argument to paste or pipe multiline content. Runs of spaces,
newlines, and punctuation collapse into a single dash.

`text slug` and `text kebab` produce ASCII. Accented Latin letters lose their
accents, Russian and Uzbek Cyrillic are transliterated (`Привет мир` becomes
`privet-mir`), and letters such as `ß` and `ø` are spelled out. Other scripts
are dropped; if nothing is left, the command exits `1` instead of printing an
empty result.
