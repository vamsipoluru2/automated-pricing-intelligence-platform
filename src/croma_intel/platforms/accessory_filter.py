"""Accessory rejection — a hard gate, applied before any candidate can be selected.

M0 §12.2 confirmed the hazard with live data: a search that surfaces a laptop also
surfaces bags, sleeves and chargers, and those accessory titles carry their own
model codes and the same size tokens as the product. Selecting one would be a
silent-wrong result of the worst kind — a real URL, a real price, wrong product.

Design notes:
  * multi-signal, not a keyword blocklist. A bare deny-list rejects
    "…with Backlit Keyboard" (a laptop) and misses "Sleeve for Lenovo LOQ".
  * the head-noun guard is what prevents that: an accessory term appearing *after*
    several product anchors is a feature mention, not the product type.
  * price is deliberately absent here. M0 §16 showed intra-series spread of 78%,
    and architectural rule 5 keeps price out of identity decisions.

Pure, stdlib-only, offline-testable.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

# Terms that name an accessory when they are the product's head noun.
ACCESSORY_TERMS: tuple[str, ...] = (
    "bag", "backpack", "sleeve", "case", "cover", "pouch", "skin",
    "charger", "adapter", "adaptor", "power supply", "cable", "cord",
    "mouse", "mice", "keyboard", "keypad", "stand", "riser", "dock", "docking station",
    "remote", "screen guard", "screen protector", "tempered glass", "protector",
    "cleaning kit", "cooling pad", "hub", "converter", "extension",
    "battery", "spare part", "replacement", "cartridge", "filter",
)

# Phrases that mark a product as being FOR another product — the strongest signal.
COMPATIBILITY_PATTERNS: tuple[str, ...] = (
    r"\bfor\s+(?:the\s+)?(?:lenovo|hp|dell|asus|acer|msi|apple|samsung)\b",
    r"\bcompatible\s+with\b",
    r"\bsuitable\s+for\b",
    r"\bfits\b",
    r"\bdesigned\s+for\b",
)

# Tokens that anchor a listing as an actual computing product.
PRODUCT_ANCHORS: tuple[str, ...] = (
    "laptop", "notebook", "gaming laptop", "processor", "ryzen", "core i3", "core i5",
    "core i7", "core i9", "ultra 5", "ultra 7", "ssd", "ram", "graphics", "rtx", "gtx",
    "windows", "display", "gb/", "tb/", "hz",
)

MIN_ANCHORS_FOR_FEATURE_GUARD = 2
HEAD_NOUN_WINDOW_TOKENS = 6


@dataclass(frozen=True)
class AccessoryVerdict:
    is_accessory: bool
    reasons: tuple[str, ...] = ()
    anchors_found: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reasons"] = list(self.reasons)
        d["anchors_found"] = list(self.anchors_found)
        return d


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _found_anchors(text: str, anchors: tuple[str, ...] = PRODUCT_ANCHORS) -> tuple[str, ...]:
    return tuple(a for a in anchors if a in text)


def _term_in_head(text: str, term: str) -> bool:
    """True when the term appears among the first tokens — i.e. as the product type."""
    tokens = text.split()
    head = " ".join(tokens[:HEAD_NOUN_WINDOW_TOKENS])
    return re.search(rf"\b{re.escape(term)}\b", head) is not None


def classify(
    title: str, *, category: str = "", anchors: tuple[str, ...] = PRODUCT_ANCHORS
) -> AccessoryVerdict:
    """Decide whether a search result is an accessory rather than the product.

    *anchors* are the tokens that mark the listing as the product itself. They are
    category-dependent — "ryzen" anchors a laptop, not a mixer grinder — and default
    to the laptop set this filter was built and validated against.
    """
    text = _normalize(title)
    cat = _normalize(category)
    if not text and not cat:
        # Rule 3: absent information never yields a favourable verdict, but neither
        # does it invent an accessory. Caller treats "unknown" as not-selectable.
        return AccessoryVerdict(False, ("NO_TEXT",), (), "no title or category to assess")

    reasons: list[str] = []
    found = _found_anchors(text, anchors)

    # --- decisive: category says accessory
    accessory_category_tokens = ("accessor", "bag", "cover", "charger", "adapter", "cable")
    if cat and any(tok in cat for tok in accessory_category_tokens):
        reasons.append(f"CATEGORY_ACCESSORY:{cat[:40]}")
        return AccessoryVerdict(True, tuple(reasons), found, "category identifies an accessory")

    # --- decisive: "for <brand>" / "compatible with"
    for pattern in COMPATIBILITY_PATTERNS:
        if re.search(pattern, text):
            reasons.append(f"COMPATIBILITY_PHRASE:/{pattern}/")
            return AccessoryVerdict(
                True, tuple(reasons), found, "title describes an item made for another product"
            )

    # --- head-noun accessory term, guarded by product anchors
    for term in ACCESSORY_TERMS:
        if not re.search(rf"\b{re.escape(term)}\b", text):
            continue
        in_head = _term_in_head(text, term)
        if in_head:
            if len(found) >= MIN_ANCHORS_FOR_FEATURE_GUARD:
                # e.g. "Laptop ... Backlit Keyboard ..." — a feature, not the product.
                reasons.append(f"ACCESSORY_TERM_AS_FEATURE:{term}")
                continue
            reasons.append(f"ACCESSORY_HEAD_NOUN:{term}")
            return AccessoryVerdict(
                True, tuple(reasons), found, f"'{term}' is the product's head noun"
            )
        if not found:
            reasons.append(f"ACCESSORY_TERM_NO_ANCHOR:{term}")
            return AccessoryVerdict(
                True, tuple(reasons), found, f"'{term}' present with no product anchors"
            )
        reasons.append(f"ACCESSORY_TERM_AS_FEATURE:{term}")

    return AccessoryVerdict(False, tuple(reasons), found, "no decisive accessory signal")


def is_accessory(title: str, *, category: str = "") -> bool:
    return classify(title, category=category).is_accessory
