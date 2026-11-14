# M1 — Browser Access Gate

**Milestone:** M1 (browser infrastructure + reference-product capture path only)
**Date:** 2026-08-13 (rev. 3 — real DOM evidence)
**Status:** Implementation complete. **Live run executed by the operator.** Browser access **PROVEN**. Identification defect found, root-caused, and fixed; fix awaits live re-verification.
**Verdict:** ✅ **GO — full chain proven live: homepage → pincode → UI search → identification → PDP → capture → offline replay.** See §21.

> **Revision note.** Rev. 1 recorded INCONCLUSIVE because Playwright could not be
> installed in the dev sandbox. The operator has since run the gate on a networked
> machine. **§0.5 is the authoritative account of what happened**; §1–§12 are
> preserved as the rev. 1 record, with statuses corrected in place where the live
> run superseded them.

---

## 0.5 Live Run Addendum — Browser Access Is Proven

### 0.5.1 Operator-reported result (2026-08-13)

```
M1 GATE: NO_GO
SKU: 323360
Model: 83S000FYIN

[PASS] playwright_available: available
[PASS] browser_launch: chromium launched
[PASS] croma_search: status=SUCCESS http=200
[FAIL] result_identification: search returned zero candidate results
```

### 0.5.2 What this settles — **Croma browser access: CONFIRMED**

This is the first real evidence of a browser reaching Croma, and it is decisive on the question M1 existed to answer:

| Assumption | Rev. 1 | Rev. 2 | Evidence |
|---|---|---|---|
| Playwright can launch Chromium | UNKNOWN | ✅ **CONFIRMED** | `browser_launch: chromium launched` |
| Croma serves a real browser | NOT CONFIRMED | ✅ **CONFIRMED** | `http=200` |
| Croma does **not** block Playwright | UNKNOWN | ✅ **CONFIRMED** | `status=SUCCESS` — the block detector ran and found no signal; had a wall been served, status would be `BLOCKED` |
| Page had real content | UNKNOWN | ✅ **CONFIRMED** | `SUCCESS` also means it passed the empty-shell check (>5 000 bytes with content markers) |

**M0's HTTP 403 was a property of the browserless client, exactly as M0 §14 cautioned. A real browser is served normally. Croma is not blocking us.**

### 0.5.3 Root cause of the identification failure — **a defect in my code, not Croma**

The failure is real but entirely downstream of access. Two of my own defects combined:

**Defect 1 — navigation waited on the wrong signal.**
```python
def goto(self, url, *, wait_until="domcontentloaded"):   # rev. 1
```
`domcontentloaded` fires when the initial HTML is parsed — **before** a client-rendered page fetches and renders its results.

**Defect 2 — the scroll step provided no effective delay.**
`scroll_until_stable()` measures `document.body.scrollHeight` and stops after `stability_checks` (3) consecutive unchanged readings at `settle_ms` (350 ms). On a page whose grid is still empty, the height is *trivially stable from the first measurement*, so it returned after ≈1.05 s and did nothing useful.

**There was no wait-for-content step anywhere in the gate.** The consequence:

```
goto(domcontentloaded) → shell only, no product links
scroll_until_stable()  → height stable (page is empty) → returns in ~1s
page.content()         → captures the PRE-HYDRATION shell
extract_search_candidates() → 0 anchors matching /p/{digits}
ResultIdentifier       → NOT_FOUND, "zero candidate results"   ← correctly refused to guess
```

Every component behaved correctly given its input. The identifier's refusal to fall back to a first result is exactly architectural rule 10 working as designed — it declined to invent a product from an empty page. **The bug is that it was handed a snapshot taken too early.**

**Defect 3 — the artifact could not diagnose itself.** M0 §17.1 required screenshots and visible text as capture evidence; schema v1.0.0 stored neither, and recorded no wait evidence. So the failing run produced an artifact that could not distinguish "captured too early" from "Croma returned nothing" — the single most important distinction at that moment.

### 0.5.4 Fixes applied

| # | Fix | Where |
|---|---|---|
| **F1** | **`wait_for_results()`** — waits until at least one anchor matching `/p/{digits}` exists, then settles, then waits for `networkidle` as a backstop. Timeout is an outcome, not an exception. | `browser/playwright_driver.py` |
| **F2** | Configurable capture timing: `wait_until`, `results_timeout_ms` (20 s), `results_settle_ms` (750 ms), `networkidle_timeout_ms` (10 s) | `config/settings.py` |
| **F3** | **Capture schema v1.1.0** adds `visible_text`, `screenshot_path`, `results_wait{performed, appeared, initial_count, final_count, reached_networkidle, waited_ms}` | `domain/capture.py` |
| **F4** | Screenshot, visible-text and search-input capture | `browser/playwright_driver.py` |
| **F5** | **`dom_probe.py`** — broad DOM discovery + root-cause classifier. Reports structure; never selects a product | `browser/dom_probe.py` (new) |
| **F6** | Gate runs the probe on identification failure and writes `root_cause` into the report | `gate/m1_gate.py` |
| **F7** | **`diagnose`** (live, one-shot full evidence) and **`probe`** (offline, over a stored capture) CLI commands | `cli/main.py` |
| **F8** | Regression fixtures + 32 new tests locking the failure mode | `tests/fixtures/croma/`, `tests/replay/`, `tests/unit/` |

**On the wait condition and your "do not guess selectors" instruction.** `wait_for_results()` keys on `a[href*='/p/']` — this is **not** a guessed selector. It is the single structural fact M0 confirmed empirically across twelve real Croma URLs (M0 §2.2), and it is the same fact the parser already relied on. No new selector hypotheses were introduced, and `platforms/croma/selectors.py` is **unchanged** — those remain unvalidated and must be corrected from your captured DOM.

**No confidence gate was weakened.** `ResultIdentifier` is byte-for-byte unchanged: decisive SKU evidence only, corroborating signals never sufficient, structured failure over guessing. The fix supplies a correct snapshot; it does not lower the bar for accepting one.

### 0.5.5 Offline reproduction — the failure is now pinned by tests

Committed fixtures reproduce both states without network:

| Fixture | Encodes |
|---|---|
| `search_323360_prehydration.html` | HTTP 200, full chrome, **zero `/p/` anchors**, SKU + model present only inside `__NEXT_DATA__`, SPA markers |
| `search_323360_hydrated.html` | Rendered grid; target SKU **third**, with a decoy at position 2 carrying the **correct model but wrong SKU** |

Offline probe against the failure fixture:

```
$ python -m croma_intel.cli.main probe --capture-id 3114cbdf… --sku 323360 --model 83S000FYIN
status               : SUCCESS  http=200
title                : 'Search Results - Croma'
total_anchors        : 12 (12 with href)
pdp_anchors (/p/id)  : 0
token '323360'       : FOUND x5
token '83S000FYIN'   : FOUND x1 (ctx: '"code":"323360","name":"Lenovo LOQ 15ARP10E Gaming Laptop","mpn":"83S000FYIN"')
product-ish classes  : plp=6, card=4, product=2, grid=2
overlay-ish classes  : pincode=2
spa markers          : __next_data__, id="root"
likely client-rendered: True

ROOT CAUSE: LIKELY CAPTURED BEFORE HYDRATION. SPA markers present and zero PDP
anchors; SKU '323360' IS present in raw HTML — results exist in a data payload but
were not rendered as links yet. This is a capture-timing defect in our driver, not
a Croma block and not an empty result set.
```

The classifier separates five distinct causes, each demanding a different response — anchors present (identification bug) · SPA shell (capture timing) · token present without anchors (non-anchor results) · overlay present (interstitial) · genuinely empty (a real finding about Croma). Conflating them is what sent this investigation toward "blocked" in the first place.

### 0.5.6 What is still NOT proven

**The fix has not been verified against live Croma.** The pre-hydration hypothesis is strongly supported — it explains every observed symptom and is reproduced offline — but it is a hypothesis until your next run confirms it. Alternatives remain open and the probe distinguishes them: results may render without `/p/` anchor hrefs; an interstitial may suppress them; or the search URL form may be wrong.

### 0.5.7 Your next command

```bash
python -m croma_intel.cli.main diagnose --sku 323360 --model 83S000FYIN --headed
```

This captures **everything you listed in tasks 2–5 in one run** — final URL, page title, visible body text, HTML snapshot, screenshot, navigation status, search input value, exact navigation URL, anchor counts, href histogram, `/p/` anchor count, SKU/model token hits with context, product-like and overlay-like class inventory, SPA markers — then prints a root cause. The HTML is stored, so `probe` can re-inspect it offline afterwards with no further live runs.

Then:
```bash
python -m croma_intel.cli.main m1-gate --sku 323360 --model 83S000FYIN --headed
```

---

## 0. Preface — What M1 Did and Did Not Establish

M1's brief was to prove one chain end-to-end with Playwright:

```
SKU → Croma search → correct result → PDP → browser capture → immutable artifact
```

**The chain is implemented in full and every link that can be verified without a network has been verified**, by 90 executed automated tests. The one link that requires a network — actually reaching croma.com — **could not be exercised**, because the development sandbox has no usable egress.

That distinction is the whole of this document. Two different failures look superficially alike and must not be conflated:

| Observation | Correct verdict |
|---|---|
| Playwright ran, reached Croma, and was blocked | **NO-GO** — the project's foundation is unavailable |
| Playwright could not be installed or run at all | **INCONCLUSIVE** — nothing was learned about Croma |

**This spike is the second case.** Recording it as NO-GO would wrongly kill the project on the strength of a sandbox limitation; recording it as GO would be a fabrication. It is INCONCLUSIVE, and §13 states exactly what must happen next.

### Documents that were requested but do not exist

The brief asked me to read six documents. Five have never been written:

| Requested | Status |
|---|---|
| `CLAUDE.md` | ❌ Did not exist → **created during M1** (also M0 §21.9) |
| `docs/ARCHITECTURE.md` | ❌ Does not exist |
| `docs/MATCHING_RULES.md` | ❌ Does not exist |
| `docs/PRODUCT_SCHEMA.md` | ❌ Does not exist |
| `docs/MILESTONES.md` | ❌ Does not exist |
| `docs/recon/M0_RECON_FINDINGS.md` | ✅ Exists — read in full |

The architecture, matching rules, product schema, and milestone plan currently exist **only in design-session history**. M0 §21.9 already flagged this; it remains outstanding. I did not author them under M1 because M1's scope is the browser gate, and inventing four specification documents unasked would be scope creep of exactly the kind the brief forbids. It is listed in §11 as a known limitation and in §13 as an M2 precondition.

I have preserved the ten architectural rules in `CLAUDE.md` and enforced four of them as executable tests (§10.3), which is a stronger guarantee than prose.

---

## 1. Browser Environment

### 1.1 What is present

| Component | Status | Evidence |
|---|---|---|
| Python | ✅ 3.10.0 | `python --version` |
| Node.js | ✅ v20.20.2 | `node --version` |
| pip | ⚠️ 21.2.3, **cannot reach PyPI** | see §1.2 |
| **Playwright** | ❌ **NOT INSTALLED** | `ModuleNotFoundError: No module named 'playwright'` |
| pytest | ❌ Not installed | `ModuleNotFoundError` |
| pydantic | ❌ Not installed | `ModuleNotFoundError` |

### 1.2 Why dependencies could not be installed — CONFIRMED

```
$ python -m pip install pytest
WARNING: Retrying (Retry(total=4, …)) after connection broken by
  'SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED]
   certificate verify failed: unable to get local issuer certificate (_ssl.c:997)'))'
ERROR: Could not find a version that satisfies the requirement pytest (from versions: none)
ERROR: No matching distribution found for pytest
```

TLS interception is in play and the intercepting CA is not in the trust store. Combined with M0's finding that `curl` returns status `000` for all five retailer hosts, **the sandbox has no usable outbound network.**

This is an environment property, not a project defect, and it says nothing whatsoever about Croma.

### 1.3 Design response — the constraint was turned into an architectural asset

Rather than produce untested code and assert it works, I split the system so that everything except the network hop is verifiable **right now**:

| Layer | Dependencies | Verifiable in sandbox? |
|---|---|---|
| `domain/capture.py`, `domain/errors.py` | **stdlib only** | ✅ Yes |
| `browser/block_detector.py`, `browser/artifacts.py` | **stdlib only** | ✅ Yes |
| `config/settings.py` | **stdlib only** | ✅ Yes |
| `platforms/croma/result_identifier.py`, `search_parser.py`, `selectors.py` | **stdlib only** | ✅ Yes |
| `cli/main.py` (argparse) | **stdlib only** | ✅ Yes |
| `gate/m1_gate.py` | stdlib + lazy driver | ✅ Structure yes, execution no |
| `browser/playwright_driver.py` | **Playwright** (lazy import) | ❌ Requires install |

Playwright is imported **lazily and only inside the driver**. This is enforced by a test, not by convention:

```
=== STATIC CHECK: offline core imports without playwright/pydantic ===
all 10 modules import cleanly with playwright ABSENT
playwright NOT in sys.modules -> lazy import confirmed
```

This is not a workaround. It is the M0 replay guarantee (architectural rule 1) made structural: if any offline module needed a browser, the guarantee would be unenforceable.

### 1.4 Browser capabilities implemented — PARTIALLY CONFIRMED (code complete, unexecuted)

Every capability the brief required is implemented in `browser/playwright_driver.py`:

| Requirement | Implementation | Status |
|---|---|---|
| Chromium launch | `PlaywrightDriver.start()`, engine from config | Code complete, **unexecuted** |
| Headed mode for debugging | `--headed` CLI flag → `headless=False` | Code complete, **unexecuted** |
| Headless capability | `--headless` / config default `true` | Code complete, **unexecuted** |
| Browser context creation | `new_context()` with viewport, UA, locale `en-IN`, timezone `Asia/Kolkata` | Code complete, **unexecuted** |
| Timeout handling | `navigation_timeout_ms` 45 000, `default_timeout_ms` 30 000, both configurable | Code complete, **unexecuted** |
| Persistent storage state | `storage_state` loaded on start, written on close | Code complete, **unexecuted** |
| Clean shutdown | `close()` — context → browser → playwright, each individually guarded, never raises | Code complete, **unexecuted** |
| Navigation outcome recording | `NavigationOutcome(ok, http_status, final_url, error)` | Code complete, **unexecuted** |

**Nothing in this table has run against a real browser.** They are marked "code complete" and must not be read as validated.

---

## 2. Croma Accessibility

### **✅ CONFIRMED (rev. 2) — a real browser reaches Croma and is served normally.**

> **Superseded.** The rev. 1 text below stands as the sandbox record. The operator's
> live run (§0.5) returned `http=200`, `status=SUCCESS`, no block signal. **Croma
> does not block Playwright.** M0's 403 was a property of the browserless client.

### Rev. 1 record — NOT CONFIRMED (no Croma request was made in the sandbox)

Zero network requests were issued to croma.com during this milestone. The gate correctly refuses to run and reports why:

```
$ python -m croma_intel.cli.main m1-gate --sku 323360 --model 83S000FYIN

M1 GATE: INCONCLUSIVE
  sku      : 323360
  model    : 83S000FYIN
  report   : artifacts\reports\m1_gate_323360.json

  steps:
    [FAIL] playwright_available: ModuleNotFoundError: No module named 'playwright'

  Playwright is not installed, so the browser gate could not be executed. This is an
  ENVIRONMENT limitation, not evidence about Croma. Run this gate on a networked
  machine before deciding GO/NO-GO.

exit code = 2
```

The exit code is deliberate and machine-checkable:

| Exit | Meaning |
|---|---|
| `0` | **GO** — full chain succeeded |
| `1` | **NO-GO** — Croma was reached and the chain failed (block, no identification, bad capture) |
| `2` | **INCONCLUSIVE** — the gate could not execute; no conclusion about Croma |

A CI job or an operator can therefore never misread an environment failure as a Croma verdict.

**M0's finding stands unchanged and unrefuted:** Croma returned HTTP 403 to a browserless client on 4/4 requests. M1 was supposed to test that against a real browser and did not get the chance.

---

## 3. Search Behaviour

### **UNKNOWN — no live search was executed.**

What is implemented and verified offline:

**Search URL construction — CONFIRMED (unit-tested):**
```
Settings().search_url("323360") == "https://www.croma.com/search/?text=323360"
```
Query values are percent-encoded (`"lenovo loq"` → `…text=lenovo%20loq`).

**The gate never accepts a URL as input.** `m1-gate` takes `--sku` and `--model` only; the PDP URL is *derived* from search results at runtime. There is no code path that navigates to a supplied PDP URL, which is what the brief required.

**Search-result parsing — CONFIRMED offline.** `platforms/croma/search_parser.py` is a pure function over stored HTML using `html.parser` (stdlib). It keys on the one thing M0 actually confirmed — Croma PDP URLs contain `/p/{numeric-id}` — rather than on unvalidated CSS selectors. Verified behaviour:

| Behaviour | Status |
|---|---|
| Extracts only `/p/{id}` links, ignores `/c/{id}` category links | ✅ tested |
| Deduplicates by PDP id, keeps richest title | ✅ tested |
| Resolves relative → absolute URLs | ✅ tested |
| Records document-order position | ✅ tested |
| Tolerates malformed HTML without raising | ✅ tested |
| Respects a candidate limit | ✅ tested |

Being a pure function of stored HTML, search results can be **re-derived offline from a capture** — which is what makes the M0 replay guarantee real rather than aspirational.

**Unknown until the live run:** actual search-result DOM structure, result count for SKU `323360`, whether Croma search returns accessories or unrelated products for a SKU query, whether the SKU is rendered on result cards, and whether pagination/lazy-loading applies.

---

## 4. SKU Search Result Behaviour

### 4.1 The M0 Q2 hypothesis is testable but **NOT CONFIRMED**

M0 §2.2 inferred from twelve real URLs that **the Croma SKU is the numeric `/p/{id}`** — e.g. SKU `323360` → `croma.com/…/p/323360`. The gate tests this at runtime rather than assuming it: `SKU_IN_PDP_URL` is the primary decisive evidence, and if the hypothesis is false the gate fails with a structured `NOT_FOUND` rather than silently picking something.

### 4.2 Correct-product identification — CONFIRMED offline

`ResultIdentifier` implements the brief's requirement that the system must **not** simply choose the first result.

**Evidence hierarchy:**

| Evidence | Weight | Rationale |
|---|---|---|
| `SKU_IN_PDP_URL` — PDP id equals the requested SKU | **Decisive** | Exact identity |
| `SKU_IN_TEXT` — exact SKU token rendered on the card | **Decisive** | Exact identity |
| `MODEL_IN_TITLE` — boundary-safe model token in title | Corroborating only | Recorded, never sufficient |
| `MODEL_IN_URL` — boundary-safe model token in URL | Corroborating only | Recorded, never sufficient |

**Live demonstration, executed with sockets disabled**, with the target deliberately placed third and a *decoy carrying the correct model number* placed second:

```
candidates : [(1, '111111'), (2, '222222'), (3, '323360')]
note       : position 2 has the MODEL in its title but the WRONG sku
confidence : CONFIDENT | chosen position: 3
PASS       : picked position 3 (SKU evidence) over position 1 and model-decoy at 2
```

The identifier ignored both the first result and the model-bearing decoy, choosing on SKU identity alone.

### 4.3 Structured failures — CONFIRMED

Every non-confident outcome returns a JSON-serializable `IdentificationReport` rather than a guess:

| Situation | Confidence | Chosen | Reason |
|---|---|---|---|
| Exactly one decisive SKU hit | `CONFIDENT` | that candidate | evidence quoted |
| Zero results | `NOT_FOUND` | `None` | "search returned zero candidate results" |
| Results, but no SKU evidence | `NOT_FOUND` | `None` | "…refusing to fall back to first result" |
| Model matches but SKU does not | `NOT_FOUND` | `None` | corroborating evidence recorded, not acted on |
| Two candidates with the same SKU | `AMBIGUOUS` | `None` | "refusing to guess between them" |

The `AMBIGUOUS` case is worth noting: duplicate decisive evidence indicates a search or extraction defect, and picking either one would mask it.

### 4.4 Model-token comparison honours architectural rule 4 — CONFIRMED

`contains_model_token()` is boundary-safe and **never removes characters from inside an alphanumeric run**. Tested against the real product data M0 gathered:

| Case | Expected | Result |
|---|---|---|
| `83S000FYIN` in `"LOQ 83S000FYIN Gaming"` | match | ✅ |
| `83S000FYIN` in `"83S000FYIN-IND"` (delimiter-bounded) | match | ✅ |
| `83S000FYIN` in `"83S000FYINX"` | **no match** | ✅ |
| `83S000FYIN` in `"183S000FYIN"` | **no match** | ✅ |
| `83S000FYIN` vs `83S00000US` (M0 proof 1: India vs US) | **no match** | ✅ |
| `83DV018NIN` vs `83DV018QIN` (M0 proof 3: edit distance 1) | **no match** | ✅ |
| Empty input either side (rule 3) | **no match** | ✅ |

---

## 5. PDP Accessibility

### **UNKNOWN — no PDP was opened.**

Implemented in `gate/m1_gate.py` step 4, pending live execution:

- navigate to the identified PDP URL (never a hard-coded one);
- controlled scrolling (§7);
- probe for known expander selectors and record which are present;
- capture with `final_url` recorded and `redirected` computed;
- classify BLOCKED / EMPTY_SHELL / SUCCESS before anything downstream runs.

**M0 F10 is guarded.** M0 observed a Vijay Sales URL for product `230914` serving SKU `243423`. `RawCapture.redirected` compares requested vs final URL and the value is persisted in the artifact, so an ID substitution is visible rather than silent.

Per the brief, **no specification extractor was built.** The PDP step proves access and capture only.

---

## 6. Pincode Behaviour

### **NOT CONFIRMED — deliberately not implemented.**

Configuration exists (`croma.test_pincode: "400001"`, `croma.attempt_pincode: true`) and hypothesised selectors are listed in `platforms/croma/selectors.py`, but **no pincode interaction code was written.**

This was a judgement call, and I want to be explicit about it rather than have it look like an omission. M0 obtained **zero** Croma page content, so every pincode selector would be a pure guess. Writing an interaction sequence against guessed selectors would produce code that:

- cannot be tested (no fixture exists),
- will almost certainly be wrong,
- and — worst — could silently "succeed" by matching nothing, producing a capture that *appears* pincode-scoped but is not. That is a silent-wrong artifact, exactly what architectural rule 10 exists to prevent.

The correct sequence is: run the live gate, capture a real PDP, **read the actual pincode DOM from the captured HTML**, then implement against evidence. The captured artifact makes that a purely offline exercise afterwards.

**Recorded as an M2 precondition in §13.** No EUP was introduced, and no bank offers were interpreted (rule 7).

---

## 7. Dynamic / Scroll Behaviour

### **PARTIALLY CONFIRMED — implemented and schema-tested; never run against a real page.**

`scroll_until_stable()` implements a bounded, configurable loop: scroll by viewport, wait `settle_ms`, re-measure `document.body.scrollHeight`, stop after `stability_checks` consecutive unchanged heights, `max_scrolls`, or `max_duration_ms`.

It always returns a `ScrollReport`, which is persisted **inside the capture**:

```json
"scroll": {
  "performed": true, "scroll_count": 5,
  "initial_height_px": 0, "final_height_px": 0, "height_changed": false,
  "reached_stable": true, "expanders_clicked": 0, "expander_labels": [], "duration_ms": 0
}
```

This is the evidence that distinguishes **"the content is absent"** from **"the content had not loaded yet"** — without it, a partial capture is indistinguishable from a complete one, and every downstream extraction becomes untrustworthy.

Expander selectors are *probed and recorded* (`extra.expander_selectors_present`) but **not clicked**. Same reasoning as §6: clicking unvalidated selectors is guesswork, whereas recording which of the hypothesised selectors exist turns the first live run into evidence that corrects `selectors.py`.

---

## 8. CAPTCHA / Block Behaviour

### **CONFIRMED offline — detection logic is fully tested in both directions.**

`browser/block_detector.py` uses weighted signals with an explicit threshold plus *decisive* signals that alone force a BLOCKED verdict.

**Decisive:** HTTP 403 / 429 / 503; `<title>` matching *access denied*, *attention required*, *just a moment*, *robot check*, *security check*, *403 forbidden*, *are you a human*, *blocked*.

**Weighted body markers:** Cloudflare (`/cdn-cgi/challenge-platform`, `cf-browser-verification`, `cf_chl_opt`), reCAPTCHA / hCaptcha widgets, PerimeterX, Incapsula, Amazon's *"Enter the characters you see below"*, *"verify you are human"*, Akamai reference, *unusual traffic*.

**Weak:** body-size signals, **suppressed entirely when a legitimate content marker is present.**

### 8.1 The false-positive half is tested as rigorously as the true-positive half

The brief was explicit: *"Do not classify normal Croma content as a block."* A detector that flags real pages would destroy every run, so these are regression guards:

| Case | Must be | Result |
|---|---|---|
| Normal Croma PDP, HTTP 200 | not blocked | ✅ |
| Page with the word *"captcha"* in help prose | not blocked | ✅ |
| Small page carrying a content marker | not blocked | ✅ |
| **HTTP 404** | **not blocked** (missing ≠ walled) | ✅ |
| Clean page | evidence still recorded (`"no block signals detected"`) | ✅ |

### 8.2 A test I got wrong, and the design decision behind fixing it

My first version of the suite asserted that a 13-byte body (`<html></html>`) must be `BLOCKED`. It failed: the detector scored it 0.8 against a 1.0 threshold.

On inspection **the detector was right and my test was wrong.** A tiny body is genuinely ambiguous — a bot wall and a broken page look identical at that size, and they need different handling. Asserting "tiny ⇒ blocked" would have introduced exactly the false-positive class §8.1 exists to prevent.

Crucially, a tiny body still cannot reach downstream as good data: `is_empty_shell()` catches it and the capture is classified `EMPTY_SHELL`, never `SUCCESS`. So the safety property holds through a different, more accurate path.

I corrected the test to assert the true behaviour (signal recorded, score raised, verdict withheld, empty-shell catches it) and added a companion test proving that tiny-body **plus one weak corroborating signal** does cross the threshold. Recorded here because the reasoning matters more than the line change.

### 8.3 Blocks are capture failures, never empty extractions

`CaptureStatus` distinguishes `SUCCESS` / `BLOCKED` / `EMPTY_SHELL` / `NAVIGATION_FAILED` / `TIMEOUT`. The gate halts on `BLOCKED` and returns NO-GO with the evidence captured — it never proceeds to identification with a wall's HTML. `EMPTY_SHELL` exists specifically for M0 F3 (Reliance Digital's HTTP 200 with no content).

---

## 9. Capture Format

### **CONFIRMED — implemented, tested, and exercised end-to-end offline.**

### 9.1 Required fields — all present

| Required by brief | Field |
|---|---|
| capture_id | `capture_id` — SHA-256, content-derived |
| timestamp | `captured_at` — UTC ISO-8601, ms precision |
| platform | `platform` |
| input_sku | `input_sku` |
| request URL | `requested_url` |
| final URL | `final_url` (+ derived `redirected`) |
| page title | `page_title` |
| raw HTML | `html` (gzipped blob) + `html_sha256` + `html_length` |
| capture metadata | `page_kind`, `http_status`, `notes`, `extra`, `schema_version` |
| browser metadata | `browser{engine, version, headless, user_agent, viewport, locale, timezone, proxy_used}` |
| success/failure status | `status` + `block{…}` + `scroll{…}` |

### 9.2 Storage layout

```
artifacts/captures/
  manifests/<capture_id>.json                      human-readable, diffable
  blobs/<sha[:2]>/<html_sha256>.html.gz            content-addressed, deduplicated
```

Blobs are keyed by content hash, so re-capturing an unchanged page costs no extra bytes — **verified by test**: two captures with identical HTML but different timestamps produce two manifests and exactly one blob.

### 9.3 Integrity is verifiable, not assumed

`capture_id` is a SHA-256 over the canonical (sorted-key) manifest excluding `capture_id` itself. Consequences, all tested:

- identical inputs → identical id (deterministic);
- different HTML or timestamp → different id;
- **key ordering does not affect the id** (an unsorted dump would silently break verification);
- tampering with the HTML → `html_sha256 mismatch`;
- tampering with any manifest field → `capture_id mismatch`.

`CaptureStore.read()` verifies by default and raises `CaptureIntegrityError` rather than returning suspect data.

### 9.4 Immutability

`RawCapture` and every nested structure are frozen dataclasses. Attempted mutation raises — tested.

---

## 10. Replay Result

### **CONFIRMED — the offline guarantee is enforced by disabling sockets, not by convention.**

### 10.1 Executed evidence

A seeded Croma-shaped search capture (5 500 bytes) was written, then the full downstream pipeline was run with `socket.socket` and `socket.create_connection` patched to raise:

```
loaded            : 6b502a400827cc04 | html bytes: 5500
integrity         : (True, 'ok')
candidates parsed : [(1, '323360'), (2, '999999')]
confidence        : CONFIDENT
chosen position   : 1
chosen url        : https://www.croma.com/lenovo-loq-15arp10e/p/323360
reason            : exactly one candidate carries decisive SKU identity evidence:
                    PDP url id '323360' == requested SKU '323360'
```

Capture load → integrity verification → HTML parse → candidate extraction → SKU identification, all with the network made physically unavailable. *(In this particular fixture the target happened to sit at position 1; the no-first-result guarantee is proven separately in §4.2, where the target is third.)*

Via the CLI:
```
$ python -m croma_intel.cli.main replay --capture-id 6b502a40…
html bytes loaded offline: 5500
integrity: OK (ok)

$ python -m croma_intel.cli.main captures --verify
[OK  ] 6b502a400827cc0465f98f1b61187f9e1eb0243cdfae1d335b9ae4cb082f55ea ok
1 captures, 0 failures
```

### 10.2 Test suite result — 90 tests, all passing

```
======================================================================
CROMA-INTEL — OFFLINE TEST SUITE (no network, no third-party deps)
======================================================================
python : 3.10.0
scope  : tests/unit, tests/replay
----------------------------------------------------------------------
Ran 90 tests in 0.129s
OK
run=90 failures=0 errors=0 skipped=0
RESULT: PASS
======================================================================
```

| Suite | Covers |
|---|---|
| `test_capture_schema.py` | fields, determinism, key-order independence, serialization round-trip, immutability, tamper detection |
| `test_block_detector.py` | real blocks detected; **legitimate content not flagged**; empty-shell |
| `test_result_identifier.py` | no-first-result, boundary-safe model tokens, structured failures, ambiguity |
| `test_search_parser.py` | PDP-link extraction, dedup, malformed HTML, config round-trip |
| `test_architecture_rules.py` | executable guards on the architectural rules (§10.3) |
| `test_replay_offline.py` | **sockets disabled**, integrity, dedup, store failure modes |

### 10.3 Architectural rules enforced as tests, not prose

| Rule | Test |
|---|---|
| 1 — capture/extraction separated | Playwright import confined to the driver; offline modules import with it absent |
| 2 / 3 — UNKNOWN first-class; missing info never matches | absent model → never `CONFIDENT`; empty inputs → no token match |
| 4 — never strip inside an alphanumeric run | seven boundary cases from real M0 data |
| 7 — **EUP does not exist** | AST walk over all source; no EUP identifier, attribute, or string constant |
| 10 — wrong match worse than no match | corroborating-only evidence can never promote a candidate |
| M1 scope | no `matching/`, `ranking/`, `excel/`, `llm/`, `derivative/` packages; `platforms/` contains only `croma` |

The EUP guard is AST-based rather than text-based: it forbids EUP *identifiers* while permitting a docstring that declares EUP out of scope. Banning the word outright would discourage stating the boundary explicitly, which is worse for the codebase.

### 10.4 Static checks

```
compileall src tests : OK
offline core imports : all 10 modules import cleanly with playwright ABSENT
lazy import          : playwright NOT in sys.modules after importing everything
```

`mypy` and `ruff` are configured in `pyproject.toml` but **could not be run** — not installable (§1.2).

---

## 11. Known Limitations

| # | Limitation | Severity | Consequence |
|---|---|---|---|
| **L1** | **The live gate has never been executed.** No Croma request was made in M1. | 🔴 **Critical** | M1's central question is unanswered. §13. |
| **L2** | All Playwright code is **unexecuted**. Launch, context, timeouts, storage state, scrolling, shutdown are code-complete but unproven. | 🔴 Critical | First live run should be expected to surface bugs. |
| **L3** | **Every selector in `platforms/croma/selectors.py` is a guess.** M0 obtained zero Croma HTML. | 🟠 High | They are hypotheses. The gate records which ones exist so the first run corrects them. |
| **L4** | **Pincode interaction not implemented** (§6). | 🟠 High | Deliberate. Needs a real DOM first. |
| **L5** | **Expanders probed, not clicked** (§7). | 🟡 Medium | Deliberate, same reasoning. |
| **L6** | Config is JSON, not YAML. | 🟢 Low | PyYAML uninstallable. Shape is unchanged; migrates directly. |
| **L7** | `mypy` / `ruff` not run. | 🟡 Medium | Not installable. `compileall` passed. |
| **L8** | **Four specification documents do not exist** (ARCHITECTURE, MATCHING_RULES, PRODUCT_SCHEMA, MILESTONES). | 🟠 High | Architecture lives only in session history. Carried from M0 §21.9. |
| **L9** | Search-result parsing keys on `/p/{id}` URLs rather than result-card containers. | 🟡 Medium | Robust to redesign, but cannot yet read per-card SKU/category text. Refine after the first real capture. |
| **L10** | No proxy support; `proxy_used` is always `false`. | 🟢 Low | Out of M1 scope. Interface point exists. |

---

## 12. Evidence

### 12.1 Files created — 22 files, 2 134 LOC source, 1 074 LOC tests

```
CLAUDE.md                                       156   project context + 10 architectural rules
pyproject.toml                                   44   zero required deps; browser/dev extras
config/default.json                              46   all tunables
.gitignore                                       23

src/croma_intel/
  domain/capture.py                             344   RawCapture, statuses, integrity   [stdlib]
  domain/errors.py                               71   typed error hierarchy             [stdlib]
  browser/artifacts.py                          116   content-addressed store           [stdlib]
  browser/block_detector.py                     178   CAPTCHA/bot-wall detection        [stdlib]
  browser/playwright_driver.py                  315   ONLY module importing playwright
  config/settings.py                            121   layered config                    [stdlib]
  platforms/croma/result_identifier.py          282   SKU identification, no guessing   [stdlib]
  platforms/croma/search_parser.py              145   pure HTML → candidates            [stdlib]
  platforms/croma/selectors.py                   91   UNVALIDATED selector hypotheses   [stdlib]
  gate/m1_gate.py                               311   M1 orchestration
  cli/main.py                                   157   argparse CLI                      [stdlib]

tests/
  run_offline.py                                 65   stdlib runner
  unit/test_capture_schema.py                   148
  unit/test_block_detector.py                   136
  unit/test_result_identifier.py                161
  unit/test_search_parser.py                    119
  unit/test_architecture_rules.py               168
  replay/test_replay_offline.py                 168
  integration/test_croma_live.py                109   opt-in; triple-guarded

docs/recon/M1_BROWSER_GATE.md                        this document
```

### 12.2 Commands and results

| Command | Result |
|---|---|
| `python -m tests.run_offline` | **90 tests, 0 failures, 0 errors — PASS** |
| `python -m compileall -q src tests` | **OK** |
| `python -m croma_intel.cli.main doctor` | playwright MISSING, exit 1 |
| `python -m croma_intel.cli.main m1-gate --sku 323360 --model 83S000FYIN` | **INCONCLUSIVE, exit 2** |
| `python -m croma_intel.cli.main captures --verify` | 1 capture, 0 failures |
| `python -m croma_intel.cli.main replay --capture-id …` | integrity OK, 5 500 bytes offline |
| offline pipeline with sockets disabled | capture → parse → identify, **CONFIDENT** |
| no-first-result demo (target at position 3) | **picked position 3**, ignored model-decoy at 2 |

### 12.3 How to run the live gate

```bash
pip install -e ".[browser]"
playwright install chromium

# Headed, for debugging — watch it work
python -m croma_intel.cli.main m1-gate --sku 323360 --model 83S000FYIN --headed

# Headless
python -m croma_intel.cli.main m1-gate --sku 323360 --model 83S000FYIN --headless

# Or as an integration test (triple-guarded: env var + playwright + directory)
CROMA_LIVE=1 python -m tests.integration.test_croma_live
```

Exit `0` = GO · `1` = NO-GO · `2` = INCONCLUSIVE. A JSON report lands in `artifacts/reports/`, captures in `artifacts/captures/`.

---

## 13. M1 GO / NO-GO (rev. 1 — superseded by §14)

# ⚠️ INCONCLUSIVE

**Not GO. Not NO-GO. The gate was built but could not be run.**

### What was achieved

1. **The full chain is implemented** — SKU → search → identification → PDP → capture → artifact — and takes **no URL as input**, as required.
2. **90 automated tests pass**, covering capture schema, serialization, integrity, block detection (both directions), result identification, offline replay with sockets disabled, and executable guards on the architectural rules.
3. **The replay guarantee is proven** — capture → parse → identify runs to a `CONFIDENT` result with the network physically disabled.
4. **The no-guessing requirement is proven** — the identifier chose the third result over both the first result and a decoy carrying the correct model number.
5. **Block detection will not sabotage real runs** — 404s, prose mentions of "captcha", and small-but-real pages are all correctly *not* flagged.
6. **Architectural rules are enforced in CI-able tests**, including an AST-level guard that no EUP identifier exists anywhere in the source.

### What was not achieved

**The one thing M1 existed to determine: whether Playwright can reliably access Croma.**

Playwright could not be installed (`SSL: CERTIFICATE_VERIFY_FAILED`), so not a single request was made to croma.com. Every browser-layer capability is code-complete and **unexecuted**.

### Why this is INCONCLUSIVE rather than NO-GO

M0 recorded Croma returning HTTP 403 to a browserless client and was explicit that this characterises *a weak client*, not Croma's posture toward a real browser. M1 was meant to settle that and could not. Declaring NO-GO would kill the project on the basis of a sandbox TLS configuration — precisely the unsupported inference both M0 and M1 were designed to avoid.

### Required next action — before any M2 work

> **Run the live gate on a networked machine.**
> ```bash
> pip install -e ".[browser]" && playwright install chromium
> python -m croma_intel.cli.main m1-gate --sku 323360 --model 83S000FYIN --headed
> ```
>
> | Exit | Verdict | Next step |
> |---|---|---|
> | `0` | **M1 GO** | Attach the JSON report + captures here. Then address L3/L4/L5 (real selectors, pincode, expanders) from the captured HTML — all offline work. Then M2. |
> | `1` | **M1 NO-GO** | **Halt.** Escalate to a scope decision: official Croma data access, a different execution environment, or re-scoping. Do not engineer around a hard block. |
> | `2` | Still INCONCLUSIVE | Environment problem persists; fix installation before drawing any conclusion. |

### Preconditions for M2, beyond the gate itself

1. Live gate returns **GO** with a stored PDP capture.
2. `selectors.py` corrected **from the captured HTML**, not from guesswork (L3).
3. Pincode mechanism implemented against the real DOM (L4).
4. The four missing specification documents written (L8) — the architecture cannot stay in session history.

### Scope statement

M1 implemented only M1. No competitor scrapers, matching, ranking, derivative detection, specification extraction, Excel, EUP, or LLM code exists — asserted by prose and **enforced by tests** (§10.3). M2 has not been started.

---

**END OF M1 BROWSER GATE REPORT**

---

## 14. M1 VERDICT (rev. 2 — superseded by §16)

# ⚠️ INCONCLUSIVE

**Browser access works. Reference-product identification remains unresolved live.**

This is the middle option of the three, and it is a materially better position than rev. 1: the question that could have killed the project is now answered.

### ✅ Proven

| Claim | Evidence |
|---|---|
| **Playwright launches Chromium** | `browser_launch: chromium launched` |
| **Croma serves a real browser — it does not block Playwright** | `croma_search: status=SUCCESS http=200`; block detector ran and found no signal |
| The page had genuine content, not an empty shell | `SUCCESS` requires passing the empty-shell check |
| The identifier refuses to guess | It returned `NOT_FOUND` on an empty page rather than inventing a product — rule 10 working correctly |
| Root cause of the zero-candidate failure | Capture taken before hydration; reproduced offline and pinned by tests |
| The failure is now self-diagnosing | Probe classifies five distinct causes; schema v1.1 persists screenshot, visible text and wait evidence |

### ❌ Not yet proven

| Claim | Why not |
|---|---|
| **The fix actually works against live Croma** | Not re-run. The hypothesis explains every symptom and reproduces offline, but it is unconfirmed live. |
| Reference-product identification end to end | Depends on the above. |
| Croma PDP access | Never reached — the run stopped at identification. |
| Everything in `platforms/croma/selectors.py` | Still unvalidated guesses (L3). Deliberately **unchanged**. |
| Pincode, expanders | Still not implemented (L4, L5) — still awaiting a real DOM. |

### Why not GO

GO requires *both* access and identification proven. Identification is fixed in code and reproduced offline, but has not run against Croma. Declaring GO on an unverified fix would be exactly the confident guess this architecture exists to prevent.

### Why emphatically not NO-GO

NO-GO means Croma genuinely blocks the browser. **The evidence says the opposite.** `http=200` with no block signal is a normally-served page. The failure was in my capture timing. Nothing here supports stopping.

### Architecture: unchanged

No architectural rule was modified, relaxed, or reinterpreted. `ResultIdentifier` is byte-for-byte identical. No confidence gate was weakened. No selector was guessed — the wait keys on the `/p/{digits}` pattern M0 confirmed across twelve real URLs. Capture/extraction separation, offline replay, no-first-result, UNKNOWN-as-real, no-missing-data→MATCH, no-LLM-authority and no-EUP all remain enforced by tests.

### To reach GO

```bash
# 1. Full live evidence in one shot (tasks 2-5)
python -m croma_intel.cli.main diagnose --sku 323360 --model 83S000FYIN --headed

# 2. The gate itself
python -m croma_intel.cli.main m1-gate --sku 323360 --model 83S000FYIN --headed
```

Exit `0` = **GO** · `1` = identification still failing (the JSON report carries `root_cause` and `dom_probe`) · `2` = environment problem.

Send me `artifacts/reports/m1_gate_323360.json`, the screenshot, and the capture id. If it still fails, the probe output tells us which of the remaining causes it is, and I will correct `selectors.py` and the parser **from your captured DOM** — offline, no further live runs needed to develop the fix.

### Test status

**122 offline tests, 0 failures** (90 in rev. 1 + 32 new). `compileall` clean. Offline core imports with Playwright absent.

**M2 remains not started.**

---

**END OF M1 BROWSER GATE REPORT (rev. 2)**

---

# 15. Rev. 3 — Live `diagnose` Run: Real DOM Evidence

**Date:** 2026-08-13. Command: `diagnose --sku 323360 --model 83S000FYIN --headed`.
**This section supersedes §14.** It is based on an actual Croma page, not inference.

## 15.1 ⚠️ Headline operational finding — Croma rate-limits aggressively

Three requests were made in quick succession. Only the first succeeded:

| # | Request | Result |
|---|---|---|
| 1 | `diagnose` → `/search/?text=323360` | **HTTP 200**, 485,676 bytes, real content |
| 2 | exploratory DOM inspection | **HTTP 403** (silent — the ad-hoc script bypassed the detector) |
| 3 | staged React-mount polling | **HTTP 403 Access Denied**, body: *"You don't have permission to access…"*, `Reference #18.7ca2dfad.1786601179.1011c308`, `errors.edgesuite.net` |

`errors.edgesuite.net` is **Akamai**. This is progressive, rate-based bot defense: a browser is served, then throttled after a few requests in a short window.

**I stopped live requests immediately on seeing the second 403.** Continued probing deepens the block and risks the project's access. All subsequent analysis was done offline from the stored capture — precisely the situation the capture/replay architecture was designed for, and the first time it has actually paid for itself.

**This is a new architectural constraint, not previously known.** M0 saw a 403 from a browserless client; this is different — a *real browser* gets 200, then 403. Consequences recorded in `CLAUDE.md`:

- one live run at a time, never loop or auto-retry;
- at least 15 minutes cooldown after any 403;
- debug from stored captures via `probe`, never from fresh requests;
- new `RateLimitConfig` (`min_seconds_between_requests: 20`, `max_requests_per_session: 10`, `cooldown_seconds_after_block: 900`, `abort_run_after_consecutive_blocks: 2`).

## 15.2 What the successful load actually contained

```
requested url   : https://www.croma.com/search/?text=323360
final url       : https://www.croma.com/search/?text=323360   (no redirect)
http status     : 200
capture status  : SUCCESS
page title      : 'Search 323360 | Croma Electronics | Online Electronics Shopping | ...'
block verdict   : blocked=False (no block signals detected)
results wait    : performed=True appeared=False final_count=0
                  reached_networkidle=True waited_ms=20139
scroll          : scroll_count=3 initial=1230px final=1230px height_changed=False
html_length     : 485,676
visible_text    : 696 chars
total_anchors   : 51 (46 with href) - ALL site chrome, zero product links
```

**Browser access is CONFIRMED again, independently:** HTTP 200, half a megabyte of genuine Croma markup, and the block detector cleared it.

**The F1 fix worked as designed and correctly reported failure.** `wait_for_results` waited the full 20.1 s, reached networkidle, and honestly recorded `appeared=False`. It did not silently proceed.

### The decisive observation

```
=== ANY /p/ SUBSTRING AT ALL (not just anchors)? ===
count: 0
```

**Zero occurrences of `/p/<digits>` in 485,676 bytes** — not in anchors, not in JSON payloads, not in scripts. Product data was never delivered to the page. Every product-data marker probed (`plpProduct`, `productList`, `resultCount`, `numFound`, `products":`) was also zero.

### What *was* rendered — the visible text, verbatim

```
Menu
Update pincode for best prices and offers
Add Pincode
0
CONNECT WITH US
... footer links ...
SELECT YOUR LOCATION
To Check Products & Delivery Options available at your location
OR
Sign in to select address
Continue
```

696 characters: navigation chrome, footer, and a **location selector**. No results area, and no "no results found" message either.

## 15.3 My rev. 2 hypothesis was WRONG

Rev. 2 predicted *"captured before hydration"*. The real capture refutes it:

| Rev. 2 prediction | Reality |
|---|---|
| Products present in a data payload, not yet linked | **No product data anywhere** — zero `/p/<digits>`, zero list markers |
| SPA still hydrating when captured | **networkidle reached** after 20 s; the app had finished and fetched nothing |
| Waiting longer would fix it | Waiting 20 s changed nothing |

Recorded plainly, because the M0/M1 discipline is that a hypothesis contradicted by evidence gets retired, not defended. The synthetic pre-hydration fixture stays in the suite — it is still a real failure mode worth guarding against — but it was not *this* failure.

## 15.4 Two bugs in my diagnostics, both found by real data

**Bug A — echoed-query hits misread as product evidence.** The probe reported `token '323360': FOUND x10` and concluded *"Results are probably rendered without /p/ anchor hrefs."* **That was confidently wrong.** All ten hits were the search query echoed into `<meta name="title">`, `<meta name="keywords">`, `<title>`, and analytics URLs. None was product data.

Fixed: `find_token` now classifies every occurrence as `HEAD_OR_SCRIPT`, `ATTRIBUTE_OR_URL` or `BODY_CONTENT`, and returns `meaningful_count`. The classifier consumes `meaningful`, never raw `present`. On this capture the SKU now correctly reports `meaningful=False`.

**Bug B — the SPA marker list missed React.** Croma uses `data-react-helmet` and `div#root`; my markers (`__NEXT_DATA__`, `data-reactroot`, ...) matched none, so the probe reported `spa markers: none` and `likely_client_rendered: False` — both wrong. Both markers added.

## 15.5 Corrected diagnosis

```
spa markers          : data-react-helmet, div#root
likely client-rendered: True
location gate markers: select your location, check products & delivery options,
                       sign in to select address, update pincode for best prices, add pincode
likely location-gated: True

ROOT CAUSE: LOCATION/PINCODE GATE. Visible text is the location selector ... with zero
PDP anchors - the page is asking for a delivery location instead of showing results.
Not a block and not an empty result set. NOTE: a soft/degraded anti-bot response can
look identical; confirm by setting a location and re-running ONCE.
```

**Leading hypothesis: Croma gates search results behind a delivery-location selection.** Supported by the visible text, by 19 `pincode` and 13 `location` class tokens, and by an `Enter Pincode` input.

**Competing hypothesis I cannot yet exclude: a soft anti-bot response.** The two 403s that followed make it entirely plausible that request 1 was already degraded — served 200 with chrome but no data, as an early throttle step. `reached_networkidle=True` with *no product XHR at all* fits a soft block at least as well as a location gate.

**I cannot distinguish these without another live request, and another live request is exactly what the rate limiting punishes.** So both are recorded, the classifier names both, and the next run is designed to separate them in one shot.

## 15.6 Changes in rev. 3

| # | Change | File |
|---|---|---|
| G1 | Token hits classified by region; `meaningful_count` added; classifier uses it | `browser/dom_probe.py` |
| G2 | SPA markers: `data-react-helmet`, `div#root`, `id='root'` | `browser/dom_probe.py` |
| G3 | `LOCATION_GATE_MARKERS` + `likely_location_gated`, checked **before** token evidence | `browser/dom_probe.py` |
| G4 | Akamai deny detection: `errors.edgesuite.net`, permission-denied text, reference-id pattern | `browser/block_detector.py` |
| G5 | `RateLimitConfig` | `config/settings.py` |
| G6 | **Real live DOM committed as a fixture** (84 KB gz from 485 KB) + provenance metadata | `tests/fixtures/croma/` |
| G7 | 17 tests over the real capture, including the Akamai deny page | `tests/replay/test_live_croma_capture.py` |
| G8 | Rate-limit rules | `CLAUDE.md` |

`ResultIdentifier` remains **byte-for-byte unchanged**. `platforms/croma/selectors.py` remains **unchanged** — still unvalidated, and the live page contained no product markup to validate against. No confidence gate was weakened; no selector was guessed.

**139 offline tests, 0 failures** (90 → 122 → 139).

## 15.7 The one next live run — and only one

```bash
# Wait at least 15 minutes since the last 403 before running this.
python -m croma_intel.cli.main diagnose --sku 323360 --model 83S000FYIN --headed
```

Watch the headed browser and answer one question: **does a location/pincode dialog appear, and do results render after you set a pincode by hand?**

- **Results appear after setting a location** → location gate confirmed. I implement pincode handling (M1 task 6) against the real DOM and the gate should pass.
- **No dialog, or results still absent with a location set** → soft block confirmed. That is a genuine access finding and materially changes the M1 verdict.
- **HTTP 403** → still rate-limited. Wait longer; do not retry in a loop.

Send me the new `capture_id` and the screenshot at `artifacts/screenshots/diagnose_323360.png`. Everything after that is offline work.

---

# 16. M1 VERDICT (rev. 3 — superseded by §17)

# ⚠️ INCONCLUSIVE

**Browser access is proven. Reference-product identification is not.**

### Proven

| Claim | Evidence |
|---|---|
| Playwright launches and drives Chromium | Two independent successful runs |
| **Croma serves a real browser** | HTTP 200, 485,676 bytes of genuine markup, block detector clear |
| The capture/replay architecture works under pressure | Croma blocked us after one run; all further analysis proceeded offline from the stored capture |
| `wait_for_results` behaves correctly | Waited 20.1 s, reached networkidle, honestly reported `appeared=False` |
| The identifier refuses to guess | `NOT_FOUND` on an empty page rather than inventing a product |
| Search results are genuinely absent from the delivered page | Zero `/p/<digits>` in 485 KB; zero product-data markers |
| **Croma applies progressive Akamai rate limiting** | 200 → 403 → 403 within minutes |

### Not proven

| Claim | Status |
|---|---|
| **Why** results are absent — location gate vs. soft block | Two live hypotheses; one run separates them |
| Reference-product identification end to end | Blocked on the above |
| Croma PDP access | Never reached |
| `selectors.py` | Still unvalidated; the live page contained no product markup |

### Not NO-GO

NO-GO means Croma blocks the browser outright. It does not: a browser gets HTTP 200 with real content. The 403s are **rate limiting after repeated requests** — an operational constraint to respect, not a wall. Nothing here supports stopping.

### Not GO

GO needs identification proven end to end. Results were never rendered, so it could not be. Declaring GO now would be the confident guess this architecture exists to prevent.

### Architecture: intact

Capture/extraction separation, offline replay, no-first-result, UNKNOWN-as-real, no-missing-data-to-MATCH, no-LLM-authority, no-EUP — all unchanged and still enforced by tests. Rev. 3 fixed two diagnostic bugs and added one operational constraint; it changed no rule.

**M2 remains not started.**

---

**END OF M1 BROWSER GATE REPORT (rev. 3)**


---

# 17. Rev. 4 — Second Live Window: Rate Limit Quantified, Experiment Redesigned (verdict superseded by §18)

**Date:** 2026-08-13, 13:58 (146 minutes after the rev. 3 window).

## 17.1 The empty result is REPRODUCIBLE, not a transient throttle

A fresh `diagnose` run after a full cooldown produced a near-identical page:

| | Window 1 (11:32) | Window 2 (13:58) |
|---|---|---|
| HTTP status | 200 | 200 |
| html_length | 485,676 | 485,130 |
| visible_text | 696 chars | 696 chars |
| pdp anchors | 0 | 0 |
| location gate markers | 5 | 5 |
| `results_wait` | appeared=False, networkidle=True, 20,139 ms | appeared=False, networkidle=True, 20,078 ms |

Reproducing byte-for-similar after 2.5 hours with a fresh context **weakens the
"transient soft block" explanation**: a throttle would be expected to vary. The page
is a stable, coherent 485 KB state — full navigation, footer, and a location
selector — not a degraded stub.

## 17.2 Rate limit quantified: ~ONE navigation per window

The follow-up interaction script issued a second `goto` and was immediately denied:

```
nav: True http: 403
visible text: 'Access Denied ... You don't have permission to access
"http://www.croma.com/search/?" on this server.
Reference #18.8fa2dfad.1786609847.c1b06276
https://errors.edgesuite.net/18.8fa2dfad.1786609847.c1b06276'
```

Both windows show the same shape: **request 1 → 200, request 2 → 403.** This is
sharper than rev. 3's "rate-limits aggressively" and it invalidates a whole class of
debugging approach — navigate-then-interact cannot work.

## 17.3 Consequences

| # | Change | File |
|---|---|---|
| H1 | `PlaywrightDriver._enforce_rate_limit()` — spaces navigations, caps per session, raises rather than silently burning the budget | `browser/playwright_driver.py` |
| H2 | `set_location(pincode)` — satisfies the location gate on the **already-loaded page**, no re-navigation; returns before/after PDP-anchor counts | `browser/playwright_driver.py` |
| H3 | `diagnose --set-pincode PIN` — runs the decisive experiment within a single navigation | `cli/main.py` |
| H4 | Rate-limit rules sharpened to "one navigation per window" | `CLAUDE.md` |

`ResultIdentifier` and `selectors.py` remain unchanged. No confidence gate weakened.
**139 offline tests, 0 failures.**

## 17.4 The decisive experiment, now runnable in one request

```bash
# Wait at least 15 minutes since the last 403.
python -m croma_intel.cli.main diagnose --sku 323360 --model 83S000FYIN --headed --set-pincode 400001
```

One navigation. It loads the page, records the gated state, fills and submits the
pincode **in place**, waits for products, and prints:

```
LOCATION-GATE EXPERIMENT:
  pincode field found : ...
  pdp anchors before  : 0
  pdp anchors after   : ...
  products appeared   : ...
  VERDICT: ...
```

- **products appeared = True** → location gate confirmed; I implement pincode
  handling against the captured DOM and the gate should pass.
- **products appeared = False** → the location gate is not the cause; a degraded
  anti-bot response becomes the leading explanation and the M1 verdict changes
  materially.
- **HTTP 403** → still inside the cooldown. Wait longer; do not retry in a loop.

## 17.5 Verdict unchanged: ⚠️ INCONCLUSIVE

Browser access remains **proven** (two independent HTTP 200 loads with real
content). Identification remains **unproven** — results never rendered. The two
competing explanations are now testable in a single request, which is the whole
point of rev. 4.

**M2 remains not started.**

---

**END OF M1 BROWSER GATE REPORT (rev. 4)**


---

# 18. Rev. 5 — Location-Gate Hypothesis REFUTED

**Date:** 2026-08-13, 14:24. Command:
`diagnose --sku 323360 --model 83S000FYIN --headed --set-pincode 560087`
One navigation. HTTP 200, no block.

## 18.1 The gate was satisfied — and it changed nothing

The interaction succeeded on the already-loaded page:

```
field_found=True pdp_anchors 0 -> 0
  clicked:text:Select your location
  filled:input[placeholder*='Pincode' i]
  clicked:button:Continue
```

Croma accepted the location. Proof from the captured visible text:

| | Before pincode | After pincode |
|---|---|---|
| visible text opens with | `Menu / Update pincode for best prices and offers / Add Pincode` | `Menu / Bengaluru, / 560087` |
| location gate markers | 5 (`select your location`, `check products & delivery options`, ...) | **none** |
| `likely_location_gated` | True | **False** |
| overlay classes | pincode=19 location=13 modal=2 | pincode=17 location=11 modal=1 |
| **pdp anchors** | **0** | **0** |

**The location gate is refuted as the cause.** It was genuinely satisfied — the page
now displays `Bengaluru, 560087` — and products still did not render.

## 18.2 The sharper finding: there is no results component at all

The full visible text after setting the location is 535 characters:

```
Menu
Bengaluru,
560087
0

CONNECT WITH US
... footer ...
PRODUCTS
Televisions & Accessories ... Top Brands
```

Menu, location, cart count, footer. **No results area. No product grid. And no
empty-state message either** — no "no results found", no "0 products", nothing.

That absence is the important part. A genuine zero-result search renders an
empty-state message; this page renders *no results component whatsoever*. Meanwhile
the page title is correctly `Search 323360 | Croma ...` and the meta tags carry the
query, so routing worked and the app knew what was asked.

## 18.3 Hypothesis status

| Hypothesis | Status |
|---|---|
| Captured before hydration (rev. 2) | ❌ Refuted (rev. 3) — networkidle reached, no data anywhere |
| Location/pincode gate (rev. 3) | ❌ **Refuted (rev. 5)** — gate satisfied, products still absent |
| Search genuinely returns 0 results | ⚠️ Weakened — no empty-state message is rendered |
| **Results API refused or never called for this client** | ✅ **Now leading** |

## 18.4 The evidence I was missing

I could not distinguish "the results API refused us" from "the results API returned
empty" because **the driver never recorded network traffic** — despite M0 §17.1
listing intercepted XHR/JSON as a required capture artifact and as the *preferred*
extraction source. That gap is why three live windows produced no answer.

Implemented in rev. 5:

| # | Change | File |
|---|---|---|
| J1 | XHR/fetch recorder — URL, method, status, content-type, size-capped JSON bodies | `browser/playwright_driver.py` |
| J2 | `NetworkConfig` (record, capture_bodies, caps, body-URL keywords) | `config/settings.py` |
| J3 | Network log persisted into the capture artifact (`extra.network`) | `cli/main.py` |
| J4 | `diagnose` prints candidate results-API calls, flags any status ≥ 400, and states explicitly when **no** results call was made | `cli/main.py` |

`ResultIdentifier` and `selectors.py` remain unchanged. **139 offline tests, 0 failures.**

## 18.5 The next single run

```bash
# Wait at least 15 minutes since the last request.
python -m croma_intel.cli.main diagnose --sku 323360 --model 83S000FYIN --headed --set-pincode 560087
```

It now prints a `NETWORK (XHR/fetch)` block that resolves this directly:

- **A results call with status 4xx/5xx** → the API refuses this client. That is an
  access finding, and it means M1 needs session/header work rather than selectors.
- **A results call returning 200 with an empty payload** → Croma genuinely has no
  results for SKU `323360` via this search path. The fix is the search strategy —
  M0 §2.2 inferred the SKU may be reachable directly at `/p/323360`, which is one
  navigation to test.
- **No results call at all** → the SPA never requests products, pointing at a
  client-side guard or a route the automated context does not trigger.

## 18.6 Verdict: ⚠️ INCONCLUSIVE (superseded by §19)

Browser access remains **proven** — three independent HTTP 200 loads with real
content, block detector clear each time. Identification remains **unproven**, and
after rev. 5 the cause is narrowed to the results API rather than to rendering,
timing, or the location gate.

**M2 remains not started.**

---

**END OF M1 BROWSER GATE REPORT (rev. 5)**


---

# 19. Rev. 6 — Network Evidence: The SPA Never Requests Products

**Date:** 2026-08-25. Two live requests: one `diagnose` (HTTP 200), one direct-PDP
probe (HTTP 403, rate-limited). Live requests halted after the second.

## 19.1 The network log settles the open question

44 XHR/fetch calls were recorded on the search page. The breakdown:

| Category | Count | Examples |
|---|---|---|
| Analytics / RUM | ~20 | Adobe `demdex.net`, `omtrdc.net`, GA4 `analytics.google.com`, Hotjar, FullStory, RudderStack, AppDynamics |
| Ad tech | ~18 | DoubleClick, `googleadservices`, Criteo |
| Croma session | 1 | `api.tatadigital.com/api/v2/sso/check-session` → 200 |
| **Croma content API** | 1 | `api.croma.com/sanity/v1/cms?query=…homePageFooterPage…` → **200, 11,002 bytes JSON** |
| **Akamai Bot Manager** | 1 | `www.croma.com/_bm/get_params` → 200 |
| **Product / search API** | **0** | — |

**Non-2xx responses: 0.**

Three conclusions follow directly:

1. **Nothing was refused.** Zero 4xx/5xx among 44 calls. The "results API rejects our
   client" hypothesis is refuted.
2. **Nothing was asked.** There is no search, product, PLP, catalog, or `occ` call of
   any kind. The "API returned an empty set" hypothesis is refuted too — no request
   was ever made.
3. **Croma's own API works for this client.** The Sanity CMS call to
   `api.croma.com` returned 11 KB of JSON successfully. So this is not a
   network, TLS, CORS, or blanket-API problem.

**The SPA loads, authenticates its session, fetches footer content from Croma's API,
fires forty analytics beacons — and never once requests the products it was routed
to display.**

## 19.2 Akamai Bot Manager is active

`https://www.croma.com/_bm/get_params` is the Akamai Bot Manager client sensor
(`_bm`). Combined with:

- one navigation per window before HTTP 403 (`errors.edgesuite.net`),
- a shell that renders fully while the product fetch silently never fires,

the pattern matches a **soft block**: serve the page, run the sensor, and withhold
the valuable payload from a client scored as automation. This is consistent across
four successful loads and is not explained by timing, hydration, or the location gate.

## 19.3 Hypothesis ledger — final state for M1

| Hypothesis | Status |
|---|---|
| Captured before hydration | ❌ Refuted (rev. 3) |
| Location/pincode gate | ❌ Refuted (rev. 5) — gate satisfied, no change |
| Results API refuses the client | ❌ Refuted (rev. 6) — zero non-2xx |
| Search genuinely returns 0 results | ❌ Effectively refuted — no query issued, no empty-state rendered |
| **Client-side bot-management suppression** | ✅ **Leading, well supported** |
| Direct PDP path (`/p/{sku}`) viable | ⚠️ **UNTESTED** — the probe was rate-limited before it could load |

## 19.4 What was NOT established

The direct-PDP hypothesis from M0 §2.2 — that the SKU is the PDP id, so
`croma.com/p/323360` reaches the product without search — **remains untested.** The
one attempt returned 403 because it was the second navigation in the window, which is
evidence about rate limiting, not about the URL. This matters: if direct PDP works,
the reference-product path survives even with search suppressed.

The blocked attempt was correctly recorded (`capture status: BLOCKED`,
`block.is_blocked: True`), so the safety property held — a wall never entered the
pipeline as product data.

## 19.5 Verdict: ⚠️ INCONCLUSIVE, trending toward NO-GO (superseded by §21)

**Proven:** Croma serves a real browser (four HTTP 200 loads, block detector clear
each time); the capture/replay architecture works; the block detector correctly
classifies Akamai denials; the identifier never guesses.

**Proven negative:** a vanilla Playwright browser can load Croma's search page but
**cannot obtain product data through it**, because the application does not issue the
product request. No amount of selector work, waiting, scrolling, or location handling
changes this — all four were tested and eliminated.

**Not yet decided:** whether the direct PDP path bypasses the problem. That single
untested question separates "search is suppressed" from "product data is suppressed",
and it is the difference between a workable M1 and a genuine NO-GO.

## 19.6 Next step — one navigation, after a cooldown

```bash
# Wait at least 15 minutes. This must be the FIRST request in the window.
python -m croma_intel.cli.main diagnose --sku 323360 --model 83S000FYIN --headed
```

…but pointed at the PDP rather than search. If it renders a product with a price,
the reference path is viable via SKU→PDP construction (which still takes **no URL as
input** — the URL is derived from the SKU, per M0 §2.2).

If the direct PDP is also empty or blocked, the honest conclusion is that Croma's bot
management defeats a vanilla browser, and the decision becomes a scope question —
official data access, a different execution environment, or fingerprint/stealth work
— rather than an engineering one. That is a decision for you, not something to
engineer around silently.

**M2 remains not started.**

---

**END OF M1 BROWSER GATE REPORT (rev. 6)**

---

# 20. Rev. 7 — M1 GATE: GO. Real Browser Discovery Flow Works

**Date:** 2026-08-25. Command:

```
python -m croma_intel.cli.main m1-gate --sku 323360 --model 83S000FYIN --headed
```

## 20.1 The correction that fixed it

Revisions 3–6 investigated why `/search/?text=323360` rendered a page with zero
products. The answer was not timing, not the location gate, not an API refusal —
**it was the approach**. Navigating directly to a constructed search URL produced a
shell whose product component never populated. Driving the visible UI produces
results immediately.

| | Constructed search URL (rev. 2–6) | Real UI flow (rev. 7) |
|---|---|---|
| Navigation | `goto("/search/?text=323360")` | `goto("https://www.croma.com")` then type + Enter |
| Product cards rendered | **0** (four separate runs) | **1** |
| Product API called | **none** (44 XHR, zero product calls) | results present in DOM |
| Outcome | NO_GO | **GO** |

The earlier conclusion that "the SPA never requests products" was correct *for that
entry path*. It was not a property of Croma or of automation in general.

## 20.2 Every gate step passed

```
[PASS] playwright_available : available
[PASS] browser_launch       : chromium launched
[PASS] homepage             : status=SUCCESS  title='Croma Electronics | Online Electronics Shopping...'
[PASS] pincode              : requested=560087 detected=560087 reused=True
[PASS] search_via_ui        : typed '323360' into 'input#searchV2'
[PASS] results_rendered     : 1 product card(s) in the rendered DOM
[PASS] result_identified    : single candidate at strongest tier SKU_EXACT
[PASS] pdp_url_captured     : page.url=...
[PASS] pdp_verified         : all identity checks true
[PASS] offline_replay       : ok
```

### Discovered PDP URL — read from `page.url`, never constructed

```
https://www.croma.com/lenovo-loq-15arp10e-amd-ryzen-5-gaming-laptop-16gb-512gb-ssd-
windows-11-home-6gb-graphics-15-6-inch-144-hz-fhd-ips-display-nvidia-geforce-rtx-3050-
ms-office-home-2024-luna-grey-1-8-kg-/p/323360
```

The slug could not have been guessed. It also incidentally confirms the M0 §2.2 Q2
hypothesis — the SKU *is* the `/p/` id — but that was **discovered, not assumed**,
which is the point.

### Identification evidence

```
tier              : SKU_EXACT (1)
evidence          : PDP url id '323360' == requested SKU '323360'
                    result card sku text '323360' == requested SKU '323360'
candidates        : 1 rendered, 1 assessed, 0 rejected as accessories
selected          : pos=1 id=323360
                    'Compare Lenovo LOQ 15ARP10E AMD Ryzen 5 Gaming Laptop (16GB, 512GB SSD, ...)'
```

Only one card rendered, so position 1 was also the correct answer here. The
no-first-result guarantee is enforced by evidence rather than by position, and is
proven against multi-result decoys by tests G and H.

### PDP verification — all checks true

```
url_is_pdp=True  url_id_matches_sku=True  sku_present_in_page=True
title_present=True  model_present=True  not_accessory=True
has_pdp_content=True  identity_signal=True
```

**`model_present=True` is a new finding:** `83S000FYIN` appears on the Croma PDP
(2 occurrences). M0 could not establish where Croma exposes the model number; it is
on the product page.

### Immutable artifact

| Field | Value |
|---|---|
| capture_id | `5df6c46a45c3c93d941a5c4c078cd7e5585613729b0e5844101c65ccd13c1f4b` |
| final_url | the discovered PDP URL above |
| input_sku | `323360` |
| requested_model | `83S000FYIN` |
| pincode | `560087` |
| pdp_url_source | `page.url` |
| captured_at | `2026-08-25T07:46:18.110+00:00` |
| html | **707,724 bytes** |
| html_sha256 | `05c1f7acb8f6db11c7a8b451f1aa2e84…` |
| status | `SUCCESS` (blocked=False) |
| screenshot | `artifacts/screenshots/pdp_323360.png` |
| integrity | `(True, 'ok')` |

Offline content check, performed with the network unavailable: `83S000FYIN` ×2,
`323360` ×378, `Add to Cart` ×1, `Lenovo LOQ 15ARP10E` ×85, breadcrumb
`Computers & Tablets > Laptops > Gaming Laptops`.

## 20.3 One honest caveat about the click

The recorded click strategy was `anchor_click+follow_discovered_href`. The anchor
click fired but did not navigate on its own, so the fallback followed the anchor's
**own href as read from the rendered DOM**. That is not a constructed URL — it is the
link the browser discovered — but neither is it a pure click, and the artifact records
which path was taken rather than papering over it.

Worth tightening during M2 hardening: identify what intercepts the click (the card
carries a "Compare" control) and target the correct child element.

## 20.4 What changed in rev. 7

| # | Change | File |
|---|---|---|
| K1 | `CromaDiscovery` — homepage → pincode → UI search → rendered results → identify → click → `page.url` → verify | `platforms/croma/discovery.py` (new) |
| K2 | `accessory_filter` — multi-signal hard gate (category, "for &lt;brand&gt;", head-noun, anchor guard) | `platforms/croma/accessory_filter.py` (new) |
| K3 | Ranking tiers SKU_EXACT &gt; MODEL_EXACT &gt; CATEGORY &gt; TITLE_SIMILARITY; accessories rejected **before** ranking (rule 5) | `platforms/croma/result_identifier.py` |
| K4 | Gate rewritten around the UI flow; the failure path never sets a PDP URL | `gate/m1_gate.py` |
| K5 | Robust click strategies, with the strategy recorded as evidence | `platforms/croma/discovery.py` |
| K6 | Default pincode `560087` | `config/settings.py`, `config/default.json` |
| K7 | Tests A–M (25 new) against a scripted fake page | `tests/unit/test_discovery_flow.py` |

### Deliberate spec change, flagged

Exact model is now a **decisive** tier-2 signal. Previously a test asserted that
model-alone was insufficient — that was a design choice of mine, not an architectural
rule, and the M1 correction lists exact model as rank 2. Justification: M0 §10.3
showed `83DV018NIN` and `83DV018QIN` differ by one character and are different
products, so a boundary-safe exact MTM match is identity, not similarity. Weak title
similarity still cannot promote a candidate — see
`test_weak_similarity_alone_still_cannot_promote`.

## 20.5 Architecture: intact

Capture/extraction separation, deterministic offline replay, no-first-result,
UNKNOWN-as-real, no-missing-data-to-MATCH, no-LLM-authority, no-EUP — all unchanged
and still enforced by tests. **166 offline tests, 0 failures.**

---

# 21. M1 VERDICT (rev. 7 — FINAL)

# ✅ GO

**Croma browser access and reference-product identification are both proven.**

| Requirement | Status |
|---|---|
| Browser launches | ✅ |
| Croma homepage loads | ✅ |
| Pincode handled and verified (560087) | ✅ |
| SKU search performed through the visible browser UI | ✅ |
| Correct product result identified (never first-by-default) | ✅ SKU_EXACT |
| Product opened | ✅ |
| Canonical PDP URL captured from `page.url` | ✅ never constructed |
| PDP verified as the requested product | ✅ all checks |
| PDP HTML captured | ✅ 707,724 bytes |
| Deterministic offline replay | ✅ integrity ok |

**M2 is NOT started.**

---

**END OF M1 BROWSER GATE REPORT (rev. 7 — GO)**
