"""Offer extraction from the rendered Croma PDP.

Offers are the one part of the product record that is **not** in
``window.__INITIAL_DATA__``. In the reference capture the state blob ends at byte
524,750 and the offer carousel begins at 601,832 — it is rendered markup only, so
this module reads the DOM while everything authoritative comes from the reducer.

Two hazards drive the design.

**Offers are not prices.** Nothing here can reach :class:`PriceQuote`. A carousel
slide reading "Instant discount of 10% up to Rs 1500" is a conditional promotion,
and M0 §11.2 showed that admitting such figures into price resolution is how a wrong
MOP gets published.

**Bank names are rewritten client-side.** The page ships a script that substitutes
display names for internal codes::

    if (bankName.textContent == "JP_KOTAK" || bankName.textContent == "NB_KOTAK") {
        bankName.textContent = "Kotak Mahindra Bank"; }

A capture taken after hydration therefore shows the display name and no code; one
taken before shows the code. Both are recorded as separate sourced fields, and
whichever is genuinely absent stays UNKNOWN. The rendered name never overwrites a
raw code, and a code is never invented from a name — the substitution table is
one-way and guessing its inverse would be fabrication.

Stdlib only. Pure. No I/O.
"""

from __future__ import annotations

import re
from html import unescape

from ...domain.product import UNKNOWN, Offer, OfferSet, Sourced, SourceKind

#: The carousel header, e.g. ``(7 OFFERS)``.
_DECLARED_COUNT_RE = re.compile(r"\((\d+)\s*OFFERS?\)", re.IGNORECASE)

_OFFER_CONTAINER_RE = re.compile(r'<div[^>]*\bclass="[^"]*\boffer-container\b[^"]*"', re.IGNORECASE)
_OFFER_BLOCK_RE = re.compile(r'<div[^>]*\bclass="[^"]*\bbank-offer-product\b[^"]*"', re.IGNORECASE)
_BANK_NAME_RE = re.compile(
    r'<span[^>]*\bclass="[^"]*\bbank-name-text\b[^"]*"[^>]*>(.*?)</span>', re.IGNORECASE | re.DOTALL
)
_OFFER_TEXT_RE = re.compile(
    r'<span[^>]*\bclass="[^"]*\bbank-offers-text-pdp-carousel\b[^"]*"[^>]*>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)

#: Croma's internal payment-instrument codes, as used by the rewrite script.
#: ``JP_`` is the juspay gateway prefix, ``NB_`` net-banking.
_BANK_CODE_RE = re.compile(r"\b((?:JP|NB)_[A-Z0-9]{2,12})\b")

_TAG_RE = re.compile(r"<[^>]+>")


def _text_of(fragment: str) -> str:
    """Strip markup and normalise whitespace, resolving HTML entities."""
    return " ".join(unescape(_TAG_RE.sub(" ", fragment)).split())


def _balanced_div(html: str, start: int) -> tuple[int, int]:
    """``(start, end)`` of the ``<div>`` element whose opening tag begins at *start*.

    Counts nested ``<div`` / ``</div>`` while skipping quoted attribute values, so a
    ``>`` inside an attribute cannot end a tag early. Falls back to end-of-document
    when the markup is unbalanced — a truncated capture should yield a short offer,
    not an exception.
    """
    pos = start
    depth = 0
    length = len(html)
    while pos < length:
        lt = html.find("<", pos)
        if lt == -1:
            break
        closing = html.startswith("</", lt)
        # walk to the end of this tag, honouring quoted attributes
        scan = lt + 1
        quote = ""
        while scan < length:
            ch = html[scan]
            if quote:
                if ch == quote:
                    quote = ""
            elif ch in "\"'":
                quote = ch
            elif ch == ">":
                break
            scan += 1
        tag_end = scan + 1
        name_match = re.match(r"</?\s*([A-Za-z0-9-]+)", html[lt:tag_end])
        if name_match and name_match.group(1).lower() == "div":
            if closing:
                depth -= 1
                if depth == 0:
                    return start, tag_end
            elif not html[lt:tag_end].rstrip().endswith("/>"):
                depth += 1
        pos = tag_end
    return start, length


def _sourced_text(raw: str | None, path: str, note: str = "") -> Sourced[str]:
    """A rendered-DOM string, or UNKNOWN when the node is absent or empty."""
    if raw is None:
        return Sourced.missing(path, note=note or "node not present")
    text = _text_of(raw)
    if not text:
        return Sourced(UNKNOWN, path, SourceKind.ABSENT, note="node present but empty")
    return Sourced(text, path, SourceKind.RENDERED_DOM, note=note)


def extract_offers(html: str) -> OfferSet:
    """Extract the offer carousel from rendered PDP markup.

    Each offer is parsed from its own ``bank-offer-product`` element rather than by
    zipping two independent lists of names and texts: one slide in the reference
    capture has an empty bank name, and positional zipping would have shifted every
    later offer onto the wrong bank.
    """
    container = _OFFER_CONTAINER_RE.search(html)
    if container is None:
        return OfferSet(
            offers=(),
            declared_count=UNKNOWN,
            note="no offer-container element in the rendered markup",
        )
    region_start, region_end = _balanced_div(html, container.start())
    region = html[region_start:region_end]

    declared: int | object = UNKNOWN
    declared_match = _DECLARED_COUNT_RE.search(region)
    if declared_match is not None:
        declared = int(declared_match.group(1))

    offers: list[Offer] = []
    for index, block_match in enumerate(_OFFER_BLOCK_RE.finditer(region)):
        block_start, block_end = _balanced_div(region, block_match.start())
        block = region[block_start:block_end]
        path = f"dom.offer-container.bank-offer-product[{index}]"

        name_match = _BANK_NAME_RE.search(block)
        text_match = _OFFER_TEXT_RE.search(block)
        code_match = _BANK_CODE_RE.search(_text_of(block))

        offers.append(
            Offer(
                index=index,
                text=_sourced_text(
                    text_match.group(1) if text_match else None,
                    f"{path}.bank-offers-text-pdp-carousel",
                ),
                bank_name=_sourced_text(
                    name_match.group(1) if name_match else None, f"{path}.bank-name-text"
                ),
                # Absent after hydration, by design. Recorded as UNKNOWN rather than
                # reverse-engineered from the display name.
                bank_code=(
                    Sourced(code_match.group(1), f"{path}.bank-name-text", SourceKind.RENDERED_DOM)
                    if code_match
                    else Sourced.missing(
                        f"{path}.bank-name-text",
                        note="no JP_/NB_ code in the rendered markup; "
                        "client-side script substitutes the display name",
                    )
                ),
            )
        )

    return OfferSet(
        offers=tuple(offers),
        declared_count=declared,  # type: ignore[arg-type]
        kind=SourceKind.RENDERED_DOM,
        note="offers are rendered-DOM only; they are absent from window.__INITIAL_DATA__",
    )


__all__ = ["extract_offers"]
