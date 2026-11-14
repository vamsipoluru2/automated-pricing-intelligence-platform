"""Flipkart PDP extraction — pure over a stored capture.

Live findings, 2026-08-27 (SKU-equivalent PDP for Lenovo LOQ 15ARP10E)
---------------------------------------------------------------------
1. **There is no specification table.** No ``<tr>`` anywhere, no ``Processor Name`` /
   ``RAM Type`` / ``Model Number`` labels, and none of the expected expander controls
   ("All Specifications", "View all specifications"). Twelve progressive scrolls
   revealed nothing further. The page has a "Key Highlights" block only.
2. **There is no embedded state blob.** ``window.__INITIAL_STATE__`` appears only as
   a *reference inside script bodies*, never as an assignment;
   ``window.__staticRouterHydrationData`` is 1.5 KB of routing data with no product
   content. The M2 bounded parser correctly refuses both.
3. **F1 upheld: no model number is published anywhere on the page.**
4. **F8 reproduced live.** One page carries six monetary figures: MRP ₹1,53,590,
   selling ₹1,10,990, an *additive* ``+₹306 Protect Promise Fee``, a personalised
   ``₹1,06,990 Lowest price for you``, ``₹17,915 x 6m`` EMI, and exchange/bank offers.

Consequences, applied here
--------------------------
Specifications come from two rendered-DOM sources, in priority order, and the record
is explicitly marked incomplete:

* the **title specification block** — a delimited run such as
  ``(16 GB/512 GB SSD/Windows 11 Home/6 GB Graphics/NVIDIA GeForce RTX 3050/144 Hz)``;
* the **search-card summary bullets**, which carry display size when the title does
  not (``39.62 cm (15.6 inch) Display``).

Neither is a structured specification table, so ``full_specs_visible`` is False and a
warning is always emitted. This is A6 answered with evidence rather than assumed.

Pure. No I/O, no network. All browser work lives in ``browser_driver.py``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any

from ...domain.candidate import CandidateProduct, DiscoveryContext
from ...domain.capture import RawCapture
from ...domain.matching import ListingCondition, ModelAvailability
from ...domain.product import UNKNOWN, Money, Unknown
from ...normalization.spec_value import (
    normalize_cpu_model,
    normalize_gpu_model,
    parse_capacity_gb,
    parse_display_inches,
    parse_refresh_hz,
)

PLATFORM = "flipkart"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TITLE_SPEC_BLOCK_RE = re.compile(r"\(([^()]*(?:GB|TB|Hz|Windows|Graphics)[^()]*)\)", re.IGNORECASE)
_BREADCRUMB_RE = re.compile(r"Home\s*/\s*(.+?)\s*/\s*[^/]{40,}", re.IGNORECASE)
_PID_RE = re.compile(r"[?&]pid=([A-Z0-9]{8,})", re.IGNORECASE)
_ITM_RE = re.compile(r"/p/(itm[0-9a-z]+)", re.IGNORECASE)
_RUPEE_RE = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")

#: Phrases that disqualify a nearby rupee figure from being MOP. Every one was
#: observed on the live PDP; M0 §11.2 rule 2 made adjacency-disqualification the
#: mechanism, and this is that allowlist's negative half.
NON_MOP_MARKERS: tuple[str, ...] = (
    "protect promise fee", "lowest price for you", "no cost emi", "x 6m", "x 3m",
    "x 9m", "x 12m", "exchange offer", "bank offer", "cashback", "off apply",
    "buy at", "apply offers", "up to", "coupon", "partner offer", "emi",
)

#: Words that mark a category as an accessory rather than a computing product.
_ACCESSORY_CATEGORY_TOKENS: tuple[str, ...] = (
    "bag", "sleeve", "cover", "case", "accessor", "charger", "adapter", "cooling",
    "stand", "skin", "protector", "battery", "keyboard", "mouse",
)


@dataclass(frozen=True)
class SpecExpansionReport:
    """Evidence about the attempt to reveal a full specification table.

    Recorded whether or not it succeeded. Without it, "Flipkart has no spec table"
    and "we did not look properly" are indistinguishable — which is exactly the
    ambiguity that cost M1 six revisions.
    """

    expander_attempted: bool = False
    expander_found: bool = False
    expander_clicked: bool = False
    full_specs_visible: bool = False
    spec_group_count: int = 0
    spec_row_count: int = 0
    scrolls_performed: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "expander_attempted": self.expander_attempted,
            "expander_found": self.expander_found,
            "expander_clicked": self.expander_clicked,
            "full_specs_visible": self.full_specs_visible,
            "spec_group_count": self.spec_group_count,
            "spec_row_count": self.spec_row_count,
            "scrolls_performed": self.scrolls_performed,
            "detail": self.detail,
        }


def visible_text(html: str) -> str:
    body = html[html.find("<body"):] if "<body" in html else html
    stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.DOTALL | re.IGNORECASE)
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", stripped))).strip()


# ---------------------------------------------------------------- identity


def extract_title(html: str) -> str | Unknown:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if match:
        raw = _WS_RE.sub(" ", unescape(match.group(1))).strip()
        raw = re.sub(r"\s*[|-]\s*(Buy|Price in India|Flipkart\.com).*$", "", raw, flags=re.IGNORECASE)
        if raw:
            return raw
    text = visible_text(html)
    block = _TITLE_SPEC_BLOCK_RE.search(text)
    if block:
        start = max(0, block.start() - 120)
        return text[start:block.end()].strip()
    return UNKNOWN


def extract_breadcrumb(html: str) -> tuple[str, ...]:
    """The category trail. M0 §3.3: Flipkart terminates in a **brand** node."""
    text = visible_text(html)
    index = text.find("Home /")
    if index < 0:
        return ()
    segment = text[index: index + 260]
    parts = [p.strip() for p in segment.split("/")]
    trail: list[str] = []
    for part in parts:
        # The product leaf contains the title, which itself contains slashes from the
        # specification block — so a long or bracketed segment means the category
        # trail has ended and the title has begun.
        if not part or len(part) > 40 or "(" in part:
            break
        trail.append(part)
        if len(trail) >= 5:
            break
    return tuple(trail)


def extract_ids(url: str, html: str) -> tuple[str | Unknown, str | Unknown]:
    """``(pid, item_id)``. M0 §3.3 recommends the PID as platform_product_id."""
    pid = _PID_RE.search(url) or _PID_RE.search(html)
    itm = _ITM_RE.search(url) or _ITM_RE.search(html)
    return (
        pid.group(1).upper() if pid else UNKNOWN,
        itm.group(1) if itm else UNKNOWN,
    )


def extract_model_number(html: str) -> tuple[str | Unknown, ModelAvailability]:
    """Look for a labelled model field. Returns UNKNOWN + NEVER when absent.

    A labelled field is required. The title is **not** an acceptable source: M0 §10.3
    made model identity a labelled-field-only proposition, and inferring one from
    marketing text is precisely the guess rule 10 forbids.
    """
    text = visible_text(html)
    for label in ("Model Number", "Model ID", "Part Number", "Manufacturer Part"):
        match = re.search(rf"{re.escape(label)}\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/#]{{4,20}})", text)
        if match:
            return match.group(1), ModelAvailability.SOMETIMES
    return UNKNOWN, ModelAvailability.NEVER


def extract_model_series(title: str) -> str | Unknown:
    """Series code from the title, e.g. ``LOQ 15ARP10E`` or ``15ARP9``.

    A series is not identity — it names a family, and M0 §10.3 showed one family
    spans materially different configurations. It is used only to distinguish
    VARIANT from DIFFERENT, never to promote a match.
    """
    match = re.search(r"\b((?:LOQ|TUF|VICTUS|IDEAPAD|VIVOBOOK|LEGION|NITRO|INSPIRON|"
                      r"PAVILION|ASPIRE|KATANA|OMEN)(?:\s+[A-Z0-9]{2,10}){0,2})\b",
                      title.upper())
    if not match:
        return UNKNOWN
    # Trim trailing product-type words: "LOQ 15ARP10E GAMING" is the series plus the
    # start of "Gaming Laptop", and a series that absorbs the product type would
    # never compare equal to the same series written without it.
    stop = {"GAMING", "LAPTOP", "NOTEBOOK", "THIN", "BUSINESS", "CREATOR", "PROCESSOR"}
    tokens = [t for t in match.group(1).split()]
    while tokens and tokens[-1] in stop:
        tokens.pop()
    return " ".join(tokens) if tokens else UNKNOWN


# ------------------------------------------------------------------- specs


def parse_title_spec_block(title: str) -> dict[str, str]:
    """Canonical specs from the delimited block inside a Flipkart title.

    ``(16 GB/512 GB SSD/Windows 11 Home/6 GB Graphics/NVIDIA GeForce RTX 3050/144 Hz)``

    Slash-delimited and positional-free: every field is identified by its own
    content, so a missing element shifts nothing.
    """
    specs: dict[str, str] = {}
    match = _TITLE_SPEC_BLOCK_RE.search(title or "")
    fields = [f.strip() for f in match.group(1).split("/")] if match else []

    cpu = normalize_cpu_model(title)
    if cpu is not UNKNOWN:
        specs["cpu.model"] = str(cpu)

    for field_text in fields:
        lower = field_text.lower()
        if "graphics" in lower:
            vram = parse_capacity_gb(field_text)
            if vram is not UNKNOWN:
                specs["gpu.vram_gb"] = str(vram)
            continue
        if any(token in lower for token in ("ssd", "hdd", "emmc")):
            capacity = parse_capacity_gb(field_text)
            if capacity is not UNKNOWN:
                specs["storage.size_gb"] = str(capacity)
            continue
        if "windows" in lower or "mac os" in lower or "chrome os" in lower:
            specs["os"] = field_text
            continue
        if "hz" in lower:
            refresh = parse_refresh_hz(field_text)
            if refresh is not UNKNOWN:
                specs["display.refresh_hz"] = str(refresh)
            continue
        if re.fullmatch(r"\d{1,3}\s*GB", field_text, re.IGNORECASE):
            capacity = parse_capacity_gb(field_text)
            if capacity is not UNKNOWN and "ram.size_gb" not in specs:
                specs["ram.size_gb"] = str(capacity)
            continue

    gpu = normalize_gpu_model(title)
    if gpu is not UNKNOWN:
        specs["gpu.model"] = str(gpu)
    inches = parse_display_inches(title)
    if inches is not UNKNOWN:
        specs["display.size_in"] = str(inches)
    return specs


def parse_card_summary(bullets: Sequence[str]) -> dict[str, str]:
    """Canonical specs from search-card bullets.

    The card carries display size (``39.62 cm (15.6 inch) Display``) which the title
    omits, so it is a genuine second source rather than a duplicate.
    """
    specs: dict[str, str] = {}
    for bullet in bullets:
        lower = bullet.lower()
        if "display" in lower or "inch" in lower or " cm" in lower:
            inches = parse_display_inches(bullet)
            if inches is not UNKNOWN:
                specs["display.size_in"] = str(inches)
        if "ram" in lower:
            capacity = parse_capacity_gb(bullet)
            if capacity is not UNKNOWN:
                specs["ram.size_gb"] = str(capacity)
            ram_type = re.search(r"\b((?:LP)?DDR\d[A-Z0-9]*)\b", bullet, re.IGNORECASE)
            if ram_type:
                specs["ram.type"] = ram_type.group(1).upper()
        if "ssd" in lower or "hdd" in lower:
            capacity = parse_capacity_gb(bullet)
            if capacity is not UNKNOWN:
                specs["storage.size_gb"] = str(capacity)
        if "operating system" in lower:
            specs["os"] = re.sub(r"\s*Operating System\s*", "", bullet, flags=re.IGNORECASE).strip()
        if "processor" in lower:
            cpu = normalize_cpu_model(bullet)
            if cpu is not UNKNOWN:
                specs["cpu.model"] = str(cpu)
    return specs


# ------------------------------------------------------------------- pricing


def resolve_pdp_price(html: str) -> tuple[Money | Unknown, Money | Unknown, str]:
    """Resolve MOP and MRP from a Flipkart PDP, rejecting the five decoys.

    The live page carries six monetary figures. Each candidate figure is judged by
    the words *around it*: any adjacency to a marker in :data:`NON_MOP_MARKERS`
    disqualifies it (M0 §11.2 rule 2). What remains must reconcile with the stated
    discount percentage, or the result is MOP_UNRESOLVED.
    """
    text = visible_text(html)
    discount = re.search(r"(\d{1,2})%", text)
    if discount is None:
        return UNKNOWN, UNKNOWN, "no stated discount; cannot identify MOP positively"
    stated = Decimal(discount.group(1))

    # Positive identification, not elimination. The selling price is the figure that
    # *reconciles* with the struck-through MRP and the stated discount. Filtering by
    # nearby markers alone rejected the real MOP, because Flipkart renders the
    # additive "+₹306 Protect Promise Fee" immediately after it.
    for match in _RUPEE_RE.finditer(text):
        try:
            mop_amount = Decimal(match.group(1).replace(",", ""))
        except (InvalidOperation, ValueError):
            continue
        if mop_amount <= 0:
            continue
        # Markers that *precede* a figure qualify it ("Buy at ₹…", "Pay ₹…").
        lead = text[max(0, match.start() - 24): match.start()].lower()
        if any(marker in lead for marker in NON_MOP_MARKERS):
            continue
        # The MRP is rendered without a rupee symbol, so look for a bare grouped
        # number in the short run before the selling price.
        window = text[max(0, match.start() - 60): match.start()]
        for token in re.findall(r"[\d,]{5,}", window):
            try:
                mrp_amount = Decimal(token.replace(",", ""))
            except (InvalidOperation, ValueError):
                continue
            if mrp_amount <= mop_amount:
                continue
            implied = (mrp_amount - mop_amount) / mrp_amount * 100
            if abs(implied - stated) <= Decimal("1.5"):
                return (
                    Money(amount=mop_amount, currency="INR", raw=f"₹{match.group(1)}"),
                    Money(amount=mrp_amount, currency="INR", raw=f"₹{token}"),
                    f"MOP and MRP reconciled against stated {stated}% off",
                )

    return UNKNOWN, UNKNOWN, f"no figure reconciled with the stated {stated}% off"


# ----------------------------------------------------------------- extractor


def extract_flipkart_pdp(
    capture: RawCapture,
    *,
    position: int = 0,
    query: str = "",
    query_strategy: str = "",
    card_bullets: Sequence[str] = (),
    expansion: SpecExpansionReport | None = None,
) -> CandidateProduct:
    """Build a platform-neutral candidate from a stored Flipkart PDP capture."""
    html = capture.html
    title = extract_title(html)
    title_text = str(title) if title is not UNKNOWN else ""

    specs = parse_card_summary(card_bullets)
    specs.update(parse_title_spec_block(title_text))   # title block is the stronger source
    for key, value in parse_card_summary(card_bullets).items():
        specs.setdefault(key, value)

    pid, _item = extract_ids(capture.final_url, html)
    model, availability = extract_model_number(html)
    mop, mrp, price_note = resolve_pdp_price(html)
    breadcrumb = extract_breadcrumb(html)

    notes = [f"price: {price_note}"]
    if expansion is not None:
        notes.append(f"specs: {expansion.detail}")
        if not expansion.full_specs_visible:
            notes.append("specification evidence is INCOMPLETE: no structured table rendered")

    return CandidateProduct(
        discovery=DiscoveryContext(
            platform=PLATFORM,
            source_url=capture.requested_url,
            final_url=capture.final_url,
            query=query,
            query_strategy=query_strategy,
            position=position,
            capture_id=capture.capture_id,
        ),
        title=title,
        brand=_brand_from(title_text, breadcrumb),
        model_number=model,
        model_series=extract_model_series(title_text),
        platform_product_id=pid,
        url_derived_id=pid,
        category_path=breadcrumb,
        specs=specs,
        spec_text=" | ".join(card_bullets),
        condition=ListingCondition.UNKNOWN,
        model_availability=availability,
        mop=mop,
        mrp=mrp,
        notes=tuple(notes),
    )


def _brand_from(title: str, breadcrumb: Sequence[str]) -> str | Unknown:
    from .query_builder import BRANDS

    haystack = f"{title} {' '.join(breadcrumb)}".upper()
    for brand in BRANDS:
        if re.search(rf"\b{re.escape(brand)}\b", haystack):
            return brand if brand in ("HP", "MSI", "ASUS", "LG") else brand.title()
    return UNKNOWN


def category_is_accessory(breadcrumb: Sequence[str]) -> bool:
    trail = " ".join(breadcrumb).lower()
    return any(token in trail for token in _ACCESSORY_CATEGORY_TOKENS)


__all__ = [
    "NON_MOP_MARKERS",
    "PLATFORM",
    "SpecExpansionReport",
    "category_is_accessory",
    "extract_breadcrumb",
    "extract_flipkart_pdp",
    "extract_ids",
    "extract_model_number",
    "extract_model_series",
    "extract_title",
    "parse_card_summary",
    "parse_title_spec_block",
    "resolve_pdp_price",
    "visible_text",
]
