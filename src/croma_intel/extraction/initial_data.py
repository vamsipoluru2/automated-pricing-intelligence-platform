"""Locate and slice the embedded page-state blob.

The four steps this module owns, in order:

1. **Locate** the ``window.__INITIAL_DATA__ = {…}`` assignment in the captured HTML.
2. **Bound** it — find where the object ends, string-aware, so that a ``}`` inside a
   product description does not truncate the payload.
3. **Index** its top-level members, recording each one's span *without* parsing.
4. **Materialise only what was asked for**, keeping the exact source slice of each
   so any extracted field can be traced back to bytes in the artifact.

Step 4 is the point. In the reference capture the payload is 441 KB, of which the
required reducers are 49 KB (11%) and ``headerReducer`` — site navigation chrome —
is 392 KB (89%). Parsing the whole thing would be slower, would couple extraction to
markup nobody reads, and is precisely what failed when first attempted.

Stdlib only. Pure. No I/O, no network.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..domain.errors import ReducerNotFoundError, StateBlobNotFoundError
from ..domain.product import UNKNOWN
from .js_object import JsParseError, find_value_span, parse_span, scan_object_members

#: The state variable Croma assigns its server-rendered store to.
CROMA_STATE_VARIABLE = "__INITIAL_DATA__"


def _assignment_pattern(variable: str) -> re.Pattern[str]:
    # ``window.__INITIAL_DATA__= {`` in the real capture — the spacing around '=' is
    # not stable, so it is matched rather than assumed.
    return re.compile(r"window\s*\.\s*" + re.escape(variable) + r"\s*=\s*")


@dataclass(frozen=True)
class BlobLocation:
    """Where the state object lives inside the HTML."""

    variable: str
    start: int   # index of the opening '{'
    end: int     # index just past the closing '}'

    @property
    def length(self) -> int:
        return self.end - self.start


def locate_state_blob(html: str, variable: str = CROMA_STATE_VARIABLE) -> BlobLocation:
    """Find the state assignment and bound its object.

    Every occurrence of the marker is tried in document order, because the literal
    string can also appear inside an unrelated script or a CMS payload. The first one
    that is genuinely followed by a well-formed object wins; a match that is only a
    mention is skipped rather than treated as a corrupt blob.
    """
    pattern = _assignment_pattern(variable)
    last_error: JsParseError | None = None
    found_marker = False

    for match in pattern.finditer(html):
        found_marker = True
        pos = match.end()
        while pos < len(html) and html[pos] in " \t\r\n":
            pos += 1
        if pos >= len(html) or html[pos] != "{":
            continue
        try:
            start, end = find_value_span(html, pos)
        except JsParseError as exc:
            last_error = exc
            continue
        return BlobLocation(variable=variable, start=start, end=end)

    if found_marker and last_error is not None:
        raise StateBlobNotFoundError(
            f"window.{variable} is present but no occurrence bounded a valid object: {last_error}"
        )
    if found_marker:
        raise StateBlobNotFoundError(
            f"window.{variable} is present but is not followed by an object literal"
        )
    raise StateBlobNotFoundError(f"no window.{variable} assignment in the document")


@dataclass(frozen=True)
class ReducerPayload:
    """One materialised top-level member, with the bytes it came from."""

    name: str
    value: Any
    start: int
    end: int
    raw: str

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def raw_sha256(self) -> str:
        """Hash of the exact source slice.

        Lets a downstream field be tied to a specific region of a specific artifact,
        not merely to "the capture" as a whole.
        """
        return hashlib.sha256(self.raw.encode("utf-8", errors="replace")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "length": self.length,
            "raw_sha256": self.raw_sha256,
        }


@dataclass(frozen=True)
class ExtractedState:
    """The parsed subset of the state blob, plus evidence of what was skipped."""

    variable: str
    location: BlobLocation
    available: tuple[str, ...]
    payloads: Mapping[str, ReducerPayload]
    missing_optional: tuple[str, ...] = ()

    def __contains__(self, name: object) -> bool:
        return name in self.payloads

    def value(self, name: str) -> Any:
        """The parsed payload for *name*, or UNKNOWN when it was not loaded."""
        payload = self.payloads.get(name)
        if payload is None:
            return UNKNOWN
        return payload.value

    @property
    def bytes_available(self) -> int:
        return self.location.length

    @property
    def bytes_parsed(self) -> int:
        return sum(p.length for p in self.payloads.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable": self.variable,
            "blob_start": self.location.start,
            "blob_end": self.location.end,
            "bytes_available": self.bytes_available,
            "bytes_parsed": self.bytes_parsed,
            "available": list(self.available),
            "parsed": {k: v.to_dict() for k, v in sorted(self.payloads.items())},
            "missing_optional": list(self.missing_optional),
        }


def extract_state(
    html: str,
    *,
    variable: str = CROMA_STATE_VARIABLE,
    required: Sequence[str] = (),
    optional: Sequence[str] = (),
) -> ExtractedState:
    """Parse only the named top-level members of the state blob.

    A missing *required* member raises :class:`ReducerNotFoundError`; a missing
    *optional* one is recorded in ``missing_optional`` and left absent. Nothing else
    in the blob is parsed, so unrelated malformed chrome cannot break extraction of
    the product.
    """
    location = locate_state_blob(html, variable)
    members = scan_object_members(html, location.start)

    absent = [name for name in required if name not in members]
    if absent:
        raise ReducerNotFoundError(
            f"window.{variable} is missing required member(s) {absent}; "
            f"present: {sorted(members)}"
        )

    payloads: dict[str, ReducerPayload] = {}
    for name in list(required) + [n for n in optional if n in members]:
        start, end = members[name]
        payloads[name] = ReducerPayload(
            name=name,
            value=parse_span(html, (start, end)),
            start=start,
            end=end,
            raw=html[start:end],
        )

    return ExtractedState(
        variable=variable,
        location=location,
        available=tuple(members),
        payloads=payloads,
        missing_optional=tuple(n for n in optional if n not in members),
    )


# ------------------------------------------------------------------ navigation


def dig(root: Any, path: str | Iterable[str]) -> Any:
    """Walk a dotted path through parsed data. Returns UNKNOWN rather than raising.

    An absent key, a ``null``, a non-mapping in the middle of the path and a page
    that literally said ``undefined`` all resolve to UNKNOWN — the caller cannot
    accidentally treat any of them as a value, because UNKNOWN refuses truth-testing.
    """
    keys = path.split(".") if isinstance(path, str) else list(path)
    current: Any = root
    for key in keys:
        if current is UNKNOWN or current is None:
            return UNKNOWN
        if isinstance(current, Mapping):
            if key not in current:
                return UNKNOWN
            current = current[key]
            continue
        if isinstance(current, (list, tuple)):
            try:
                index = int(key)
            except ValueError:
                return UNKNOWN
            if not (0 <= index < len(current)):
                return UNKNOWN
            current = current[index]
            continue
        return UNKNOWN
    if current is None:
        return UNKNOWN
    return current


def dig_str(root: Any, path: str) -> Any:
    """:func:`dig`, then require a non-empty string. Otherwise UNKNOWN.

    Croma uses ``""`` for "no value" in several fields (``SAP_BOM_DESC``). An empty
    string is an absence dressed as data, so it is normalised to UNKNOWN here rather
    than propagating as a real value that happens to be falsy.
    """
    value = dig(root, path)
    if value is UNKNOWN:
        return UNKNOWN
    if isinstance(value, bool):
        return UNKNOWN
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return UNKNOWN
    stripped = value.strip()
    if not stripped:
        return UNKNOWN
    return stripped
