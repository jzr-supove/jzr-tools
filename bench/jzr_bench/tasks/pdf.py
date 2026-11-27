"""Combine two PDFs, leaving out the last page of the first."""

from __future__ import annotations

from pathlib import Path

from .base import CheckResult, Task, sources_unchanged

PROMPT = "Combine a.pdf and b.pdf into combined.pdf, a.pdf first, without the last page of a.pdf."
LABELS = ("A1", "A2", "A3", "B1", "B2")
EXPECTED = ["A1", "A2", "B1", "B2"]


def _pdf_with_pages(labels: list[str]) -> bytes:
    """A small valid PDF, one Letter page per label with the label as Helvetica text."""
    objects: list[bytes] = []
    kids = []
    font_id = 3 + 2 * len(labels)
    for index, label in enumerate(labels):
        page_id, content_id = 3 + 2 * index, 4 + 2 * index
        kids.append(f"{page_id} 0 R")
        stream = f"BT /F1 48 Tf 72 680 Td (Page {label}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    body = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(labels)} >>".encode(),
        *objects,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(body, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(body) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(body) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _prepare(fixture_dir: Path) -> None:
    (fixture_dir / "a.pdf").write_bytes(_pdf_with_pages(["A1", "A2", "A3"]))
    (fixture_dir / "b.pdf").write_bytes(_pdf_with_pages(["B1", "B2"]))


def _labels(path: Path) -> list[str]:
    """For each page, its label (or "?" when it has none or several)."""
    import logging

    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # broken files are reported as a problem, not logged
    reader = PdfReader(path, strict=False)
    found = []
    for page in reader.pages:
        text = page.extract_text() or ""
        here = [label for label in LABELS if label in text]
        found.append(here[0] if len(here) == 1 else "?")
    return found


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    problems = sources_unchanged(work_dir, fixture_dir, ["a.pdf", "b.pdf"])
    combined = work_dir / "combined.pdf"
    if not combined.is_file():
        problems.append("combined.pdf does not exist")
        return CheckResult(False, problems)
    try:
        labels = _labels(combined)
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
