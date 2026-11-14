# CLAUDE.md — Croma Competitive Intelligence Engine

Project context for AI assistants and new engineers. Read this first.

## What this project is

A product-matching and competitive-price-extraction tool for Croma.

**Input:** Croma SKU + Material Description + Model number (never a URL).
**Output:** competitor products (Flipkart, Amazon.in, Reliance Digital, Vijay Sales)
matched to the Croma reference product, classified, ranked, and exported to Excel.

Example input:
```
SKU:   323360
Desc:  LENOVO 15 LOQE FYIN 7KR5 16GB6X 512+3050
Model: 83S000FYIN
```

## Current state

| Milestone | Status | Artifact |
|---|---|---|
| M0 — Recon spike | ✅ Complete | `docs/recon/M0_RECON_FINDINGS.md` |
| M1 — Browser access gate | ✅ **GO** — full chain proven live 2026-08-25 | `docs/recon/M1_BROWSER_GATE.md` |
| M2 — Croma PDP extractor | ✅ Complete — 353 offline tests | `src/croma_intel/extraction/`, `platforms/croma/pdp_extractor.py` |
| M3+ | ⛔ Not started. | — |

**M1 PASSED.** Croma is reached through the REAL browser UI only: homepage -> pincode
-> type into the visible search box -> submit -> rendered results -> click the result.
Navigating to a constructed `/search/?text=` URL renders ZERO products (rev. 2-6).
NEVER construct a PDP URL; read it from `page.url`. See `M1_BROWSER_GATE.md` §20.

## The ten architectural rules (non-negotiable)

These survive every milestone. Changing one requires an explicit, documented decision.

1. **Capture and extraction are separated by an immutable artifact.** Browser code
   produces a `RawCapture`. Extraction is a pure function over a capture and must
   never touch the network.
2. **UNKNOWN is a first-class value.** It is not `None`-coerced, not defaulted, and
   never silently becomes a match.
3. **Missing information must never produce MATCH.**
4. **Model normalization must never remove characters from inside an alphanumeric run.**
   Validated against real data in M0 §10.3 — `83S000FYIN` (India) vs `83S00000US` (US)
   are different products; `83DV018NIN` vs `83DV018QIN` are edit-distance 1 with
   different RAM and GPU.
5. **Gates happen before ranking.** Condition/category/accessory/bundle/brand rejection
   runs before any scoring.
6. **MOP is typed and sourced.** Never "the price on the page". A figure becomes MOP
   only via a per-platform label allowlist. Fail closed to `MOP_UNRESOLVED`.
7. **EUP does not exist in this codebase.** No field, no column, no calculation.
   It is a downstream concern consuming this system's output.
8. **The LLM has no decision authority.** Four bounded, schema-validated call sites.
   No path from an LLM response to a classification.
9. **`--no-llm` must remain a complete execution path.**
10. **A wrong match is worse than no match.** Prefer rejection and `requires_review`
    over a confident guess.

## Key M0 findings that constrain implementation

- **Croma returned HTTP 403** to a browserless client (4/4 requests). Resolved in M1:
  a real browser IS served (HTTP 200 with content). See the rate-limit warning below.
- **Flipkart exposes no Lenovo MTM** — titles carry only the series code (`15ARP9`).
  Flipkart will realistically never reach `EXACT_MATCH`; it needs a spec-only path.
- **Flipkart model search is broken** — searching `83S000FYIN` returned 2,135 results
  ranked #1 by "Softsens Baby Cream". Q1 exact-model search is disabled for Flipkart.
- **Reliance Digital PDPs are an empty SPA shell** (HTTP 200, page title only).
  Requires a browser; prefer XHR interception over DOM scraping.
- **Vijay Sales is fully static and rich** — everything in the initial payload.
- **Two silent-wrong hazards found:** the `"Store Display Unit - "` title prefix
  (Vijay Sales) and Flipkart's `"Lowest Price for You"` tier + additive
  `"Protect Promise Fee"`.
- **URL-derived IDs are advisory only.** A Vijay Sales URL for product `230914`
  served SKU `243423`.

## Key M2 findings — the Croma PDP extraction contract

Established from the real PDP capture for SKU 323360 (committed as a fixture).

**`window.__INITIAL_DATA__` is the authoritative source.** A 441 KB Redux-style state
blob with six top-level reducers. Everything on the page that matters is in it —
except offers.

| Field | Source path |
|---|---|
| SKU | `pdpReducer.pdpData.code` |
| Name / brand / EAN | `pdpReducer.pdpData.{name,manufacturer,ean}` |
| **Model number (MTM)** | `pdpData.classifications[*].features[?code~SG-ManufacturerDetails-ModelNumber]` |
| Model series | same group, `…-ModelSeries` |
| Category | `pdpData.categoryL0/L1/L2` + `pdpBreadcrumbs` |
| **MOP** | `pdpPriceReducer.pdpPriceData.sellingPrice.value` |
| **MRP** | `pdpPriceReducer.pdpPriceData.mrp.value` |
| Specifications | `pdpData.classifications` — 28 groups, 111 labelled features |
| Offers | **rendered DOM only** — not in the state blob |

**Croma's MOP label allowlist is now established** (it was UNKNOWN in M0 §11.2): the
allowlist is a *key path*, which is stronger than a rendered label. The EMI
instalment, exchange quote and bank-offer discounts live nowhere near these keys.

**Four hazards, each with a regression test:**

1. **The blob is not JSON.** It carries seven bare `undefined` literals — including
   inside `pdpReducer` and `pdpPriceReducer`. `json.loads` on the whole payload
   raises. A global `replace("undefined","null")` would corrupt the word wherever it
   legitimately appears inside strings. Parse with `extraction/js_object.py`, which
   implements JSON + `undefined` → UNKNOWN and fails closed on anything else.
2. **Only 11% of the blob is needed.** Required reducers total 49 KB; `headerReducer`
   is 392 KB of navigation chrome. Extraction is bounded — it locates member spans
   and parses only what it needs, so malformed chrome cannot break the product.
3. **The marketing description contradicts the spec table, and the table is right.**
   The prose claims "16GB of LPDDR5-5500"; `classifications` says `DDR5` / `SO-DIMM`
   / 2 slots, 1 empty. LPDDR5 is soldered and cannot be socketed. **Never read a spec
   from prose.** The conflict is emitted as a warning, never resolved silently.
4. **Bank names are rewritten client-side.** An inline script maps `JP_KOTAK` →
   "Kotak Mahindra Bank". A post-hydration capture has the display name and no code.
   Record whichever is present; never reverse-engineer the code from the name.

Also: the offer carousel header declared **7** offers while only **6** rendered.
`OfferSet` reports `declared_count` and `extracted_count` separately and warns on
disagreement rather than presenting a partial list as complete.

## ⚠️ Croma rate limiting — read before running anything live

**Croma allows roughly ONE navigation per cooldown window, then returns HTTP 403
(Akamai).** Reproduced in two independent windows on 2026-08-13:

| Window | Req 1 | Req 2 | Req 3 |
|---|---|---|---|
| 11:32 | **HTTP 200**, 485,676 bytes | 403 | 403 |
| 13:58 (146 min later) | **HTTP 200**, 485,130 bytes | **403** | — |

The 403 body carries `errors.edgesuite.net` and a `Reference #…` id.

**Consequence for debugging: never navigate twice.** Any experiment that needs
interaction must do it on the already-loaded page — `diagnose --set-pincode` exists
for exactly this reason. `PlaywrightDriver._enforce_rate_limit()` now refuses
excess navigations rather than relying on operator discipline.

**Rules:**

1. **One live run at a time.** Never loop, never retry immediately.
2. **Wait ≥15 minutes after any 403** (`rate_limit.cooldown_seconds_after_block`).
3. **Debug from stored captures, not from new requests.** This is what the
   capture/replay architecture is for — `probe --capture-id …` re-analyses a stored
   page offline as many times as needed, with zero further requests.
4. A block is a **capture failure** (`CaptureStatus.BLOCKED`), never empty data.

Repeated probing deepens the block and can cost the project its access entirely.

## Development environment constraints (dev sandbox)

**Update 2026-08-26: `pip install` now works.** `pytest`, `mypy` and `ruff` are
installed and all four gates run. The constraints below are no longer environmental —
but the stdlib-only rule for the offline core is **kept deliberately**, because it is
what makes the replay guarantee verifiable anywhere, not a workaround for a missing
package manager. Do not relax it because a dependency is now installable.

Historically the sandbox had **no usable network egress**:

- `curl` → status `000` for all hosts
- `pip install` → `SSL: CERTIFICATE_VERIFY_FAILED` (TLS interception, CA not trusted)
- Playwright, pytest, and pydantic could not be installed

**Consequences, deliberately retained:**

- The offline core (`domain/`, `browser/artifacts.py`, `browser/block_detector.py`,
  `platforms/croma/result_identifier.py`, `config/`) uses **Python stdlib only** and
  is fully testable in the sandbox.
- Playwright is imported **lazily**, only inside `browser/playwright_driver.py`.
  Importing any other module must not require it.
- Tests use `unittest` (stdlib) so they execute without pytest. They are also
  pytest-collectable when pytest is available.
- Config is JSON (stdlib) rather than YAML to avoid a PyYAML dependency.
  Migrate to YAML when dependencies can be installed.

**Do not "fix" these by adding third-party imports to the offline core.** The
dependency-free core is what makes the M0 replay guarantee testable.

## Commands

```bash
# Offline test suite — runs anywhere, no network, no third-party deps
python -m tests.run_offline
# ...or, equivalently, under pytest
PYTHONPATH=src python -m pytest -q

# M2: extract the product record from a stored capture (offline, never networks)
python -m croma_intel.cli.main extract --capture-id <id>
python -m croma_intel.cli.main extract --capture-id <id> --json
#   against the committed reference fixture:
python -m croma_intel.cli.main extract \
  --capture-id 5df6c46a45c3c93d941a5c4c078cd7e5585613729b0e5844101c65ccd13c1f4b \
  --store tests/fixtures/croma/pdp_323360

# The live M1 browser gate — REQUIRES network + `pip install playwright`
#                             + `playwright install chromium`
python -m croma_intel.cli.main m1-gate --sku 323360 --headed
python -m croma_intel.cli.main m1-gate --sku 323360 --headless

# Replay a stored capture offline (never touches the network)
python -m croma_intel.cli.main replay --capture-id <id>
```

## Layout

```
config/default.json           tunables; no magic numbers in code
src/croma_intel/
  domain/       capture.py, errors.py          pure, stdlib only
                product.py                     M2 output models; UNKNOWN, Sourced,
                                               Money, SpecificationTable, OfferSet
  extraction/   js_object.py                   bounded JSON+undefined parser
                initial_data.py                locate/slice __INITIAL_DATA__ reducers
  browser/      interfaces.py                  protocols
                block_detector.py              stdlib only
                artifacts.py                   stdlib only (content-addressed store)
                scroll.py                      policy objects, stdlib only
                playwright_driver.py           ONLY module importing playwright
  platforms/croma/
                selectors.py                   ordered fallback selector sets
                result_identifier.py           stdlib only; picks the right SKU result
                search.py                      search flow (needs a driver)
                pdp_extractor.py               M2: the authoritative PDP extractor
                offer_parser.py                M2: offers from rendered DOM
  matching/     profiles.py                    per-category CRITICAL specs, tokens, anchors
                                               (only LAPTOP admitted; one capture per new category)
  gate/         m1_gate.py                     M1 orchestration
  cli/          main.py
tests/
  unit/         offline, stdlib unittest
  replay/       offline replay guarantee
  integration/  live Croma — opt-in, never in CI
  run_offline.py                               stdlib test runner
artifacts/      runtime captures (gitignored)
docs/recon/     M0_RECON_FINDINGS.md, M1_BROWSER_GATE.md
```

## Scope discipline

Each milestone implements only itself. As of M2, these do **not** exist and must not
be added: competitor scrapers, matching engine, spec **normalization** (M2 preserves
Croma's labels verbatim; it does not canonicalise them), ranking, derivative
detection, Excel output, EUP, LLM calls.

M2 is extraction only. `pdp_extractor.py` turns one captured Croma PDP into one
structured record. It must not grow cross-platform comparison, scoring, or any
notion of a competitor.

`ResultIdentifier` picks the correct Croma search result for a *known SKU*. It is
**not** the matching engine and must not grow into one — no cross-platform
comparison, no spec comparison, no scoring beyond SKU-identity evidence.

## Docs

- `docs/recon/M0_RECON_FINDINGS.md` — live-web recon, 12 findings, GO/NO-GO
- `docs/recon/M1_BROWSER_GATE.md` — browser gate design, results, GO/NO-GO

**Not yet written** (referenced by the milestone plan but never authored):
`docs/ARCHITECTURE.md`, `docs/MATCHING_RULES.md`, `docs/PRODUCT_SCHEMA.md`,
`docs/MILESTONES.md`. The architecture currently lives only in design-session
history — writing these up is outstanding work carried from M0 §21.9.
