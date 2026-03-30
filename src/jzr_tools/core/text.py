"""Plain Python text helpers."""

import re
import unicodedata


def slugify(text: str, *, separator: str = "-", lowercase: bool = True) -> str:
    """Convert text into a normalized ASCII slug."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    collapsed = re.sub(r"[^A-Za-z0-9]+", separator, ascii_text).strip(separator)

    if lowercase:
        return collapsed.lower()
    return collapsed
