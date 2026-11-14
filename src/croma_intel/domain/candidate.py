"""Platform-neutral competitor record and its discovery provenance.

M2's :class:`ExtractedProduct` is Croma-shaped — its source paths name Croma
reducers. Matching needs a record both sides project into, so this module defines
:class:`CandidateProduct` and an adapter from the M2 type. **M2 is not modified.**

Discovery provenance is not optional decoration. A candidate that cannot say which
query surfaced it, at what position, from which platform, and at what URL is not
auditable — and an unauditable match is indistinguishable from a guess. Every field
in :class:`DiscoveryContext` exists because a specific M0 finding demands it:

* ``query`` and ``position`` — F2. Flipkart returned 2,135 results for an MTM with
  baby cream at #1. Without the query and rank, a bad candidate is untraceable to the
  query strategy that produced it.
* ``source_url`` and ``final_url`` — F10. A Vijay Sales URL for product ``230914``
  served SKU ``243423``. Requested and served must both be recorded to detect it.
* ``capture_id`` — rule 1. Every candidate traces back to an immutable artifact.

Stdlib only. Pure. No I/O, no network.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .matching import ListingCondition, ModelAvailability
from .product import UNKNOWN, ExtractedProduct, Money, Unknown, is_known

# ------------------------------------------------------------------ provenance


@dataclass(frozen=True)
class DiscoveryContext:
    """How this candidate came to be considered. Required for every candidate."""

    platform: str
    source_url: str = ""
    final_url: str = ""
    query: str = ""
    query_strategy: str = ""
    position: int = 0
    capture_id: str = ""
    pincode: str | Unknown = UNKNOWN

    @property
    def redirected(self) -> bool | Unknown:
        """F10: requested and served can disagree. UNKNOWN when only one is known."""
        if not self.source_url or not self.final_url:
            return UNKNOWN
        return self.source_url != self.final_url

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "source_url": self.source_url,
            "final_url": self.final_url,
            "query": self.query,
            "query_strategy": self.query_strategy,
            "position": self.position,
            "capture_id": self.capture_id,
            "pincode": "UNKNOWN" if self.pincode is UNKNOWN else self.pincode,
            "redirected": "UNKNOWN" if self.redirected is UNKNOWN else self.redirected,
        }


# ------------------------------------------------------------------- candidate


@dataclass(frozen=True)
class CandidateProduct:
    """One product, from any platform, in a form the matcher can compare.

    ``specs`` is a mapping of canonical :class:`SpecKey` *values* (dotted strings) to
    normalized values — flat here on purpose, because comparison is per-canonical-key.
    The grouped structure M2 preserves stays with the source record; this is the
    projection used for identity work.

    Every text field defaults to UNKNOWN rather than to an empty string, so an absent
    value can never be compared as though it were data.
    """

    discovery: DiscoveryContext

    title: str | Unknown = UNKNOWN
    brand: str | Unknown = UNKNOWN
    model_number: str | Unknown = UNKNOWN
    model_series: str | Unknown = UNKNOWN
    platform_product_id: str | Unknown = UNKNOWN
    url_derived_id: str | Unknown = UNKNOWN

    category_path: tuple[str, ...] = ()
    category_ids: tuple[str, ...] = ()

    specs: Mapping[str, str] = field(default_factory=dict)
    spec_text: str = ""          # raw specification blob, for embedded-token scans

    condition: ListingCondition = ListingCondition.UNKNOWN
    model_availability: ModelAvailability = ModelAvailability.UNKNOWN

    mop: Money | Unknown = UNKNOWN
    mrp: Money | Unknown = UNKNOWN

    is_bundle: bool | Unknown = UNKNOWN
    bundle_items: tuple[str, ...] = ()

    notes: tuple[str, ...] = ()

    @property
    def platform(self) -> str:
        return self.discovery.platform

    @property
    def searchable_text(self) -> str:
        """Title plus specification text — the haystack for boundary-safe scans.

        M0 §5.4 found the MTM at varying positions in competitor titles, and M0 §3.4
        left open whether Flipkart hides it in the spec table. Scanning both is the
        only position-independent approach.
        """
        parts = [str(self.title) if is_known(self.title) else "", self.spec_text]
        return " ".join(p for p in parts if p)

    def spec(self, key: str) -> str | Unknown:
        """Normalized value for a canonical spec key, or UNKNOWN."""
        value = self.specs.get(key)
        if value is None:
            return UNKNOWN
        text = str(value).strip()
        return text if text else UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        def plain(value: Any) -> Any:
            if value is UNKNOWN:
                return "UNKNOWN"
            if isinstance(value, Money):
                return value.to_dict()
            if hasattr(value, "value") and isinstance(value, (ListingCondition, ModelAvailability)):
                return value.value
            return value

        return {
            "discovery": self.discovery.to_dict(),
            "title": plain(self.title),
            "brand": plain(self.brand),
            "model_number": plain(self.model_number),
            "model_series": plain(self.model_series),
            "platform_product_id": plain(self.platform_product_id),
            "url_derived_id": plain(self.url_derived_id),
            "category_path": list(self.category_path),
            "category_ids": list(self.category_ids),
            "specs": dict(self.specs),
            "condition": self.condition.value,
            "model_availability": self.model_availability.value,
            "mop": plain(self.mop),
            "mrp": plain(self.mrp),
            "is_bundle": plain(self.is_bundle),
            "bundle_items": list(self.bundle_items),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------- adapter

#: Croma classification labels → canonical spec keys. Croma pre-splits its
#: specification table, so this is a rename rather than a parse — the compound-row
#: splitter in ``normalization.spec_value`` exists for platforms that do not.
CROMA_SPEC_LABELS: tuple[tuple[str, str], ...] = (
    ("Processor Variant", "cpu.model"),
    ("Processor Brand", "cpu.brand"),
    ("Processor Type", "cpu.family"),
    ("RAM", "ram.size_gb"),
    ("Type of RAM", "ram.type"),
    ("RAM Form Factor", "ram.form_factor"),
    ("SSD Capacity", "storage.size_gb"),
    ("Storage Type", "storage.type"),
    ("GPU Model", "gpu.model"),
    ("GPU Brand", "gpu.brand"),
    ("Dedicated Graphics Memory", "gpu.vram_gb"),
    ("Display Size (In Inches)", "display.size_in"),
    ("Refresh Rate", "display.refresh_hz"),
    ("Screen Resolution", "display.resolution"),
    ("Display Type", "display.panel"),
    ("Operating System", "os"),
)

#: Spec keys whose Croma value is a capacity string needing reduction to whole GB.
_CAPACITY_KEYS = frozenset({"ram.size_gb", "storage.size_gb", "gpu.vram_gb"})


def candidate_from_croma(product: ExtractedProduct) -> CandidateProduct:
    """Project an M2 :class:`ExtractedProduct` into the neutral candidate form.

    Read-only over the M2 record. The reference product and every competitor then
    enter the matcher through the same door, which is what keeps the comparison
    symmetric and prevents Croma-shaped assumptions leaking into platform adapters.
    """
    from ..normalization.spec_value import (
        normalize_cpu_model,
        normalize_gpu_model,
        parse_capacity_gb,
        parse_display_inches,
        parse_refresh_hz,
    )

    table = product.specifications
    specs: dict[str, str] = {}
    for label, key in CROMA_SPEC_LABELS:
        raw = table.value_of(label)
        if raw is UNKNOWN:
            continue
        text = str(raw)
        if key in _CAPACITY_KEYS:
            capacity = parse_capacity_gb(text)
            if capacity is not UNKNOWN:
                specs[key] = str(capacity)
            continue
        if key == "display.size_in":
            inches = parse_display_inches(text)
            if inches is not UNKNOWN:
                specs[key] = str(inches)
            continue
        if key == "display.refresh_hz":
            hz = parse_refresh_hz(text)
            if hz is not UNKNOWN:
                specs[key] = str(hz)
            continue
        if key == "gpu.model":
            gpu = normalize_gpu_model(text)
            if gpu is not UNKNOWN:
                specs[key] = str(gpu)
            continue
        if key == "cpu.model":
            cpu = normalize_cpu_model(text)
            if cpu is not UNKNOWN:
                specs[key] = str(cpu)
            continue
        specs[key] = text

    identity = product.identity
    category = identity.category

    def value_of(sourced: Any) -> Any:
        return sourced.value if sourced.known else UNKNOWN

    return CandidateProduct(
        discovery=DiscoveryContext(
            platform=identity.platform,
            source_url=product.provenance.final_url,
            final_url=product.provenance.final_url,
            query=str(value_of(identity.sku)) if identity.sku.known else "",
            query_strategy="reference",
            position=0,
            capture_id=product.provenance.capture_id,
            pincode=value_of(product.provenance.pincode),
        ),
        title=value_of(identity.name),
        brand=value_of(identity.brand),
        model_number=value_of(identity.model_number),
        model_series=value_of(identity.model_series),
        platform_product_id=value_of(identity.sku),
        url_derived_id=value_of(identity.sku),
        category_path=tuple(c.name for c in category.breadcrumbs),
        category_ids=tuple(
            str(f.value) for f in (category.l0, category.l1, category.l2) if f.known
        ),
        specs=specs,
        spec_text=" ".join(
            f"{feature.name}: {' '.join(feature.values)}"
            for _group, feature in table.iter_features()
        ),
        # Croma sells new retail stock; no condition qualifier has been observed on a
        # Croma PDP. Stated as an assumption rather than a finding — see
        # docs/MATCHING_RULES.md §Assumptions.
        condition=ListingCondition.NEW,
        model_availability=ModelAvailability.ALWAYS,
        mop=value_of(product.pricing.mop),
        mrp=value_of(product.pricing.mrp),
        is_bundle=UNKNOWN,
    )


__all__ = [
    "CROMA_SPEC_LABELS",
    "CandidateProduct",
    "DiscoveryContext",
    "candidate_from_croma",
]
