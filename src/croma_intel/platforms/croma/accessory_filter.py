"""Deprecated location — re-exports the shared accessory filter.

Scope change S3 (M3, approved 2026-08-26) promoted this module to
``croma_intel.platforms.accessory_filter``. The rejection rules were never
Croma-specific: bags, sleeves and chargers collide with laptops on every platform,
and keeping a per-platform copy would guarantee the copies drift apart.

This shim exists so M1 code — ``platforms/croma/result_identifier.py`` imports it as
``from . import accessory_filter`` — keeps working untouched. There is exactly one
implementation; this module is a name for it, not a second copy.

New code should import from ``croma_intel.platforms.accessory_filter``.
"""

from __future__ import annotations

from ..accessory_filter import (  # re-exported for backward compatibility; see __all__
    ACCESSORY_TERMS,
    COMPATIBILITY_PATTERNS,
    HEAD_NOUN_WINDOW_TOKENS,
    MIN_ANCHORS_FOR_FEATURE_GUARD,
    PRODUCT_ANCHORS,
    AccessoryVerdict,
    classify,
    is_accessory,
)

__all__ = [
    "ACCESSORY_TERMS",
    "COMPATIBILITY_PATTERNS",
    "HEAD_NOUN_WINDOW_TOKENS",
    "MIN_ANCHORS_FOR_FEATURE_GUARD",
    "PRODUCT_ANCHORS",
    "AccessoryVerdict",
    "classify",
    "is_accessory",
]
