# jzr pdf

## jzr pdf compress

```text
Usage: jzr pdf compress [OPTIONS] [SOURCES]...

  Make PDF files smaller, mostly by shrinking their images.

  Each file is written as <name>_compressed.pdf beside its source unless -o or --outdir
  says otherwise; a result that is not smaller is not kept. Levels: lossless leaves
  images untouched; recommended keeps 150 DPI at the size images are shown, as JPEG
  quality 75; strong keeps 100 DPI, as JPEG quality 60. Text, links, bookmarks and forms
  are kept.

Options:
  -d, --dir DIRECTORY             Directory to scan when no SOURCES are listed.
                                  [default: .]
  -p, --pattern TEXT              Regex filter applied to candidate filenames.
  -r, --recursive                 Recurse into subdirectories while scanning.
  -o, --output FILE               Write the one listed SOURCE to this file.
  --outdir DIRECTORY              Write outputs here instead of beside each source.
  --level [lossless|recommended|strong]
                                  How hard to compress images (default recommended).
  --strip                         Also remove the document info and XMP metadata.
  --overwrite                     Replace existing output files.
  --dry-run                       Show what would be converted; write nothing.
```

Examples:

- `jzr pdf compress report.pdf` - writes report_compressed.pdf, images at 150 DPI
- `jzr pdf compress -d scans --level strong --dry-run` - lists what a scan of scans/ would write
- `jzr pdf compress big.pdf -o small.pdf --strip` - also drops the document info and XMP

## jzr pdf from-images

```text
Usage: jzr pdf from-images [OPTIONS] PICTURES...

  Make a PDF with one page per picture, in the order given.

  Each picture is fitted and centred on the page. JPEGs go in without re-encoding
  (camera and GPS data are left out), PNGs losslessly, other formats as PNG or JPEG. The
  PDF is written as <first name>_combined.pdf beside the first picture unless -o says
  otherwise. The margin is ignored with --paper original.

Options:
  -o, --output FILE               Write the PDF to this file (default: <first
                                  name>_combined.pdf).
  --paper [a4|letter|original]    Page size; original makes each page the picture's size
                                  at 96 DPI (default a4).
  --orientation [auto|portrait|landscape]
                                  Page orientation; auto turns the page for a wide
                                  picture (default auto).
  --margin MM                     Margin in millimetres, 0-30 (default 10); original
                                  paper has none.  [0<=x<=30]
  --smaller                       Shrink JPEGs to at most 2000 px a side at quality 75
                                  when that is smaller.
  --overwrite                     Replace existing output files.
  --dry-run                       Show what would be written; write nothing.
```

Examples:

- `jzr pdf from-images scan1.jpg scan2.jpg -o scans.pdf` - one A4 page per picture
- `jzr pdf from-images *.png --paper original` - each page the picture's own size
- `jzr pdf from-images photo.heic --orientation landscape --margin 0` - fills a landscape page

## jzr pdf images

```text
Usage: jzr pdf images [OPTIONS] FILE

  Render the pages of a PDF as pictures.

  Each page is written as <name>_page-001.jpg (or .png) beside FILE unless --outdir says
  otherwise, drawn on white with its annotations.

Options:
  -t, --to [jpg|png]           Picture format (default jpg).
  --dpi INTEGER RANGE          Resolution, 36-600 (default 150).  [36<=x<=600]
  -q, --quality INTEGER RANGE  JPEG quality, 1-100 (default 90).  [1<=x<=100]
  --pages PAGES                Only these pages, such as 1-3,5 or 2-last.
  --outdir DIRECTORY           Write the pictures here instead of beside FILE.
  --overwrite                  Replace existing output files.
  --dry-run                    Show what would be written; write nothing.
```

Examples:

- `jzr pdf images slides.pdf` - writes slides_page-001.jpg and on, at 150 DPI
- `jzr pdf images report.pdf --to png --dpi 300 --pages 1` - the first page as a sharp PNG

## jzr pdf info

```text
Usage: jzr pdf info [OPTIONS] FILES...

  Show the pages, sizes, metadata and features of PDF files.

  Features are the parts a rewrite may lose or change, such as bookmarks, forms,
  signatures and scripts. An encrypted file is reported, not opened. --json prints one
  array with the same facts.

Options:
  --json      Print one JSON array instead of text.
```

Examples:

- `jzr pdf info report.pdf` - pages, paper size, metadata and features
- `jzr pdf info a.pdf b.pdf --json` - the same facts as one JSON array

## jzr pdf merge

```text
Usage: jzr pdf merge [OPTIONS] [FILES]...

  Join PDF files into one, in the order given.

  The sources are never changed. Bookmarks are kept; features that may not survive, such
  as forms or signatures, are named in notes.

Options:
  -o, --output FILE  Write the result to this file.  [default: <first>_merged.pdf]
  --overwrite        Replace existing output files.
  --dry-run          Show what would be written; write nothing.
```

Examples:

- `jzr pdf merge a.pdf b.pdf c.pdf` - writes a_merged.pdf, in the order given
- `jzr pdf merge *.pdf -o all.pdf --dry-run` - shows the result without writing it

## jzr pdf organize

```text
Usage: jzr pdf organize [OPTIONS] FILE

  Reorder, rotate or remove the pages of a PDF.

  --order comes first, then --delete (by source page number), then each --rotate. The
  source is never changed; features that may not survive, such as forms or signatures,
  are named in notes.

Options:
  --order PAGES           Pages in their new order, such as 3,1,2 or 12-1 or last-1;
                          pages not listed are dropped.
  --delete PAGES          Pages to remove, such as 2,5-7 or last.
  --rotate [PAGES:]ANGLE  Turn pages clockwise: 90, 180 or 270, or PAGES:ANGLE such as
                          2-4:90 or last:90. Repeatable.
  -o, --output FILE       Write the result to this file.  [default:
                          <name>_organized.pdf]
  --overwrite             Replace existing output files.
  --dry-run               Show what would be written; write nothing.
```

Examples:

- `jzr pdf organize scan.pdf --rotate 2-4:90` - turns pages 2 to 4 clockwise
- `jzr pdf organize scan.pdf --delete 1,5` - writes scan_organized.pdf without pages 1 and 5
- `jzr pdf organize scan.pdf --order 12-1` - reverses the pages

## jzr pdf split

```text
Usage: jzr pdf split [OPTIONS] FILE

  Split a PDF into several: every page, every N pages, or groups.

  Without --every or --pages each page becomes a file. The source is never changed;
  features that may not survive, such as forms or signatures, are named in notes.

Options:
  --every N           Put N pages in each file.  [x>=1]
  --pages GROUPS      Groups of pages, such as "1-3;4,6;7-" or "1;2-last" (one file per
                      group).
  --outdir DIRECTORY  Write the files here instead of beside the source.
  --overwrite         Replace existing output files.
  --dry-run           Show what would be written; write nothing.
```

Examples:

- `jzr pdf split book.pdf` - one file per page, from book_split-001.pdf on
- `jzr pdf split book.pdf --every 10` - ten pages per file
- `jzr pdf split book.pdf --pages "1-3;4,6;7-"` - one file per group

## jzr pdf text

```text
Usage: jzr pdf text [OPTIONS] FILE

  Print the text of a PDF, or convert it to Markdown.

  Only text-based PDFs have text to read: there is no OCR for scans. Plain text keeps
  the reading order of the PDF and puts a form feed between pages. --markdown guesses
  headings, lists, tables and links from the layout, so check the result.

Options:
  --markdown         Convert to Markdown instead of plain text.
  --pages PAGES      Only these pages, such as 1-3,5 or 2-last.
  --page-breaks      With --markdown, put --- between pages.
  -o, --output FILE  Write to this file instead of printing.
  --overwrite        Replace the output file if it exists.
```

Examples:

- `jzr pdf text report.pdf` - plain text, a form feed between pages
- `jzr pdf text report.pdf --markdown -o report.md` - headings, lists, tables and links
- `jzr pdf text slides.pdf --pages 2-4 --markdown --page-breaks` - --- between pages
