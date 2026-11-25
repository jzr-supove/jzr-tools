"""The size, in points, at which each image of a PDF is drawn.

The content streams of every page are read token by token, following
``q``/``Q``/``cm`` and ``Do`` into form XObjects. Broken content never raises:
what cannot be read is left out, and those images get no size.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pypdf import PdfWriter

Matrix = tuple[float, float, float, float, float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
FORM_DEPTH = 12
# Bounds the work on pathological files; each token costs about a microsecond.
TOKEN_BUDGET = 5_000_000

_WHITE = b"\x00\t\n\x0c\r "
_DELIMITERS = _WHITE + b"()<>[]{}/%"
_REGULAR = rb"[^\x00\t\n\x0c\r ()<>\[\]{}/%]"
_TOKEN = re.compile(
    rb"[\x00\t\n\x0c\r ]+"  # white space
    rb"|%[^\r\n]*"  # comment
    rb"|\((?:[^()\\]|\\.)*\)"  # a string without nested parentheses
    rb"|\("  # any other string: walked by hand
    rb"|<<|>>"
    rb"|<[^>]*>?"  # hex string
    rb"|[\[\]{})>]"
    rb"|/" + _REGULAR + rb"*"  # name
    + rb"|" + _REGULAR + rb"+",  # number or operator
    re.S,
)
_NUMBER = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)\Z")
_IMAGE_DATA = re.compile(rb"[\x00\t\n\x0c\r ]ID[\x00\t\n\x0c\r ]")
_IMAGE_END = re.compile(rb"[\x00\t\n\x0c\r ]EI(?=[\x00\t\n\x0c\r ()<>\[\]{}/%]|\Z)")


def multiply(m: Matrix, n: Matrix) -> Matrix:
    """``m`` x ``n`` for PDF's row-vector matrices."""
    return (
        m[0] * n[0] + m[1] * n[2],
        m[0] * n[1] + m[1] * n[3],
        m[2] * n[0] + m[3] * n[2],
        m[2] * n[1] + m[3] * n[3],
        m[4] * n[0] + m[5] * n[2] + n[4],
        m[4] * n[1] + m[5] * n[3] + n[5],
    )


def _string_end(data: bytes, at: int) -> int:
    """The index after the literal string starting at ``at``, or len(data) if unclosed."""
    depth = 0
    i = at
    n = len(data)
    while i < n:
        c = data[i]
        if c == 0x5C:  # backslash
            i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


MAX_DECODED = 256 * 1024 * 1024


def _decoded(stream: object) -> bytes | None:
    """The decoded bytes of ``stream``, or None when they cannot be read."""
    if stream.get("/Filter") == "/FlateDecode" and "/DecodeParms" not in stream:  # type: ignore[attr-defined]
        # Plain zlib: a damaged stream is refused instead of read as garbage.
        import zlib

        try:
            inflater = zlib.decompressobj()
            data = inflater.decompress(stream._data, MAX_DECODED)  # type: ignore[attr-defined]
        except (zlib.error, AttributeError, TypeError):
            return None
        return data if inflater.eof else None
    try:
        data = stream.get_data()  # type: ignore[attr-defined]
    except Exception:  # pypdf raises many kinds for broken streams
        return None
    if isinstance(data, str):
        data = data.encode("latin-1", "replace")
    return data if isinstance(data, bytes) else None


def _resolved(value: object) -> object:
    from pypdf.generic import IndirectObject

    for _ in range(32):
        if not isinstance(value, IndirectObject):
            return value
        try:
            value = value.get_object()
        except Exception:
            return None
    return None


def _numbers(value: object) -> Matrix | None:
    from pypdf.generic import ArrayObject

    value = _resolved(value)
    if not isinstance(value, ArrayObject) or len(value) != 6:
        return None
    try:
        numbers = tuple(float(_resolved(item)) for item in value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(x) for x in numbers):
        return None
    return numbers  # type: ignore[return-value]


class _Scan:
    def __init__(self) -> None:
        self.sizes: dict[int, tuple[float, float]] = {}
        self.budget = TOKEN_BUDGET
        self.forms: dict[int, bytes | None] = {}

    def xobject(self, resources: object, name: bytes) -> tuple[int, object] | None:
        from pypdf.generic import DictionaryObject, IndirectObject

        resources = _resolved(resources)
        if not isinstance(resources, DictionaryObject):
            return None
        xobjects = _resolved(resources.get("/XObject"))
        if not isinstance(xobjects, DictionaryObject):
            return None
        reference = xobjects.get(name.decode("latin-1"))
        if not isinstance(reference, IndirectObject):
            return None
        return reference.idnum, _resolved(reference)

    def walk(
        self, data: bytes, start: Matrix, resources: object, depth: int, active: set[int]
    ) -> None:
        from pypdf.generic import DictionaryObject, StreamObject

        ctm = start
        saved: list[Matrix] = []
        numbers: list[float] = []
        name: bytes | None = None
        n = len(data)
        i = 0
        match = _TOKEN.match
        while i < n:
            self.budget -= 1
            if self.budget < 0:
                return
            found = match(data, i)
            if found is None:  # cannot happen with this pattern, but never loop forever
                i += 1
                continue
            token = found.group()
            i = found.end()
            first = token[0]
            if first in _WHITE or first == 0x25:  # white space, comment
                self.budget += 1
                continue
            if first == 0x28:  # ( string
                if len(token) == 1:
                    i = _string_end(data, i - 1)
                numbers.clear()
                name = None
                continue
            if first == 0x3C:  # < or <<
                if token != b"<<":
                    numbers.clear()
                    name = None
                continue
            if first in b"[]{})>":
                continue
            if first == 0x2F:  # /Name
                name = token
                continue
            if _NUMBER.match(token):
                value = float(token)
                if math.isfinite(value):
                    numbers.append(value)
                    if len(numbers) > 6:
                        del numbers[0]
                continue
            if token == b"q":
                saved.append(ctm)
            elif token == b"Q":
                if saved:
                    ctm = saved.pop()
            elif token == b"cm":
                if len(numbers) == 6:
                    ctm = multiply(tuple(numbers), ctm)  # type: ignore[arg-type]
            elif token == b"Do":
                found_object = self.xobject(resources, name) if name is not None else None
                if found_object is not None:
                    number, target = found_object
                    if isinstance(target, StreamObject):
                        subtype = target.get("/Subtype")
                        if subtype == "/Image":
                            width = math.hypot(ctm[0], ctm[1])
                            height = math.hypot(ctm[2], ctm[3])
                            old = self.sizes.get(number, (0.0, 0.0))
                            self.sizes[number] = (max(old[0], width), max(old[1], height))
                        elif subtype == "/Form" and depth < FORM_DEPTH and number not in active:
                            if number not in self.forms:
                                self.forms[number] = _decoded(target)
                            content = self.forms[number]
                            if content:
                                matrix = _numbers(target.get("/Matrix")) or IDENTITY
                                own = _resolved(target.get("/Resources"))
                                active.add(number)
                                self.walk(
                                    content,
                                    multiply(matrix, ctm),
                                    own if isinstance(own, DictionaryObject) else resources,
                                    depth + 1,
                                    active,
                                )
                                active.discard(number)
                                if self.budget < 0:
                                    return
            elif token == b"BI":
                start_data = _IMAGE_DATA.search(data, i - 1)
                if start_data is None:
                    return
                end = _IMAGE_END.search(data, start_data.end())
                if end is None:
                    return
                i = end.end()
            numbers.clear()
            name = None


def _inherited(page: object, key: str) -> object:
    node = page
    for _ in range(64):
        if node is None:
            return None
        try:
            if key in node:  # type: ignore[operator]
                return _resolved(node[key])  # type: ignore[index]
            node = _resolved(node.get("/Parent"))  # type: ignore[attr-defined]
        except Exception:
            return None
    return None


def drawn_sizes(writer: PdfWriter) -> dict[int, tuple[float, float]]:
    """The largest (width, height) in points of each drawn image, by object number."""
    from pypdf.generic import ArrayObject, DictionaryObject, StreamObject

    scan = _Scan()
    for page in writer.pages:
        if scan.budget < 0:
            break
        try:
            resources = _inherited(page, "/Resources")
            if not isinstance(resources, DictionaryObject) or "/XObject" not in resources:
                continue
            contents = _resolved(page.get("/Contents"))
            streams = list(contents) if isinstance(contents, ArrayObject) else [contents]
            parts = []
            for stream in streams:
                stream = _resolved(stream)
                if isinstance(stream, StreamObject):
                    data = _decoded(stream)
                    if data is not None:
                        parts.append(data)
            if parts:
                scan.walk(b"\n".join(parts), IDENTITY, resources, 0, set())
        except Exception:  # broken pages are left out
            continue
    return scan.sizes
