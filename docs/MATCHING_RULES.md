# Matching Rules

The normative specification of M3a identity matching. Outstanding since M0 §21.9;
written 2026-08-26 alongside the implementation.

**Scope.** Deciding whether a competitor listing is the same product as the Croma
reference. Not ranking (M4), not derivative selection, not output.

---

## 1. Pipeline

```
reference: CandidateProduct          candidate: CandidateProduct
         │                                    │
         └──────────────┬─────────────────────┘
                        ▼
              1. GATES (rule 5)          ── reject → REJECTED + reason code
                        ▼
              2. model comparison
                        ▼
              3. CRITICAL spec comparison (three-valued)
                        ▼
              4. DECISION TABLE lookup    ── (ModelComparison × SpecState)
                        ▼
              5. order score              ── within class only
                        ▼
                  MatchVerdict
```

Steps are ordered, not concurrent. **No identity comparison happens until every gate
has been evaluated** — a laptop bag never reaches step 2.

---

## 2. Candidate schema

`CandidateProduct` is platform-neutral. `ExtractedProduct` (M2, Croma-shaped) is
projected into it by `candidate_from_croma`; M2 is not modified.

| Field | Type | Notes |
|---|---|---|
| `discovery` | `DiscoveryContext` | **required** — see below |
| `title` | `str \| UNKNOWN` | |
| `brand`, `model_number`, `model_series` | `str \| UNKNOWN` | model read from a labelled field, never a title regex |
| `platform_product_id` | `str \| UNKNOWN` | read from the page |
| `url_derived_id` | `str \| UNKNOWN` | **advisory only** (F10) |
| `category_path`, `category_ids` | `tuple[str, ...]` | |
| `specs` | `Mapping[str, str]` | canonical `SpecKey` → normalized value |
| `spec_text` | `str` | raw blob, for boundary-safe token scans |
| `condition` | `ListingCondition` | |
| `model_availability` | `ModelAvailability` | per-platform, not per-listing |
| `mop`, `mrp` | `Money \| UNKNOWN` | carried, **never** used for identity |
| `is_bundle`, `bundle_items` | | flagging only |

### DiscoveryContext — required, not decoration

`platform` · `source_url` · `final_url` · `query` · `query_strategy` · `position` ·
`capture_id` · `pincode`

Each earns its place from a finding: `query`/`position` because F2 put baby cream at
rank 1 for an MTM query; `source_url`/`final_url` because F10 served a different
product than was requested; `capture_id` because rule 1 requires every claim to trace
to an immutable artifact.

---

## 3. Gate order

Fixed in `GATE_ORDER`. All gates evaluate; the **earliest failure in this order** is
the headline reason, which makes the outcome deterministic.

| # | Gate | Rejects on | Evidence |
|---|---|---|---|
| 1 | **ACCESSORY** | title/category identifies an accessory | M0 §12.2 |
| 2 | **CATEGORY** | candidate resolves to a different category, or names none of the reference's | M0 §12.1, F11 |
| 3 | **CONDITION** | anything other than `NEW` | F7 |
| 4 | **IDENTITY_REDIRECT** | url id ≠ served product id | F10 |
| 5 | **BUNDLE** | *never rejects* — flags only | M0 §6.5 |

Accessory is first because it is the cheapest decisive rejection and the one M0
proved most dangerous to miss: an accessory can carry the exact reference MTM *and*
the laptop's size tokens.

The accessory gate reads its product anchors from the reference's category profile
(§5a) — "ryzen" anchors a laptop, not a pedestal fan.

**Absence never rejects.** A candidate with no category passes gate 2 and is deferred
to specs — rejecting on absence would discard every platform without a breadcrumb.
Rule 3 is upheld at the classifier instead, which cannot reach a positive
classification on missing evidence.

---

## 4. Model comparison

Six outcomes. **There is no fuzzy or edit-distance tier and none may be added** —
F9 proved `83DV018NIN` and `83DV018QIN` are edit-distance 1 with different RAM *and*
different GPU. An AST test enforces this.

| Tier | Outcome | Meaning |
|---|---|---|
| T0 | `EXACT` | identical after safe normalization |
| T2 | `SUFFIX_EQUIVALENT` | differ only by a **delimiter-bounded**, allow-listed suffix |
| T3 | `EMBEDDED` | one is a bounded token in the other's text |
| — | `DIFFERENT` | both known, not equal |
| — | `UNAVAILABLE` | platform never publishes a model code |
| — | `UNKNOWN` | expected, absent |

`UNAVAILABLE` and `UNKNOWN` are **not interchangeable**. The first is a platform
property that legitimises a specification-only match; the second is missing evidence
that cannot.

### Safe transformations (M0 §10.4)

NFKC · case folding · whitespace collapse · delimiter-bounded allow-listed suffix.

**Never:** strip trailing alpha from inside a run · truncate the configuration block ·
approximate matching. `83S000FYIN` has no delimiter, so it is never split, so its
trailing `IN` is never mistaken for a market suffix.

---

## 5. Specification comparison

Three-valued: `MATCH` / `MISMATCH` / `UNKNOWN`. **UNKNOWN never counts as agreement.**

The CRITICAL set comes from the reference's **category profile** (§5a). Laptop
(8): `cpu.model` · `ram.size_gb` · `storage.size_gb` · `gpu.model` ·
`gpu.vram_gb` · `display.size_in` · `display.refresh_hz` · `os`

Every CRITICAL key lands in exactly one bucket, so `matched + mismatched + unknown`
always accounts for the whole set.

| Key class | Comparison |
|---|---|
| capacities, refresh | numeric equality |
| `display.size_in` | **±0.15 in tolerance** — M0 §9: 39.62 cm and 39.6 cm are the same panel |
| everything else | exact, case-insensitive |

Exactness elsewhere is a safety property: `RTX 3050A` ≠ `RTX 3050`, `13450HX` ≠
`13450H`.

### SpecState — the second table axis

| State | Condition |
|---|---|
| `ANY_MISMATCH` | ≥1 CRITICAL spec disagrees — **dominates** |
| `ALL_MATCH` | all known on both sides, all agree |
| `PARTIAL` | no mismatch, ≥ `MIN_KNOWN_CRITICAL_SPECS` known, some unknown |
| `INSUFFICIENT` | no mismatch, too few known |

---

## 5a. Category profiles

`matching/profiles.py`. The pipeline is identical for every category; a
`CategoryProfile` carries only what differs: `category_tokens`, `critical_specs`,
`min_known_critical_specs`, `product_anchors`.

**Admission.** A profile is registered only once a real Croma PDP capture of that
category is committed. A CRITICAL set chosen without one is a guess that silently
decides what counts as a match. Roadmap (approved 2026-09-16): laptops ✅ → mobiles →
tablets → TVs → kitchen appliances → home-care appliances.

**Resolution is leaf-first.** Croma's L0 for a laptop is `Computers & Tablets`, so a
joined-path token search would file every laptop under tablets. Breadcrumbs are read
from most to least specific; the first crumb naming a category decides; a crumb
naming two is ambiguous → unresolved. Croma's trail **ends with the product title**,
which is excluded — prose is not a category.

**Unresolved reference → `INSUFFICIENT_EVIDENCE` / `REFERENCE_CATEGORY_UNRESOLVED`**,
before model comparison. An exact model code does not rescue it: with no CRITICAL
set there is nothing to corroborate or contradict it with.

**Size bound.** `MAX_CRITICAL_SPECS = 9`, enforced at construction — see §7.

---

## 6. The decision table

Total over `ModelComparison × SpecState` — 24 entries, no gaps, asserted by test.
No thresholds; a test rejects float literals in the module.

| Model | SpecState | Classification | Reason code |
|---|---|---|---|
| EXACT / SUFFIX_EQUIVALENT / EMBEDDED | ALL_MATCH | `EXACT_MATCH` | `MODEL_EXACT_SPECS_CORROBORATE` |
| " | PARTIAL | `EXACT_MATCH` | `MODEL_EXACT_SPECS_PARTIAL` |
| " | INSUFFICIENT | `EXACT_MATCH_UNVERIFIED` | `MODEL_EXACT_SPECS_UNVERIFIED` |
| " | ANY_MISMATCH | **`CONFLICT`** | `MODEL_EXACT_SPEC_CONTRADICTION` |
| UNAVAILABLE | ALL_MATCH | `CONFIGURATION_MATCH` | `SPECS_MATCH_MODEL_UNAVAILABLE` |
| UNAVAILABLE | PARTIAL | `INSUFFICIENT_EVIDENCE` | `SPECS_PARTIAL_MODEL_UNAVAILABLE` |
| UNAVAILABLE | INSUFFICIENT | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_CRITICAL_SPECS` |
| UNAVAILABLE | ANY_MISMATCH | `DIFFERENT` | `SPECS_MISMATCH_MODEL_UNAVAILABLE` |
| UNKNOWN | ANY_MISMATCH | `DIFFERENT` | `MODEL_MISSING_SPECS_MISMATCH` |
| UNKNOWN | others | `INSUFFICIENT_EVIDENCE` | `MODEL_MISSING_INSUFFICIENT` |
| DIFFERENT | any | `DIFFERENT` | `MODEL_DIFFERENT` |

**Refinement:** `DIFFERENT` + same `model_series` → `VARIANT`
(`MODEL_DIFFERENT_SAME_SERIES`). This only relabels a negative; it can never produce
a positive match.

### CONFLICT means the evidence contradicts itself

It asserts **nothing about the cause**. Any of these produce it, and this layer
cannot tell them apart:

- either side was mis-extracted;
- a retailer published an incorrect specification — M2 found exactly that, a Croma
  description claiming LPDDR5 where the labelled table says DDR5/SO-DIMM;
- a manufacturer shipped several configurations under one model code;
- a retailer relabelled or reused a code.

Reporting it as a match would be silent-wrong; reporting it as DIFFERENT would bury a
contradiction worth seeing. It requires a human.

### Why `UNAVAILABLE + PARTIAL` is not a match

This is the Flipkart row, and it is deliberately strict. Flipkart's initial payload
carries only summary specs (M0 §3.6), which land in `PARTIAL`. Allowing a match there
would let partial evidence produce a confident answer, contradicting rule 3.

Consequence: **a Flipkart candidate cannot reach `CONFIGURATION_MATCH` until M3b-1
captures the full specification table.** The design forces the capture to be complete
rather than lowering the bar to make the first platform work.

---

## 7. Scoring

`order_score` orders candidates **within** a classification. It cannot move one
between classes: class ranks are separated by ≥10 (CONFLICT 20 · INSUFFICIENT 10 ·
DIFFERENT 0 are the tightest) while the spec bonus is at most `MAX_CRITICAL_SPECS` = 9,
and tests assert both.

---

## 8. Reason-code vocabulary

Closed set, part of the output contract. Renaming one is a breaking change.

**Gate rejections** — `GATE_ACCESSORY` · `GATE_CATEGORY_MISMATCH` ·
`GATE_CONDITION_NOT_NEW` · `GATE_CONDITION_UNKNOWN` · `GATE_IDENTITY_REDIRECT`

**Flags** — `FLAG_BUNDLE`

**Decisive model** — `MODEL_EXACT_SPECS_CORROBORATE` · `MODEL_EXACT_SPECS_PARTIAL` ·
`MODEL_EXACT_SPECS_UNVERIFIED` · `MODEL_EXACT_SPEC_CONTRADICTION`

**Specification-only** — `SPECS_MATCH_MODEL_UNAVAILABLE` ·
`SPECS_MISMATCH_MODEL_UNAVAILABLE` · `SPECS_PARTIAL_MODEL_UNAVAILABLE`

**Model missing** — `MODEL_MISSING_SPECS_MISMATCH` · `MODEL_MISSING_INSUFFICIENT`

**Model different** — `MODEL_DIFFERENT` · `MODEL_DIFFERENT_SAME_SERIES`

**Insufficient** — `INSUFFICIENT_CRITICAL_SPECS` · `NO_REFERENCE_IDENTITY` ·
`REFERENCE_CATEGORY_UNRESOLVED`

---

## 9. Assumptions — unresolved, stated deliberately

These are judgements, not findings. Each is a single named constant so it can be
revised against real data once M3b delivers competitor captures.

| # | Assumption | Where | Risk if wrong |
|---|---|---|---|
| **A1** | `MIN_KNOWN_CRITICAL_SPECS = 4` of 8 | `spec_compare.py` | Too low admits weak matches; too high blocks legitimate ones. **No data yet supports any value.** |
| **A2** | An unmarked title means NEW | `gates.condition_gate` | Unavoidable — no platform labels new stock "New", so requiring a marker rejects everything. Detected markers always win. A platform that marks condition only in structured data would slip through. |
| **A3** | The 8 CRITICAL specs are the right set | `spec_value.CRITICAL_SPECS` | Chosen because all 8 exist in Croma's table *and* in observed competitor rows. `gpu.tdp_w` (Flipkart-only, M0 §9) may deserve inclusion — Q13 unresolved. |
| **A4** | Croma listings are NEW and always expose a model | `candidate_from_croma` | No condition qualifier has been observed on a Croma PDP, but only one PDP has ever been captured. |
| **A5** | Each profile's category tokens suffice (laptop: `laptop`/`notebook`/`computer`) | `profiles.py` | Category taxonomy is only fully known for Croma. A canonical mapping is deferred until real competitor breadcrumbs exist. |
| **A6** | Flipkart is permanently `ModelAvailability.NEVER` | fixture data | **Q3 is unresolved.** If the MTM is in the expandable spec table, Flipkart becomes `SOMETIMES` and can reach `EXACT_MATCH`. M3b-1 must answer this first. |

---

## 10. Out of scope for M3a

Ranking · derivative selection · Excel · LLM call sites · competitor platform
adapters (M3b) · price-based identity (M0 §16: 78% intra-series spread; price is
never an identity signal and is carried but never compared).
