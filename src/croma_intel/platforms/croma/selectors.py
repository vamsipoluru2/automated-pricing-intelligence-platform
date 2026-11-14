"""Croma selector sets — ordered fallbacks.

⚠️ UNVALIDATED. M0 obtained zero Croma page content (HTTP 403 on 4/4 requests), so
no selector here has ever been checked against a real Croma DOM. These are
*hypotheses* derived from the URL shapes confirmed in M0 §2.2 and from conventional
e-commerce markup.

The first job of the live M1 gate is to find out which of these, if any, work. The
gate records which selector matched so this file can be corrected from evidence
rather than guesswork.

Ordered lists: try each in turn, take the first that yields results.
"""

from __future__ import annotations

# Result-card containers on the search page.
SEARCH_RESULT_CONTAINERS: tuple[str, ...] = (
    "[data-testid='product-card']",
    ".product-item",
    ".cp-product",
    "li.product-item",
    "div[class*='ProductCard']",
    "div[class*='product-card']",
)

# Anchor carrying the PDP href. Scoped inside a result container where possible.
SEARCH_RESULT_LINKS: tuple[str, ...] = (
    "a[href*='/p/']",
    "a.product-title",
    "[data-testid='product-title'] a",
)

# Product title text on a result card.
SEARCH_RESULT_TITLES: tuple[str, ...] = (
    "[data-testid='product-title']",
    "h3.product-title",
    ".product-title",
    "a[href*='/p/']",
)

# PDP: product title.
PDP_TITLE: tuple[str, ...] = (
    "h1[data-testid='pd-title']",
    "h1.pd-title",
    "h1.product-title",
    "h1",
)

# PDP: breadcrumb trail.
PDP_BREADCRUMB: tuple[str, ...] = (
    "[data-testid='breadcrumb']",
    "nav.breadcrumb",
    ".breadcrumb",
    "ol.breadcrumb",
)

# PDP: any element likely to carry the SKU / product code.
PDP_SKU: tuple[str, ...] = (
    "[data-testid='pd-sku']",
    "[class*='sku']",
    "[id*='sku']",
)

# Pincode entry. M0 could not observe this at all — pure hypothesis.
PINCODE_TRIGGER: tuple[str, ...] = (
    "[data-testid='delivery-pincode']",
    "[class*='pincode']",
    "button[class*='delivery']",
    "#pincodeInput",
)
PINCODE_INPUT: tuple[str, ...] = (
    "input[name='pincode']",
    "input[id*='pincode']",
    "input[placeholder*='PIN']",
    "input[placeholder*='pincode']",
)
PINCODE_SUBMIT: tuple[str, ...] = (
    "button[data-testid='pincode-submit']",
    "button[type='submit']",
    "button[class*='pincode']",
)

# Expandable specification sections on the PDP.
SPEC_EXPANDERS: tuple[str, ...] = (
    "button[data-testid='view-more-specs']",
    "button[class*='view-more']",
    "a[class*='view-all-spec']",
    "button:has-text('View More')",
    "button:has-text('View All')",
)
