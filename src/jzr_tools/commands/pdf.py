"""PDF CLI commands.

* ``jzr pdf info FILES...`` shows the pages, sizes, metadata and features.
* ``jzr pdf merge FILES...`` joins PDFs in the order given.
* ``jzr pdf split FILE`` writes one PDF per page, per N pages or per group.
* ``jzr pdf organize FILE`` reorders, rotates or removes pages.
* ``jzr pdf compress [SOURCES]`` makes PDFs smaller, mostly through their images.
* ``jzr pdf images FILE`` renders pages as JPEG or PNG pictures.
* ``jzr pdf from-images IMAGES...`` makes one PDF page per picture.
* ``jzr pdf text FILE`` prints the text of a PDF, or Markdown with ``--markdown``.

pypdf and pypdfium2 are imported inside the commands, so ``jzr pdf -h`` stays
fast.
"""

from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
from typing import TYPE_CHECKING

import click

from jzr_tools.commands._batch import (
    batch_options,
    check_output,
    compile_pattern,
    report,
    summarize,
)

if TYPE_CHECKING:
    from jzr_tools.core.pdfdoc import Checked

_EXISTING_FILE = click.Path(exists=True, dir_okay=False, path_type=Path)


@click.group()
def app() -> None:
    """Merge, split, compress, render and read PDF files."""


def _output_option(help_text: str):  # noqa: ANN202
    return click.option(
        "-o",
        "--output",
        type=click.Path(dir_okay=False, path_type=Path),
        metavar="FILE",
        default=None,
        help=help_text,
    )


_overwrite_option = click.option("--overwrite", is_flag=True, help="Replace existing output files.")
_dry_run_option = click.option(
    "--dry-run", is_flag=True, help="Show what would be written; write nothing."
)


def _check_pdf_name(output: Path | None) -> None:
    if output is not None and output.suffix.lower() != ".pdf":
        raise click.UsageError(f"--output {output} does not end in .pdf.")


def _notes(sources: list[Checked], given: list[Path]) -> None:
    """Print each source's feature notes once, naming the source as it was given."""
    from jzr_tools.core.pdfdoc import FEATURES

    shown: set[str] = set()
    for checked, path in zip(sources, given):
        name = str(path)
        if name in shown:
            continue
        shown.add(name)
        for code in checked.features:
            click.echo(f"note: {name}: {FEATURES[code]}", err=True)


def _check_sources(paths: list[Path]) -> tuple[list[Checked], int]:
    """Open every source, printing a line for each one that fails, and count the failures."""
    from jzr_tools.core.pdfdoc import PdfError, open_checked

    checked: list[Checked] = []
    failed = 0
    done: dict[str, Checked] = {}
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        if key in done:
            checked.append(done[key])
            continue
        try:
            result = open_checked(path)
        except PdfError as error:
            click.echo(f"fail {path}: {error}", err=True)
            failed += 1
            continue
        done[key] = result
        checked.append(result)
    return checked, failed


def _same_file(target: Path, sources: list[Path]) -> bool:
    if not target.exists():
        return False
    return any(source.exists() and os.path.samefile(target, source) for source in sources)


def _count(pages: int) -> str:
    return f"{pages} page" if pages == 1 else f"{pages} pages"


def _pages_text(pages: list[int]) -> str:
    from jzr_tools.core.pdfdoc import range_text

    return f"page {pages[0]}" if len(pages) == 1 else f"pages {range_text(pages)}"


def _fail_build(source: Path, error: Exception, failures: int = 1) -> None:
    click.echo(f"fail {source}: {error}", err=True)
    summarize("pdf", Counter({"failed": failures}))


@app.command()
@click.argument("files", nargs=-1, required=True, type=_EXISTING_FILE)
@click.option("--json", "as_json", is_flag=True, help="Print one JSON array instead of text.")
def info(files: tuple[Path, ...], as_json: bool) -> None:
    """Show the pages, sizes, metadata and features of PDF files.

    Features are the parts a rewrite may lose or change, such as bookmarks,
    forms, signatures and scripts. An encrypted file is reported, not opened.
    --json prints one array with the same facts.
    """
    import json

    from jzr_tools.core import pdf_info
    from jzr_tools.core.pdfdoc import PdfError

    documents: list[dict[str, object]] = []
    failed = False
    printed = False
    for path in files:
        try:
            facts = pdf_info.read_info(path)
        except (PdfError, OSError) as error:
            reason = error.strerror if isinstance(error, OSError) and error.strerror else error
            click.echo(f"fail {path}: {reason}", err=True)
            failed = True
            continue
        if as_json:
            documents.append(pdf_info.info_as_dict(facts, file=str(path)))
            continue
        rows = pdf_info.info_rows(facts)
        width = max(len(label) for label, _ in rows)
        if printed:
            click.echo()
        click.echo(str(path))
        for label, value in rows:
            click.echo(f"  {label.ljust(width)}  {value}")
        printed = True

    if as_json:
        click.echo(json.dumps(documents, indent=2, ensure_ascii=False))
    if failed:
        raise click.exceptions.Exit(1)


@app.command()
@click.argument("files", nargs=-1, type=_EXISTING_FILE)
@_output_option("Write the result to this file.  [default: <first>_merged.pdf]")
@_overwrite_option
@_dry_run_option
def merge(files: tuple[Path, ...], output: Path | None, overwrite: bool, dry_run: bool) -> None:
    """Join PDF files into one, in the order given.

    The sources are never changed. Bookmarks are kept; features that may not
    survive, such as forms or signatures, are named in notes.
    """
    if len(files) < 2:
        raise click.UsageError("give at least two PDFs to merge")
    _check_pdf_name(output)

    from jzr_tools.core import pdf_pages
    from jzr_tools.core.media import format_size
    from jzr_tools.core.pdfdoc import PdfError, output_name, write_pdf

    paths = list(files)
    sources, failures = _check_sources(paths)
    if failures:
        summarize("pdf", Counter({"failed": failures}))
        return
    target = output if output is not None else output_name(paths[0], "merged")
    _notes(sources, paths)
    head = f"{paths[0]} + {len(paths) - 1} more -> {target}"
    total = sum(len(source.pages) for source in sources)

    if _same_file(target, paths):
        click.echo(f"skip {target}: source is the target", err=True)
        summarize("pdf", Counter({"skipped": 1}))
        return
    if target.exists() and not overwrite:
        click.echo(f"skip {target}: target exists (use --overwrite)", err=True)
        summarize("pdf", Counter({"skipped": 1}))
        return
    if dry_run:
        click.echo(f"{head} ({_count(total)})")
        summarize("pdf", Counter({"planned": 1}))
        return
    try:
        planned = pdf_pages.merge(sources)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_pdf(target, planned.data)
    except (PdfError, OSError) as error:
        reason = error.strerror if isinstance(error, OSError) and error.strerror else error
        _fail_build(paths[0], reason)
        return
    click.echo(f"{head} ({_count(len(planned.pages))}, {format_size(len(planned.data))})")
    summarize("pdf", Counter({"merged": 1}))


@app.command()
@click.argument("file", type=_EXISTING_FILE)
@click.option(
    "--every",
    type=click.IntRange(min=1),
    metavar="N",
    default=None,
    help="Put N pages in each file.",
)
@click.option(
    "--pages",
    "groups_text",
    metavar="GROUPS",
    default=None,
    help='Groups of pages, such as "1-3;4,6;7-" (one file per group).',
)
@click.option(
    "--outdir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write the files here instead of beside the source.",
)
@_overwrite_option
@_dry_run_option
def split(
    file: Path,
    every: int | None,
    groups_text: str | None,
    outdir: Path | None,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Split a PDF into several: every page, every N pages, or groups.

    Without --every or --pages each page becomes a file. The source is never
    changed; features that may not survive, such as forms or signatures, are
    named in notes.
    """
    if every is not None and groups_text is not None:
        raise click.UsageError("--every and --pages do not combine.")

    from jzr_tools.core import pdf_pages
    from jzr_tools.core.pdfdoc import PdfError, output_name, parse_pages, range_text, write_pdf

    sources, failures = _check_sources([file])
    if failures:
        summarize("pdf", Counter({"failed": 1}))
        return
    source = sources[0]
    count = len(source.pages)
    if groups_text is not None:
        try:
            groups = parse_pages(groups_text, count, groups=True)
        except ValueError as error:
            raise click.UsageError(f"--pages: {error}") from error
        groups = [sorted(group) for group in groups]
    else:
        size = every or 1
        groups = [list(range(start, min(start + size, count + 1))) for start in range(1, count + 1, size)]

    digits = max(3, len(str(len(groups))))
    if len(groups) == 1:
        targets = [output_name(file, "split", outdir=outdir)]
    else:
        targets = [
            output_name(file, "split", index=index, digits=digits, outdir=outdir)
            for index in range(1, len(groups) + 1)
        ]

    _notes(sources, [file])
    for target in targets:
        if _same_file(target, [file]):
            click.echo(f"fail {file}: {target} is the source", err=True)
            summarize("pdf", Counter({"failed": 1}))
            return
        if target.exists() and not overwrite:
            click.echo(f"fail {file}: {target} exists (use --overwrite)", err=True)
            summarize("pdf", Counter({"failed": 1}))
            return

    if not dry_run:
        try:
            pieces = pdf_pages.split(source, groups)
        except PdfError as error:
            _fail_build(file, error)
            return
        try:
            if outdir is not None:
                outdir.mkdir(parents=True, exist_ok=True)
            for target, piece in zip(targets, pieces):
                write_pdf(target, piece.data)
        except OSError as error:
            _fail_build(file, error.strerror or error)
            return

    for target, group in zip(targets, groups):
        click.echo(f"{file} -> {target} ({_pages_text(group)})")
    written = {page for group in groups for page in group}
    left_out = [page for page in range(1, count + 1) if page not in written]
    if left_out:
        click.echo(f"note: pages not written: {range_text(left_out)}", err=True)
    summarize("pdf", Counter({"planned" if dry_run else "written": len(groups)}))


@app.command()
@click.argument("file", type=_EXISTING_FILE)
@click.option(
    "--order",
    metavar="PAGES",
    default=None,
    help="Pages in their new order, such as 3,1,2 or 12-1; pages not listed are dropped.",
)
@click.option("--delete", metavar="PAGES", default=None, help="Pages to remove, such as 2,5-7.")
@click.option(
    "--rotate",
    metavar="[PAGES:]ANGLE",
    multiple=True,
    help="Turn pages clockwise: 90, 180 or 270, or PAGES:ANGLE such as 2-4:90. Repeatable.",
)
@_output_option("Write the result to this file.  [default: <name>_organized.pdf]")
@_overwrite_option
@_dry_run_option
def organize(
    file: Path,
    order: str | None,
    delete: str | None,
    rotate: tuple[str, ...],
    output: Path | None,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Reorder, rotate or remove the pages of a PDF.

    --order comes first, then --delete (by source page number), then each
    --rotate. The source is never changed; features that may not survive,
    such as forms or signatures, are named in notes.
    """
    if order is None and delete is None and not rotate:
        raise click.UsageError("give --order, --delete or --rotate.")
    _check_pdf_name(output)

    from jzr_tools.core import pdf_pages
    from jzr_tools.core.pdfdoc import PdfError, output_name, write_pdf

    sources, failures = _check_sources([file])
    if failures:
        summarize("pdf", Counter({"failed": 1}))
        return
    source = sources[0]
    count = len(source.pages)
    try:
        pages, turns = pdf_pages.plan_order(count, order=order, delete=delete, rotate=rotate)
    except ValueError as error:
        raise click.UsageError(str(error)) from error

    target = output if output is not None else output_name(file, "organized")
    _notes(sources, [file])
    parts = []
    if pages != sorted(set(pages)):
        parts.append("reordered")
    rotated = sum(1 for page in pages if turns.get(page))
    if rotated:
        parts.append(f"{rotated} rotated")
    removed = count - len(set(pages))
    if removed:
        parts.append(f"{removed} removed")
    details = _count(len(pages)) + (f"; {', '.join(parts)}" if parts else "")

    if _same_file(target, [file]):
        click.echo(f"skip {target}: source is the target", err=True)
        summarize("pdf", Counter({"skipped": 1}))
        return
    if target.exists() and not overwrite:
        click.echo(f"skip {target}: target exists (use --overwrite)", err=True)
        summarize("pdf", Counter({"skipped": 1}))
        return
    if not dry_run:
        try:
            planned = pdf_pages.organize(source, pages, turns)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_pdf(target, planned.data)
        except (PdfError, OSError) as error:
            reason = error.strerror if isinstance(error, OSError) and error.strerror else error
            _fail_build(file, reason)
            return
    click.echo(f"{file} -> {target} ({details})")
    summarize("pdf", Counter({"planned" if dry_run else "organized": 1}))


@app.command()
@batch_options(
    click.option(
        "--level",
        type=click.Choice(["lossless", "recommended", "strong"]),
        default="recommended",
        help="How hard to compress images (default recommended).",
    ),
    click.option("--strip", is_flag=True, help="Also remove the document info and XMP metadata."),
)
def compress(
    sources: tuple[Path, ...],
    directory: Path | None,
    pattern: str | None,
    recursive: bool,
    output: Path | None,
    outdir: Path | None,
    level: str,
    strip: bool,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Make PDF files smaller, mostly by shrinking their images.

    Each file is written as <name>_compressed.pdf beside its source unless -o
    or --outdir says otherwise; a result that is not smaller is not kept.
    Levels: lossless leaves images untouched; recommended keeps 150 DPI at
    the size images are shown, as JPEG quality 75; strong keeps 100 DPI, as
    JPEG quality 60. Text, links, bookmarks and forms are kept.
    """
    regex = compile_pattern(pattern)
    check_output(output, sources=sources, outdir=outdir)
    _check_pdf_name(output)
    if sources and (directory is not None or recursive):
        raise click.UsageError("--dir and --recursive only apply when no SOURCES are listed.")

    from jzr_tools.core import pdf_compress
    from jzr_tools.core.batch import find_sources
    from jzr_tools.core.image_compress import compress_targets

    if sources:
        selected = list(sources)
        if regex is not None:
            selected = [path for path in selected if regex.search(path.name) is not None]
        root = None
    else:
        root = directory if directory is not None else Path(".")
        found = find_sources(root, extensions={"pdf"}, pattern=regex, recursive=recursive)
        # Earlier results are not compressed again.
        selected = [
            path for path in found if not path.stem.endswith(pdf_compress.COMPRESSED_STEM)
        ]

    if not selected:
        click.echo("No matching files.", err=True)
        raise click.exceptions.Exit(1)

    if output is not None:
        targets = [output]
    else:
        targets = compress_targets(selected, extension="pdf", outdir=outdir, root=root)

    counts: Counter[str] = Counter()
    for source, target in zip(selected, targets):
        result, notes = pdf_compress.compress_file(
            source, target, level=level, strip=strip, overwrite=overwrite, dry_run=dry_run
        )
        counts[result.status] += 1
        report(result)
        for note in notes:
            click.echo(f"note: {note}", err=True)

    summarize("pdf", counts)


@app.command()
@click.argument("file", type=_EXISTING_FILE)
@click.option(
    "-t",
    "--to",
    "target",
    type=click.Choice(["jpg", "png"], case_sensitive=False),
    default="jpg",
    help="Picture format (default jpg).",
)
@click.option(
    "--dpi",
    type=click.IntRange(36, 600),
    default=150,
    help="Resolution, 36-600 (default 150).",
)
@click.option(
    "-q",
    "--quality",
    type=click.IntRange(1, 100),
    default=None,
    help="JPEG quality, 1-100 (default 90).",
)
@click.option("--pages", "pages_text", metavar="PAGES", help="Only these pages, such as 1-3,5.")
@click.option(
    "--outdir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write the pictures here instead of beside FILE.",
)
@_overwrite_option
@_dry_run_option
def images(
    file: Path,
    target: str,
    dpi: int,
    quality: int | None,
    pages_text: str | None,
    outdir: Path | None,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Render the pages of a PDF as pictures.

    Each page is written as <name>_page-001.jpg (or .png) beside FILE unless
    --outdir says otherwise, drawn on white with its annotations.
    """
    extension = target.lower()
    if quality is not None and extension != "jpg":
        raise click.UsageError("--quality only applies to jpg.")

    from jzr_tools.core import pdf_render
    from jzr_tools.core.pdfdoc import parse_pages

    sources, failures = _check_sources([file])
    if failures:
        summarize("pdf", Counter({"failed": 1}))
        return
    source = sources[0]
    count = len(source.pages)
    if pages_text is not None:
        try:
            pages = sorted(parse_pages(pages_text, count)[0])
        except ValueError as error:
            raise click.UsageError(f"--pages: {error}") from error
    else:
        pages = list(range(1, count + 1))

    plans = pdf_render.render_plan(
        file, source.pages, pages, extension=extension, dpi=dpi, outdir=outdir
    )
    counts: Counter[str] = Counter()
    document = None
    try:
        for plan in plans:
            if plan.problem is not None:
                click.echo(f"fail {file}: {plan.problem}", err=True)
                counts["failed"] += 1
                continue
            if plan.target.exists() and not overwrite:
                click.echo(f"skip {plan.target}: target exists (use --overwrite)", err=True)
                counts["skipped"] += 1
                continue
            line = f"{file} -> {plan.target} ({plan.width}x{plan.height})"
            if dry_run:
                click.echo(line)
                counts["planned"] += 1
                continue
            try:
                if document is None:
                    document = pdf_render.open_document(file.read_bytes())
                picture = pdf_render.render_page(document, plan, dpi=dpi)
                data = pdf_render.encode(
                    picture, extension, dpi=dpi, quality=quality if quality is not None else 90
                )
                from jzr_tools.core.pillow_engine import write_bytes

                write_bytes(plan.target, data)
            except OSError as error:
                click.echo(f"fail {file}: {error.strerror or error}", err=True)
                counts["failed"] += 1
                continue
            except Exception:  # pdfium raises its own errors for broken pages
                click.echo(f"fail {file}: page {plan.number}: {pdf_render.NOT_RENDERED}", err=True)
                counts["failed"] += 1
                continue
            click.echo(line)
            counts["written"] += 1
    finally:
        if document is not None:
            document.close()
    summarize("pdf", counts)


@app.command("from-images")
@click.argument("pictures", nargs=-1, required=True, type=click.Path(path_type=Path))
@_output_option("Write the PDF to this file (default: <first name>_combined.pdf).")
@click.option(
    "--paper",
    type=click.Choice(["a4", "letter", "original"], case_sensitive=False),
    default="a4",
    help="Page size; original makes each page the picture's size at 96 DPI (default a4).",
)
@click.option(
    "--orientation",
    "page_orientation",
    type=click.Choice(["auto", "portrait", "landscape"], case_sensitive=False),
    default="auto",
    help="Page orientation; auto turns the page for a wide picture (default auto).",
)
@click.option(
    "--margin",
    type=click.FloatRange(0, 30),
    default=10.0,
    metavar="MM",
    help="Margin in millimetres, 0-30 (default 10); original paper has none.",
)
@click.option(
    "--smaller",
    is_flag=True,
    help="Shrink JPEGs to at most 2000 px a side at quality 75 when that is smaller.",
)
@_overwrite_option
@_dry_run_option
def from_images(
    pictures: tuple[Path, ...],
    output: Path | None,
    paper: str,
    page_orientation: str,
    margin: float,
    smaller: bool,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Make a PDF with one page per picture, in the order given.

    Each picture is fitted and centred on the page. JPEGs go in without
    re-encoding (camera and GPS data are left out), PNGs losslessly, other
    formats as PNG or JPEG. The PDF is written as <first name>_combined.pdf
    beside the first picture unless -o says otherwise. The margin is ignored
    with --paper original.
    """
    _check_pdf_name(output)
    paths = list(pictures)
    first = paths[0]
    target = output if output is not None else first.parent / f"{first.stem}_combined.pdf"
    if _same_file(target, paths) or any(
        os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(path))
        for path in paths
    ):
        click.echo(f"skip {target}: source is the target", err=True)
        summarize("pdf", Counter({"skipped": 1}))
        return
    if target.exists() and not overwrite:
        click.echo(f"skip {target}: target exists (use --overwrite)", err=True)
        summarize("pdf", Counter({"skipped": 1}))
        return

    from jzr_tools.core import pdf_build
    from jzr_tools.core.media import format_size
    from jzr_tools.core.pdfdoc import PdfError, verify, write_pdf

    embedded = []
    failures = 0
    for path in paths:
        try:
            picture = pdf_build.read_image(path, smaller=smaller)
            pdf_build.check_page(picture, paper.lower())
        except pdf_build.ImageError as error:
            click.echo(f"fail {path}: {error}", err=True)
            failures += 1
            continue
        except OSError as error:
            click.echo(f"fail {path}: {error.strerror or error}", err=True)
            failures += 1
            continue
        embedded.append(picture)
    if failures:
        summarize("pdf", Counter({"failed": failures}))
        return

    names = str(first) if len(paths) == 1 else f"{first} + {len(paths) - 1} more"
    if dry_run:
        click.echo(f"{names} -> {target} ({_count(len(paths))})")
        summarize("pdf", Counter({"planned": 1}))
        return
    try:
        data, boxes = pdf_build.build(
            embedded, paper=paper.lower(), page_orientation=page_orientation.lower(), margin=margin
        )
        verify(data, boxes)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_pdf(target, data)
    except PdfError as error:
        _fail_build(first, error)
        return
    except OSError as error:
        click.echo(f"fail {target}: {error.strerror or error}", err=True)
        summarize("pdf", Counter({"failed": 1}))
        return
    click.echo(f"{names} -> {target} ({_count(len(paths))}, {format_size(len(data))})")
    summarize("pdf", Counter({"combined": 1}))


MARKDOWN_LIMIT = 5_000_000
STRUCTURE_NOTE = (
    "this PDF has tables, columns or rotated text; check the structure and reading order"
)
FONT_NOTE = "some font information could not be read; check bold text"


@app.command()
@click.argument("file", type=_EXISTING_FILE)
@click.option("--markdown", is_flag=True, help="Convert to Markdown instead of plain text.")
@click.option("--pages", "pages_text", metavar="PAGES", help="Only these pages, such as 1-3,5.")
@click.option("--page-breaks", is_flag=True, help="With --markdown, put --- between pages.")
@_output_option("Write to this file instead of printing.")
@click.option("--overwrite", is_flag=True, help="Replace the output file if it exists.")
def text(
    file: Path,
    markdown: bool,
    pages_text: str | None,
    page_breaks: bool,
    output: Path | None,
    overwrite: bool,
) -> None:
    """Print the text of a PDF, or convert it to Markdown.

    Only text-based PDFs have text to read: there is no OCR for scans. Plain
    text keeps the reading order of the PDF and puts a form feed between
    pages. --markdown guesses headings, lists, tables and links from the
    layout, so check the result.
    """
    if page_breaks and not markdown:
        raise click.UsageError("--page-breaks only applies to --markdown.")
    if output is not None:
        if _same_file(output, [file]) or os.path.normcase(os.path.abspath(output)) == os.path.normcase(
            os.path.abspath(file)
        ):
            raise click.UsageError(f"the output would replace {file}; give another -o")
        if output.exists() and not overwrite:
            raise click.UsageError(f"{output} exists; add --overwrite to replace it")

    from jzr_tools.core import pdf_markdown, pdf_text
    from jzr_tools.core.pdfdoc import PdfError, open_checked, parse_pages

    def fail(message: str) -> None:
        click.echo(f"{file}: {message}", err=True)
        raise SystemExit(1)

    try:
        checked = open_checked(file)
    except PdfError as error:
        fail(str(error))
    count = len(checked.pages)
    if pages_text is not None:
        try:
            pages = sorted(parse_pages(pages_text, count)[0])
        except ValueError as error:
            raise click.UsageError(f"--pages: {error}") from error
    else:
        pages = list(range(1, count + 1))

    try:
        document = pdf_text.open_document(file.read_bytes())
    except OSError as error:
        fail(f"cannot read: {error.strerror or error}")
    except Exception:  # pdfium has its own errors for files pypdf accepted
        fail(pdf_text.NOT_READ)
    notes: list[str] = []
    try:
        if markdown:
            characters = pdf_text.count_characters(document, pages)
            if characters > MARKDOWN_LIMIT:
                fail(
                    f"too much text for --markdown ({characters:,} characters); "
                    "use --pages to convert part of it"
                )
            laid_out, fonts_missing = pdf_text.markdown_pages(document, pages)
            result, empty, complex_layout = pdf_markdown.pages_to_markdown(
                laid_out, headings=True, page_breaks=page_breaks
            )
        else:
            plain = pdf_text.plain_text(document, pages)
            result, empty, complex_layout, fonts_missing = plain.text, plain.empty_pages, False, False
    except pdf_markdown.NoText as error:
        fail(str(error))
    finally:
        document.close()
    if empty:
        notes.append("pages without selectable text: " + ", ".join(str(number) for number in empty))
    if complex_layout:
        notes.append(STRUCTURE_NOTE)
    if fonts_missing:
        notes.append(FONT_NOTE)

    data = result.encode("utf-8")
    if output is None:
        stream = click.get_binary_stream("stdout")
        stream.write(data)
        stream.flush()
    else:
        try:
            _write_new(output, data)
        except OSError as error:
            fail(f"cannot write {output}: {error.strerror or error}")
        click.echo(f"wrote {output} ({_count(len(pages))}, {len(result):,} characters)", err=True)
    for note in notes:
        click.echo(f"note: {note}", err=True)


def _write_new(path: Path, data: bytes) -> None:
    """Write ``data`` through a temporary name beside ``path``, then rename it."""
    import tempfile

    folder = path.parent if str(path.parent) else Path(".")
    handle, temporary = tempfile.mkstemp(dir=folder, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
