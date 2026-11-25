# Images

[← jzr-tools](../README.md) · [Install](install.md) · [Images](images.md) · [Video and audio](video-audio.md) · [PDF](pdf.md) · [Text and data](text-and-data.md) · [Files](files.md) · [Ports and host](system.md) · [Shortcuts](shortcuts.md) · [Completion](completion.md) · [AI agents](ai-agents.md)

## Converting

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
`pillow`, `magick`), `--background COLOR`, `--overwrite`, `--dry-run`.

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
  (`jpg`, `bmp`, `ppm`). `--background` picks another colour: `#rgb`,
  `#rrggbb`, `white`, `black` or `grey`. For a target that keeps
  transparency it is ignored, with a note.
- TIFF output is compressed with lossless LZW. A photo whose EXIF holds a
  date taken, a GPS position or camera settings is written uncompressed
  instead, because the LZW writer would drop those.
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

## Resizing

`jzr image resize` writes resized copies and keeps each image's proportions.
Give one size:

```bash
jzr image resize photo.jpg --width 1600        # height follows
jzr image resize photo.jpg --height 900        # width follows
jzr image resize *.jpg --width 1600 --height 1600   # fit inside the box
jzr image resize --max-side 2048 -d ~/Photos -r # longest side, a whole folder
jzr image resize icon.png --percent 50
jzr image resize logo.png --percent 300 --upscale --to webp
```

Each copy is named after its new size, `photo_1600x1200.jpg`, beside its
source, unless `-o` names the file or `--outdir` names a folder. A folder scan
skips files that already end in `_<W>x<H>`, so running it twice does not
resize the results again.

Behavior worth knowing:

- Sizes are of the upright picture: a phone photo stored sideways is resized
  as it is shown, and written upright.
- Images are never enlarged unless `--upscale` is given. A file that is
  already small enough is skipped with the size it has. An enlarged result
  is marked `enlarged; may look soft`.
- The format stays the same unless `--to` changes it. `-q` and
  `--background` work as in `image convert`.
- EXIF and the colour profile are kept. `--strip` leaves out EXIF, XMP and
  comments and keeps the colour profile.
- Animated GIFs and WebPs are not resized; they fail alone with a message.
- Resampling is Lanczos. Transparent edges do not darken.
- `--dry-run` reads each image's size, so it shows the real output names,
  but it writes nothing.

## Compressing

`jzr image compress` re-encodes images to make the files smaller:

```bash
jzr image compress photo.jpg                 # photo_compressed.jpg
jzr image compress -d ~/Photos -r            # a whole folder
jzr image compress shots/*.png --to webp     # change the format too
jzr image compress photo.jpg -s 500KB        # at most 500 kB
jzr image compress photo.jpg --max-side 2048 --strip
```

Each copy is written as `<name>_compressed.<ext>` beside its source, unless
`-o` names the file or `--outdir` names a folder. A folder scan skips files
that already end in `_compressed`.

| Format | Default |
|---|---|
| JPEG | Quality 82, with optimised Huffman tables |
| WebP | Quality 80 |
| AVIF | Quality 60 |
| HEIC | Quality 50 |
| PNG | Lossless, strongest zlib setting; the pixels are unchanged |
| TIFF | Lossless LZW |

Behavior worth knowing:

- A copy that is not smaller than its source is not kept; the file is
  skipped with both sizes. With `--to` changing the format, the copy is kept
  and marked when it is larger.
- `-q` sets the quality of JPEG, WebP, AVIF and HEIC output.
- `-s/--size` searches for the highest quality, from 92 down to 40, that
  fits. If quality 40 is still too big, the image is scaled down, at most
  three times, and the result line says so:
  `(2.4 MB -> 486.0 kB, -80%, quality 71, resized to 2400x1800 to fit)`.
  PNG and TIFF can only be scaled down. A file already under the size is
  skipped. `-q` and `--size` cannot be combined.
- `--max-side N` scales the image down so that its longest side is at most N.
  It never enlarges.
- `--to` takes jpg, png, webp, avif or heic. Files in other formats, such as
  BMP or GIF, are compressed only with `--to`, and a folder scan picks them
  up only then.
- Pixels are made upright first. EXIF and the colour profile are kept;
  `--strip` leaves out EXIF, XMP and comments and keeps the colour profile.
- Animated GIFs and WebPs are not compressed; they fail alone with a message.
- `--dry-run` does not open the files. It shows the output names, but not
  which files would be skipped as already small.

## Removing metadata

`jzr image strip` removes metadata from JPEG, PNG and WebP files without
re-encoding them, so the pixels and the quality stay exactly the same:

```bash
jzr image strip photo.jpg                 # photo_clean.jpg
jzr image strip -d ~/Photos -r --dry-run  # what each file would lose
jzr image strip shot.png --in-place       # clean the file itself
```

```text
photo.jpg -> photo_clean.jpg (removed EXIF, GPS, XMP; 48.2 kB smaller)
```

- Removed: EXIF (camera, dates, GPS, thumbnails), XMP, IPTC, comments, PNG
  text chunks and timestamps, unknown blocks, and data after the end of the
  image, such as the video of a phone's motion photo.
- Kept: the image data, the rendering headers (gamma, sRGB, JFIF), the
  colour profile (`--no-color-profile` removes it too) and the orientation,
  rewritten as a block that holds nothing else.
- Each copy is written as `<name>_clean.<ext>`, unless `-o`, `--outdir` or
  `--in-place` says otherwise. `--in-place` replaces each source through a
  temporary file and cannot be combined with `-o` or `--outdir`.
- A file with nothing to remove is skipped. HEIC, AVIF, TIFF and other
  formats fail alone; `jzr image compress --strip` handles them by
  re-encoding.
- Damaged files, animated PNGs and WebPs, and files whose metadata cannot be
  read safely (so the orientation might be lost) fail and nothing is written.
- `--dry-run` reads each file and says what would be removed.

## Image info

`jzr image info` shows what an image file holds, without decoding its pixels:

```bash
jzr image info IMG_1042.jpg
jzr image info *.png --json       # one JSON array with the same facts
jzr image info photo.heic --no-gps
```

```
IMG_1042.jpg
  format     JPEG
  size       4032x3024 (upright 3024x4032, orientation 6)
  mode       RGB, 8 bits per channel
  file size  3.1 MB
  frames     1
  color      ICC profile "Display P3"
  dpi        72
  camera     Apple iPhone 15
  taken      2026:10:02 14:31:05
  software   17.6
  gps        41.311081, 69.240562
  metadata   EXIF, XMP, ICC
```

Rows the file has nothing for are left out. An animated GIF or WebP shows its
frame count, whether it loops and how long it plays. The GPS position is
shown so you can check a photo before sharing it; `--no-gps` leaves it out.
A file that is not a readable image is reported on stderr, the others are
still shown, and the exit code is `1`.
