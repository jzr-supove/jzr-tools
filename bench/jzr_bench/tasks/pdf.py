"""Combine two PDFs, leaving out the last page of the first."""

from __future__ import annotations

from pathlib import Path

from ._pdfs import _labels, _pdf_with_pages
from .base import CheckResult, Task, sources_unchanged

PROMPT = "Combine a.pdf and b.pdf into combined.pdf, a.pdf first, without the last page of a.pdf."
LABELS = ("A1", "A2", "A3", "B1", "B2")
EXPECTED = ["A1", "A2", "B1", "B2"]


def _prepare(fixture_dir: Path) -> None:
    (fixture_dir / "a.pdf").write_bytes(_pdf_with_pages(["A1", "A2", "A3"]))
    (fixture_dir / "b.pdf").write_bytes(_pdf_with_pages(["B1", "B2"]))


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    problems = sources_unchanged(work_dir, fixture_dir, ["a.pdf", "b.pdf"])
    combined = work_dir / "combined.pdf"
    if not combined.is_file():
        problems.append("combined.pdf does not exist")
        return CheckResult(False, problems)
    try:
        labels = _labels(combined, LABELS)
    except Exception as error:  # pypdf raises many kinds for broken files
        problems.append(f"combined.pdf is not a valid PDF that can be read: {type(error).__name__}: {error}")
        return CheckResult(False, problems)

    if labels != EXPECTED:
        shown = ", ".join(labels) or "no pages"
        detail = f"combined.pdf pages are {shown}; expected A1, A2, B1, B2 in that order"
        if "A3" in labels:
            detail += " (A3, the last page of a.pdf, must be left out)"
        if len(labels) != len(EXPECTED):
            detail += f" ({len(labels)} pages, not {len(EXPECTED)})"
        if "?" in labels:
            detail += " (? marks a page without its label text)"
        problems.append(detail)
    if problems:
        return CheckResult(False, problems)
    return CheckResult(True, ["combined.pdf has A1, A2, B1, B2"])


TASK = Task(name="pdf", prompt=PROMPT, prepare_fixtures=_prepare, check=_check)
