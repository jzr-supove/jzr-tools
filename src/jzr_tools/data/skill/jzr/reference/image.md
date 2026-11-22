# jzr image

## jzr image compress

```text
Usage: jzr image compress [OPTIONS] [SOURCES]...

  Make image files smaller, optionally to a target size.

  Each file is re-encoded in its own format (or with --to another) and written as
  <name>_compressed.<ext> beside its source unless -o or --outdir says otherwise. A copy
  that is not smaller than its source is not kept, unless --to changes the format.

Options:
  -d, --dir DIRECTORY             Directory to scan when no SOURCES are listed.
                                  [default: .]
  -p, --pattern TEXT              Regex filter applied to candidate filenames.
  -r, --recursive                 Recurse into subdirectories while scanning.
  -o, --output FILE               Write the one listed SOURCE to this file.
  --outdir DIRECTORY              Write outputs here instead of beside each source.
  -t, --to [jpg|png|webp|avif|heic]
                                  Re-encode into this format.
  -q, --quality INTEGER RANGE     Output quality, 1-100 (higher is better).  [1<=x<=100]
  -s, --size SIZE                 Largest file size, such as 500KB or 2MB; quality is
                                  lowered (and the image shrunk if needed) to fit.
  --max-side N                    Longest side in pixels; smaller images are not
                                  enlarged.  [x>=1]
  --strip                         Leave out EXIF, XMP and comments; the colour profile
                                  is kept.
  --background COLOR              Colour for transparent areas in jpg output (default
                                  white).
  --overwrite                     Replace existing output files.
  --dry-run                       Show what would be converted; write nothing.
```

Examples:

- `jzr image compress photo.jpg` - writes photo_compressed.jpg
- `jzr image compress photo.jpg -s 300KB` - stays at or under 300 KB
- `jzr image compress -d photos -r --to webp --max-side 1920 --outdir small` - compresses a folder tree into WebP, at most 1920 pixels a side

## jzr image convert

```text
Usage: jzr image convert [OPTIONS] [SOURCES]...

  Convert images to another format.

  List SOURCES to convert those files, or leave them out to convert every image in --dir
  that is not already in the target format.

Options:
  -t, --to TEXT                   Target format, e.g. jpg. Taken from --output when left
                                  out.
  -f, --from TEXT                 Only convert this source format; lets SOURCES omit the
                                  extension.
  -d, --dir DIRECTORY             Directory to scan when no SOURCES are listed.
                                  [default: .]
  -p, --pattern TEXT              Regex filter applied to candidate filenames.
  -r, --recursive                 Recurse into subdirectories while scanning.
  -o, --output FILE               Write the one listed SOURCE to this file.
  --outdir DIRECTORY              Write outputs here instead of beside each source.
  -q, --quality INTEGER RANGE     Output quality, 1-100 (higher is better).  [1<=x<=100]
  -b, --backend [auto|pillow|magick]
                                  Conversion engine: built-in Pillow, or ImageMagick
                                  from PATH.  [default: auto]
  --background COLOR              Colour for transparent areas in jpg, bmp and ppm
                                  output (default white).
  --overwrite                     Replace existing output files.
  --dry-run                       Show what would be converted; write nothing.
```

Examples:

- `jzr image convert photo.heic --to jpg` - writes photo.jpg
- `jzr image convert logo.png -o logo.webp -q 90` - takes the format from the output name
- `jzr image convert -d scans --from tiff --to png --dry-run` - shows what a folder would become

## jzr image formats

```text
Usage: jzr image formats [OPTIONS]

  Show supported extensions and the conversion engines available.
```

Examples:

- `jzr image formats` - lists the formats that can be read and written

## jzr image info

```text
Usage: jzr image info [OPTIONS] FILES...

  Show the format, size, colour profile and metadata of images.

  GPS positions are shown so you can check a photo before sharing it; --no-gps leaves
  them out. --json prints one array with the same facts.

Options:
  --json      Print one JSON array.
  --no-gps    Leave the GPS position out.
```

Examples:

- `jzr image info photo.jpg` - shows size, colour profile, camera and GPS
- `jzr image info a.png b.jpg --json` - prints one JSON array

## jzr image resize

```text
Usage: jzr image resize [OPTIONS] [SOURCES]...

  Make images smaller (or, with --upscale, larger).

  Give one size: --width, --height, both (the image fits inside that box), --percent or
  --max-side. Proportions are always kept. Each file is written as <name>_<W>x<H>.<ext>
  beside its source unless -o or --outdir says otherwise.

Options:
  -d, --dir DIRECTORY          Directory to scan when no SOURCES are listed.  [default:
                               .]
  -p, --pattern TEXT           Regex filter applied to candidate filenames.
  -r, --recursive              Recurse into subdirectories while scanning.
  -o, --output FILE            Write the one listed SOURCE to this file.
  --outdir DIRECTORY           Write outputs here instead of beside each source.
  --width N                    Width in pixels; the height follows.  [x>=1]
  --height N                   Height in pixels; the width follows.  [x>=1]
  --percent P                  Scale both sides by this percentage.  [0<x<=1000]
  --max-side N                 Longest side in pixels.  [x>=1]
  --upscale                    Allow making images larger.
  -t, --to TEXT                Write in this format instead of the source's.
  -q, --quality INTEGER RANGE  Output quality, 1-100 (higher is better).  [1<=x<=100]
  --strip                      Leave out EXIF, XMP and comments; the colour profile is
                               kept.
  --background COLOR           Colour for transparent areas in jpg, bmp and ppm output
                               (default white).
  --overwrite                  Replace existing output files.
  --dry-run                    Show what would be converted; write nothing.
```

Examples:

- `jzr image resize photo.jpg --width 1200` - keeps the proportions
- `jzr image resize photo.jpg --width 800 --height 600` - fits inside an 800x600 box
- `jzr image resize -d photos --max-side 1600 --outdir web` - resizes every image in a folder
