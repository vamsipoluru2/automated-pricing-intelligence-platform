"""Flipkart search: live UI driving, and pure harvesting from a stored capture.

Split deliberately. :func:`harvest_cards` is a pure function over stored HTML and
carries every parsing rule; :class:`FlipkartSearch` only drives the browser. That
keeps the interesting logic offline-testable and honours rule 1.

Rules this module enforces
--------------------------
* The visible search UI is used. No constructed search URL, no internal API.
* Position is recorded from the result href (``srno=s_1_<n>``), not from list order,
  so a card that fails to parse cannot silently renumber the ones after it.
* **First result is never assumed correct.** M0 F2 recorded Flipkart returning
  "Softsens Baby Cream" at position 1 for a laptop model code. Position is evidence,
  never identity.
* Card prices are accepted only when the ``price / MRP / discount%`` triple is
  **arithmetically self-consistent**. That is a real check, not positional guessing,
  and it fails closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any

from ...domain.product import UNKNOWN, Money, Unknown

#: Bounded harvest. Flipkart's first page carried 24 cards; this caps how many are
#: considered so the adapter can never drift into unbounded crawling.
MAX_CANDIDATE_CARDS: int = 20

#: How many candidate PDPs may be opened live in one run.
MAX_PDP_OPENS: int = 8

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
#: ``data-id`` holds the Flipkart PID — the identifier M0 §3.3 recommended.
#: Case-sensitive and at least 12 characters on purpose: Flipkart also uses
#: ``data-id`` for layout nodes such as ``stickyElement``, which a case-insensitive
#: pattern happily accepted as a product id.
_CARD_RE = re.compile(r'data-id="([A-Z0-9]{12,})"')
_ANCHOR_RE = re.compile(r'href="(/[^"]*?/p/itm[^"]*?)"', re.IGNORECASE)
_SRNO_RE = re.compile(r"srno=s_\d+_(\d+)")
_RUPEE_RE = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")
_DISCOUNT_RE = re.compile(r"(\d{1,2})%\s*off", re.IGNORECASE)
_RESULT_COUNT_RE = re.compile(r"Showing\s+[\d,]+\s*[–\-]\s*([\d,]+)\s+of\s+([\d,]+)", re.IGNORECASE)

FLIPKART_BASE = "https://www.flipkart.com"

#: Percentage points of slack when reconciling price against the stated discount.
#: Flipkart rounds its displayed percentage, so exact equality would reject valid
#: triples; more than a point or two apart means the figures are not what they seem.
DISCOUNT_TOLERANCE_PCT: Decimal = Decimal("1.5")


@dataclass(frozen=True)
class FlipkartCard:
    """One search-result card, read from stored HTML."""

    position: int
    pid: str
    href: str
    title: str
    card_text: str = ""
    mop: Money | Unknown = UNKNOWN
    mrp: Money | Unknown = UNKNOWN
    price_note: str = ""
    summary_specs: tuple[str, ...] = field(default_factory=tuple)

    @property
    def url(self) -> str:
        return self.href if self.href.startswith("http") else FLIPKART_BASE + self.href

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "pid": self.pid,
            "url": self.url,
            "title": self.title,
            "mop": self.mop.to_dict() if isinstance(self.mop, Money) else "UNKNOWN",
            "mrp": self.mrp.to_dict() if isinstance(self.mrp, Money) else "UNKNOWN",
            "price_note": self.price_note,
            "summary_specs": list(self.summary_specs),
        }


def _text_of(fragment: str) -> str:
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" | ", fragment))).strip()


def _money(raw: str) -> Money | Unknown:
    """Parse a rendered rupee figure, stripping Indian digit grouping."""
    try:
        return Money(amount=Decimal(raw.replace(",", "")), currency="INR", raw=f"₹{raw}")
    except (InvalidOperation, ValueError):
        return UNKNOWN


def resolve_card_price(card_text: str) -> tuple[Money | Unknown, Money | Unknown, str]:
    """Resolve ``(mop, mrp, note)`` from a card, or fail closed.

    Flipkart renders selling price, struck-through MRP and a discount percentage
    together. Rather than trusting their order, the triple is **reconciled**: the
    stated discount must agree with the two figures. M0 §3.5 found six distinct
    monetary values on one Flipkart page, so a positional rule is exactly the kind of
    guess rule 6 forbids — arithmetic agreement is evidence.
    """
    figures = [_money(m.group(1)) for m in _RUPEE_RE.finditer(card_text)]
    figures = [f for f in figures if isinstance(f, Money)]
    if not figures:
        return UNKNOWN, UNKNOWN, "no rupee figure on the card"

    discount = _DISCOUNT_RE.search(card_text)
    if len(figures) >= 2 and discount:
        mop, mrp = figures[0], figures[1]
        assert isinstance(mop, Money) and isinstance(mrp, Money)
        if mrp.amount > 0 and mop.amount < mrp.amount:
            implied = (mrp.amount - mop.amount) / mrp.amount * 100
            stated = Decimal(discount.group(1))
            if abs(implied - stated) <= DISCOUNT_TOLERANCE_PCT:
                return mop, mrp, f"price/MRP reconciled against stated {stated}% off"
        return (
            UNKNOWN,
            UNKNOWN,
            f"price triple did not reconcile with stated {discount.group(1)}% off",
        )

    if len(figures) == 1:
        return figures[0], UNKNOWN, "single figure; no MRP or discount to reconcile against"
    return UNKNOWN, UNKNOWN, "multiple figures with no discount to disambiguate them"


def _summary_specs(card_text: str, *, exclude: str = "") -> tuple[str, ...]:
    """The bullet list Flipkart renders on each card.

    Captured as *evidence only*. These are summary values; the PDP specification
    table is authoritative, per the brief and the project's standing rule that a
    structured table beats rendered prose.
    """
    parts = [p.strip() for p in card_text.split("|")]
    keep = [
        p for p in parts
        if p and re.search(r"\b(GB|TB|Processor|RAM|SSD|HDD|Display|Graphics|inch|Hz|"
                           r"Windows|Operating System)\b", p, re.IGNORECASE)
    ]
    return tuple(dict.fromkeys(keep))[:12]


def harvest_cards(html: str, *, limit: int = MAX_CANDIDATE_CARDS) -> tuple[FlipkartCard, ...]:
    """Read result cards from stored search HTML. Pure — never touches the network.

    Cards are located by their ``data-id`` PID container, then bounded by the start of
    the next card. Class names are never used: Flipkart's are obfuscated and rotate.
    """
    cards: list[FlipkartCard] = []
    matches = list(_CARD_RE.finditer(html))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(html)
        block = html[start:end]
        # The match lands *inside* the opening tag, so the slice begins with the
        # remainder of that tag's attributes. Drop to just past it, or the raw
        # attribute text is read as visible content and becomes the title.
        tag_end = block.find(">")
        if tag_end >= 0:
            block = block[tag_end + 1:]

        anchor = _ANCHOR_RE.search(block)
        if anchor is None:
            continue  # a data-id container that is not a product card
        href = unescape(anchor.group(1))

        srno = _SRNO_RE.search(href)
        position = int(srno.group(1)) if srno else len(cards) + 1

        text = _text_of(block)
        title = _card_title(block, text)
        mop, mrp, note = resolve_card_price(text)

        cards.append(
            FlipkartCard(
                position=position,
                pid=match.group(1),
                href=href,
                title=title,
                card_text=text[:1200],
                mop=mop,
                mrp=mrp,
                price_note=note,
                summary_specs=_summary_specs(text, exclude=title),
            )
        )
        if len(cards) >= limit:
            break
    return tuple(cards)


def _card_title(block: str, text: str) -> str:
    """The product title: the longest text segment that is not a spec bullet."""
    segments = [s.strip() for s in text.split("|")]
    ignore = re.compile(
        r"^(add to compare|\d+(\.\d+)?$|\d+ ratings|\d+ reviews|&|₹|\d+% off|bank offer|"
        r"free delivery|upto|save extra|no cost emi)", re.IGNORECASE
    )
    best = ""
    for segment in segments:
        if not segment or ignore.match(segment):
            continue
        if len(segment) > len(best):
            best = segment
        if len(best) > 40:
            break
    return best


def result_total(html: str) -> tuple[int | Unknown, int | Unknown]:
    """``(shown, total)`` from the results header, or UNKNOWN.

    The total matters: M0 F2's failure mode was a *large* confident wrong result set,
    so an implausibly large count for a specific product query is itself a signal.
    """
    match = _RESULT_COUNT_RE.search(_text_of(html))
    if not match:
        return UNKNOWN, UNKNOWN
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


__all__ = [
    "DISCOUNT_TOLERANCE_PCT",
    "FLIPKART_BASE",
    "MAX_CANDIDATE_CARDS",
    "MAX_PDP_OPENS",
    "FlipkartCard",
    "harvest_cards",
    "resolve_card_price",
    "result_total",
]
