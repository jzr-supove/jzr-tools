# PDF

[← jzr-tools](../README.md) · [Install](install.md) · [Images](images.md) · [Video and audio](video-audio.md) · [PDF](pdf.md) · [Text and data](text-and-data.md) · [Files](files.md) · [Ports and host](system.md) · [Shortcuts](shortcuts.md) · [Completion](completion.md) · [AI agents](ai-agents.md)


```bash
jzr pdf info report.pdf                    # pages, sizes, metadata, features
jzr pdf merge a.pdf b.pdf c.pdf            # a_merged.pdf, in the order given
jzr pdf split book.pdf                     # book_split-001.pdf, one per page
jzr pdf split book.pdf --every 10          # ten pages per file
jzr pdf split book.pdf --pages "1-3;4,6;7-"   # one file per group
jzr pdf organize scan.pdf --rotate 2-4:90 --delete 1
jzr pdf organize scan.pdf --order 12-1     # reverse the pages
jzr pdf compress report.pdf               # report_compressed.pdf
jzr pdf compress -d scans --level strong  # every PDF in scans/
jzr pdf images slides.pdf --dpi 200       # slides_page-001.jpg, ...
jzr pdf from-images a.jpg b.png -o both.pdf   # one A4 page each
jzr pdf text report.pdf --markdown -o report.md
```

```text
report.pdf
  version    1.3
  pages      12
  page size  A4 portrait (210 x 297 mm): 1-11
             Legal portrait (216 x 356 mm): 12
  rotated    3 (90°)
  producer   pypdf
  text       none (scanned?)
  file size  1.9 kB
```

- Sources are never changed. Outputs are written beside the first source
  (`-o` names the merge or organize result, `--outdir` the split files) and
  an existing file is only replaced with `--overwrite`. `--dry-run` shows what
  would be written.
- Page lists are numbers and ranges such as `1-3,5`. `7-` runs to the last
  page; in `--order`, `12-1` runs backwards. `--pages` takes groups split by
  `;`, one file each, and names the pages that were left out.
- `organize` applies `--order` first, then `--delete` (by the page numbers
  of the source), then each `--rotate`, which turns pages clockwise by 90, 180
  or 270 on top of their own rotation.
- Every source is checked before anything is written: encrypted files,
  damaged ones and pages with a size, scaling or rotation the tools cannot
  keep fail with one line and nothing is written. Each result is read back
  to check that every page kept its size and rotation.
- Parts of a PDF that may not survive a rewrite are named in notes, once per
  source: bookmarks and tags, annotations and links, the opening view, form
  fields, digital signatures (which will not validate in the new file),
  scripts and attachments. A link whose address is malformed is disabled in
  the result; the page itself is kept.
- `compress` keeps text, fonts, links, bookmarks and forms as they are. It
  removes page thumbnails and unused objects, compresses what was stored
  uncompressed and stores identical streams once. Images are re-encoded at
  the size they are shown: `--level recommended` (the default) keeps 150 DPI
  and at most 2000 px a side as JPEG quality 75, `strong` 100 DPI and 1400
  px at quality 60, and `lossless` leaves images alone. Flat graphics stay
  lossless; CMYK, 1-bit, JPEG 2000, JBIG2 and similar images are left as they
  are. A result that is not smaller is not kept (`already compact`), and
  `--strip` also removes the document info and XMP metadata. It takes
  SOURCES or a folder scan like `jzr image compress`.
- `images` draws each page on white, with its annotations, as
  `<name>_page-001.jpg` (or `--to png`) at `--dpi` 36-600 (default 150);
  `--pages` picks pages. A page over 8192 px a side or 16 million pixels at
  that DPI fails alone and asks for a lower `--dpi`.
- `from-images` fits and centres each picture on A4 (`--paper letter`, or
  `original` for the picture's own size at 96 DPI), turning the page for
  wide pictures unless `--orientation` says otherwise, with a `--margin` of
  10 mm. JPEGs go in without re-encoding, minus their camera and GPS data,
  and their EXIF orientation is honoured; PNGs go in losslessly; HEIC,
  WebP, GIF, BMP and TIFF become PNG or JPEG data. `--smaller` shrinks JPEGs
  to at most 2000 px at quality 75 when that is smaller.
- `text` prints the text in the PDF's own reading order, with a form feed
  between pages, or writes it with `-o`. `--markdown` guesses headings,
  lists, tables, form fields and links from the layout, so check the
  result; `--page-breaks` puts `---` between pages. Scanned pages have no
  text to read (there is no OCR) and are named in a note.
- `info --json` prints the same facts as one array. An encrypted file shows
  only that it is encrypted and its size.
