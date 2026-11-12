"""Plain Python text helpers."""

from datetime import date
import re
import unicodedata


# Letters that Unicode decomposition cannot reduce to ASCII and would
# otherwise be dropped. Keys are lowercase; uppercase is derived.
_TRANSLITERATION = {
    # Russian
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # Uzbek Cyrillic
    "ў": "o", "қ": "q", "ғ": "g", "ҳ": "h",
    # Latin letters with no decomposition
    "ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "ł": "l", "đ": "d", "ð": "d",
    "þ": "th", "ı": "i",
}  # fmt: skip


def _transliterate(text: str) -> str:
    """Replace known non-ASCII letters with ASCII spellings, keeping case."""
    converted = []
    # Compose first so letters such as "ё" and "й" are matched whole instead
    # of being split into a base letter plus an accent.
    for character in unicodedata.normalize("NFC", text):
        replacement = _TRANSLITERATION.get(character)
        if replacement is None:
            replacement = _TRANSLITERATION.get(character.lower())
            if replacement is not None:
                replacement = replacement.capitalize()
        converted.append(character if replacement is None else replacement)
    return "".join(converted)


def slugify(text: str, *, separator: str = "-", lowercase: bool = True) -> str:
    """Convert text into a normalized ASCII slug."""
    normalized = unicodedata.normalize("NFKD", _transliterate(text))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    # Joining the alphanumeric runs keeps the separator literal: it is never
    # read as a regex template and never trimmed out of the content.
    collapsed = separator.join(re.findall(r"[A-Za-z0-9]+", ascii_text))

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
