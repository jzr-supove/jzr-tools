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
| `jzr image formats` | List supported extensions and the backends found on `PATH` |
| `jzr text slug` | Turn text into a URL-safe slug |
| `jzr text kebab` | Turn text into lowercase kebab-case, optionally dated and copied |
| `jzr port show` | Show which processes are using a local port |
| `jzr port kill` | Force-kill the processes using a local port |
| `jzr killport` | Shortcut for `jzr port kill` |
| `jzr version` | Print the installed toolkit version |

## Image Conversion

`jzr image` shells out to ImageMagick (`magick`) or `ffmpeg`, whichever is
installed. At least one of them is required; `magick` is preferred when both
are present.

Name the files, or leave them out to convert a directory:

```bash
jzr image convert photo.png --to jpg -q 90          # one file
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
`-p/--pattern`, `-r/--recursive`, `-o/--outdir`, `-b/--backend` (`auto`,
`magick`, `ffmpeg`), `--overwrite`, `--dry-run`.

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
- Outputs land beside their source unless `--outdir` is given. When a
  directory is scanned with `--recursive`, its subdirectories are recreated
  under `--outdir`; explicitly listed sources are written flat.
- No output is written twice in one run. Sources that would share a name
  (`logo.png` and `logo.webp` both becoming `logo.jpg`) get `-2`, `-3`, ...
  appended, in sorted source order.
- Existing outputs are skipped unless `--overwrite` is passed.
- Only the first frame of a multi-frame source (animated GIF, multi-size ICO)
  is used unless the target is itself animated (`gif`, `webp`, `avif`).
- ffmpeg cannot write `heic`/`heif`; those targets need ImageMagick.
- A failed file does not stop the batch; the run exits `1` and reports which
  files failed.
- `--dry-run` prints the exact backend command for each file.
- A `%` in a file or directory name is taken literally, never as a
  numbered-sequence escape such as `%d`. ImageMagick cannot be told to leave
  one alone in an output path, so an output whose path contains `%` is written
  under a temporary `jzr-tmp-...` name in the target directory and then
  renamed; `--dry-run` shows that name and prints a note saying so.
- `-q/--quality` is the JPEG-style knob, 1-100, higher is better. It behaves as
  expected for the lossy formats (`jpg`, `webp`, `avif`, `heic`). For `png` and
  `tiff` it only tunes compression -- those stay lossless -- and for `gif`,
  `bmp`, `tga`, `ppm` and `ico` it does nothing. Either way the run prints a
  note on stderr, so quality is never silently misapplied.
- Quality maps per backend: ImageMagick gets `-quality`, ffmpeg gets `-q:v`
  (inverted 2-31 scale) for JPEG, `-quality` for WebP, and `-crf` for AVIF.

`jzr image formats` lists the supported extensions and which backends were
found on `PATH`.

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
