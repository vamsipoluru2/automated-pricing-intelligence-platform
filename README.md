# Automated Pricing Intelligence Platform

An evidence-driven system that finds the same product on competing retail sites, decides whether it is *really* the same product, and extracts a trustworthy price for it.

Given a Croma SKU, material description and model number, the platform locates the matching listing on competitor marketplaces, classifies the match with an auditable reason, and records the price with its provenance. Results are designed to feed an Excel report for pricing teams.

> **Design philosophy: a wrong match is worse than no match.** Whenever evidence is missing or contradictory, the system rejects the candidate or flags it for human review. It never guesses.

---

## Table of contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [The agents](#the-agents)
- [Architectural rules](#architectural-rules)
- [Project status](#project-status)
- [Project layout](#project-layout)
- [Getting started](#getting-started)
- [Usage](#usage)
- [Testing](#testing)
- [Responsible scraping](#responsible-scraping)
- [Documentation](#documentation)

---

## What it does

**Input:** a Croma SKU, material description and model number. Never a URL.

```
SKU:   323360
Desc:  LENOVO 15 LOQE FYIN 7KR5 16GB6X 512+3050
Model: 83S000FYIN
```

**Output:** competitor listings matched to the Croma reference product, each with:

- a **classification** (for example exact match, specification match, conflict, rejected)
- the **evidence** behind that classification and a machine-readable reason code
- a **typed, sourced price (MOP)** and MRP, never just "the price on the page"
- warnings wherever the data was incomplete or self-contradicting

Currently supported: **Croma** (reference side) and **Flipkart** (competitor side), for the **laptop** category. Amazon.in, Reliance Digital and Vijay Sales are planned.

## How it works

The platform is a pipeline of single-purpose stages. The browser only *captures*; everything else is a pure function over stored captures, so any run can be re-derived offline, forever.

```
 SKU + description + model
          │
          ▼
 ┌──────────────────┐   RawCapture    ┌──────────────────┐
 │  Capture agent   │ ──────────────► │ Immutable store  │  (content-addressed,
 │  (real browser)  │                 │ artifacts/       │   gzip, hash-verified)
 └──────────────────┘                 └────────┬─────────┘
                                               │  offline from here on
                                               ▼
                                   ┌──────────────────────┐
                                   │  Extraction agents   │  Croma PDP / Flipkart PDP
                                   └──────────┬───────────┘
                                              ▼
                                   ┌──────────────────────┐
                                   │   Normalization      │  model codes, spec values
                                   └──────────┬───────────┘
                                              ▼
                                   ┌──────────────────────┐
                                   │  Gate agent          │  condition / category /
                                   │  (reject early)      │  accessory / bundle / brand
                                   └──────────┬───────────┘
                                              ▼
                                   ┌──────────────────────┐
                                   │  Matching agent      │  decision table, no
                                   │  (classifier)        │  weighted scores
                                   └──────────┬───────────┘
                                              ▼
                                   ┌──────────────────────┐
                                   │  Selection / report  │  ranked, evidenced output
                                   └──────────────────────┘
```

## The agents

Each "agent" is a module with one responsibility, a narrow input and output contract, and no hidden side effects. They are deterministic and fully testable offline.

| Agent | Responsibility | Location |
|---|---|---|
| **Capture agent** | Drives a real browser through the genuine site UI (homepage, pincode, search box, results, click-through) and stores the page as an immutable `RawCapture`. The only component that touches the network. | `browser/`, `gate/` |
| **Block detector** | Recognises bot-protection responses (for example Akamai 403 pages) and marks the capture as `BLOCKED`, a capture failure, never empty data. | `browser/block_detector.py` |
| **Discovery / result identifier** | Picks the correct search result for a known SKU using SKU-identity evidence only. Deliberately not a matching engine. | `platforms/croma/discovery.py`, `result_identifier.py` |
| **PDP extractor (Croma)** | Parses the product page state blob (`window.__INITIAL_DATA__`) with a bounded, fail-closed parser. Extracts SKU, model number, specs, MOP, MRP and offers. | `platforms/croma/pdp_extractor.py`, `extraction/` |
| **Offer parser** | Reads bank and exchange offers from the rendered DOM, reporting declared vs. extracted counts so partial lists are never passed off as complete. | `platforms/croma/offer_parser.py` |
| **Query builder (Flipkart)** | Plans search queries from the reference product, using the model code only where it is safe to do so. | `platforms/flipkart/query_builder.py` |
| **Flipkart search / PDP extractor** | Harvests result cards and resolves the real selling price from the product page. | `platforms/flipkart/` |
| **Normalizer** | Normalizes model codes and spec values without ever removing characters from inside an alphanumeric run (so `83S000FYIN` and `83S00000US` stay different products). | `normalization/` |
| **Gate agent** | Rejects ineligible listings (refurbished, wrong category, accessories, bundles, brand mismatch) *before* any similarity work. | `matching/gates.py`, `platforms/accessory_filter.py` |
| **Spec comparator** | Compares critical specifications per category profile and produces an explicit state, not a score. | `matching/spec_compare.py`, `matching/profiles.py` |
| **Classifier** | Maps `(model comparison, spec state)` to a classification through a literal decision table with no thresholds. | `matching/classifier.py` |
| **Selection agent** | Orders candidates that already share a classification and surfaces warnings. | `pipeline/selection.py` |

### Is there an LLM in the loop?

No LLM has decision authority anywhere in this system. A `--no-llm` path is a required, complete mode of operation. Any future LLM use is limited to a small number of bounded, schema-validated call sites and can never turn a response into a classification.

## Architectural rules

These survive every milestone. Changing one requires an explicit, documented decision.

1. Capture and extraction are separated by an immutable artifact. Extraction never touches the network.
2. `UNKNOWN` is a first-class value. It is never coerced, defaulted, or silently treated as a match.
3. Missing information must never produce a match.
4. Model normalization must never remove characters from inside an alphanumeric run.
5. Gates run before ranking.
6. MOP is typed and sourced through a per-platform allowlist. It fails closed to `MOP_UNRESOLVED`.
7. Effective unit price (EUP) is out of scope. It is a downstream concern.
8. The LLM has no decision authority.
9. `--no-llm` must remain a complete execution path.
10. A wrong match is worse than no match.

## Project status

| Milestone | Scope | Status |
|---|---|---|
| M0 | Reconnaissance of target sites | Complete |
| M1 | Real-browser access gate for Croma | Complete |
| M2 | Croma product page extraction | Complete |
| M3a | Identity matching core (gates, classifier, decision table) | Implemented |
| M3b | Competitor discovery (Flipkart first) | In progress |
| M4+ | Ranking, additional competitors, Excel export | Not started |

Category rollout is deliberately one at a time: laptops first, then mobiles, tablets, TVs, and kitchen and home-care appliances.

## Project layout

```
config/default.json         all tunables, no magic numbers in code
src/croma_intel/
  domain/                   pure data models (capture, product, candidate, result)
  extraction/               bounded JSON+undefined parser, state-blob slicing
  normalization/            model-code and spec-value normalization
  browser/                  capture layer (Playwright imported lazily, only here)
  platforms/croma/          Croma search, discovery, PDP and offer extraction
  platforms/flipkart/       Flipkart query planning, search, PDP extraction
  matching/                 gates, profiles, spec comparison, classifier
  pipeline/                 run orchestration and candidate selection
  gate/                     M1 browser gate orchestration
  cli/                      command-line entry point
scripts/                    batch runners
tests/
  unit/                     offline unit tests (stdlib unittest)
  replay/                   offline replay guarantees against stored captures
  integration/              live-site tests, opt-in only, never in CI
docs/                       recon findings, gate design, matching rules, M3 brief
```

## Getting started

Requirements: Python 3.10+.

```bash
# Clone and install the dependency-free offline core
git clone <your-repo-url>
cd <repo>
pip install -e .

# Development tools (pytest, mypy, ruff)
pip install -e ".[dev]"

# Only needed for live capture
pip install -e ".[browser]"
playwright install chromium
```

The offline core (domain, extraction, matching, normalization) uses only the Python standard library by design. That is what makes the replay guarantee verifiable on any machine.

## Usage

```bash
# Inspect environment readiness
python -m croma_intel.cli.main doctor

# List or verify stored captures
python -m croma_intel.cli.main captures

# Extract a product record from a stored capture (offline, no network)
python -m croma_intel.cli.main extract --capture-id <id> --json

# Re-analyse a stored capture offline
python -m croma_intel.cli.main replay --capture-id <id>

# Live browser gate against Croma (needs network and Playwright)
python -m croma_intel.cli.main m1-gate --sku 323360 --headed
```

## Testing

```bash
# Offline suite: no network, no third-party packages
python -m tests.run_offline

# Or under pytest
PYTHONPATH=src python -m pytest -q

# Quality gates
ruff check .
mypy src
```

Live tests live in `tests/integration/` and run only when explicitly enabled (`CROMA_LIVE=1`).

## Responsible scraping

Retail sites apply bot protection and rate limits. This project is built to be conservative:

- Live captures are rare and deliberate. The result is stored so analysis never needs a second request.
- A driver-level guard refuses excess navigations rather than relying on operator discipline.
- After any block, wait at least 15 minutes before trying again. Never loop or retry immediately.
- Use this tool only on sites and at volumes you are permitted to, and respect each site's terms of service.

## Documentation

- [`docs/recon/M0_RECON_FINDINGS.md`](docs/recon/M0_RECON_FINDINGS.md): live-web recon and findings
- [`docs/recon/M1_BROWSER_GATE.md`](docs/recon/M1_BROWSER_GATE.md): browser gate design and results
- [`docs/MATCHING_RULES.md`](docs/MATCHING_RULES.md): normative identity-matching specification
- [`docs/M3_IMPLEMENTATION_BRIEF.md`](docs/M3_IMPLEMENTATION_BRIEF.md): competitor discovery and matching brief
- [`CLAUDE.md`](CLAUDE.md): project context for AI assistants and new engineers

## License

Add a license of your choice before publishing (for example MIT).
