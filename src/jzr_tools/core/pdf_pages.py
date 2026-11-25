"""Building new PDFs from the pages of checked sources: merge, split and organize.

Each builder copies pages with pypdf into a new document, disables malformed
links, writes the result to memory and reads it back to check that every page
kept its boxes and rotation. Sources are only read.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import re
from typing import TYPE_CHECKING

from jzr_tools.core.pdfdoc import PageBox, bad_uri, parse_pages, quiet, verify

if TYPE_CHECKING:
    from pypdf import PdfWriter

    from jzr_tools.core.pdfdoc import Checked

ROTATE_USAGE = "--rotate takes 90, 180 or 270, optionally after PAGES: (such as 2-4:90)"
_ANGLES = {"90": 90, "180": 180, "270": 270}


@dataclass(frozen=True)
class Planned:
    """A built PDF and the pages it was checked against."""

    data: bytes
    pages: list[PageBox]


def _disable_bad_links(writer: PdfWriter) -> None:
    from pypdf.generic import ArrayObject, DictionaryObject

    for page in writer.pages:
        annots = page.get("/Annots")
        annots = annots.get_object() if annots is not None else None
        if not isinstance(annots, ArrayObject):
            continue
        for item in annots:
            annot = item.get_object()
            if (
                isinstance(annot, DictionaryObject)
                and annot.get("/Subtype") == "/Link"
                and bad_uri(annot.get("/A"))
            ):
                del annot["/A"]


def _finish(writer: PdfWriter, expected: list[PageBox]) -> Planned:
    _disable_bad_links(writer)
    buffer = io.BytesIO()
    with quiet():
        writer.write(buffer)
    data = buffer.getvalue()
    verify(data, expected)
    return Planned(data, expected)


def _new_writer() -> PdfWriter:
    from pypdf import PdfWriter

    return PdfWriter()


def merge(sources: list[Checked]) -> Planned:
    """Every page of every source, in the order given, with their bookmarks."""
    writer = _new_writer()
    expected: list[PageBox] = []
    with quiet():
        for source in sources:
            writer.append(source.reader)
            expected.extend(source.pages)
    return _finish(writer, expected)


def _copy(source: Checked, pages: list[int], rotations: dict[int, int] | None = None) -> Planned:
    writer = _new_writer()
    rotations = rotations or {}
    expected = []
    with quiet():
        writer.append(source.reader, pages=[number - 1 for number in pages])
        for page, number in zip(writer.pages, pages):
            box = source.pages[number - 1]
            turn = rotations.get(number, 0) % 360
            if turn:
                page.rotate(turn)
            expected.append(PageBox(box.media, box.crop, (box.rotation + turn) % 360))
    return _finish(writer, expected)


def split(source: Checked, groups: list[list[int]]) -> list[Planned]:
    """One new PDF per group; pages keep their source order within a group."""
    return [_copy(source, sorted(group)) for group in groups]


def organize(source: Checked, order: list[int], rotations: dict[int, int]) -> Planned:
    """The source pages in ``order``, each turned clockwise by ``rotations[page]``."""
    return _copy(source, order, rotations)


def _pages_of(option: str, text: str, count: int, *, backwards: bool = False) -> list[int]:
    try:
        return parse_pages(text, count, backwards=backwards)[0]
    except ValueError as error:
        raise ValueError(f"{option}: {error}") from error


def plan_order(
    count: int, *, order: str | None, delete: str | None, rotate: tuple[str, ...]
) -> tuple[list[int], dict[int, int]]:
    """The output pages (source numbers) and the clockwise turn of each source page.

    ``order`` comes first (backwards ranges allowed), then ``delete`` removes
    source pages, then each ``[PAGES:]ANGLE`` of ``rotate`` adds a turn.
    Raises ValueError with the text to show.
    """
    pages = list(range(1, count + 1)) if order is None else _pages_of(
        "--order", order, count, backwards=True
    )
    if delete is not None:
        removed = set(_pages_of("--delete", delete, count))
        pages = [page for page in pages if page not in removed]
        if not pages:
            raise ValueError("--delete would remove every page")

    turns: dict[int, int] = {}
    for item in rotate:
        where, colon, angle_text = item.strip().rpartition(":")
        angle = _ANGLES.get(angle_text.strip())
        if angle is None or (colon and not where.strip()):
            raise ValueError(ROTATE_USAGE)
        if colon:
            if not re.search(r"\d", where):
                raise ValueError(ROTATE_USAGE)
            targets = _pages_of("--rotate", where, count, backwards=True)
        else:
            targets = list(dict.fromkeys(pages))
        for page in targets:
            turns[page] = (turns.get(page, 0) + angle) % 360
    return pages, {page: turn for page, turn in turns.items() if turn}
