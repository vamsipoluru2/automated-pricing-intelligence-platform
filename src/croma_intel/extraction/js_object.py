"""A bounded parser for JavaScript object literals embedded in HTML.

Why this exists
---------------
Croma serves its product state as ``window.__INITIAL_DATA__ = {...}``. That payload
is *JavaScript source*, not JSON, and ``json.loads`` refuses it: the real capture
carries seven bare ``undefined`` values, one of which sits inside the product
reducer itself.

Three tempting fixes are all rejected here:

* ``json.loads`` on the whole blob — fails, and would fail on 89% of the payload
  that nothing needs anyway.
* ``text.replace("undefined", "null")`` — a global string replacement corrupts every
  legitimate occurrence of the word inside product descriptions and URLs. It cannot
  distinguish a syntactic literal from eight letters inside a string.
* ``eval`` / ``ast.literal_eval`` — executing or approximating attacker-controlled
  page content, and ``literal_eval`` rejects ``true``/``false``/``null`` anyway.

Instead this module implements the grammar that is actually there: JSON, plus the
single extra literal ``undefined``, which maps to :data:`UNKNOWN` rather than to
``None`` so that "the page said undefined" stays distinguishable from "the key was
absent". Any other token fails closed with :class:`JsParseError`.

The scanner is *lazy*: :func:`scan_object_members` locates each member's value span
by structural skipping without materialising it, so a caller can parse the 49 KB it
needs out of a 441 KB blob and never touch the rest.

Stdlib only. Pure. No I/O.
"""

from __future__ import annotations

import json
from typing import Any

from ..domain.errors import JsParseError
from ..domain.product import UNKNOWN

# JSON whitespace, per RFC 8259. Deliberately not ``str.isspace()``: that admits
# vertical tab, form feed and Unicode separators, none of which are legal here.
_WS = " \t\n\r"

_LITERALS: dict[str, Any] = {
    "true": True,
    "false": False,
    "null": None,
    # The whole reason this module exists. Mapped to UNKNOWN, not None: rule 2
    # requires that missing information stay distinguishable from a real null.
    "undefined": UNKNOWN,
}

_NUMBER_CHARS = "0123456789+-.eE"

#: Structural nesting cap. Real Croma payloads nest ~8 deep; anything approaching
#: this is malformed or hostile, and a runaway recursion should fail closed rather
#: than exhaust the interpreter stack.
MAX_DEPTH = 64


def _context(text: str, pos: int, width: int = 60) -> str:
    lo = max(0, pos - width)
    hi = min(len(text), pos + width)
    return text[lo:pos] + " ⟪HERE⟫ " + text[pos:hi]


def _fail(text: str, pos: int, message: str) -> JsParseError:
    return JsParseError(f"{message} at offset {pos}: …{_context(text, pos)}…", position=pos)


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in _WS:
        pos += 1
    return pos


# ----------------------------------------------------------------- string scan


def _string_end(text: str, start: int) -> int:
    """Index just past the closing quote of the string literal at *start*.

    *start* must index the opening ``"``. Backslash escapes are honoured so that an
    escaped quote does not terminate the scan.
    """
    if start >= len(text) or text[start] != '"':
        raise _fail(text, start, "expected a string")
    pos = start + 1
    while pos < len(text):
        ch = text[pos]
        if ch == "\\":
            pos += 2  # skip the escape and whatever it escapes
            continue
        if ch == '"':
            return pos + 1
        pos += 1
    raise _fail(text, start, "unterminated string")


def _scan_string(text: str, start: int) -> tuple[str, int]:
    """Decode the string literal at *start*. Returns ``(value, next_pos)``.

    Decoding is delegated to :func:`json.loads` on the exact literal slice — public
    stdlib API, and correct for the escape cases that matter here (``\\u002F``
    appears throughout Croma URLs, and surrogate pairs appear in emoji-bearing CMS
    copy). Re-implementing that by hand would be a needless source of subtle bugs.
    """
    end = _string_end(text, start)
    try:
        value = json.loads(text[start:end])
    except ValueError as exc:
        raise _fail(text, start, f"invalid string literal ({exc})") from exc
    if not isinstance(value, str):  # pragma: no cover - defensive
        raise _fail(text, start, "string literal did not decode to str")
    return value, end


# ------------------------------------------------------------- structural skip


def skip_value(text: str, pos: int, depth: int = 0) -> int:
    """Return the index just past the value starting at *pos*, without building it.

    This is the lazy half of the module. It walks structure only, so locating one
    reducer inside a 441 KB payload costs a scan rather than a full materialisation.
    """
    if depth > MAX_DEPTH:
        raise _fail(text, pos, f"nesting deeper than MAX_DEPTH={MAX_DEPTH}")
    pos = _skip_ws(text, pos)
    if pos >= len(text):
        raise _fail(text, pos, "unexpected end of input")

    ch = text[pos]
    if ch == '"':
        return _string_end(text, pos)
    if ch in "{[":
        closing = "}" if ch == "{" else "]"
        pos += 1
        while True:
            pos = _skip_ws(text, pos)
            if pos >= len(text):
                raise _fail(text, pos, "unexpected end of input inside a container")
            if text[pos] == closing:
                return pos + 1
            if text[pos] == ",":
                pos += 1
                continue
            if ch == "{":
                if text[pos] != '"':
                    raise _fail(text, pos, "expected a quoted object key")
                pos = _string_end(text, pos)
                pos = _skip_ws(text, pos)
                if pos >= len(text) or text[pos] != ":":
                    raise _fail(text, pos, "expected ':' after object key")
                pos += 1
            pos = skip_value(text, pos, depth + 1)
    # a bare literal or a number: run to the next structural delimiter
    start = pos
    while pos < len(text) and text[pos] not in ",}]" and text[pos] not in _WS:
        pos += 1
    if pos == start:
        raise _fail(text, pos, "expected a value")
    return pos


def find_value_span(text: str, pos: int) -> tuple[int, int]:
    """``(start, end)`` of the value beginning at or after *pos*."""
    start = _skip_ws(text, pos)
    return start, skip_value(text, start)


# ----------------------------------------------------------------- lazy member scan


def scan_object_members(text: str, start: int) -> dict[str, tuple[int, int]]:
    """Map each key of the object at *start* to its value's ``(start, end)`` span.

    Values are located, not parsed. This is what makes bounded extraction possible:
    the caller decides which members are worth materialising.

    On a duplicate key the first occurrence wins and the later one is ignored, which
    matches nothing in particular — duplicates do not occur in real payloads — but is
    at least deterministic rather than order-dependent.
    """
    pos = _skip_ws(text, start)
    if pos >= len(text) or text[pos] != "{":
        raise _fail(text, pos, "expected an object")
    pos += 1
    members: dict[str, tuple[int, int]] = {}
    while True:
        pos = _skip_ws(text, pos)
        if pos >= len(text):
            raise _fail(text, pos, "unexpected end of input inside an object")
        if text[pos] == "}":
            return members
        if text[pos] == ",":
            pos += 1
            continue
        if text[pos] != '"':
            raise _fail(text, pos, "expected a quoted object key")
        key, pos = _scan_string(text, pos)
        pos = _skip_ws(text, pos)
        if pos >= len(text) or text[pos] != ":":
            raise _fail(text, pos, "expected ':' after object key")
        value_start, value_end = find_value_span(text, pos + 1)
        members.setdefault(key, (value_start, value_end))
        pos = value_end


# ---------------------------------------------------------------------- parser


def parse_value(text: str, pos: int = 0, depth: int = 0) -> tuple[Any, int]:
    """Materialise the value starting at *pos*. Returns ``(value, next_pos)``."""
    if depth > MAX_DEPTH:
        raise _fail(text, pos, f"nesting deeper than MAX_DEPTH={MAX_DEPTH}")
    pos = _skip_ws(text, pos)
    if pos >= len(text):
        raise _fail(text, pos, "unexpected end of input")

    ch = text[pos]

    if ch == '"':
        return _scan_string(text, pos)

    if ch == "{":
        obj: dict[str, Any] = {}
        pos += 1
        expecting = False  # a ',' has been consumed, so a member must follow
        while True:
            pos = _skip_ws(text, pos)
            if pos >= len(text):
                raise _fail(text, pos, "unexpected end of input inside an object")
            if text[pos] == "}":
                if expecting:
                    raise _fail(text, pos, "trailing comma in object")
                return obj, pos + 1
            if obj and not expecting:
                if text[pos] != ",":
                    raise _fail(text, pos, "expected ',' or '}' in object")
                pos += 1
                expecting = True
                continue
            if text[pos] != '"':
                raise _fail(text, pos, "expected a quoted object key")
            key, pos = _scan_string(text, pos)
            pos = _skip_ws(text, pos)
            if pos >= len(text) or text[pos] != ":":
                raise _fail(text, pos, "expected ':' after object key")
            value, pos = parse_value(text, pos + 1, depth + 1)
            obj[key] = value
            expecting = False

    if ch == "[":
        arr: list[Any] = []
        pos += 1
        expecting = False
        while True:
            pos = _skip_ws(text, pos)
            if pos >= len(text):
                raise _fail(text, pos, "unexpected end of input inside an array")
            if text[pos] == "]":
                if expecting:
                    raise _fail(text, pos, "trailing comma in array")
                return arr, pos + 1
            if arr and not expecting:
                if text[pos] != ",":
                    raise _fail(text, pos, "expected ',' or ']' in array")
                pos += 1
                expecting = True
                continue
            value, pos = parse_value(text, pos, depth + 1)
            arr.append(value)
            expecting = False

    # bare token: a named literal, or a number
    start = pos
    while pos < len(text) and text[pos] not in ",}]" and text[pos] not in _WS:
        pos += 1
    token = text[start:pos]
    if not token:
        raise _fail(text, start, "expected a value")
    if token in _LITERALS:
        return _LITERALS[token], pos
    if token[0] in "-0123456789" and all(c in _NUMBER_CHARS for c in token):
        try:
            return (int(token) if _is_integer_token(token) else float(token)), pos
        except ValueError as exc:
            raise _fail(text, start, f"invalid number {token!r}") from exc
    # Fail closed. An unrecognised bare word means the grammar assumption is wrong,
    # and guessing at it is how a silent-wrong extraction starts.
    raise _fail(text, start, f"unsupported bare token {token!r}")


def _is_integer_token(token: str) -> bool:
    return not any(c in token for c in ".eE")


def parse(text: str) -> Any:
    """Parse *text* as a single complete value. Trailing content is an error."""
    value, pos = parse_value(text, 0)
    pos = _skip_ws(text, pos)
    if pos != len(text):
        raise _fail(text, pos, "trailing content after the parsed value")
    return value


def parse_span(text: str, span: tuple[int, int]) -> Any:
    """Parse exactly the region *span* of *text*."""
    start, end = span
    value, pos = parse_value(text, start)
    pos = _skip_ws(text, pos)
    if pos != end:
        raise _fail(text, pos, f"value did not fill its span (ended at {pos}, expected {end})")
    return value
