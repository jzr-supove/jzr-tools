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
python -m unittest discover -s tests
```

You can also run the package directly:

```bash
python -m jzr_tools --help
```

## Layout

- `src/jzr_tools/cli.py`: root Typer app and command registration
- `src/jzr_tools/commands/`: CLI-facing command modules
- `src/jzr_tools/core/`: plain Python business logic
- `tests/`: CLI and core behavior checks

## Included v1 Commands

- `jzr version`: print the installed toolkit version
- `jzr killport 3000`: force-kill processes using local port 3000
- `jzr png2jpg test_name`: convert `test_name.png` into `test_name.jpg`
- `jzr text slug "Some Text"`: convert text into a slug
- `jzr text kebab "ApiSix deployment Notes" --date -c`: convert text into lowercase kebab-case, optionally prefix it with today's ISO date, and optionally copy it to the clipboard
- `jzr fs ls [PATH]`: list directory entries
- `jzr system which COMMAND`: resolve an executable on `PATH`
- `jzr convert ...`: convert images (see below)

## Image Conversion

`jzr convert` shells out to ImageMagick (`magick`) or `ffmpeg`, whichever is
installed. At least one of them is required; `magick` is preferred when both
are present.

Format-pair form, where the subcommand is any supported `<from>2<to>` pair:

```bash
jzr convert png2jpg photo.png -q 90        # one file
jzr convert png2webp a.png b.png -q 80     # several files
jzr convert heic2jpg -d ~/Photos -r        # every .heic under a directory
```

Batch form, selecting sources by regex instead:

```bash
jzr convert --ext jpg --pattern "^IMG_.*"
jzr convert --ext webp --pattern "screenshot" -d ~/Pictures -r -q 85
```

`--pattern` is a regular expression matched against each candidate's path
relative to the search directory, so it is just the filename unless
`--recursive` is used.

Shared options: `-q/--quality` (1-100), `-d/--dir`, `-p/--pattern`,
`-r/--recursive`, `-o/--outdir`, `-b/--backend` (`auto`, `magick`, `ffmpeg`),
`--overwrite`, `--dry-run`.

Behavior worth knowing:

- Outputs land beside their source unless `--outdir` is given.
- Existing outputs are skipped unless `--overwrite` is passed.
- A failed file does not stop the batch; the run exits `1` and reports which
  files failed.
- `--dry-run` prints the exact backend command for each file.
- `-q/--quality` is the JPEG-style knob, 1-100, higher is better. It behaves as
  expected for the lossy formats (`jpg`, `webp`, `avif`, `heic`). For `png` and
  `tiff` it only tunes compression -- those stay lossless -- and for `gif`,
  `bmp`, `tga`, `ppm` and `ico` it does nothing. Either way the run prints a
  note on stderr, so quality is never silently misapplied.
- Quality maps per backend: ImageMagick gets `-quality`, ffmpeg gets `-q:v`
  (inverted 2-31 scale) for JPEG, `-quality` for WebP, and `-crf` for AVIF.

`jzr convert formats` lists the supported extensions and which backends were
found on `PATH`.

## Kill a Port

```bash
jzr killport 3000
```

Force-kills all visible processes bound to the exact local port, for TCP and UDP
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

## Text Kebab Shortcut

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

## ImageMagick Shortcut

If ImageMagick is installed and `magick` is available on `PATH`, you can convert a
PNG without repeating the basename:

```bash
jzr png2jpg test_name
```

This runs the equivalent of:

```bash
magick test_name.png -quality 92 test_name.jpg
```

You can also pass an explicit PNG path or override the quality:

```bash
jzr png2jpg path/to/test_name.png --quality 85
```
