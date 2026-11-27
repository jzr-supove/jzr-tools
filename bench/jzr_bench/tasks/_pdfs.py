"""Labelled test PDFs and reading their labels back, shared by the pdf and pdfsplit tasks."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


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


def _labels(path: Path, known: Sequence[str]) -> list[str]:
    """For each page, which of the known labels it shows (or "?" when it has none or several)."""
    import logging

    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # broken files are reported as a problem, not logged
    reader = PdfReader(path, strict=False)
    found = []
    for page in reader.pages:
        text = page.extract_text() or ""
        here = [label for label in known if label in text]
        found.append(here[0] if len(here) == 1 else "?")
    return found
