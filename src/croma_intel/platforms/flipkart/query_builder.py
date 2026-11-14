"""Deterministic Flipkart query construction from a Croma product name.

Why this exists
---------------
M0 F2 established that searching Flipkart with a manufacturer model code is *worse
than useless*: ``83S000FYIN`` returned 2,135 results ranked #1 by "Softsens Baby
Cream". There is no zero-result signal — the failure mode is a large, confident,
wrong result set. So Flipkart discovery leads with a **product-name query**, and the
model code is never the primary query.

Equally, the full Croma title cannot be used verbatim. It is a 200-character
marketing string carrying colour, weight, bundled software and panel adjectives:

    ASUS TUF A15 AMD Ryzen 7 170 Gaming Laptop (16GB, 512GB SSD, Windows 11 Home,
    4GB Graphics, 15.6 inch 144 Hz Full HD IPS Display, NVIDIA GeForce RTX 3050,
    MS Office Home 2024, Graphite Black, 2.30 KG)

The target is the identity-bearing subset, in a stable order:

    ASUS TUF A15 AMD Ryzen 7 Gaming Laptop 16GB 512GB SSD RTX 3050

Neither too long (the full title matches nothing) nor too short (``ASUS TUF A15``
returns every variant in the series). Both failure directions are tested.

**No LLM.** This is a pure function over text plus, optionally, the canonical
specifications M2 already extracted. Same input, same query, forever.

Stdlib only. Pure. No I/O, no network.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...domain.product import UNKNOWN, Unknown

# --------------------------------------------------------------- vocabularies

#: Laptop brands sold on both Croma and Flipkart. A closed list is deliberate: an
#: open-ended "first token is the brand" rule would treat a marketing prefix such as
#: "New" or "2024" as a brand.
BRANDS: tuple[str, ...] = (
    "ASUS", "LENOVO", "HP", "DELL", "ACER", "MSI", "APPLE", "SAMSUNG", "LG",
    "INFINIX", "REDMI", "REALME", "GIGABYTE", "ALIENWARE", "HONOR", "VAIO",
    "MICROSOFT", "TOSHIBA", "FUJITSU", "CHUWI", "AVITA", "ULTIMUS", "PRIMEBOOK",
    "ZEBRONICS", "WINGS", "THOMSON", "AXL", "COLORFUL", "TECNO", "ITEL",
)

#: Product types, longest first so "gaming laptop" wins over "laptop".
PRODUCT_TYPES: tuple[str, ...] = (
    "gaming laptop", "thin and light laptop", "business laptop", "2 in 1 laptop",
    "creator laptop", "chromebook", "notebook", "laptop",
)

#: CPU family phrases, longest first.
CPU_FAMILIES: tuple[str, ...] = (
    "core ultra 9", "core ultra 7", "core ultra 5",
    "ryzen ai 9", "ryzen ai 7", "ryzen ai 5",
    "ryzen 9", "ryzen 7", "ryzen 5", "ryzen 3",
    "core i9", "core i7", "core i5", "core i3",
    "snapdragon x elite", "snapdragon x plus",
    "m4 max", "m4 pro", "m3 max", "m3 pro", "m2 max", "m2 pro",
    "m4", "m3", "m2", "m1",
    "celeron", "pentium", "athlon",
)

#: Tokens that end the series run. Reaching one means the model name is over.
_SERIES_STOP_WORDS: frozenset[str] = frozenset(
    {"amd", "intel", "apple", "qualcomm", "mediatek", "with", "and", "gaming",
     "laptop", "notebook", "chromebook", "thin", "business", "creator", "core",
     "ryzen", "snapdragon", "celeron", "pentium", "athlon", "processor"}
)

#: Marketing noise removed from the query. Each entry is a phrase M0 or M2 observed
#: in a real Croma title and which adds no discriminating power on Flipkart.
NOISE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bms\s*office(?:\s+home)?(?:\s+\d{4})?\b", "bundled software"),
    (r"\bmicrosoft\s+office(?:\s+home)?(?:\s+\d{4})?\b", "bundled software"),
    (r"\bwindows\s*\d*\s*(?:home|pro|professional)?\b", "operating system marketing"),
    (r"\bmac\s*os\b", "operating system marketing"),
    (r"\b\d+(?:\.\d+)?\s*kg\b", "weight"),
    (r"\bfull\s*hd\b", "panel adjective"),
    (r"\bfhd\b", "panel adjective"),
    (r"\bwuxga\b|\bwqxga\b|\bqhd\b|\buhd\b", "panel adjective"),
    (r"\bips\b", "panel adjective"),
    (r"\banti[\s-]?glare\b", "panel adjective"),
    (r"\bdisplay\b", "generic word"),
    (r"\bgraphics\b", "generic word"),
    (r"\bwith\b", "generic word"),
    (r"\binch(?:es)?\b", "unit word"),
    (r"\bwarranty\b", "warranty text"),
    (r"\b\d+\s*(?:year|yr|month)s?\b", "warranty text"),
    (r"\bfree\b|\bcombo\b|\boffer\b", "promotional text"),
    (r"\b\d+(?:\.\d+)?\s*(?:star|stars)\b", "rating text"),
    (r"\b\d+\s*reviews?\b", "review text"),
)

#: Colour words. Removed because Flipkart lists colour variants as separate
#: products, so including it narrows the result set to one arbitrary variant.
COLOUR_WORDS: tuple[str, ...] = (
    "graphite black", "luna grey", "luna gray", "arctic grey", "arctic gray",
    "cloud grey", "cloud gray", "storm grey", "storm gray", "mecha grey",
    "jaeger gray", "eclipse gray", "off black", "midnight blue", "abyss blue",
    "cobalt blue", "platinum silver", "moonlight white", "pure white",
    "starlight", "midnight", "space grey", "space gray", "silver", "black",
    "white", "grey", "gray", "blue", "green", "red", "gold", "pink", "purple",
)

# ------------------------------------------------------------------- patterns

_RAM_RE = re.compile(r"\b(\d{1,3})\s*GB\b", re.IGNORECASE)
_STORAGE_RE = re.compile(r"\b(\d{3,4})\s*(GB|TB)\s*(?:SSD|HDD|EMMC)?\b", re.IGNORECASE)
_TB_STORAGE_RE = re.compile(r"\b(\d)\s*TB\b", re.IGNORECASE)
#: The variant suffix must be *immediately adjacent* to the digits. Allowing
#: whitespace before it made "RTX 3050 MS Office" parse as "RTX 3050MS".
_GPU_RE = re.compile(r"\b(RTX|GTX|RX|ARC)\s*(\d{3,4})([A-Z]{0,3})\b", re.IGNORECASE)
_VRAM_RE = re.compile(r"\b(\d{1,2})\s*GB\s+(?:GDDR\d?\s+)?GRAPHICS\b", re.IGNORECASE)
_DISPLAY_RE = re.compile(r"\b(\d{2}(?:\.\d)?)\s*(?:inch|inches|\")", re.IGNORECASE)
_REFRESH_RE = re.compile(r"\b(\d{2,3})\s*Hz\b", re.IGNORECASE)
#: A CPU model token: four or five digits, optionally 1-3 trailing letters.
#: Deliberately excludes short numbers such as the "170" in "AMD Ryzen 7 170",
#: which is a tier number rather than a model and adds noise on Flipkart.
_CPU_MODEL_RE = re.compile(r"\b(\d{4,5}[A-Z]{0,3})\b")

_WS_RE = re.compile(r"\s+")


# ----------------------------------------------------------------- data model


@dataclass(frozen=True)
class FlipkartQueryPlan:
    """A deterministic search plan, persisted into the discovery artifact.

    ``omitted_terms`` is not decoration: when discovery returns nothing useful, the
    first question is always "what did we throw away?", and the answer must be in the
    artifact rather than re-derived from memory.
    """

    primary_query: str
    fallback_queries: tuple[str, ...] = ()
    extracted_terms: Mapping[str, str] = field(default_factory=dict)
    omitted_terms: tuple[tuple[str, str], ...] = ()
    reference_sku: str = ""
    reference_model: str | Unknown = UNKNOWN
    source_title: str = ""
    strategy: str = "product_name_v1"

    @property
    def all_queries(self) -> tuple[str, ...]:
        return (self.primary_query, *self.fallback_queries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_query": self.primary_query,
            "fallback_queries": list(self.fallback_queries),
            "extracted_terms": dict(self.extracted_terms),
            "omitted_terms": [{"term": t, "reason": r} for t, r in self.omitted_terms],
            "reference_sku": self.reference_sku,
            "reference_model": (
                "UNKNOWN" if self.reference_model is UNKNOWN else str(self.reference_model)
            ),
            "source_title": self.source_title,
            "strategy": self.strategy,
        }


# ------------------------------------------------------------------ extractors


def _normalize(text: str) -> str:
    """Flatten punctuation to spaces without merging adjacent tokens."""
    flattened = re.sub(r"[(),/|\[\]{}:;]+", " ", text or "")
    flattened = flattened.replace("+", " ")
    return _WS_RE.sub(" ", flattened).strip()


def _find_brand(text: str) -> str | Unknown:
    upper = text.upper()
    for brand in BRANDS:
        if re.search(rf"\b{re.escape(brand)}\b", upper):
            return brand.title() if brand not in ("HP", "MSI", "ASUS", "LG") else brand
    return UNKNOWN


def _find_phrase(text: str, phrases: Sequence[str]) -> str | Unknown:
    lower = text.lower()
    for phrase in phrases:
        if re.search(rf"\b{re.escape(phrase)}\b", lower):
            return phrase
    return UNKNOWN


def _find_series(text: str, brand: str | Unknown) -> str | Unknown:
    """Tokens between the brand and the first stop word — the model name.

    ``ASUS TUF A15 AMD Ryzen 7 …`` yields ``TUF A15``; ``Lenovo LOQ 15ARP10E AMD …``
    yields ``LOQ 15ARP10E``. Capped at three tokens so a title lacking a stop word
    cannot swallow the whole string.
    """
    if brand is UNKNOWN:
        return UNKNOWN
    match = re.search(rf"\b{re.escape(str(brand))}\b", text, re.IGNORECASE)
    if not match:
        return UNKNOWN
    tokens = text[match.end():].split()
    series: list[str] = []
    for token in tokens[:6]:
        bare = token.strip(",.()").lower()
        if not bare or bare in _SERIES_STOP_WORDS:
            break
        if _RAM_RE.fullmatch(token) or _GPU_RE.fullmatch(token):
            break
        series.append(token.strip(",.()"))
        if len(series) >= 3:
            break
    return " ".join(series) if series else UNKNOWN


def _find_ram_gb(text: str) -> str | Unknown:
    """RAM capacity, distinguished from storage and VRAM.

    Disambiguation is positional and unit-based rather than guessy: storage figures
    are >= 128 GB or carry a TB unit, VRAM sits next to the word "Graphics", and what
    remains in the plausible 2-96 GB band is system memory.
    """
    vram = {m.group(1) for m in _VRAM_RE.finditer(text)}
    qualifiers = {"ssd", "hdd", "emmc", "ufs", "graphics", "gddr", "gddr6", "vram"}
    for match in _RAM_RE.finditer(text):
        value = int(match.group(1))
        # Only the *immediately* following word qualifies the figure. A wider window
        # reached the next specification: in "16GB 512GB SSD" it saw "SSD" and
        # discarded the RAM.
        next_word = text[match.end():].lstrip().split(" ")[0].strip(",.()").lower()
        if next_word in qualifiers:
            continue
        if match.group(1) in vram:
            continue
        if 2 <= value <= 96:
            return f"{value}GB"
    return UNKNOWN


def _find_storage(text: str) -> str | Unknown:
    tb = _TB_STORAGE_RE.search(text)
    if tb:
        return f"{tb.group(1)}TB SSD"
    for match in _STORAGE_RE.finditer(text):
        value = int(match.group(1))
        if match.group(2).upper() == "GB" and value >= 128:
            return f"{value}GB SSD"
    return UNKNOWN


def _find_gpu(text: str) -> str | Unknown:
    match = _GPU_RE.search(text)
    if not match:
        return UNKNOWN
    # Suffix preserved: RTX 3050A is a different part from RTX 3050 (M0 §9).
    return f"{match.group(1).upper()} {match.group(2)}{match.group(3).upper()}"


#: CPU vendors, and the families that belong to each. The vendor is part of the
#: searchable identity — "Ryzen 7" alone is ambiguous shorthand, "AMD Ryzen 7" is how
#: both retailers write it.
_CPU_VENDORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AMD", ("ryzen",)),
    ("Intel", ("core i", "core ultra", "celeron", "pentium")),
    ("Apple", ("m1", "m2", "m3", "m4")),
    ("Qualcomm", ("snapdragon",)),
)


def _display_case(family: str) -> str:
    """Title-case a family, preserving the lowercase ``i`` in Core i5/i7/i9."""
    return re.sub(r"\bI([3579])\b", r"i\1", family.title())


def _qualified_cpu_family(text: str, cpu_family: str | Unknown) -> str | Unknown:
    """Prefix the family with its vendor when the title names one."""
    if cpu_family is UNKNOWN:
        return UNKNOWN
    family = str(cpu_family)
    upper = text.upper()
    for vendor, prefixes in _CPU_VENDORS:
        if not any(family.startswith(p) for p in prefixes):
            continue
        if re.search(rf"\b{re.escape(vendor.upper())}\b", upper):
            return f"{vendor} {_display_case(family)}"
        return _display_case(family)
    return _display_case(family)


def _find_cpu_model(text: str, cpu_family: str | Unknown) -> str | Unknown:
    """A four/five-digit CPU model token, if one is present after the family."""
    if cpu_family is UNKNOWN:
        return UNKNOWN
    match = re.search(rf"\b{re.escape(str(cpu_family))}\b", text, re.IGNORECASE)
    window = text[match.end(): match.end() + 20] if match else text
    found = _CPU_MODEL_RE.search(window.upper())
    return found.group(1) if found else UNKNOWN


def _collect_omissions(text: str) -> tuple[tuple[str, str], ...]:
    omitted: list[tuple[str, str]] = []
    lower = text.lower()
    for phrase in COLOUR_WORDS:
        if re.search(rf"\b{re.escape(phrase)}\b", lower):
            omitted.append((phrase, "colour"))
            break
    for pattern, reason in NOISE_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            token = _WS_RE.sub(" ", match.group(0)).strip()
            if token and not any(token.lower() == t.lower() for t, _ in omitted):
                omitted.append((token, reason))
    return tuple(omitted)


# --------------------------------------------------------------------- builder


def build_query_plan(
    title: str,
    *,
    sku: str = "",
    model: str | Unknown = UNKNOWN,
    specs: Mapping[str, str] | None = None,
    include_display: bool = False,
) -> FlipkartQueryPlan:
    """Build a Flipkart search plan from a Croma product name.

    *specs* are the canonical specifications M2 already extracted. When supplied they
    **override** values parsed from the title, on the same principle the extractor
    uses: a structured specification table beats marketing prose. The title remains
    the sole source for brand, series and product type, which no spec table carries.

    The model number is recorded on the plan for traceability but is deliberately
    **never placed in a query** — see F2.
    """
    if not title or not title.strip():
        return FlipkartQueryPlan(
            primary_query="",
            extracted_terms={},
            omitted_terms=(),
            reference_sku=sku,
            reference_model=model,
            source_title=title or "",
            strategy="product_name_v1_empty",
        )

    text = _normalize(title)
    specs = specs or {}

    brand = _find_brand(text)
    series = _find_series(text, brand)
    product_type = _find_phrase(text, PRODUCT_TYPES)
    cpu_family = _qualified_cpu_family(text, _find_phrase(text, CPU_FAMILIES))

    def from_specs(key: str, formatter) -> object:
        raw = specs.get(key)
        return formatter(raw) if raw else UNKNOWN

    ram = from_specs("ram.size_gb", lambda v: f"{int(float(v))}GB")
    if ram is UNKNOWN:
        ram = _find_ram_gb(text)

    storage = from_specs("storage.size_gb", lambda v: _format_storage(int(float(v))))
    if storage is UNKNOWN:
        storage = _find_storage(text)

    gpu = specs.get("gpu.model") or _find_gpu(text)
    cpu_model = specs.get("cpu.model") or _find_cpu_model(text, cpu_family)
    display = specs.get("display.size_in") or _display_from_title(text)
    refresh = specs.get("display.refresh_hz") or _refresh_from_title(text)

    extracted: dict[str, str] = {}
    for key, value in (
        ("brand", brand), ("series", series), ("cpu_family", cpu_family),
        ("cpu_model", cpu_model), ("product_type", product_type), ("ram", ram),
        ("storage", storage), ("gpu", gpu), ("display_size_in", display),
        ("refresh_hz", refresh),
    ):
        if value is not UNKNOWN and value:
            extracted[key] = str(value)

    primary = _assemble(extracted, include_display=include_display)
    plan = FlipkartQueryPlan(
        primary_query=primary,
        fallback_queries=_fallbacks(extracted, primary),
        extracted_terms=extracted,
        omitted_terms=_collect_omissions(title),
        reference_sku=sku,
        reference_model=model,
        source_title=title,
    )
    return plan


def _format_storage(gigabytes: int) -> str:
    if gigabytes >= 1024 and gigabytes % 1024 == 0:
        return f"{gigabytes // 1024}TB SSD"
    return f"{gigabytes}GB SSD"


def _display_from_title(text: str) -> str | Unknown:
    match = _DISPLAY_RE.search(text)
    return match.group(1) if match else UNKNOWN


def _refresh_from_title(text: str) -> str | Unknown:
    match = _REFRESH_RE.search(text)
    return match.group(1) if match else UNKNOWN


def _assemble(terms: Mapping[str, str], *, include_display: bool) -> str:
    """Fixed term order, so the same product always yields the same query."""
    parts: list[str] = []
    for key in ("brand", "series", "cpu_family"):
        if key in terms:
            parts.append(terms[key])
    if "product_type" in terms:
        parts.append(terms["product_type"].title())
    for key in ("ram", "storage", "gpu"):
        if key in terms:
            parts.append(terms[key])
    if include_display and "display_size_in" in terms:
        parts.append(f"{terms['display_size_in']} inch")
    return _WS_RE.sub(" ", " ".join(parts)).strip()


def _fallbacks(terms: Mapping[str, str], primary: str) -> tuple[str, ...]:
    """Progressively broader queries, tried only if the primary yields nothing.

    Each drops the least identity-bearing term still present, so the result set
    widens in a predictable order rather than collapsing straight to the series.
    """
    candidates: list[str] = []

    # Display size is not in the primary query: nearly every gaming laptop is 15.6",
    # so it costs a token and buys almost no discrimination. It appears here as the
    # first *narrowing* fallback, for the case where the primary is too broad.
    candidates.append(_assemble(terms, include_display=True))

    core = {k: v for k, v in terms.items() if k in ("brand", "series", "cpu_family", "gpu")}
    candidates.append(_assemble(core, include_display=False))

    identity = {k: v for k, v in terms.items() if k in ("brand", "series", "product_type")}
    candidates.append(_assemble(identity, include_display=False))

    seen = {primary}
    out: list[str] = []
    for query in candidates:
        if query and query not in seen:
            seen.add(query)
            out.append(query)
    return tuple(out)


__all__ = [
    "BRANDS",
    "COLOUR_WORDS",
    "CPU_FAMILIES",
    "NOISE_PATTERNS",
    "PRODUCT_TYPES",
    "FlipkartQueryPlan",
    "build_query_plan",
]
