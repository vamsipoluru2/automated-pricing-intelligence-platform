"""Category profiles — what "the same product" means, per product category.

The matching pipeline (gates → model → CRITICAL specs → decision table) is identical
for every category. What differs is *which* evidence decides identity: a laptop is
its CPU, GPU, RAM and storage; a phone is none of those things. A
:class:`CategoryProfile` carries exactly the category-dependent inputs and nothing
else, so the rules in docs/MATCHING_RULES.md stay single and category-neutral.

Admission rule
--------------
A profile may only be registered once a **real Croma PDP capture** of that category
exists in ``tests/fixtures``. The laptop CRITICAL set was chosen from Croma's
111-feature table (M2); a set chosen without a capture is a guess, and a guessed
CRITICAL set silently decides what counts as a match (rule 10). Roadmap order,
approved 2026-09-16: mobiles → tablets → TVs → kitchen appliances → home-care
appliances.

Resolution is leaf-first
------------------------
Croma's L0 breadcrumb for a laptop is ``Computers & Tablets``. Searching the joined
path for a token would file every laptop under tablets the moment a tablet profile
exists. :func:`resolve_category` therefore walks breadcrumbs from the most specific
to the least and decides at the first crumb that names a category. A crumb naming
two categories is ambiguous and resolves to nothing — UNKNOWN, never a guess.

Stdlib only. Pure. No I/O.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ..domain.candidate import CandidateProduct
from ..domain.product import is_known
from ..normalization.spec_value import CRITICAL_SPECS, SpecKey
from ..platforms.accessory_filter import PRODUCT_ANCHORS
from .spec_compare import MIN_KNOWN_CRITICAL_SPECS

#: Upper bound on a CRITICAL set. ``order_score`` adds one point per matched spec, and
#: the smallest gap between classification ranks is 10 (CONFLICT 20, INSUFFICIENT 10,
#: DIFFERENT 0); a set of 10 or more could let a well-corroborated candidate out-score
#: its way into the class above. Enforced at profile construction and by test.
MAX_CRITICAL_SPECS: int = 9


class ProductCategory(str, Enum):
    """Categories with a registered profile. Grows one member per admitted profile."""

    LAPTOP = "LAPTOP"


@dataclass(frozen=True)
class CategoryProfile:
    """The category-dependent inputs to matching. Nothing else belongs here."""

    category: ProductCategory
    #: Breadcrumb tokens naming this category, matched on word starts
    #: (``laptop`` hits ``Gaming Laptops``).
    category_tokens: tuple[str, ...]
    #: Specs that decide identity. Any mismatch → a different product.
    critical_specs: tuple[SpecKey, ...]
    #: CRITICAL specs that must be known on both sides for a spec verdict.
    min_known_critical_specs: int
    #: Title tokens anchoring a listing as the product itself, not an accessory for it.
    product_anchors: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.category_tokens:
            raise ValueError(f"{self.category.value}: category_tokens must not be empty")
        if not self.critical_specs:
            raise ValueError(f"{self.category.value}: critical_specs must not be empty")
        if len(set(self.critical_specs)) != len(self.critical_specs):
            raise ValueError(f"{self.category.value}: duplicate CRITICAL spec")
        if len(self.critical_specs) > MAX_CRITICAL_SPECS:
            raise ValueError(
                f"{self.category.value}: {len(self.critical_specs)} CRITICAL specs exceeds "
                f"{MAX_CRITICAL_SPECS}; order_score could cross a classification boundary"
            )
        if not 1 <= self.min_known_critical_specs <= len(self.critical_specs):
            raise ValueError(
                f"{self.category.value}: min_known_critical_specs must be within "
                f"1..{len(self.critical_specs)}"
            )


LAPTOP = CategoryProfile(
    category=ProductCategory.LAPTOP,
    category_tokens=("laptop", "notebook", "computer"),
    critical_specs=CRITICAL_SPECS,
    min_known_critical_specs=MIN_KNOWN_CRITICAL_SPECS,
    product_anchors=PRODUCT_ANCHORS,
)

#: Every admitted profile. Order is irrelevant to resolution — ambiguity resolves to
#: None rather than to whichever profile happens to be listed first.
PROFILES: tuple[CategoryProfile, ...] = (LAPTOP,)

_BY_CATEGORY: dict[ProductCategory, CategoryProfile] = {p.category: p for p in PROFILES}


def profile_for(category: ProductCategory) -> CategoryProfile:
    return _BY_CATEGORY[category]


def _crumb_hits(crumb: str, profile: CategoryProfile) -> bool:
    text = crumb.lower()
    return any(re.search(rf"\b{re.escape(token)}", text) for token in profile.category_tokens)


def resolve_category(category_path: Sequence[str]) -> CategoryProfile | None:
    """The profile named by the most specific breadcrumb, or None.

    None means *unresolved* — no crumb names a category, or the deciding crumb names
    more than one. Callers must treat None as missing evidence, never as a default.
    """
    for crumb in reversed(tuple(category_path)):
        hits = [p for p in PROFILES if _crumb_hits(crumb, p)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    return None


def category_crumbs(product: CandidateProduct) -> tuple[str, ...]:
    """The product's breadcrumbs that are categories, i.e. excluding its own title.

    Croma's breadcrumb trail ends with the full product name (observed on the 323360
    capture). A title is not a category, and reading it leaf-first would let prose
    such as "... tablet mode" decide what the product is.
    """
    title = str(product.title).strip().casefold() if is_known(product.title) else ""
    return tuple(c for c in product.category_path if c.strip().casefold() != title)


def resolve_product_category(product: CandidateProduct) -> CategoryProfile | None:
    """:func:`resolve_category` over :func:`category_crumbs`."""
    return resolve_category(category_crumbs(product))


def mentions_category(category_path: Sequence[str], profile: CategoryProfile) -> bool:
    """True when any breadcrumb names *profile*'s category."""
    return any(_crumb_hits(crumb, profile) for crumb in category_path)


__all__ = [
    "LAPTOP",
    "MAX_CRITICAL_SPECS",
    "PROFILES",
    "CategoryProfile",
    "ProductCategory",
    "category_crumbs",
    "mentions_category",
    "profile_for",
    "resolve_category",
    "resolve_product_category",
]
