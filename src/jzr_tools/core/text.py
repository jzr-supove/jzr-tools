"""Plain Python text helpers."""

from datetime import date
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


def kebab_case(text: str, *, date_prefix: date | str | None = None) -> str:
    """Convert text into lowercase kebab-case with an optional date prefix."""
    converted = slugify(text, separator="-", lowercase=True)

    if date_prefix is None:
        return converted

    if isinstance(date_prefix, date):
        prefix = date_prefix.isoformat()
    else:
        prefix = date_prefix

    return f"{prefix}_{converted}"
