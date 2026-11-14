"""Model-code normalization and comparison.

This is the most safety-critical module in M3. A false positive here merges two
different laptops into one price comparison; M0 §10.3 proved that risk is real, not
theoretical:

    83S000FYIN  →  Lenovo LOQ 15ARP10E, India
    83S00000US  →  Lenovo LOQ-E,        United States      (different product)

    83DV018NIN  →  LOQ 15, 16 GB RAM, RTX 3050
    83DV018QIN  →  LOQ 15, 24 GB RAM, RTX 4050              (edit distance 1)

Two codes one character apart are different products with different RAM *and*
different GPUs. **Therefore fuzzy and edit-distance model matching are banned.**
There is no similarity threshold in this module and none may be added.

Architectural rule 4, stated operationally
------------------------------------------
**Never remove a character from inside an alphanumeric run.** A suffix may only be
dropped when a real delimiter separates it — ``2Z326PA#ACJ`` (HP, observed on Croma)
splits at ``#``; ``83S000FYIN`` does not split at all, because there is nothing to
split on. The trailing ``IN`` is *inside* the run and is product-distinguishing.

Safe transformations, per M0 §10.4
----------------------------------
=========================  ========  ==============================================
Transformation             Verdict   Evidence
=========================  ========  ==============================================
Unicode NFKC               safe      no counter-example
Case folding               safe      VS URL slugs lowercase, titles uppercase
Whitespace collapse        safe      no counter-example
Delimiter-bounded suffix   cond.     HP ``#ACJ``; brand-scoped
Strip trailing alpha       UNSAFE    ``83S000FYIN`` → ``83S000FY`` merges regions
Truncate config block      UNSAFE    ``83S000FYIN`` vs ``83S000CPIN`` share 6 chars
Fuzzy / edit distance      UNSAFE    edit distance 1 = different product
=========================  ========  ==============================================

Stdlib only. Pure. No I/O.
"""

from __future__ import annotations

import re
import unicodedata

from ..domain.matching import ModelAvailability, ModelComparison

#: Characters that genuinely delimit a suffix. ``#`` is the only one with direct
#: evidence (HP ``2Z326PA#ACJ``); the others are included because they are
#: unambiguous separators, not because a case was observed.
SUFFIX_DELIMITERS: str = "-–—/#\\_ "

#: Suffixes that may be dropped when delimiter-bounded. Deliberately short: M0 §15.2
#: found **no** ``-IN``-suffixed MTM in fifteen real examples, so T2 is retained as
#: cheap insurance rather than as a response to observed data. Adding to this list
#: requires a real observed listing, not a plausible one.
DROPPABLE_SUFFIXES: frozenset[str] = frozenset(
    {
        "IN", "IND", "INDIA",   # market codes
        "US", "USA", "UK", "EU", "AP", "APAC",
        "ACJ",                  # HP packaging code, observed on Croma
    }
)

_WS_RE = re.compile(r"\s+")


def normalize_model(raw: str | None) -> str:
    """Apply only the transformations M0 §10.4 marked safe.

    NFKC, case folding and whitespace collapse. **Nothing is removed** — not
    hyphens, not spaces inside the code, not trailing characters. The result is
    suitable for equality comparison and for boundary-safe token scanning.
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", str(raw))
    text = _WS_RE.sub(" ", text).strip().upper()
    return text


def split_delimited_suffix(model: str) -> tuple[str, str]:
    """Split a **delimiter-bounded** trailing token. Returns ``(base, suffix)``.

    Returns ``(model, "")`` when there is no delimiter — which is the common case and
    the whole point. ``83S000FYIN`` has no delimiter, so it is never split, so its
    trailing ``IN`` can never be mistaken for a market suffix.
    """
    normalized = normalize_model(model)
    if not normalized:
        return "", ""
    for index in range(len(normalized) - 1, 0, -1):
        if normalized[index] in SUFFIX_DELIMITERS:
            base = normalized[:index].rstrip(SUFFIX_DELIMITERS)
            suffix = normalized[index + 1 :].strip()
            if base and suffix:
                return base, suffix
            return normalized, ""
    return normalized, ""


def strip_droppable_suffix(model: str) -> str:
    """Remove a delimiter-bounded suffix, but only one on the allowlist.

    An unrecognised delimited suffix is *kept*: ``83DV018N-XYZ`` is not assumed to be
    ``83DV018N`` with decoration, because an unknown token might be the thing that
    distinguishes two configurations.
    """
    base, suffix = split_delimited_suffix(model)
    if suffix and suffix in DROPPABLE_SUFFIXES:
        return base
    return normalize_model(model)


def contains_model_token(text: str | None, model: str | None) -> bool:
    """Boundary-safe whole-token search for *model* inside free text.

    A match must be bounded by a non-alphanumeric character or a string edge, so
    ``83S000FYIN`` is found in ``"LOQ 83S000FYIN Gaming"`` and in
    ``"83S000FYIN-IND"``, but **not** inside ``"83S000FYINX"`` — a different code.

    Required because M0 §5.4 found the MTM at varying positions in competitor titles:
    mid-title on Reliance Digital (``LOQ 15IRX9 83DV00L9IN Gaming Laptop``) and
    end-of-title on Vijay Sales (``... MS-Office) 83DV018QIN``). Position-based
    extraction fails; whole-title token scanning does not.

    .. note::
       ``platforms/croma/result_identifier.contains_model_token`` is an independent
       implementation of this same rule, retained so M1 stays untouched.
       ``tests/unit/test_model_code.py`` asserts the two agree across a shared
       corpus, so they cannot diverge silently.
    """
    if not text or not model:
        return False
    haystack = normalize_model(text)
    needle = normalize_model(model)
    if not haystack or not needle:
        return False
    for match in re.finditer(re.escape(needle), haystack):
        start, end = match.start(), match.end()
        before_ok = start == 0 or not haystack[start - 1].isalnum()
        after_ok = end == len(haystack) or not haystack[end].isalnum()
        if before_ok and after_ok:
            return True
    return False


def compare_models(
    reference: str | None,
    candidate: str | None,
    *,
    candidate_text: str | None = None,
    availability: ModelAvailability = ModelAvailability.UNKNOWN,
) -> tuple[ModelComparison, str]:
    """Compare two model codes. Returns ``(comparison, detail)``.

    ``candidate_text`` is the candidate's free text (title, spec blob) and enables
    the T3 EMBEDDED tier when the candidate has no discrete model field.

    ``availability`` distinguishes the two reasons a model can be absent, which the
    decision table treats very differently:

    * ``NEVER`` → :data:`ModelComparison.UNAVAILABLE`. The platform does not publish
      model codes (Flipkart, F1). A specification-only match is legitimate here.
    * anything else → :data:`ModelComparison.UNKNOWN`. The code should have been
      there and was not, which is missing evidence, not a platform property.
    """
    ref = normalize_model(reference)
    cand = normalize_model(candidate)

    if not ref:
        return ModelComparison.UNKNOWN, "reference model is absent"

    if not cand:
        if candidate_text and contains_model_token(candidate_text, ref):
            return (
                ModelComparison.EMBEDDED,
                f"reference model {ref!r} appears as a bounded token in candidate text",
            )
        if availability is ModelAvailability.NEVER:
            return (
                ModelComparison.UNAVAILABLE,
                "platform does not publish model codes; specification-only path applies",
            )
        return ModelComparison.UNKNOWN, "candidate model is absent"

    # T0 — identical after safe normalization.
    if ref == cand:
        return ModelComparison.EXACT, f"exact model match {ref!r}"

    # T2 — equal once a delimiter-bounded, allow-listed suffix is dropped.
    ref_base = strip_droppable_suffix(ref)
    cand_base = strip_droppable_suffix(cand)
    if ref_base == cand_base and ref_base:
        return (
            ModelComparison.SUFFIX_EQUIVALENT,
            f"{ref!r} and {cand!r} agree on base {ref_base!r} after dropping a delimited suffix",
        )

    # T3 — one is a bounded token inside the other's text.
    if contains_model_token(cand, ref) or contains_model_token(ref, cand):
        return (
            ModelComparison.EMBEDDED,
            f"one model is a bounded token within the other ({ref!r} / {cand!r})",
        )
    if candidate_text and contains_model_token(candidate_text, ref):
        return (
            ModelComparison.EMBEDDED,
            f"reference model {ref!r} appears as a bounded token in candidate text",
        )

    # Both known and not equal. No similarity fallback exists, by design.
    return ModelComparison.DIFFERENT, f"{ref!r} != {cand!r}; no fuzzy fallback (rule 4 / F9)"


__all__ = [
    "DROPPABLE_SUFFIXES",
    "SUFFIX_DELIMITERS",
    "compare_models",
    "contains_model_token",
    "normalize_model",
    "split_delimited_suffix",
    "strip_droppable_suffix",
]
