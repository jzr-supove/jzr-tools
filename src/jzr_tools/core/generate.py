"""Generators: UUIDs, passwords, passphrases, random numbers and items.

Every random value comes from :mod:`secrets` (the operating system's
source), and every choice is exactly uniform: ``secrets.randbelow`` never has
modulo bias. The standard ``random`` module is never used.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
import math
import secrets
import time
from typing import Callable, Sequence
import uuid


class GenerateError(ValueError):
    """Options or input that cannot work; the message is meant for the user."""


# --- UUIDs -------------------------------------------------------------------

MAX_UUIDS = 100_000
_MAX_MS = (1 << 48) - 1
_COUNTER_BITS = 12
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1
# The counter starts in the lower half, so it has room to count up.
_COUNTER_START_BITS = 11


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def make_uuids(
    count: int,
    *,
    version: int = 4,
    clock_ms: Callable[[], int] | None = None,
    randbits: Callable[[int], int] = secrets.randbits,
) -> list[uuid.UUID]:
    """Return ``count`` new UUIDs of ``version`` 4 or 7.

    Version 7 IDs made by one call are strictly increasing: within one
    millisecond the 12-bit ``rand_a`` field counts up (RFC 9562, method 1).
    """
    if not 1 <= count <= MAX_UUIDS:
        raise GenerateError(f"Choose a count from 1 to {MAX_UUIDS}.")
    if version == 4:
        return [uuid.uuid4() for _ in range(count)]
    if version != 7:
        raise GenerateError(f"UUID version {version} is not supported; use 4 or 7.")

    clock = clock_ms if clock_ms is not None else _now_ms
    made: list[uuid.UUID] = []
    last_ms: int | None = None
    counter = 0
    for _ in range(count):
        ms = clock()
        if last_ms is not None and ms <= last_ms:
            # Same millisecond, or a clock that went back: count up instead.
            ms = last_ms
            counter += 1
            if counter > _COUNTER_MAX:
                ms = last_ms + 1
                counter = randbits(_COUNTER_START_BITS)
        else:
            counter = randbits(_COUNTER_START_BITS)
        if not 0 <= ms <= _MAX_MS:
            raise GenerateError("The clock is outside the range a version 7 UUID can hold.")
        last_ms = ms
        value = (
            (ms << 80)
            | (0x7 << 76)
            | (counter << 64)
            | (0b10 << 62)
            | randbits(62)
        )
        made.append(uuid.UUID(int=value))
    return made


def format_uuid(value: uuid.UUID, *, upper: bool = False, hyphens: bool = True) -> str:
    text = str(value) if hyphens else value.hex
    return text.upper() if upper else text


# --- passwords ---------------------------------------------------------------

UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
LOWER = "abcdefghijklmnopqrstuvwxyz"
DIGITS = "0123456789"
SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?/~"
SIMILAR = "Il1O0o"
MIN_LENGTH, MAX_LENGTH = 1, 1024
_MAX_TRIES = 10_000


@dataclass(frozen=True)
class PasswordOptions:
    length: int = 20
    upper: bool = True
    lower: bool = True
    digits: bool = True
    symbols: bool = True
    no_similar: bool = False


def character_classes(options: PasswordOptions) -> list[str]:
    """The chosen character sets in the order upper, lower, digits, symbols."""
    chosen = [
        characters
        for characters, wanted in (
            (UPPER, options.upper),
            (LOWER, options.lower),
            (DIGITS, options.digits),
            (SYMBOLS, options.symbols),
        )
        if wanted
    ]
    if options.no_similar:
        chosen = ["".join(c for c in characters if c not in SIMILAR) for characters in chosen]
    return chosen


def check_password_options(options: PasswordOptions) -> None:
    classes = character_classes(options)
    if not classes:
        raise GenerateError("Choose at least one type of character.")
    if not MIN_LENGTH <= options.length <= MAX_LENGTH:
        raise GenerateError(f"Choose a length from {MIN_LENGTH} to {MAX_LENGTH}.")
    if options.length < len(classes):
        raise GenerateError("Length must allow at least one character from every selected type.")


def make_password(
    options: PasswordOptions, *, choice: Callable[[Sequence[str]], str] = secrets.choice
) -> str:
    """Draw a password uniformly from all chosen characters.

    A password that misses a chosen type is drawn again as a whole, so no
    position is forced and the result stays uniform among valid passwords.
    """
    check_password_options(options)
    classes = character_classes(options)
    pool = "".join(classes)
    for _ in range(_MAX_TRIES):
        password = "".join(choice(pool) for _ in range(options.length))
        if all(any(c in characters for c in password) for characters in classes):
            return password
    raise GenerateError("Could not make a password with every selected type; try a longer one.")


def password_bits(options: PasswordOptions) -> float:
    pool = sum(len(characters) for characters in character_classes(options))
    return options.length * math.log2(pool) if pool else 0.0


# --- passphrases -------------------------------------------------------------

MIN_WORDS, MAX_WORDS = 3, 20
_MAX_SEPARATOR = 8


@lru_cache(maxsize=1)
def load_words() -> tuple[str, ...]:
    """The EFF Long Wordlist: 7,776 words."""
    text = (
        resources.files("jzr_tools")
        .joinpath("data", "eff_large_wordlist.txt")
        .read_text(encoding="ascii")
    )
    return tuple(line for line in text.splitlines() if line)


def make_passphrase(
    words: int = 6,
    separator: str = "-",
    *,
    choice: Callable[[Sequence[str]], str] = secrets.choice,
) -> str:
    if not MIN_WORDS <= words <= MAX_WORDS:
        raise GenerateError(f"Choose from {MIN_WORDS} to {MAX_WORDS} words.")
    if len(separator) > _MAX_SEPARATOR or any(
        ord(c) < 0x20 or ord(c) == 0x7F for c in separator
    ):
        raise GenerateError(
            f"Use a separator of at most {_MAX_SEPARATOR} printable characters."
        )
    pool = load_words()
    return separator.join(choice(pool) for _ in range(words))


def passphrase_bits(words: int) -> float:
    return words * math.log2(len(load_words()))


# --- random numbers and items ------------------------------------------------


def _check_count(count: int) -> None:
    if count < 1:
        raise GenerateError("Choose a count of 1 or more.")


def _distinct_positions(
    size: int, count: int, randbelow: Callable[[int], int]
) -> list[int]:
    """``count`` distinct positions in ``range(size)``, in draw order.

    A partial Fisher-Yates shuffle that remembers only the swapped places, so
    a range of 10**18 costs no more than a range of 10.
    """
    swapped: dict[int, int] = {}
    positions: list[int] = []
    for index in range(count):
        other = index + randbelow(size - index)
        positions.append(swapped.get(other, other))
        swapped[other] = swapped.get(index, index)
    return positions


def random_ints(
    low: int,
    high: int,
    count: int = 1,
    *,
    unique: bool = False,
    randbelow: Callable[[int], int] = secrets.randbelow,
) -> list[int]:
    """``count`` integers from ``low`` to ``high``, both included."""
    if low > high:
        raise GenerateError("MIN must not be greater than MAX.")
    _check_count(count)
    size = high - low + 1
    if not unique:
        return [low + randbelow(size) for _ in range(count)]
    if count > size:
        raise GenerateError(f"Only {size} different numbers lie between {low} and {high}.")
    return [low + position for position in _distinct_positions(size, count, randbelow)]


def read_items(text: str) -> list[str]:
    """Lines of ``text``, trimmed, without blank lines; duplicates kept."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def pick_items(
    items: Sequence[str],
    count: int = 1,
    *,
    repeat: bool = False,
    randbelow: Callable[[int], int] = secrets.randbelow,
) -> list[str]:
    if not items:
        raise GenerateError("No items to pick from.")
    _check_count(count)
    if repeat:
        return [items[randbelow(len(items))] for _ in range(count)]
    if count > len(items):
        raise GenerateError(
            f"Only {len(items)} items to pick from; add --repeat to allow repeats."
        )
    return [items[position] for position in _distinct_positions(len(items), count, randbelow)]


def shuffle_items(
    items: Sequence[str], *, randbelow: Callable[[int], int] = secrets.randbelow
) -> list[str]:
    if not items:
        raise GenerateError("No items to shuffle.")
    shuffled = list(items)
    for index in range(len(shuffled) - 1, 0, -1):
        other = randbelow(index + 1)
        shuffled[index], shuffled[other] = shuffled[other], shuffled[index]
    return shuffled
