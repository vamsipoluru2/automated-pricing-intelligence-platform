"""Flipkart selectors, derived from a live capture on 2026-08-27.

**Flipkart's CSS class names are obfuscated and rotate** — the search page observed
carried ``jIjQ8S``, ``k7wcnx``, ``_Omnvo``, none of which are stable or meaningful.
Keying on them would produce an adapter that breaks silently on the next deploy.

So every selector here is **structural**: attributes and element relationships that
express what the markup *is* rather than how it is currently styled.

The single most useful finding: each result card is a container carrying
``data-id="<PID>"`` — the Flipkart Product ID, machine-readable, exactly the
identifier M0 §3.3 recommended as ``platform_product_id``. Position is likewise
available structurally, encoded in each result href as ``srno=s_1_<n>``.
"""

from __future__ import annotations

# --------------------------------------------------------------------- search

#: The visible search box. Two nodes matched on the homepage; ``.first`` is used.
SEARCH_INPUT: tuple[str, ...] = (
    "input[name='q']",
    "input[title*='Search for Products']",
    "input[type='text'][placeholder*='Search']",
)

#: Overlays that can intercept the first click. Flipkart shows a login modal to
#: fresh sessions; it is dismissible and must not be treated as a block.
DISMISS_CONTROLS: tuple[str, ...] = (
    "button:has-text('✕')",
    "span:has-text('✕')",
    "button._2KpZ6l._2doB4z",
)

#: A result card: any element carrying a Flipkart PID.
RESULT_CARD: str = "[data-id]"

#: The product anchor inside a card. ``/p/itm`` is Flipkart's product path and
#: ``pid=`` distinguishes a product link from a category or banner link.
PRODUCT_ANCHOR: str = "a[href*='/p/itm']"

#: Search-result header, e.g. ``Showing 1 – 24 of 251 results for "..."``.
RESULT_COUNT_TEXT: str = "text=/Showing\\s+\\d+/"

# ------------------------------------------------------------------------ PDP

#: Controls that reveal the full specification table. M0 §3.6 established the
#: initial payload carries only summary specs, so expansion is mandatory rather
#: than opportunistic.
SPEC_EXPANDERS: tuple[str, ...] = (
    "button:has-text('All Specifications')",
    "div:has-text('All Specifications') >> button",
    "button:has-text('View all specifications')",
    "a:has-text('View all specifications')",
    "button:has-text('Read More')",
    "div._1AtVbE button:has-text('More')",
)

#: Specification tables render as label/value row pairs. Structural, not classed.
SPEC_ROW: str = "tr"
SPEC_SECTION_HEADING: str = "div:has(> table)"

#: Anything that looks like a rupee figure on the PDP. Deliberately broad: the
#: extractor never trusts these directly, it label-matches them (rule 6).
PRICE_CANDIDATES: str = "div:has-text('₹')"

__all__ = [
    "DISMISS_CONTROLS",
    "PRICE_CANDIDATES",
    "PRODUCT_ANCHOR",
    "RESULT_CARD",
    "RESULT_COUNT_TEXT",
    "SEARCH_INPUT",
    "SPEC_EXPANDERS",
    "SPEC_ROW",
    "SPEC_SECTION_HEADING",
]
