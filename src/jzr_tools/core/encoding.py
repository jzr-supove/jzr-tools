"""Base64 and URL encoding, and reading a URL into its parts.

Error messages never hold the input, which may be a secret.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import re
from urllib.parse import parse_qsl, quote, unquote, unquote_to_bytes, urlsplit

INVALID_BASE64 = "Invalid Base64 input. Check the alphabet and padding."
BAD_ESCAPE = "Invalid percent-encoding: % must be followed by two hex digits."
NOT_UTF8 = "The decoded text is not valid UTF-8."
NOT_ABSOLUTE = "Not an absolute URL: it needs a scheme such as https://."
BAD_PORT = "The port is not a number from 0 to 65535."

_STANDARD = re.compile(r"[A-Za-z0-9+/]*={0,2}")
_URL_SAFE = re.compile(r"[A-Za-z0-9_-]*={0,2}")
_WHITESPACE = re.compile(r"\s+")
_LONE_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
# What encodeURIComponent leaves alone, besides letters, digits and "-_.~",
# which quote() never encodes.
_COMPONENT_SAFE = "!*'()"


class EncodingError(ValueError):
    """The input cannot be encoded or decoded; the text is for the user."""


def b64_encode(data: bytes, *, url_safe: bool = False) -> str:
    """Encode ``data`` as one line of Base64.

    ``url_safe`` uses the ``-_`` alphabet and leaves the padding out.
    """
    if url_safe:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
    return base64.b64encode(data).decode("ascii")


def b64_decode(text: str, *, url_safe: bool = False) -> bytes:
    """Decode Base64, ignoring whitespace and refusing anything doubtful.

    Refused: characters outside the alphabet, ``=`` anywhere but the end,
    lengths no data has, wrong padding, and trailing bits that are not zero.
    Standard Base64 needs its padding; the URL-safe form may leave it out.
    """
    compact = _WHITESPACE.sub("", text)
    pattern = _URL_SAFE if url_safe else _STANDARD
    if pattern.fullmatch(compact) is None:
        raise EncodingError(INVALID_BASE64)
    body = compact.rstrip("=")
    padding = len(compact) - len(body)
    if len(body) % 4 == 1:
        raise EncodingError(INVALID_BASE64)
    needed = -len(body) % 4
    if padding and padding != needed:
        raise EncodingError(INVALID_BASE64)
    if not url_safe and padding != needed:
        raise EncodingError(INVALID_BASE64)
    try:
        if url_safe:
            data = base64.urlsafe_b64decode(body + "=" * needed)
        else:
            data = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as error:
        raise EncodingError(INVALID_BASE64) from error
    # Only one spelling of each value is accepted: unused low bits must be 0.
    if b64_encode(data, url_safe=url_safe).rstrip("=") != body:
        raise EncodingError(INVALID_BASE64)
    return data


def url_encode(text: str, *, form: bool = False) -> str:
    """Percent-encode ``text`` as a URL component, as encodeURIComponent does.

    ``form`` writes spaces as ``+`` (``+`` itself stays ``%2B``).
    """
    encoded = quote(text, safe=_COMPONENT_SAFE, encoding="utf-8", errors="strict")
    return encoded.replace("%20", "+") if form else encoded


def url_decode(text: str, *, form: bool = False) -> str:
    """Decode ``%XX`` escapes as UTF-8; ``form`` reads ``+`` as a space."""
    if form:
        text = text.replace("+", " ")
    if _LONE_PERCENT.search(text):
        raise EncodingError(BAD_ESCAPE)
    try:
        return unquote_to_bytes(text).decode("utf-8")
    except UnicodeDecodeError as error:
        raise EncodingError(NOT_UTF8) from error


@dataclass(frozen=True)
class ParsedUrl:
    """The parts of an absolute URL; the password itself is never kept."""

    scheme: str
    host: str | None
    port: int | None
    user: str | None
    has_password: bool
    path: str
    fragment: str | None
    query: tuple[tuple[str, str], ...]


def parse_url(text: str) -> ParsedUrl:
    """Split an absolute URL into its parts, the query into decoded rows."""
    text = text.strip()
    if _SCHEME.match(text) is None:
        raise EncodingError(NOT_ABSOLUTE)
    parts = urlsplit(text)
    try:
        port = parts.port
    except ValueError as error:
        raise EncodingError(BAD_PORT) from error
    try:
        query = tuple(
            parse_qsl(parts.query, keep_blank_values=True, encoding="utf-8", errors="strict")
        )
    except UnicodeDecodeError as error:
        raise EncodingError(NOT_UTF8) from error
    username = parts.username
    return ParsedUrl(
        scheme=parts.scheme.lower(),
        host=parts.hostname or None,
        port=port,
        user=unquote(username) if username is not None else None,
        has_password=parts.password is not None,
        path=parts.path,
        fragment=parts.fragment or None,
        query=query,
    )
