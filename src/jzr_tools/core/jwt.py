"""Decode JSON Web Tokens, read their time claims and check HMAC signatures.

Nothing here talks to a server or trusts the token: decoding shows what the
token says, and only an HMAC signature checked against a secret the user
gives says whether it is genuine. Messages never contain the token or the
secret.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

DECODE_FAILED = (
    "Cannot decode this JWT: expected three dot-separated parts "
    "with JSON header and payload objects."
)

HMAC = {"HS256": "sha256", "HS384": "sha384", "HS512": "sha512"}

_PREFIX = re.compile(r"\s*(?:authorization\s*:\s*)?(?:bearer\s+)?", re.IGNORECASE)


class JwtError(ValueError):
    """A user-facing JWT problem; never holds the token or the secret."""


def clean_token(text: str) -> str:
    """Strip whitespace and a leading ``Authorization:`` and ``Bearer``."""
    return text[_PREFIX.match(text).end() :].strip()


@dataclass(frozen=True)
class Token:
    header: dict
    payload: dict
    signing_input: bytes  # b"<part1>.<part2>" as given
    signature_part: str  # the third part as given; may be ""


def _no_constant(name: str) -> object:
    raise ValueError(name)


def _object(part: str) -> dict:
    import json

    from jzr_tools.core.encoding import EncodingError, b64_decode

    try:
        data = b64_decode(part, url_safe=True)
        value = json.loads(data.decode("utf-8"), parse_constant=_no_constant)
    except (EncodingError, UnicodeDecodeError, ValueError, RecursionError):
        raise JwtError(DECODE_FAILED) from None
    if not isinstance(value, dict):
        raise JwtError(DECODE_FAILED)
    return value


def decode(text: str) -> Token:
    """Split a token into its header, payload and signature. Raises JwtError."""
    parts = clean_token(text).split(".")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        raise JwtError(DECODE_FAILED)
    head, body, signature = parts
    header = _object(head)
    payload = _object(body)
    try:
        signing_input = f"{head}.{body}".encode("ascii")
    except UnicodeEncodeError:
        raise JwtError(DECODE_FAILED) from None
    return Token(header, payload, signing_input, signature)


def verify(token: Token, secret: bytes) -> bool:
    """Whether the HMAC signature matches ``secret``. Raises JwtError when it cannot be checked."""
    import hashlib
    import hmac

    from jzr_tools.core.encoding import EncodingError, b64_decode

    if "crit" in token.header:
        raise JwtError("cannot check a token with crit header parameters")
    alg = token.header.get("alg")
    if not isinstance(alg, str):
        raise JwtError("cannot check this alg with a shared secret")
    if alg not in HMAC:
        raise JwtError(f"cannot check alg {alg} with a shared secret")
    expected = hmac.new(secret, token.signing_input, getattr(hashlib, HMAC[alg])).digest()
    if not token.signature_part:
        return False
    try:
        given = b64_decode(token.signature_part, url_safe=True)
    except EncodingError:
        return False
    return hmac.compare_digest(expected, given)


@dataclass(frozen=True)
class Claim:
    name: str  # "exp", "iat" or "nbf"
    present: bool
    value: object  # as in the payload; None when absent
    utc: str | None
    relative: str | None
    status: str | None  # exp: "expired" / "not expired"; nbf: "not yet valid" or None
    problem: str | None  # "not a number" or "out of range"


def _micros(value: object) -> int | None:
    """The claim as Unix microseconds, or None when it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        return value * 1_000_000
    try:
        return int((Decimal(repr(value)) * 1_000_000).to_integral_value())
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _claim(payload: dict, name: str, now: int) -> Claim:
    from jzr_tools.core import timestamp

    if name not in payload:
        return Claim(name, False, None, None, None, None, None)
    value = payload[name]
    micros = _micros(value)
    if micros is None:
        return Claim(name, True, value, None, None, None, "not a number")
    status = None
    if name == "exp":
        status = "expired" if micros <= now else "not expired"
    elif name == "nbf" and micros > now:
        status = "not yet valid"
    try:
        utc = timestamp.utc_text(micros)
    except (timestamp.TimestampError, OverflowError, ValueError):
        return Claim(name, True, value, None, None, status, "out of range")
    return Claim(name, True, value, utc, timestamp.relative_text(micros, now), status, None)


def time_claims(payload: dict, now: int) -> tuple[Claim, Claim, Claim]:
    """The exp, iat and nbf claims, judged against ``now`` (Unix microseconds)."""
    return (_claim(payload, "exp", now), _claim(payload, "iat", now), _claim(payload, "nbf", now))
