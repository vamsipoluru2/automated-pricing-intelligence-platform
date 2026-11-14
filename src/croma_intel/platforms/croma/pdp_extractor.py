"""The authoritative Croma PDP extractor.

A pure function from a stored :class:`RawCapture` to an :class:`ExtractedProduct`.
No network, no browser, no I/O — given the same artifact it yields the same result
forever, which is what makes the M0 replay guarantee testable.

Source hierarchy, strongest first
--------------------------------
1. ``window.__INITIAL_DATA__`` reducer payloads — the server's own product record.
2. The rendered DOM — used only for offers, which are not in the state blob.
3. The capture manifest — provenance the page cannot know about itself (pincode).

Deliberately **not** sources:

* **Marketing description.** ``pdpData.description`` is prose. In the reference
  capture it claims "16GB of LPDDR5-5500 memory" while the labelled specification
  table says ``DDR5`` / ``SO-DIMM`` / two slots, one empty — and LPDDR5 is soldered,
  so it cannot be socketed. The prose is simply wrong. It is read here only to
  *detect* such conflicts and warn; it can never supply a value.
* **Product JSON-LD.** Poorer than the reducer (no model number, no specifications)
  and malformed in the reference capture anyway.
* **Any monetary figure not at an allow-listed price path.** See :data:`MOP_PATHS`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ...domain.capture import CaptureStatus, Platform, RawCapture
from ...domain.errors import StructureError
from ...domain.product import (
    UNKNOWN,
    Breadcrumb,
    CaptureProvenance,
    CategoryHierarchy,
    ExtractedProduct,
    Money,
    PriceQuote,
    PriceStatus,
    ProductIdentity,
    Sourced,
    SourceKind,
    SpecFeature,
    SpecGroup,
    SpecificationTable,
    is_known,
)
from ...extraction.initial_data import ExtractedState, dig, dig_str, extract_state
from .offer_parser import extract_offers

# --------------------------------------------------------------- source paths

#: Reducers without which there is no product. Absence is a hard failure.
REQUIRED_REDUCERS: tuple[str, ...] = ("pdpReducer",)

#: Reducers that enrich the record. ``pdpPriceReducer`` is deliberately optional at
#: the parse layer so that a page served without pricing yields MOP_UNRESOLVED — a
#: usable record with an honest gap — rather than an exception.
OPTIONAL_REDUCERS: tuple[str, ...] = (
    "pdpPriceReducer",
    "reviewProductReducer",
    "extendedWarrantyReducer",
)

_PDP = "pdpReducer.pdpData"

#: Rule 6 — MOP is typed and sourced. For Croma the allowlist is a *key path* in the
#: server's own price reducer, which is stronger evidence than a rendered label: it
#: cannot be confused with the EMI instalment, the exchange quote, the bank-offer
#: discount or the personalised tier, none of which appear at these paths.
MOP_PATHS: tuple[str, ...] = ("pdpPriceReducer.pdpPriceData.sellingPrice.value",)
MRP_PATHS: tuple[str, ...] = ("pdpPriceReducer.pdpPriceData.mrp.value",)

#: Stable machine-readable classification codes. Matched in preference to display
#: labels, which are presentation strings and could be relabelled or localised.
CODE_MODEL_NUMBER = "SG-ManufacturerDetails-ModelNumber"
CODE_MODEL_SERIES = "SG-ManufacturerDetails-ModelSeries"
CODE_BRAND = "SG-ManufacturerDetails-Brand"

#: Display labels, used only when the code is absent.
LABEL_MODEL_NUMBER = "Model Number"
LABEL_MODEL_SERIES = "Model Series"


# ------------------------------------------------------ specification assembly


def _feature_values(raw: Any) -> tuple[str, ...]:
    """Ordered, de-duplicated feature values.

    Croma emits genuine duplicates within a single feature (``Suitable For`` lists
    ``Everyday Use`` twice). Order is preserved because it is Croma's own display
    order and carries weak signal; repeats are dropped because they carry none.
    """
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[str] = []
    for item in raw:
        value = dig_str(item, "value") if isinstance(item, Mapping) else UNKNOWN
        if value is UNKNOWN:
            continue
        assert isinstance(value, str)
        if value not in out:
            out.append(value)
    return tuple(out)


def _as_int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def build_specification_table(state: ExtractedState) -> SpecificationTable:
    """Assemble the complete labelled specification structure.

    Groups and features keep Croma's own shape. Nothing is flattened, nothing is
    renamed, and a group whose features are all unusable is still emitted so that
    "the group exists but is empty" stays distinguishable from "the group is absent".
    """
    raw_groups = dig(state.value("pdpReducer"), "pdpData.classifications")
    if raw_groups is UNKNOWN:
        return SpecificationTable(groups=(), path=f"{_PDP}.classifications")
    if not isinstance(raw_groups, (list, tuple)):
        raise StructureError(
            f"{_PDP}.classifications is {type(raw_groups).__name__}, expected a list"
        )

    groups: list[SpecGroup] = []
    for g_index, raw_group in enumerate(raw_groups):
        if not isinstance(raw_group, Mapping):
            continue
        group_path = f"{_PDP}.classifications[{g_index}]"
        raw_features = raw_group.get("features")
        features: list[SpecFeature] = []
        if isinstance(raw_features, (list, tuple)):
            for f_index, raw_feature in enumerate(raw_features):
                if not isinstance(raw_feature, Mapping):
                    continue
                name = dig_str(raw_feature, "name")
                values = _feature_values(raw_feature.get("featureValues"))
                if name is UNKNOWN or not values:
                    continue  # an unlabelled or valueless row is not a specification
                assert isinstance(name, str)
                unit = dig_str(raw_feature, "featureUnit.symbol")
                unit_type = dig_str(raw_feature, "featureUnit.unitType")
                code = dig_str(raw_feature, "code")
                features.append(
                    SpecFeature(
                        name=name,
                        values=values,
                        code=code if isinstance(code, str) else "",
                        display_order=_as_int(raw_feature.get("displayOrder")),
                        comparable=bool(raw_feature.get("comparable") is True),
                        unit=unit if isinstance(unit, str) else "",
                        unit_type=unit_type if isinstance(unit_type, str) else "",
                        path=f"{group_path}.features[{f_index}]",
                    )
                )
        group_name = dig_str(raw_group, "name")
        group_code = dig_str(raw_group, "code")
        groups.append(
            SpecGroup(
                name=group_name if isinstance(group_name, str) else "",
                features=tuple(features),
                code=group_code if isinstance(group_code, str) else "",
                display_order=_as_int(raw_group.get("displayOrder")),
            )
        )
    return SpecificationTable(groups=tuple(groups), path=f"{_PDP}.classifications")


def _find_by_code_suffix(table: SpecificationTable, suffix: str) -> SpecFeature | Any:
    for _group, feature in table.iter_features():
        if feature.code.endswith(suffix):
            return feature
    return UNKNOWN


def _sourced_feature(
    table: SpecificationTable, *, code_suffix: str, label: str
) -> Sourced[str]:
    """Read one labelled feature, preferring its stable code over its display label.

    Returns a single value only. A feature that carries several values is reported as
    UNKNOWN rather than having one silently chosen — for an identity field like the
    model number, picking one of several would be exactly the confident guess rule 10
    forbids.
    """
    feature = _find_by_code_suffix(table, code_suffix)
    how = "code"
    if feature is UNKNOWN:
        feature = table.find(label)
        how = "label"
    if feature is UNKNOWN:
        return Sourced.missing(
            f"{_PDP}.classifications[*].features[?code~{code_suffix}]",
            note=f"neither code ~{code_suffix} nor label {label!r} present",
        )
    assert isinstance(feature, SpecFeature)
    value = feature.value
    if value is UNKNOWN:
        return Sourced(
            UNKNOWN,
            f"{feature.path}.featureValues",
            SourceKind.INITIAL_DATA,
            note=f"{len(feature.values)} values; refusing to choose one",
        )
    return Sourced(value, f"{feature.path}.featureValues[0].value", SourceKind.INITIAL_DATA,
                   note=f"matched by {how}")


# ------------------------------------------------------------------- identity


def _sourced_str(state: ExtractedState, path: str) -> Sourced[str]:
    value = dig_str(state.value(path.split(".")[0]), path.split(".", 1)[1])
    if value is UNKNOWN:
        return Sourced.missing(path)
    return Sourced(value, path, SourceKind.INITIAL_DATA)


def _breadcrumbs(state: ExtractedState) -> tuple[Breadcrumb, ...]:
    raw = dig(state.value("pdpReducer"), "pdpData.pdpBreadcrumbs")
    if not isinstance(raw, (list, tuple)):
        return ()
    crumbs: list[Breadcrumb] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        name = dig_str(item, "name")
        if name is UNKNOWN:
            continue
        assert isinstance(name, str)
        code = dig_str(item, "categoryCode")
        url = dig_str(item, "url")
        crumbs.append(
            Breadcrumb(
                name=name,
                category_code=code if isinstance(code, str) else "",
                url=url if isinstance(url, str) else "",
            )
        )
    return tuple(crumbs)


def build_identity(state: ExtractedState, table: SpecificationTable) -> ProductIdentity:
    """Assemble product identity from labelled fields only.

    The model number comes from the labelled ``Model Number`` classification, never
    from a regex over the title. M0 §10.3 showed ``83DV018NIN`` and ``83DV018QIN``
    are edit-distance 1 apart with different RAM and GPU, so identity must come from
    a field the retailer labelled as identity.
    """
    brand = _sourced_str(state, f"{_PDP}.manufacturer")
    if not brand.known:
        brand = _sourced_feature(table, code_suffix=CODE_BRAND, label="Brand")

    return ProductIdentity(
        platform=Platform.CROMA.value,
        sku=_sourced_str(state, f"{_PDP}.code"),
        name=_sourced_str(state, f"{_PDP}.name"),
        brand=brand,
        model_number=_sourced_feature(
            table, code_suffix=CODE_MODEL_NUMBER, label=LABEL_MODEL_NUMBER
        ),
        model_series=_sourced_feature(
            table, code_suffix=CODE_MODEL_SERIES, label=LABEL_MODEL_SERIES
        ),
        ean=_sourced_str(state, f"{_PDP}.ean"),
        category=CategoryHierarchy(
            l0=_sourced_str(state, f"{_PDP}.categoryL0"),
            l1=_sourced_str(state, f"{_PDP}.categoryL1"),
            l2=_sourced_str(state, f"{_PDP}.categoryL2"),
            breadcrumbs=_breadcrumbs(state),
            breadcrumbs_path=f"{_PDP}.pdpBreadcrumbs",
        ),
    )


# -------------------------------------------------------------------- pricing


def _first_allowed(state: ExtractedState, paths: Sequence[str]) -> Sourced[Money]:
    """Read a monetary value from the first allow-listed path that carries one."""
    for path in paths:
        root_name, rest = path.split(".", 1)
        raw = dig_str(state.value(root_name), rest)
        if raw is UNKNOWN:
            continue
        money = Money.parse(raw)
        if money is UNKNOWN:
            continue
        return Sourced(money, path, SourceKind.INITIAL_DATA)
    return Sourced.missing(
        paths[0] if paths else "",
        note="no allow-listed price path carried a parseable amount",
    )


def build_price_quote(state: ExtractedState) -> PriceQuote:
    """Resolve MOP and MRP, failing closed.

    A figure becomes MOP only by appearing at an allow-listed path *and* surviving
    the consistency check below. Everything else on the page that looks like money —
    the ₹5,366/mo EMI instalment, the exchange quote, the bank-offer discount — has no
    path into this function at all, because it lives nowhere near these keys.
    """
    mop = _first_allowed(state, MOP_PATHS)
    mrp = _first_allowed(state, MRP_PATHS)

    if not mop.known:
        return PriceQuote(
            mop=mop,
            mrp=mrp,
            status=PriceStatus.MOP_UNRESOLVED,
            reason="no selling price at an allow-listed source path",
        )

    assert isinstance(mop.value, Money)
    if mop.value.amount <= 0:
        return PriceQuote(
            mop=Sourced.missing(MOP_PATHS[0], note=f"rejected non-positive {mop.value.raw!r}"),
            mrp=mrp,
            status=PriceStatus.MOP_UNRESOLVED,
            reason="selling price was not positive",
        )

    # M0 §11.2 rule 5: a selling price above MRP indicates mis-extraction, not a
    # bargain. Refuse it rather than publishing an impossible figure.
    if mrp.known:
        assert isinstance(mrp.value, Money)
        if mrp.value.amount > 0 and mop.value.amount > mrp.value.amount:
            return PriceQuote(
                mop=Sourced.missing(
                    MOP_PATHS[0],
                    note=f"rejected {mop.value.raw!r}: exceeds MRP {mrp.value.raw!r}",
                ),
                mrp=mrp,
                status=PriceStatus.MOP_UNRESOLVED,
                reason="selling price exceeded MRP",
            )

    return PriceQuote(mop=mop, mrp=mrp, status=PriceStatus.RESOLVED)


# -------------------------------------------- marketing / specification conflict

#: Prose claims worth cross-checking against a labelled specification.
#:
#: Narrow on purpose. Each entry exists because a real contradiction was observed in
#: a real capture, not because a conflict is imaginable. The check only ever raises a
#: warning — a specification value is never overridden by prose, in either direction.
_CONFLICT_CHECKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # Observed 2026-08-25 on SKU 323360: description says "LPDDR5-5500", the labelled
    # table says DDR5 / SO-DIMM / 2 slots. LPDDR5 is soldered and cannot be socketed,
    # so the prose is wrong. Spec wins; the disagreement is surfaced, not hidden.
    ("Type of RAM", r"\b(LPDDR\d[A-Z0-9-]*|DDR\d[A-Z0-9-]*)\b", ("lpddr", "ddr")),
)

_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(html: str) -> str:
    return " ".join(_TAG_RE.sub(" ", html).split())


def detect_description_conflicts(
    description_html: str, table: SpecificationTable
) -> tuple[str, ...]:
    """Report where marketing prose disagrees with the labelled specification.

    Returns warnings only. This function has no authority to change a value; it
    exists so a known silent-wrong hazard becomes visible output instead of a
    discrepancy nobody notices.
    """
    if not description_html:
        return ()
    text = _plain_text(description_html)
    warnings: list[str] = []
    for label, pattern, _families in _CONFLICT_CHECKS:
        spec_value = table.value_of(label)
        if spec_value is UNKNOWN:
            continue
        assert isinstance(spec_value, str)
        claims = {m.group(0).upper() for m in re.finditer(pattern, text, re.IGNORECASE)}
        if not claims:
            continue
        canonical = spec_value.strip().upper()
        if any(claim.startswith(canonical) or canonical.startswith(claim) for claim in claims):
            continue
        warnings.append(
            f"marketing description claims {sorted(claims)} but the labelled "
            f"specification {label!r} says {spec_value!r}; the specification wins"
        )
    return tuple(warnings)


# ----------------------------------------------------------------- provenance


def build_provenance(capture: RawCapture) -> CaptureProvenance:
    """Provenance the page cannot supply about itself.

    Pincode is the load-bearing one: Croma scopes price, availability and offers by
    location, and the reference capture proves it — its exchange block reads
    "not available for your location". A price recorded without the pincode it was
    observed under is not a reproducible observation.
    """
    extra = capture.extra or {}
    pincode_raw = extra.get("pincode")
    pincode: Sourced[str] = (
        Sourced(str(pincode_raw), "capture.extra.pincode", SourceKind.CAPTURE_MANIFEST)
        if isinstance(pincode_raw, str) and pincode_raw.strip()
        else Sourced.missing(
            "capture.extra.pincode",
            note="capture did not record the pincode it was taken under",
        )
    )
    model_raw = extra.get("requested_model")
    url_source_raw = extra.get("pdp_url_source")
    return CaptureProvenance(
        capture_id=capture.capture_id,
        captured_at=capture.captured_at,
        final_url=capture.final_url,
        html_sha256=capture.html_sha256,
        pincode=pincode,
        requested_model=(
            Sourced(str(model_raw), "capture.extra.requested_model", SourceKind.CAPTURE_MANIFEST)
            if isinstance(model_raw, str) and model_raw.strip()
            else Sourced.missing("capture.extra.requested_model")
        ),
        pdp_url_source=(
            Sourced(str(url_source_raw), "capture.extra.pdp_url_source", SourceKind.CAPTURE_MANIFEST)
            if isinstance(url_source_raw, str) and url_source_raw.strip()
            else Sourced.missing("capture.extra.pdp_url_source")
        ),
        page_kind=capture.page_kind,
        platform=capture.platform,
    )


# --------------------------------------------------------------------- extract


def _unknown_field_names(product_parts: Mapping[str, Sourced[Any]]) -> tuple[str, ...]:
    return tuple(sorted(name for name, field in product_parts.items() if not field.known))


def extract_croma_pdp(capture: RawCapture) -> ExtractedProduct:
    """Extract the authoritative product record from a captured Croma PDP.

    Refuses upfront on a capture that is not a successful Croma PDP: a blocked page
    or an empty shell must fail as a capture problem, never flow onward as a product
    with missing fields.
    """
    if capture.platform != Platform.CROMA.value:
        raise StructureError(
            f"capture {capture.capture_id[:12]}… is platform {capture.platform!r}, expected croma"
        )
    if capture.status != CaptureStatus.SUCCESS.value:
        raise StructureError(
            f"capture {capture.capture_id[:12]}… has status {capture.status!r}; "
            "only a SUCCESS capture may be extracted"
        )
    if capture.block.is_blocked:
        raise StructureError(
            f"capture {capture.capture_id[:12]}… is a block page "
            f"({capture.block.decisive_signal}); refusing to extract"
        )

    state = extract_state(
        capture.html, required=REQUIRED_REDUCERS, optional=OPTIONAL_REDUCERS
    )

    table = build_specification_table(state)
    identity = build_identity(state, table)
    pricing = build_price_quote(state)
    offers = extract_offers(capture.html)

    description = dig_str(state.value("pdpReducer"), "pdpData.description")
    warnings: list[str] = []
    if isinstance(description, str):
        warnings.extend(detect_description_conflicts(description, table))

    if "pdpPriceReducer" not in state:
        warnings.append("pdpPriceReducer absent from the state blob; pricing unresolved")
    if pricing.status is PriceStatus.MOP_UNRESOLVED:
        warnings.append(f"MOP unresolved: {pricing.reason}")
    complete = offers.complete
    if complete is not UNKNOWN and complete is False:
        warnings.append(
            f"offer carousel declared {offers.declared_count} offers but "
            f"{offers.extracted_count} were present in the markup"
        )
    if table.feature_count == 0:
        warnings.append("no labelled specification features were found")

    tracked: dict[str, Sourced[Any]] = {
        "identity.sku": identity.sku,
        "identity.name": identity.name,
        "identity.brand": identity.brand,
        "identity.model_number": identity.model_number,
        "identity.model_series": identity.model_series,
        "identity.ean": identity.ean,
        "identity.category.l0": identity.category.l0,
        "identity.category.l1": identity.category.l1,
        "identity.category.l2": identity.category.l2,
        "pricing.mop": pricing.mop,
        "pricing.mrp": pricing.mrp,
        "provenance.pincode": capture_provenance_pincode(capture),
    }

    return ExtractedProduct(
        identity=identity,
        pricing=pricing,
        specifications=table,
        offers=offers,
        provenance=build_provenance(capture),
        warnings=tuple(warnings),
        unknown_fields=_unknown_field_names(tracked),
        extra={"state": state.to_dict()},
    )


def capture_provenance_pincode(capture: RawCapture) -> Sourced[str]:
    """Pincode as a sourced field, for inclusion in the unknown-field census."""
    return build_provenance(capture).pincode


def iter_sourced_fields(product: ExtractedProduct) -> Iterable[tuple[str, Sourced[Any]]]:
    """Every provenance-bearing scalar field, for auditing and reporting."""
    identity = product.identity
    yield "identity.sku", identity.sku
    yield "identity.name", identity.name
    yield "identity.brand", identity.brand
    yield "identity.model_number", identity.model_number
    yield "identity.model_series", identity.model_series
    yield "identity.ean", identity.ean
    yield "identity.category.l0", identity.category.l0
    yield "identity.category.l1", identity.category.l1
    yield "identity.category.l2", identity.category.l2
    yield "pricing.mop", product.pricing.mop
    yield "pricing.mrp", product.pricing.mrp
    yield "provenance.pincode", product.provenance.pincode
    yield "provenance.requested_model", product.provenance.requested_model
    yield "provenance.pdp_url_source", product.provenance.pdp_url_source


__all__ = [
    "MOP_PATHS",
    "MRP_PATHS",
    "OPTIONAL_REDUCERS",
    "REQUIRED_REDUCERS",
    "build_identity",
    "build_price_quote",
    "build_provenance",
    "build_specification_table",
    "detect_description_conflicts",
    "extract_croma_pdp",
    "is_known",
    "iter_sourced_fields",
]
