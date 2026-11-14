"""Extracted product models — the output of the extraction layer.

Pure, stdlib-only, no I/O, no network. Everything here is a value object derived
from an immutable capture, so the whole layer is replayable offline.

Two architectural rules shape this module:

* **Rule 2 — UNKNOWN is a first-class value.** :data:`UNKNOWN` is a singleton that
  is deliberately *not* ``None``. Truth-testing it raises, so the two idioms that
  silently destroy an unknown — ``if value:`` and ``value or default`` — fail loudly
  at the point of misuse instead of producing a confident wrong answer downstream.
* **Rule 6 — a price is typed and sourced.** :class:`PriceQuote` cannot be built
  from "a number on the page"; it carries the exact source path a figure came from
  and fails closed to :data:`PriceStatus.MOP_UNRESOLVED`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Generic, TypeVar

# --------------------------------------------------------------------- UNKNOWN


class Unknown:
    """The absence of information — a value, not a hole.

    ``None`` is unsuitable because it is the default default: it is what an absent
    dict key, an unset attribute and a failed lookup all return, so a genuine
    "we looked and could not tell" becomes indistinguishable from "nobody asked".

    Truth-testing raises :class:`TypeError` on purpose. Rule 2 says UNKNOWN must
    never be silently coerced or defaulted, and the coercions that would do that are
    exactly ``if value:`` and ``value or fallback``. Making them raise turns a silent
    wrong answer into a stack trace at the offending line.
    """

    __slots__ = ()
    _instance: Unknown | None = None

    # Annotated as ``Unknown`` rather than ``Self``: ``typing.Self`` arrives in 3.11
    # and the offline core targets 3.10 with no third-party imports. The class is
    # final by construction — it is a singleton — so the two are equivalent here.
    def __new__(cls) -> Unknown:  # noqa: PYI034
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNKNOWN"

    def __str__(self) -> str:
        return "UNKNOWN"

    def __bool__(self) -> bool:
        raise TypeError(
            "UNKNOWN has no truth value (rule 2). Use is_known(x) / x is UNKNOWN "
            "instead of `if x:` or `x or default`."
        )

    def __eq__(self, other: object) -> bool:
        return other is self

    def __ne__(self, other: object) -> bool:
        return other is not self

    def __hash__(self) -> int:
        return hash("<croma_intel.UNKNOWN>")

    def __copy__(self) -> Unknown:
        return self

    def __deepcopy__(self, memo: dict) -> Unknown:
        return self

    def __reduce__(self) -> tuple:
        return (Unknown, ())


UNKNOWN = Unknown()

T = TypeVar("T")


def is_known(value: object) -> bool:
    """``True`` when *value* carries information. The sanctioned truth test."""
    return value is not UNKNOWN


# ------------------------------------------------------------------ provenance


class SourceKind(str, Enum):
    """Where a value physically came from.

    Recorded per field rather than per record: a product assembled from the state
    blob, the rendered DOM and the capture manifest has three different evidentiary
    standings, and collapsing them would hide that.
    """

    INITIAL_DATA = "initial_data"        # window.__INITIAL_DATA__ reducer payload
    RENDERED_DOM = "rendered_dom"        # markup as the browser rendered it
    CAPTURE_MANIFEST = "capture_manifest"  # recorded by the capture stage itself
    ABSENT = "absent"                    # looked for, not present


@dataclass(frozen=True)
class Sourced(Generic[T]):
    """A value bound to the exact path it was read from.

    ``path`` is a dotted accessor into the source document, e.g.
    ``pdpPriceReducer.pdpPriceData.sellingPrice.value``. It exists so that any
    figure in the output can be traced back to one location in one artifact.
    """

    value: T | Unknown
    path: str
    kind: SourceKind = SourceKind.INITIAL_DATA
    note: str = ""

    @property
    def known(self) -> bool:
        return self.value is not UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": _plain(self.value),
            "path": self.path,
            "kind": self.kind.value,
            "note": self.note,
        }

    @staticmethod
    def missing(path: str, note: str = "") -> Sourced[Any]:
        """An explicitly-absent field. Still carries the path that was searched."""
        return Sourced(UNKNOWN, path, SourceKind.ABSENT, note)


def _plain(value: Any) -> Any:
    """JSON-safe rendering. UNKNOWN survives as the string ``"UNKNOWN"``."""
    if value is UNKNOWN:
        return "UNKNOWN"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


# ----------------------------------------------------------------------- money


@dataclass(frozen=True)
class Money:
    """An exact monetary amount.

    ``Decimal`` rather than ``float``: prices are decimal quantities and float
    rounding on a 6-figure rupee value is a real, silent corruption.
    ``raw`` keeps the source string so the parse can be audited.
    """

    amount: Decimal
    currency: str = "INR"
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"amount": str(self.amount), "currency": self.currency, "raw": self.raw}

    @staticmethod
    def parse(raw: object, currency: str = "INR") -> Money | Unknown:
        """Parse a monetary string. Returns UNKNOWN rather than guessing.

        Accepts the plain decimal strings Croma's price reducer uses
        (``"113990.00"``). Deliberately rejects anything carrying grouping, symbols
        or qualifiers — those come from rendered display text, which is not a
        sanctioned MOP source.
        """
        if raw is UNKNOWN or raw is None:
            return UNKNOWN
        if isinstance(raw, Money):
            return raw
        text = str(raw).strip()
        if not text:
            return UNKNOWN
        try:
            amount = Decimal(text)
        except (InvalidOperation, ValueError):
            return UNKNOWN
        if amount < 0:
            return UNKNOWN
        return Money(amount=amount, currency=currency, raw=text)


class PriceStatus(str, Enum):
    """Outcome of price resolution. Rule 6: fail closed, never approximate."""

    RESOLVED = "RESOLVED"
    MOP_UNRESOLVED = "MOP_UNRESOLVED"


@dataclass(frozen=True)
class PriceQuote:
    """MOP and MRP, each bound to its source path.

    Deliberately has no field for exchange value, bank-offer value, coupon, EMI
    instalment, additive fee or personalised price. Those are not prices of the
    product and M0 §11.2 showed that admitting them is how a wrong MOP gets in.
    Offers are carried separately by :class:`OfferSet`.
    """

    mop: Sourced[Money]
    mrp: Sourced[Money]
    status: PriceStatus
    currency: str = "INR"
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.status is PriceStatus.RESOLVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "mop": self.mop.to_dict(),
            "mrp": self.mrp.to_dict(),
            "status": self.status.value,
            "currency": self.currency,
            "reason": self.reason,
        }


# ------------------------------------------------------------- specifications


@dataclass(frozen=True)
class SpecFeature:
    """One labelled specification row.

    ``values`` is a tuple because Croma genuinely emits multi-valued features
    (``Suitable For`` carries seven). Flattening them to a joined string would
    destroy structure the matcher will need.
    """

    name: str
    values: tuple[str, ...]
    code: str = ""
    display_order: int = 0
    comparable: bool = False
    # Croma carries the unit separately from the value: ``Product Weight`` is the
    # bare number 1.8 and its unit symbol is ``Kg``. Dropping the unit would leave a
    # dimensionless number that a later comparison could read as grams or pounds.
    unit: str = ""
    unit_type: str = ""
    path: str = ""

    @property
    def value(self) -> str | Unknown:
        """The single value, or UNKNOWN when absent or genuinely multi-valued."""
        if len(self.values) == 1:
            return self.values[0]
        return UNKNOWN

    @property
    def value_with_unit(self) -> str | Unknown:
        """The single value and its unit symbol, e.g. ``"1.8 Kg"``."""
        single = self.value
        if single is UNKNOWN:
            return UNKNOWN
        assert isinstance(single, str)
        return f"{single} {self.unit}".strip() if self.unit else single

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "values": list(self.values),
            "code": self.code,
            "display_order": self.display_order,
            "comparable": self.comparable,
            "unit": self.unit,
            "unit_type": self.unit_type,
            "path": self.path,
        }


@dataclass(frozen=True)
class SpecGroup:
    """A named group of features, preserving Croma's own grouping."""

    name: str
    features: tuple[SpecFeature, ...]
    code: str = ""
    display_order: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "code": self.code,
            "display_order": self.display_order,
            "features": [f.to_dict() for f in self.features],
        }


@dataclass(frozen=True)
class SpecificationTable:
    """The complete labelled specification structure.

    Kept as groups-of-features rather than a flat mapping, per the M2 brief. A flat
    ``{name: value}`` dict would lose grouping and silently drop the duplicate
    feature names Croma emits across groups (``Customer Support Email`` appears in
    two groups with different values).
    """

    groups: tuple[SpecGroup, ...] = ()
    path: str = ""
    kind: SourceKind = SourceKind.INITIAL_DATA

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def feature_count(self) -> int:
        return sum(len(g.features) for g in self.groups)

    def iter_features(self) -> Iterable[tuple[SpecGroup, SpecFeature]]:
        for group in self.groups:
            for feature in group.features:
                yield group, feature

    def find(self, name: str) -> SpecFeature | Unknown:
        """First feature whose label matches *name*, case-insensitively.

        Returns UNKNOWN when absent — never a default, never an empty string.
        """
        target = name.strip().casefold()
        for _group, feature in self.iter_features():
            if feature.name.strip().casefold() == target:
                return feature
        return UNKNOWN

    def value_of(self, name: str) -> str | Unknown:
        feature = self.find(name)
        if feature is UNKNOWN:
            return UNKNOWN
        assert isinstance(feature, SpecFeature)
        return feature.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind.value,
            "group_count": self.group_count,
            "feature_count": self.feature_count,
            "groups": [g.to_dict() for g in self.groups],
        }


# ---------------------------------------------------------------------- offers


@dataclass(frozen=True)
class Offer:
    """One promotional offer. Never a price.

    ``bank_code`` and ``bank_name`` are separate *sourced* fields because the
    rendered DOM shows a display name that a client-side script substituted for an
    internal code. Recording only the rendered name would silently overwrite the raw
    value; recording only the code would be a fabrication when the code is not in
    the artifact. Whichever is genuinely present is recorded, with its provenance.
    """

    index: int
    text: Sourced[str]
    bank_name: Sourced[str]
    bank_code: Sourced[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "text": self.text.to_dict(),
            "bank_name": self.bank_name.to_dict(),
            "bank_code": self.bank_code.to_dict(),
        }


@dataclass(frozen=True)
class OfferSet:
    """Offers plus honest accounting of how many were found versus advertised.

    ``declared_count`` is what the page's own header claims; ``extracted_count`` is
    what was actually present in the markup. They can disagree — a lazily-rendered
    carousel materialises only its visible slides — and ``complete`` records that
    disagreement rather than presenting a partial list as the whole.
    """

    offers: tuple[Offer, ...] = ()
    declared_count: int | Unknown = UNKNOWN
    kind: SourceKind = SourceKind.RENDERED_DOM
    note: str = ""

    @property
    def extracted_count(self) -> int:
        return len(self.offers)

    @property
    def complete(self) -> bool | Unknown:
        if self.declared_count is UNKNOWN:
            return UNKNOWN
        assert isinstance(self.declared_count, int)
        return self.declared_count == self.extracted_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "offers": [o.to_dict() for o in self.offers],
            "declared_count": _plain(self.declared_count),
            "extracted_count": self.extracted_count,
            "complete": _plain(self.complete),
            "kind": self.kind.value,
            "note": self.note,
        }


# -------------------------------------------------------------------- identity


@dataclass(frozen=True)
class Breadcrumb:
    name: str
    category_code: str = ""
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "category_code": self.category_code, "url": self.url}


@dataclass(frozen=True)
class CategoryHierarchy:
    """Croma's three-level numeric category path.

    M0 §12.1 established these IDs cleanly separate products from accessories, which
    is why they are kept as identity rather than folded into the spec table.
    """

    l0: Sourced[str]
    l1: Sourced[str]
    l2: Sourced[str]
    breadcrumbs: tuple[Breadcrumb, ...] = ()
    breadcrumbs_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "l0": self.l0.to_dict(),
            "l1": self.l1.to_dict(),
            "l2": self.l2.to_dict(),
            "breadcrumbs": [b.to_dict() for b in self.breadcrumbs],
            "breadcrumbs_path": self.breadcrumbs_path,
        }


@dataclass(frozen=True)
class ProductIdentity:
    """What the product *is*.

    ``model_number`` is read from the labelled Model Number specification field, not
    parsed out of the title. M0 §10.3 proved model codes differing by one character
    are different products, so a regex over marketing text is not an acceptable
    source when a labelled field exists.
    """

    platform: str
    sku: Sourced[str]
    name: Sourced[str]
    brand: Sourced[str]
    model_number: Sourced[str]
    model_series: Sourced[str]
    ean: Sourced[str]
    category: CategoryHierarchy

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "sku": self.sku.to_dict(),
            "name": self.name.to_dict(),
            "brand": self.brand.to_dict(),
            "model_number": self.model_number.to_dict(),
            "model_series": self.model_series.to_dict(),
            "ean": self.ean.to_dict(),
            "category": self.category.to_dict(),
        }


# ------------------------------------------------------------------ provenance


@dataclass(frozen=True)
class CaptureProvenance:
    """Which artifact this product was extracted from, and under what conditions.

    ``pincode`` is load-bearing, not decorative: Croma scopes price, availability and
    offers by location, so a price without the pincode it was observed under is not
    a reproducible observation.
    """

    capture_id: str
    captured_at: str
    final_url: str
    html_sha256: str
    pincode: Sourced[str]
    requested_model: Sourced[str]
    pdp_url_source: Sourced[str]
    page_kind: str = ""
    platform: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "captured_at": self.captured_at,
            "final_url": self.final_url,
            "html_sha256": self.html_sha256,
            "pincode": self.pincode.to_dict(),
            "requested_model": self.requested_model.to_dict(),
            "pdp_url_source": self.pdp_url_source.to_dict(),
            "page_kind": self.page_kind,
            "platform": self.platform,
        }


@dataclass(frozen=True)
class ExtractedProduct:
    """The authoritative extraction result for one captured PDP.

    ``warnings`` records everything that was noticed but not silently resolved —
    a partial offer carousel, a spec/marketing contradiction, a missing field. It is
    part of the output, not a log side-effect, so a consumer can gate on it.
    """

    identity: ProductIdentity
    pricing: PriceQuote
    specifications: SpecificationTable
    offers: OfferSet
    provenance: CaptureProvenance
    warnings: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "pricing": self.pricing.to_dict(),
            "specifications": self.specifications.to_dict(),
            "offers": self.offers.to_dict(),
            "provenance": self.provenance.to_dict(),
            "warnings": list(self.warnings),
            "unknown_fields": list(self.unknown_fields),
            "extra": dict(self.extra),
        }
