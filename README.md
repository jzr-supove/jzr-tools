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
- `jzr text slug "Some Text"`: convert text into a slug
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
