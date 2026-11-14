# M3 — Competitor Candidate Discovery & Identity Matching

**Implementation brief — for approval. Nothing in this document is implemented.**

Status: **APPROVED 2026-08-26** · Author: design session 2026-08-26 · Supersedes: nothing
Prerequisites read: `CLAUDE.md`, `M0_RECON_FINDINGS.md`, `M1_BROWSER_GATE.md`,
all M2 source and tests.

---

## 1. What M3 is

Given the Croma reference product that M2 produces, find the same product on
competitor platforms and decide — with evidence — whether each candidate is
genuinely the same product.

```
ExtractedProduct (Croma, from M2)
      │
      ├─► query planning ──► competitor search ──► candidate capture
      │                                                   │
      │                                          CandidateProduct
      │                                                   │
      └──────────────► identity matching ◄────────────────┘
                              │
                       MatchVerdict
                    (classification + evidence + gates)
```

M3 ends at a classified, evidenced verdict per candidate. **Ranking, derivative
detection, Excel output and LLM calls remain out of scope** and must not appear.

---

## 2. The central problem with M3 as briefed

M3 as scoped contains **two halves with incompatible evidence bases**, and this is
the single most important thing to settle before any code is written.

| | Identity matching | Competitor discovery |
|---|---|---|
| Inputs available now | ✅ Real Croma product (M2), real competitor titles/models/specs (M0) | ❌ No competitor capture exists |
| Can be built offline | ✅ Entirely | ❌ Needs live access per platform |
| Blocking unknowns | None | **Q3, Q4, Q5, Q6 — all still open** |
| Failure mode if rushed | Testable, correctable | Silent, expensive, rate-limited |

M0 §20 lists five blocking questions. **Four are still unanswered**, because M1 was
deliberately Croma-only:

| # | Question | Status | Blocks |
|---|---|---|---|
| Q3 | Is the Flipkart MTM in the expandable spec table? | ❓ **Open** — M0 said "M1 must resolve this"; M1 did not | Whether Flipkart can *ever* reach EXACT_MATCH |
| Q4 | Does Reliance Digital have a product JSON API? | ❓ Open | Whether RD extraction is robust or perpetually fragile |
| Q5 | Does Amazon.in respond to a real browser? | ❓ Open | Whether Amazon is in scope at all |
| Q6 | Correct search URL patterns | ❓ Open — **all four tested patterns were wrong or blocked** | Every discovery query |

M0 §7 is blunt about this: *"Not one platform's search was successfully exercised
end-to-end."* Writing a discovery engine against four unvalidated search patterns
would repeat the exact mistake M1 rev. 2–6 made on Croma — six revisions spent
debugging a flow whose entry path was wrong.

### Recommendation: split M3

**M3a — Identity Matching Core.** Pure, offline, no network. Fully specifiable and
fully testable *today* against M0's real data and M2's real capture.

**M3b — Competitor Discovery Gate.** One platform at a time, live, each with its own
GO/NO-GO — exactly the shape that made M1 succeed.

M3a is the harder and more valuable engineering, it carries every architectural rule
that matters, and it does not depend on a single open question. M3b without M3a
produces candidates nothing can adjudicate; M3a without M3b is still independently
verifiable against transcribed real listings.

---

## 3. What the existing evidence already decides

These are not design choices open for revisiting — they are findings.

| Finding | Consequence for M3 |
|---|---|
| **F1** Flipkart exposes no MTM | Flipkart needs a spec-only path. `max_achievable_classification = CONFIGURATION_MATCH`, declared per platform, not discovered by accident |
| **F2** Flipkart model search returns 2,135 results, #1 = baby cream | Q1 (exact-model query) **disabled** for Flipkart. `early_stop_on_model_hit` must be **false** — it would stop on garbage |
| **F3** RD is an empty SPA shell | Browser mandatory; prefer XHR interception over DOM |
| **F6** Vijay Sales is static and richest | **The correct first platform.** It is the only one where a Croma→competitor EXACT_MATCH can be proven end-to-end |
| **F7** `"Store Display Unit - "` title prefix | Condition gate needs this value; it is not in the `NEW/RENEWED/REFURBISHED/OPEN_BOX/USED` enum and would otherwise pass silently |
| **F8** `"Lowest Price for You"` + additive `Protect Promise Fee` | Non-MOP price tiers must be named and excluded, as M2 already does for Croma |
| **F9** Never-strip invariant | `83DV018NIN` vs `83DV018QIN` — edit distance 1, different RAM *and* GPU. **No fuzzy model matching, ever** |
| **F10** Vijay Sales URL id ≠ served product | Identity read from the page; requested-vs-served compared and flagged `IDENTITY_REDIRECT` |
| **F11** Croma numeric category IDs | Already extracted by M2 (`3/20/806`); feeds the category gate |
| **M0 §16** 78% intra-series price spread | **Price is never an identity gate.** Weak supporting signal only |
| **M0 §9** Compound spec rows | A splitter is required — see §6, this is a scope change |
| **M0 §15.2** No `-IN` suffixed MTM ever observed | T2 (suffix-equivalent) is retained but **deprioritised**; T3 (embedded) is the tier that earns its place |

---

## 4. M3a — Identity Matching Core

### 4.1 Proposed layout

```
src/croma_intel/
  domain/
    candidate.py        CandidateProduct — platform-neutral competitor record
    matching.py         MatchClassification, SpecVerdict, MatchVerdict, GateResult
  normalization/
    spec_value.py       compound-row splitter, unit parsing, tolerances
    model_code.py       model comparison tiers (never-strip enforced)
  matching/
    gates.py            condition / accessory / category / bundle — before scoring
    spec_compare.py     three-valued CRITICAL-spec comparison
    classifier.py       the decision table
    evidence.py         per-signal evidence records
```

`ExtractedProduct` (M2) is Croma-shaped — its provenance and source paths are
Croma-specific. M3a introduces `CandidateProduct` as the neutral record both sides
are projected into. **M2 is not modified**; an adapter function projects it.

### 4.2 The decision table

Classification is a **table lookup, not a score threshold**. Score only orders
candidates *within* a class — it can never move one between classes.

| Model comparison | CRITICAL specs | Classification |
|---|---|---|
| `EXACT` / `SUFFIX_EQUIVALENT` / `EMBEDDED` | all MATCH | **EXACT_MATCH** |
| `EXACT` | any MISMATCH | **CONFLICT** → `requires_review` |
| `EXACT` | any UNKNOWN | **EXACT_MATCH_UNVERIFIED** |
| `UNAVAILABLE` (platform never exposes it) | all MATCH | **CONFIGURATION_MATCH** |
| `UNAVAILABLE` | any MISMATCH | **DIFFERENT** |
| `UNAVAILABLE` | any UNKNOWN | **INSUFFICIENT_EVIDENCE** |
| `DIFFERENT` | any | **DIFFERENT** |
| any | fewer than *N* CRITICAL specs known | **INSUFFICIENT_EVIDENCE** |

`CONFLICT` is deliberate: an exact MTM with a mismatched CRITICAL spec means one
side was mis-extracted. Reporting it as a match would be the silent-wrong case rule
10 exists to prevent; reporting it as DIFFERENT would hide an extraction bug.

### 4.3 Three-valued spec comparison

`MATCH` / `MISMATCH` / `UNKNOWN`, and **UNKNOWN never counts as agreement**
(rules 2, 3). Proposed CRITICAL set for laptops, all present in M2's 111 features:

| Spec | Croma source | Comparison |
|---|---|---|
| cpu model | `Processor Variant` = `7535HS` | exact token, never-strip |
| ram size | `RAM` = `16GB` | normalised integer GB |
| storage size | `SSD Capacity` = `512GB` | normalised integer GB |
| gpu model | `GPU Model` = `NVIDIA GeForce RTX 3050` | exact, **suffix preserved** — `3050A` ≠ `3050` (M0 §9) |
| gpu vram | `Dedicated Graphics Memory` = `6GB` | normalised integer GB |
| display size | `Display Size (In Inches)` = `15.6` | **±0.15 in tolerance** — 39.6 vs 39.62 cm are the same panel |
| refresh rate | `Refresh Rate` = `144` Hz | exact integer |
| os | `Operating System` = `Windows 11 Home` | normalised |

### 4.4 Gates run before any scoring (rule 5)

1. **Condition** — `NEW` required. Adds `STORE_DISPLAY_UNIT` (F7) and detects it as
   a *title prefix*, which is how it actually appears.
2. **Accessory** — reuse `platforms/croma/accessory_filter.py`, promoted to a shared
   location. It is already multi-signal and already tested; M3 must not fork it.
3. **Category** — canonical mapping. Note Flipkart's breadcrumb terminates in a
   **brand** node (`Lenovo Laptops`) while Vijay Sales terminates in a **product
   type** (`Gaming Laptops`); both map to the same canonical category.
4. **Bundle** — F6's free soundbar. **Flags, does not reject** — rejecting would
   discard a legitimate competitor listing.
5. **Identity redirect** — F10. Requested id vs served id.

---

## 5. M3b — Competitor Discovery Gate

Per-platform, sequential, each with its own GO/NO-GO. Proposed order, justified by
evidence rather than commercial value:

| Order | Platform | Rationale | Risk |
|---|---|---|---|
| 1 | **Vijay Sales** | F6: static, richest, exposes MTM. The only platform where an EXACT_MATCH is provable end-to-end | Q6 (search URL wrong), F7, F10 |
| 2 | **Reliance Digital** | MTM in title (§5.4) → T3 matching provable | F3 browser required, Q4 |
| 3 | **Flipkart** | Highest commercial value, hardest identity | F1, F2 — spec-only, and Q3 must be resolved first |
| 4 | **Amazon** | Everything UNKNOWN | Q5; may be dropped from v1 |

Each gate answers, on the record: correct search URL pattern; whether model search
works; where identity lives on the page; the MOP label allowlist; and whether a
capture can be replayed offline. That is the M1 template, which worked.

**Rate-limit discipline carries over unchanged.** Croma's limit is documented at
~1 navigation per window; competitor limits are **UNKNOWN and must be assumed
similar** until measured. One live run at a time, no loops, no retries.

---

## 6. Scope changes requiring explicit approval

`CLAUDE.md` currently forbids these. M3 cannot proceed without the first two.

| # | Change | Why unavoidable |
|---|---|---|
| **S1** | **Spec normalization enters scope** | Croma pre-splits specs (`Processor Variant: 7535HS`); Vijay Sales does not (`AMD Ryzen 7 7435HS (8C/16T, 3.1/4.5GHz, 4MB L2/16MB L3)`). Without a compound-row splitter the two cannot be compared at all. M0 §9 and §21.7 both call for it |
| **S2** | **A second platform package appears** under `platforms/` | `TestM1ScopeDiscipline.test_no_competitor_platform_adapters` currently asserts `platforms/` contains **only** `croma`. That test must be amended, deliberately, as part of M3 |
| **S3** | `accessory_filter` moves from `platforms/croma/` to shared | It is platform-neutral in fact; forking it per platform would guarantee divergence |

S2 in particular is a guard I wrote to hold the line at M1. It should be changed by
an explicit decision, not quietly deleted.

---

## 7. Test strategy — and an honesty problem

M2's strength was that every assertion was a fact about a real 707 KB capture.
**M3a cannot have that, and I want that stated plainly rather than discovered later.**

There are **no competitor captures**. What exists is M0's *transcribed* evidence:
verbatim titles, model numbers, spec rows and prices, quoted in the findings
document but not preserved as HTML.

Proposed handling:

- Build M3a fixtures from M0's verbatim transcriptions, in a directory named to make
  their standing obvious — `tests/fixtures/transcribed/` — with each fixture carrying
  a provenance header citing its M0 section.
- **Never** present a transcribed fixture as a capture. They live apart from
  `tests/fixtures/croma/pdp_323360/`, which is a real content-addressed artifact.
- Every transcribed fixture is replaced by a real capture as its platform passes M3b.

This is weaker evidence than M2 had. It is still real data — real titles, real MTMs,
real prices from real listings — and it is sufficient to test comparison logic. It is
not sufficient to validate extraction, which is precisely why M3b exists.

Test coverage targets: the F9 pairs (`83DV018NIN`/`83DV018QIN`, `83S000FYIN`/
`83S00000US`) as MISMATCH; the confirmed cross-platform pair `83JC0045IN` (RD and
Vijay Sales, M0 §15.1 case A) as EXACT_MATCH; `RTX 3050A` vs `RTX 3050` as MISMATCH;
Store Display Unit rejection; every decision-table row; UNKNOWN propagation.

---

## 8. What M3 must NOT do

- No ranking, no derivative detection, no Excel, no LLM call sites.
- No fuzzy or edit-distance model matching (F9).
- No price-based identity gating (M0 §16).
- No first-result assumption — carried from M1.
- No modification of the M1 browser flow or the M2 extraction layer, unless a genuine
  dependency requires it, stated and approved.
- No lint cleanup.

---

## 9. Deliverables and acceptance

**M3a** — the modules in §4.1; offline test suite green under `pytest`,
`run_offline`, `compileall`, `mypy`, `ruff`; decision table exhaustively tested;
`docs/MATCHING_RULES.md` written (outstanding since M0 §21.9).

**M3b, per platform** — a gate report in `docs/recon/` with a GO/NO-GO, a real
capture committed as a fixture, and the transcribed fixture retired.

---

## 10. Decisions — settled 2026-08-26

| # | Decision | Outcome |
|---|---|---|
| 1 | M3 shape | **Split. M3a first.** |
| 2 | First competitor platform for M3b | **Flipkart** *(overrides the Vijay Sales recommendation in §5)* |
| 3 | Scope changes | **S1, S2, S3 all approved** |
| 4 | M3a fixture base | **Transcribed M0 evidence**, under the §7 labelling discipline |

### 10.1 Consequence of choosing Flipkart first — and why it improves M3a

Flipkart is the one platform where model-based identity is *unavailable* (F1) and
model-based search is *actively harmful* (F2). Making it the first discovery target
means the **specification-only path is the primary path from day one**, not a
degraded fallback bolted on afterwards.

That is exactly what M0 §1.3 asked for:

> *"…it means Flipkart will essentially never produce an `EXACT_MATCH`, and the
> pipeline must be explicitly designed for that rather than treating it as a
> degraded case."*

So this choice **strengthens M3a's design** and is adopted without reservation. Two
consequences follow and are accepted:

- **Q3 becomes blocking for M3b-1**, not deferrable. Whether the MTM sits in
  Flipkart's expandable specification table decides whether the platform is
  permanently `CONFIGURATION_MATCH`-capped. It is a single-page live check and must
  be the first thing M3b-1 answers.
- **M3b-1 requires expander/scroll interaction** (M0 §3.6). Flipkart's full spec
  table is not in the initial payload, so capture is more involved than Vijay Sales'
  static page would have been.

The revised M3b order is therefore: **Flipkart → Vijay Sales → Reliance Digital →
Amazon**. The §5 table's rationale still stands for the remaining three.

### 10.2 Still open, deliberately

Q4 (RD JSON API) and Q5 (Amazon reachability) remain unanswered and are not blocking
for M3a or M3b-1. They are answered by their own platform gates.

**The EXACT_MATCH path is still tested in M3a** using the confirmed cross-platform
pair `83JC0045IN` (Reliance Digital and Vijay Sales, M0 §15.1 case A). Flipkart being
first for *discovery* does not remove the need to prove the exact path in *matching*.
