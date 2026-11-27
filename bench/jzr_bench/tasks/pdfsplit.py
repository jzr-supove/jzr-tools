"""Split a nine-page PDF into three parts (held out)."""

from __future__ import annotations

from pathlib import Path

from ._pdfs import _labels, _pdf_with_pages
from .base import CheckResult, Task, sources_unchanged

PROMPT = "Split report.pdf into three PDFs: pages 1 to 3, pages 4 to 6, and the rest. Keep report.pdf."
LABELS = tuple(f"P{n}" for n in range(1, 10))
GROUPS = (["P1", "P2", "P3"], ["P4", "P5", "P6"], ["P7", "P8", "P9"])
SOURCE = "report.pdf"


def _prepare(fixture_dir: Path) -> None:
    (fixture_dir / SOURCE).write_bytes(_pdf_with_pages(list(LABELS)))


def _outputs(work_dir: Path) -> list[Path]:
    """Every PDF under work_dir, at any depth, except the source itself."""
    found = [
        path
        for path in work_dir.rglob("*")
        if path.suffix.lower() == ".pdf" and path.is_file() and path != work_dir / SOURCE
    ]
    return sorted(found, key=lambda p: str(p.relative_to(work_dir)))


def _describe(name: str, labels: list[str]) -> str:
    """What is wrong with a PDF whose pages are not one of the three groups."""
    shown = ", ".join(labels) or "no pages"
    detail = f"{name} has pages {shown}, which is none of the groups P1-P3, P4-P6, P7-P9"
    for group in GROUPS:
        if sorted(labels) == sorted(group) and labels != group:
            detail += f" (the pages of {group[0]}-{group[-1]} in the wrong order)"
    if "?" in labels:
        detail += " (? marks a page without its label text)"
    return detail


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    problems = sources_unchanged(work_dir, fixture_dir, [SOURCE])
    outputs = _outputs(work_dir)
    if len(outputs) != len(GROUPS):
        listed = ", ".join(str(p.relative_to(work_dir)) for p in outputs) or "none"
        problems.append(
            f"found {len(outputs)} PDFs besides report.pdf, expected exactly three ({listed})"
            + (": more than three, an extra PDF" if len(outputs) > len(GROUPS) else "")
        )

    missing = [list(group) for group in GROUPS]
    notes = []
    for path in outputs:
        name = str(path.relative_to(work_dir))
        try:
            labels = _labels(path, LABELS)
        except Exception as error:  # pypdf raises many kinds for broken files
            problems.append(f"{name} is not a valid PDF that can be read: {type(error).__name__}: {error}")
            continue
        if labels in missing:
            missing.remove(labels)
            notes.append(f"{name} has {labels[0]}-{labels[-1]}")
        elif labels in GROUPS:
            problems.append(f"{name} has pages {', '.join(labels)}, a group another PDF already holds (a duplicate)")
        else:
            problems.append(_describe(name, labels))
    for group in missing:
        problems.append(f"no PDF holds the group of pages {', '.join(group)} (missing)")
    if problems:
        return CheckResult(False, problems)
    return CheckResult(True, notes)


TASK = Task(name="pdfsplit", prompt=PROMPT, prepare_fixtures=_prepare, check=_check)
