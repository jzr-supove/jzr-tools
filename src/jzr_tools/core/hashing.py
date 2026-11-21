"""File and text checksums, and checking them against `sha256sum`-style lists."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import BinaryIO

ALGORITHMS = ("md5", "sha1", "sha256", "sha384", "sha512", "sha3-256", "sha3-512", "blake2b")
CHUNK_SIZE = 1 << 20

_BY_LENGTH = {32: "md5", 40: "sha1", 64: "sha256", 96: "sha384", 128: "sha512"}
_HEX = re.compile(r"[0-9a-f]+")
_SUM_LINE = re.compile(r"([0-9A-Fa-f]+) ([ *])(.+)")


class HashError(ValueError):
    """Bad algorithm, digest or checksum list; the message is meant for the user."""


def new_hasher(algorithm: str):
    if algorithm not in ALGORITHMS:
        raise HashError(f"Unknown algorithm {algorithm!r}; use one of: {', '.join(ALGORITHMS)}.")
    return hashlib.new(algorithm.replace("-", "_"))


def hash_bytes(data: bytes, algorithm: str = "sha256") -> str:
    hasher = new_hasher(algorithm)
    hasher.update(data)
    return hasher.hexdigest()


def hash_stream(stream: BinaryIO, algorithm: str = "sha256") -> str:
    hasher = new_hasher(algorithm)
    while chunk := stream.read(CHUNK_SIZE):
        hasher.update(chunk)
    return hasher.hexdigest()


def hash_file(path: Path, algorithm: str = "sha256") -> str:
    new_hasher(algorithm)  # an unknown name fails before the file is opened
    with open(path, "rb") as stream:
        return hash_stream(stream, algorithm)


def normalize_hex(value: str) -> str:
    digest = value.strip().lower()
    if not _HEX.fullmatch(digest):
        raise HashError("not a hex digest")
    return digest


def format_sum_line(digest: str, name: str) -> str:
    return f"{digest}  {name}"


def parse_sum_line(line: str) -> tuple[str, str] | None:
    """Read ``HEX  NAME`` or ``HEX *NAME``; None for blank and ``#`` lines."""
    text = line.rstrip("\r\n")
    if not text.strip() or text.startswith("#"):
        return None
    match = _SUM_LINE.fullmatch(text)
    if match is None:
        raise HashError("not a checksum line")
    return match.group(1).lower(), match.group(3)


def algorithm_for_length(hex_length: int) -> str | None:
    return _BY_LENGTH.get(hex_length)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str = ""


def check_sums(sum_file: Path, *, algorithm: str | None = None) -> list[CheckResult]:
    """Check every file listed in ``sum_file``; names are relative to its folder."""
    if algorithm is not None:
        new_hasher(algorithm)
    text = sum_file.read_text(encoding="utf-8-sig")

    entries: list[tuple[str, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        try:
            parsed = parse_sum_line(line)
        except HashError:
            raise HashError(f"{sum_file.name}:{number}: not a checksum line") from None
        if parsed is None:
            continue
        digest, name = parsed
        chosen = algorithm or algorithm_for_length(len(digest))
        if chosen is None:
            raise HashError(
                f"{sum_file.name}:{number}: cannot tell the algorithm of a "
                f"{len(digest)}-character digest; name it with -a"
            )
        entries.append((digest, name, chosen))

    results: list[CheckResult] = []
    for digest, name, chosen in entries:
        path = sum_file.parent / name
        if not path.exists():
            results.append(CheckResult(name, "missing"))
            continue
        if path.is_dir():
            results.append(CheckResult(name, "unreadable", "is a folder"))
            continue
        try:
            actual = hash_file(path, chosen)
        except OSError as error:
            results.append(CheckResult(name, "unreadable", error.strerror or str(error)))
            continue
        results.append(CheckResult(name, "OK" if actual == digest else "FAILED"))
    return results
