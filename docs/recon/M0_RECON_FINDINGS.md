# M0 — Recon Spike: Findings

**Milestone:** M0 (investigation only — no production code)
**Date of recon:** 2026-08-13
**Status:** Complete
**Author:** Lead architect
**Verdict:** **CONDITIONAL GO** (see §22)

---

## 0. Preface — Project State and Evidence Discipline

### 0.1 Project state at M0 start

The project directory was **empty (0 files)** when M0 began. No `CLAUDE.md` and no design documents existed on disk — the architecture from the prior design session was delivered as conversation output only and was never written to a file. This document is therefore the **first artifact committed to the repository**.

**Action for M1:** commit the architecture document to `docs/architecture/ARCHITECTURE.md` and create `CLAUDE.md` before any implementation begins. A design that lives only in chat history is not a design the team can build against.

### 0.2 Evidence discipline

Every claim below is tagged with one of:

| Tag | Meaning |
|---|---|
| **CONFIRMED** | Directly observed in a live response captured during this spike, quoted verbatim. |
| **PARTIALLY CONFIRMED** | Observed, but through a lossy channel (see §0.3) or from a single sample; the shape is established but the detail is not guaranteed. |
| **NOT CONFIRMED** | Attempted and failed, or contradicted by observation. |
| **UNKNOWN** | Not attempted, or not obtainable with the tooling available in this environment. |

Claims that could not be evidenced are marked UNKNOWN rather than inferred. **Where an inference is drawn from indirect evidence, it is labelled INFERENCE and its basis is stated.** This distinction is not pedantry — the entire architecture rests on assumptions about live page structure, and a plausible-sounding guess recorded as fact here becomes a silent defect in M2.

### 0.3 ⚠️ Tooling constraint — read this before trusting any DOM-level claim

This spike was executed from an environment with **materially weaker web access than the production scraper will have.** This bounds what M0 could establish.

| Capability | Status | Evidence |
|---|---|---|
| Direct outbound HTTP from shell (`curl`) | **UNAVAILABLE** | `curl` returned status `000` (connection failure) for all five target hosts: croma.com, reliancedigital.in, vijaysales.com, amazon.in — with a normal desktop Chrome User-Agent. |
| Proxied page fetch (markdown-converted, model-summarised) | Available | Used for all page-level observations below. |
| Web search (titles + URLs) | Available | Used for URL-shape discovery and cross-referencing. |
| Real browser (Playwright), DOM inspection, CSS/XPath selectors | **UNAVAILABLE** | No browser automation in this environment. |
| Network/XHR interception | **UNAVAILABLE** | — |
| Cookie/session/pincode manipulation | **UNAVAILABLE** | — |
| Screenshot capture | **UNAVAILABLE** | — |

**Three consequences that must not be glossed over:**

1. **No selector-level evidence exists in this document.** Every observation is content-level ("the page displayed a price labelled VSP"), never DOM-level ("`.price__vsp > span`"). The architecture's `SelectorSet` design is therefore **entirely unvalidated**. Selector authoring is M1/M2 work and must be budgeted as such.
2. **The fetch channel summarises via a small model.** A statement that a field "was not found" means *not present in the delivered, converted payload* — it is weaker than "absent from the page." Absence claims below are marked PARTIALLY CONFIRMED and flagged for browser re-verification.
3. **Block results are a lower bound on accessibility, not an upper bound.** A 403 here does **not** prove Croma blocks Playwright. It proves this datacentre-egress, non-browser client is blocked. A real browser with a warm session may well succeed. This distinction drives the entire GO/NO-GO reasoning in §22 — please do not read "Croma: 403" as "Croma is impossible."

---

## 1. Executive Summary

### 1.1 What M0 set out to test

Whether this chain is technically feasible:

```
Croma SKU → Croma search → correct PDP → complete capture
          → competitor discovery → competitor capture
          → offline deterministic matching → classification → ranking
```

### 1.2 Headline findings

| # | Finding | Impact | Status |
|---|---|---|---|
| **F1** | **Flipkart product pages do not expose the Lenovo MTM model number.** Titles carry only the short series code (`15ARP9`), not `83JC0045IN`. No "Model Number"/"Part Number" field was present in the delivered payload. | **Severe.** Rules 1 & 2 (exact model, model variation) are inoperable for Flipkart. Flipkart must match on specifications alone. | PARTIALLY CONFIRMED |
| **F2** | **Flipkart model-number search is effectively non-functional.** Searching `83S000FYIN` returned **2,135 results**, ranked #1 being *"Softsens Baby Cream with Milk Cream and Shea Butter" (₹173)*. | **Severe.** Query strategy Q1 (exact model) is worse than useless on Flipkart — it returns confident garbage. Discovery must lead with spec-based queries there. | CONFIRMED |
| **F3** | **Reliance Digital PDPs are an empty JavaScript shell.** HTTP 200, but the payload contained only the page title *"Online Electronic Shopping Store in India - Reliance Digital"* — no product, price, or spec content. | **Moderate.** Contradicts the planned `strategy: http_first` for RD. RD requires full browser rendering. | CONFIRMED |
| **F4** | **Croma is fully inaccessible from this environment** — HTTP 403 on homepage, search, and two distinct PDPs. Zero Croma page content was obtained. | **Blocking for M0 validation, not necessarily for the project.** The reference-product path — the foundation of the entire pipeline — is unvalidated. | NOT CONFIRMED |
| **F5** | **Amazon.in is fully inaccessible from this environment** — 503 on two searches, 500 on a direct product page. | **Moderate.** Expected; matches the architecture's `AGGRESSIVE` bot-defense rating for Amazon. | NOT CONFIRMED |
| **F6** | **Vijay Sales is fully accessible and richly structured** — complete title, model, SKU, breadcrumb, VSP/MRP, and a detailed spec table all present in the initial payload. | **Positive.** Confirms the adapter model works where access exists. Correctly chosen as the M3 first-adapter target. | CONFIRMED |
| **F7** | **A new listing condition was discovered: `"Store Display Unit - "` as a title prefix on Vijay Sales.** | **High — silent-wrong risk.** A display/demo unit is not new stock. Its price would have been captured as a valid MOP and compared against Croma retail. Not in the architecture's condition enum. | CONFIRMED |
| **F8** | **A new price tier was discovered on Flipkart: `"Lowest Price for You"` (₹1,04,990 vs selling price ₹1,08,990).** | **High — silent-wrong risk.** A personalised/conditional price not in the `PriceQuote` schema. Naively taking the lowest number yields a price no ordinary customer pays. | CONFIRMED |
| **F9** | **The never-strip-inside-an-alphanumeric-run invariant is validated by real-world data.** `83S000FYIN` (India) and `83S00000US` (US) are different products distinguished only by the trailing region code inside the run. | **Positive — invariant vindicated.** Blind suffix stripping would merge two distinct regional SKUs. | CONFIRMED |
| **F10** | **A URL-ID / page-SKU mismatch was observed on Vijay Sales.** Requesting `/p/230914/...83jc0045in` returned a page reporting SKU `243423` and model `83DV018QIN`. | **Moderate.** `product_id_from_url()` cannot be the authoritative identity source; identity must be read from the rendered page. | PARTIALLY CONFIRMED |
| **F11** | **Croma exposes numeric category IDs in its URLs** (`/c/20` laptops, `/c/806` gaming laptops, `/c/2001` laptop bags, `/c/567` chargers/adapters). | **Positive.** A machine-readable, stable category signal — a strong primary input for the accessory gate. | CONFIRMED |
| **F12** | **The material-description encoding was decoded and validated against a real product.** `LENOVO 15 LOQE FYIN 7KR5 16GB6X 512+3050` resolves to the Lenovo LOQ **15ARP10E**, MTM `83S000FYIN`, Ryzen **5** 7535HS, 16 GB, 512 GB, RTX **3050**. | **Positive.** The `FYIN` token is a direct substring of the MTM — a deterministic linkage, not an LLM guess. | CONFIRMED |

### 1.3 Bottom line

The **matching architecture is sound and, in two important respects, vindicated** by live data (F9, F12). The **discovery and capture assumptions are not** — three of five platforms were unreachable or unrenderable from this environment, and the two that were reachable each revealed a silent-wrong hazard the architecture did not anticipate (F7, F8).

The single most consequential finding is **F1 + F2 combined**: on Flipkart — likely the highest-value competitor — model-based identity is unavailable *and* model-based search is actively misleading. This does not break the architecture; the three-tier classification already contemplates specification-only matching via `CONFIGURATION_MATCH`. But it means **Flipkart will essentially never produce an `EXACT_MATCH`**, and the pipeline must be explicitly designed for that rather than treating it as a degraded case.

---

## 2. Croma Findings

**Overall: NOT CONFIRMED — zero page content obtained.**

### 2.1 Access attempts (all failed)

| # | URL | Result |
|---|---|---|
| 1 | `https://www.croma.com/` | **HTTP 403 Forbidden** |
| 2 | `https://www.croma.com/search/?text=323360` | **HTTP 403 Forbidden** |
| 3 | `https://www.croma.com/lenovo-loq-15iax9-...-2-38-kg-/p/312746` | **HTTP 403 Forbidden** |
| 4 | `https://www.croma.com/lenovo-loq-15arp9-...-2-38-kg-/p/316063` | **HTTP 403 Forbidden** |
| 5 | `https://www.croma.com/` via `curl` + desktop Chrome UA | **Status `000`** (no connection — sandbox egress blocked) |

4/4 proxied requests returned 403. The block is **not** path-specific — the homepage is blocked identically to PDPs, which indicates an edge/WAF rule keyed on client reputation or TLS/HTTP fingerprint, not on scraping-sensitive routes.

**Everything in §2.2–§2.5 below derives from search-engine result titles and URLs — not from Croma page content.** It is structural evidence only.

### 2.2 PDP URL structure — CONFIRMED (from URLs)

```
https://www.croma.com/{slugified-product-title}/p/{numeric-id}
```

Eleven distinct examples observed:

| Numeric ID | Product |
|---|---|
| `318872` | Lenovo LOQ 15IRX9 i5 13th Gen, 16 GB, RTX 4050 |
| `316076` | Lenovo LOQ 15IRX9 i5 13th Gen, 24 GB, RTX 3050A |
| `316063` | Lenovo LOQ 15ARP9 Ryzen 5, 24 GB, RTX 3050 |
| `316005` | HP 15-hr1000TU Core Ultra 5 |
| `315007` | Lenovo LOQ 15ARP9 Ryzen 7, RTX 4050 |
| `312746` | Lenovo LOQ 15IAX9 i5 12th Gen, RTX 3050 |
| `311425` | HP Victus i5 13th Gen, RTX 2050 |
| `307516` | Lenovo LOQ Gen 9 Ryzen 5, RTX 3050 |
| `305682` | Lenovo LOQ 15IAX9 i5 12th Gen, RTX 3050 |
| `305669` | Lenovo LOQ 15IAX9 i5 12th Gen, RTX 2050 |
| `300257` | DELL Inspiron 3530 i5 13th Gen |
| `244320` | HP 14a-na0002tu Chromebook |

**INFERENCE (not confirmed): the Croma SKU is the numeric `/p/` ID.**
Basis: the supplied example SKU `323360` is a 6-digit integer in exactly the same numeric range and format as all twelve observed IDs. This is a strong structural match.
**Not confirmed because:** no Croma page could be opened to see whether `323360` resolves and whether the page displays `323360` as its SKU. **This is the single highest-priority M1 verification.** If it holds, SKU→PDP resolution can bypass search entirely — a major simplification.

### 2.3 Category URL structure — CONFIRMED (from URLs), high value for the accessory gate

Croma encodes category as a **stable numeric ID**:

| URL | Category |
|---|---|
| `/computers-tablets/laptops/c/20` | Laptops |
| `/computers-tablets/laptops/gaming-laptops/c/806` | Gaming Laptops |
| `/computers-tablets/laptops/windows-laptops/c/855` | Windows Laptops |
| `/computers-tablets/computer-and-laptop-accessories/c/74` | Computer & Laptop Accessories |
| `/computers-tablets/computer-laptop-accessories/batteries-chargers-adapters/c/567` | Batteries, Chargers & Adapters |
| `/accessories/computer-and-laptop-accessories/laptop-bags/c/2001` | Laptop Bags |
| `/computers-tablets/computer-and-laptop-accessories/laptop-bags/c/2001` | Laptop Bags (second path, **same ID 2001**) |

Two observations of architectural value:

1. **Product and accessory categories are cleanly separated by numeric ID** (`20`/`806` vs `74`/`567`/`2001`). This is a far stronger accessory signal than title lexicon analysis.
2. **The same category ID appears under two different URL paths** (`2001` under both `/computers-tablets/...` and `/accessories/...`). **Therefore key the category registry on the numeric ID, not on the breadcrumb path string.** A path-keyed taxonomy would treat these as two categories.

A brand-scoped variant also exists: `/lenovo-gaming-laptops/bc/b-0216-806` and `/lenovo-laptop-covers/bc/b-0216-557` — pattern `/bc/b-{brand-id}-{category-id}`, with `0216` = Lenovo. This gives brand *and* category from a single URL.

### 2.4 Title structure — CONFIRMED (from search result titles)

Croma titles follow a consistent, highly parseable template:

```
Buy {BRAND} {SERIES/MODEL} {CPU} {PRODUCT TYPE} ({RAM}, {STORAGE}, {OS},
    {GRAPHICS}, {DISPLAY}, {GPU}, {EXTRAS}, {COLOUR}, {WEIGHT}) Online - Croma
```

Verbatim example:

> `Buy Lenovo LOQ 15IAX9 Intel Core i5 12th Gen Gaming Laptop (16GB, 512GB SSD, Windows 11 Home, 6GB Graphics, 15.6 inch 144 Hz IPS Display, NVIDIA GeForce RTX 3050, MS Office 2024, Luna Grey, 2.38 KG) Online - Croma`

The parenthesised block is a **comma-delimited spec list** — a usable secondary spec source if the PDP spec table proves hard to extract. Note it is a fallback, not a primary: it is marketing copy and omits CPU model, refresh rate detail, and panel type in some listings.

**Manufacturer part numbers do appear in some Croma titles.** Verbatim:

> `Buy HP 14a-na0002tu Chromebook Intel Celeron (4GB, 64GB, Chrome OS, 14 inch, Ceramic White, 2Z326PA#ACJ) Online - Croma`

`2Z326PA#ACJ` is an HP part number **containing a `#` character**. Consequence for the model normaliser: `#` must be treated as a **separator** in the delimiter set (alongside `-`, `/`, `_`, space), and `ACJ` is a delimited regional suffix token. Critically, this means HP part numbers are structurally *different* from Lenovo MTMs — Lenovo's region code is inside the run (never strippable), HP's is after a `#` (a legitimate delimited token). **This is exactly why the model registry must be brand-scoped.** A single global rule cannot serve both.

### 2.5 Everything else about Croma — NOT CONFIRMED / UNKNOWN

| Item | Status | Note |
|---|---|---|
| SKU search behaviour, result ranking, accessory contamination | **UNKNOWN** | Search returned 403. |
| Where SKU is displayed on the PDP | **UNKNOWN** | No PDP obtained. |
| Price / MOP display, and the label used | **UNKNOWN** | No PDP obtained. |
| Exchange / bank-offer / coupon / EMI / cashback presentation | **UNKNOWN** | No PDP obtained. |
| Specification section location, lazy-loading, expanders | **UNKNOWN** | No PDP obtained. |
| Model number location(s) on PDP | **UNKNOWN** | No PDP obtained. |
| Pincode control behaviour | **UNKNOWN** | Not reachable. |
| Breadcrumb rendering on PDP | **UNKNOWN** | Category inferred from category-page URLs only. |
| JSON-LD presence | **UNKNOWN** | Not obtainable through this channel. |
| CAPTCHA vs hard block | **PARTIALLY CONFIRMED** | 403 with no body returned. No CAPTCHA challenge page was observed — the response carried no content at all. This reads as an edge-level deny, not an interactive challenge. |

---

## 3. Flipkart Findings

**Overall: PARTIALLY CONFIRMED — fully accessible, but model identity is absent.**

### 3.1 Access — CONFIRMED

Both search and PDP returned HTTP 200 with **fully rendered content** in the initial payload. Flipkart was the most accessible of the four competitors for search, and second-richest for PDP content. No CAPTCHA or challenge was encountered in any request.

### 3.2 Search behaviour — CONFIRMED, and it is a problem

Query: `https://www.flipkart.com/search?q=83S000FYIN`

Verbatim from the response:

> The query `83S000FYIN` returns **2,135 results** across multiple product categories.
> **1. Softsens Baby Cream with Milk Cream and Shea Butter** — Price: ₹173 … Rating: 4.3/5 (9,362 reviews)
> **2. Lenovo Legion Pro 5 WQXGA OLED Intel Core Ultra 9 275HX** — ₹2,33,490

Result #1 for a Lenovo laptop MTM is **baby cream**.

A second query (`83JC0045IN`) via search engine likewise reported that *"the specific model 83JC0045IN wasn't directly displayed in the top results."*

**Interpretation:** Flipkart does not treat an unrecognised alphanumeric token as an exact-match constraint. It falls back to broad/fuzzy retrieval across the whole catalogue and returns a large, ranked-by-popularity result set. There is no "0 results" signal to detect failure — the failure mode is *a confident, large, wrong result set*.

**Architectural consequence:** query strategy **Q1 (exact model) must be disabled or deprioritised for Flipkart.** Worse, the architecture's `discovery.early_stop_on_model_hit: true` optimisation is actively dangerous here — it would stop on a garbage result set. Discovery on Flipkart must lead with Q4 (brand + series + key specs).

### 3.3 PDP structure — CONFIRMED

Source: `https://www.flipkart.com/lenovo-loq-.../p/itme3c655ce4e3c8?pid=COMHFYZ36W8ZGS8Z`

**Title (verbatim):**
> `Lenovo LOQ AMD Ryzen 7 Octa Core 7435HS - (24 GB/512 GB SSD/Windows 11 Home/6 GB Graphics/NVIDIA GeForce RTX 4050/144 Hz/115 W) 15ARP9 Gaming Laptop`

Template: `{BRAND} {SERIES} {CPU} - ({RAM}/{STORAGE}/{OS}/{GRAPHICS}/{GPU}/{REFRESH}/{TDP}) {SERIES-CODE} {TYPE}`

Note the TDP token (`115 W`) — a spec Croma does not surface. Useful, because GPU TDP distinguishes otherwise-identical RTX 4050 configurations, and is a candidate CRITICAL spec for gaming laptops.

**Breadcrumb (verbatim):** `Home / Computers / Laptops / Lenovo Laptops`

Note this is **brand-terminated**, not product-type-terminated — there is no "Gaming Laptops" leaf as on Vijay Sales. The category taxonomy mapping must handle a brand node in the leaf position.

**Identifiers observed (three distinct ID spaces):**

| Identifier | Example | Location |
|---|---|---|
| Item ID | `itme3c655ce4e3c8` | URL path after `/p/` |
| Product ID (PID) | `COMHFYZ36W8ZGS8Z` | `?pid=` query parameter |
| Listing ID (LID) | `LSTCOMHFYZ36W8ZGS8ZJVRQU1` | `&lid=` query parameter |

The LID appears to embed the PID (`LST` + `COMHFYZ36W8ZGS8Z` + `JVRQU1`) — **INFERENCE** from string structure; not verified. The PID is the correct choice for `platform_product_id`, as the item ID appeared to be shared across at least two differently-priced listings (`itme3c655ce4e3c8` and `itmd3fbbdced2d6b` both resolved from near-identical slugs).

### 3.4 ⚠️ Model number — PARTIALLY CONFIRMED ABSENT (F1)

This was tested with an explicit, targeted prompt asking for any field labelled *Model Number*, *Part Number*, *MPN*, *Model ID*, or *Manufacturer Part*, and for any token matching `83XXXXXXIN`.

Verbatim response:

> **Model/Part Number Search Result:**
> No field explicitly labeled "Model Number," "Part Number," "MPN," or "Manufacturer Part" appears in the provided page content. The model designation "15ARP9" appears within the product title but no standalone model identifier matching the pattern 83XXXXXXIN was found on this page.

Corroborating evidence — **not one of the nine Flipkart LOQ titles retrieved contained an MTM.** All nine carry only a series code:

`15ARP9` ×4 · `15IRH8` ×2 · `15IRH8D1` · `15IRX9` · `15IAX9`

Contrast with Reliance Digital and Vijay Sales, where the MTM appears in essentially every title.

**Why PARTIALLY CONFIRMED, not CONFIRMED:** Flipkart PDPs carry a full specification table lower on the page, typically behind lazy-loading and a "Read More" control. This channel cannot scroll or click. The MTM may exist in that unretrieved section.

**M1 must resolve this definitively.** It is a fork in the road:
- **If the MTM is in the expandable spec table:** Flipkart supports Rules 1–2 normally; expander interaction becomes mandatory for Flipkart capture.
- **If the MTM is genuinely absent:** Flipkart can only ever reach `CONFIGURATION_MATCH`, and this must be an explicit, documented, tested design position — not an accident.

### 3.5 Price structure — CONFIRMED, and it is the worst case for MOP disambiguation

Every price figure observed on the single PDP, verbatim:

| Label as displayed | Value | Is it MOP? |
|---|---|---|
| Selling Price | **₹1,08,990** | ✅ **Yes** — this is MOP |
| **"Lowest Price for You"** | ₹1,04,990 | ❌ No — personalised/conditional (**F8, new**) |
| EMI Option | ₹17,749 × 6 months (total ₹1,06,490, No Cost EMI) | ❌ No |
| Exchange Offer | Up to ₹42,600 | ❌ No |
| Bank Offers | ₹4,000 off | ❌ No |
| Protect Promise Fee | **+₹306** | ❌ No — an *additive* fee |

**Six distinct monetary figures on one page. Only one is MOP.**

Two hazards the architecture did not anticipate:

1. **`"Lowest Price for You"` (F8)** is displayed prominently and is *lower* than the selling price. Any heuristic resembling "take the most prominent price" or "take the lowest price" captures ₹1,04,990 — a price contingent on unstated conditions. This price tier is **not in the `PriceQuote` schema** and must be added as an explicit non-MOP field.
2. **`Protect Promise Fee: +₹306`** is the first *additive* figure encountered. Every other non-MOP price is a discount. A naive "smallest/largest number" rule breaks differently on this one. It must be explicitly captured and excluded.

This single page is strong vindication of the architecture's decision to model `PriceQuote` with named non-MOP fields and a `price_basis` allowlist, rather than scraping "the price."

### 3.6 Specifications — PARTIALLY CONFIRMED

Retrieved from the initial payload: Processor, RAM, Storage, Display (15.6" FHD, 144 Hz), GPU (RTX 4050, 6 GB), OS, Weight (2.38 kg).

These are the **summary/highlights** specs. Flipkart's full specification table (which typically includes ports, battery, keyboard, warranty, and possibly the MTM) was not in the delivered payload. **Expander/scroll interaction is required — UNKNOWN until browser verification.**

---

## 4. Amazon Findings

**Overall: NOT CONFIRMED — zero content obtained. 3/3 attempts blocked.**

| # | URL | Result |
|---|---|---|
| 1 | `https://www.amazon.in/s?k=83S000FYIN` | **HTTP 503 Service Unavailable** |
| 2 | `https://www.amazon.in/s?k=83JC0045IN` | **HTTP 503 Service Unavailable** |
| 3 | `https://www.amazon.in/dp/B0CX5V8QY2` | **HTTP 500 Internal Server Error** |
| 4 | `https://www.amazon.in/s?k=laptop` via `curl` | **Status `000`** (sandbox egress) |

**Observable behaviour only, as instructed:**

- **No CAPTCHA challenge page was served.** Amazon's well-known "Enter the characters you see below" robot check did **not** appear. The responses carried **no body at all**.
- The pattern (503 on search paths, 500 on a product path) is consistent with **edge-level throttling before application handling**, not with an interactive challenge.
- The 500 on `/dp/` may indicate a different rejection path than search, **or** simply an invalid ASIN — `B0CX5V8QY2` was not verified to exist. **Do not read the 500 as evidence about Amazon's defences.** This is a flawed probe and should be repeated in M1 with a known-valid ASIN.

**Everything about Amazon.in — search behaviour, PDP structure, twister/variant mechanism, model field, price structure, marketplace-seller identification, breadcrumb — is UNKNOWN.**

The architecture rated Amazon `AGGRESSIVE` on bot defence and scheduled it last (M6) as the hardest adapter. **Nothing observed contradicts that; nothing observed confirms it either.** The rating stands as an assumption, now with weak supporting evidence.

---

## 5. Reliance Digital Findings

**Overall: PARTIALLY CONFIRMED — reachable, but renders nothing without JavaScript.**

### 5.1 The core finding (F3) — CONFIRMED

Request: `https://www.reliancedigital.in/product/lenovo-loq-15arp9-83jc0045in-gaming-laptop-...-m2bsdc-8674266`

Returned **HTTP 200**. The entire retrieved payload was:

> `Online Electronic Shopping Store in India - Reliance Digital`

No product title. No price. No specifications. No breadcrumb. This is an **empty single-page-application shell** — all content is client-side rendered.

**This directly contradicts the planned configuration:**

```yaml
reliance_digital: {enabled: true, strategy: http_first, rate_limit_rpm: 20}
```

`http_first` **cannot work for Reliance Digital.** See §21 for the required change.

**Silver lining:** an SPA of this kind almost always hydrates from a JSON API. The architecture's `NetworkRecorder` (XHR interception preferred over DOM scraping) is likely to be *more* valuable here than anywhere else. **UNKNOWN** whether such an endpoint exists — it requires browser-level network interception to discover, which is M1 work.

### 5.2 Search URL pattern — NOT CONFIRMED (pattern is wrong)

`https://www.reliancedigital.in/search?q=83S000FYIN` returned a **rendered 404 page**, verbatim:

> "Oops! The page was not found"
> "The specific page that you're looking for was not found or maybe an error, try refreshing the page."

Notably this 404 page **did** render (header, logo, Cart, Wishlist, Orders, Contact us, Store Locator, "Refresh Page"/"Go Back" buttons) — so RD renders *some* templates server-side while product pages are client-rendered. The correct search path is **UNKNOWN**.

### 5.3 PDP URL structure — CONFIRMED (from URLs)

```
https://www.reliancedigital.in/product/{slug}-{6-char-alnum}-{7-digit-numeric}
```

| Trailing tokens | Product |
|---|---|
| `-m2bsdc-8674264` | Lenovo LOQ 15IRX9 `83DV00L9IN` |
| `-m2bsdc-8674266` | Lenovo LOQ 15ARP9 `83JC0045IN` |
| `-m2bo2v-8673498` | Lenovo LOQ 15IRX9 `83DV00B7IN` |
| `-ly5fi6-7726111` | Lenovo LOQ `83GS000PIN` |
| `-ly4bpi-7724544` | Lenovo LOQ 2024 `83FQ0009IN` |

Two trailing tokens. **Note `m2bsdc` repeats across two different products** (`8674264`, `8674266`) — so the 6-char token is **not** a unique product key; it is likely a batch/import identifier. The 7-digit numeric is the product ID. **INFERENCE**, from the repeat observation.

Collection pages use a separate shape: `/collection/lenovo-gaming-laptops`, `/collection/lenovo-gaming-laptops-251218`.

### 5.4 Model number in title — CONFIRMED (high value)

**Reliance Digital places the MTM directly in the product title**, verbatim from search results:

> `Buy Lenovo LOQ 15IRX9 83DV00L9IN Gaming Laptop (Intel Core i5-13450HX/24 GB/512 GB SSD/RTX 4050 6GB/Windows 11/MSOffice/Full HD), 39.62 cm - 15.6 inch, Luna Grey at Reliance Digital`

> `Buy Lenovo LOQ Intel Core i5 12th Gen 12450HX (16 GB/512 GB SSD/Windows 11 Home/6 GB Graphics/NVIDIA GeForce RTX 3050) LOQ 83GS000PIN Gaming Laptop 39.62 cm (15.6 inch), Luna Grey, 2.38 Kg, with MS Office at Reliance Digital`

MTMs confirmed in RD titles: `83DV00L9IN`, `83GS000PIN`, `83DV00B7IN`, `83FQ0009IN`, `83JC0045IN`, `83JC00JVIN`.

**Both positions occur** — after the series code (`LOQ 15IRX9 83DV00L9IN`) and after the spec block (`... LOQ 83GS000PIN Gaming Laptop`). Position-based extraction will fail; **boundary-safe token scanning across the whole title is required** — exactly as the architecture's T3 embedded-match rule specifies.

Also note RD titles carry display size in **both** units: `39.62 cm - 15.6 inch`. This validates the dual-unit parsing case and the ±0.15 in tolerance decision (39.62 cm → 15.598 in).

### 5.5 UNKNOWN for RD

Price structure and labels, spec table structure, breadcrumb, pincode behaviour, JSON API existence, search result structure, CAPTCHA behaviour — **all UNKNOWN**, because no PDP content was ever rendered.

---

## 6. Vijay Sales Findings

**Overall: CONFIRMED — the most accessible and best-structured platform of the four.**

### 6.1 Access — CONFIRMED

Three PDPs fetched, all HTTP 200 with **complete content in the initial payload**: title, model, SKU, breadcrumb, prices, and a detailed specification table. No JS execution, scrolling, or clicking was required to obtain any of it.

Note: `https://www.vijaysales.com/search/83S000FYIN` returned **HTTP 404** — that search path is wrong. Correct search URL pattern is **UNKNOWN**.

### 6.2 PDP URL structure — CONFIRMED

```
https://www.vijaysales.com/p/{sku}/{slug-including-lowercased-model}
```

The numeric segment matches the on-page SKU, **verified on two of three fetches**:

| Requested URL | On-page SKU | On-page model | Match? |
|---|---|---|---|
| `/p/243423/...-83dv018qin` | `243423` | `83DV018QIN` | ✅ |
| `/p/241406/...-83jc00jvin` | `241406` | `83JC00JVIN` | ✅ |
| `/p/230914/...-83jc0045in` | **`243423`** | **`83DV018QIN`** | ❌ **(F10)** |

### 6.3 ⚠️ F10 — URL ID / page content mismatch — PARTIALLY CONFIRMED

The third fetch requested product `230914` with slug `...83jc0045in` (a LOQ 15ARP9, Ryzen 7, RTX 4050) and received a page reporting SKU `243423`, model `83DV018QIN` — a **different product** (LOQ 15, i5 13th Gen).

Confirmed by fetching `/p/243423/...` directly and receiving byte-identical product details (same title, same SKU, same ₹88,990 / ₹1,27,000 prices).

**Candidate explanations — none verified:**
- `230914` is delisted and 301-redirects to a successor product;
- `230914` is unavailable and the site serves a substitute;
- the fetch channel followed a redirect silently.

**This must not be diagnosed from this evidence.** What *is* established, and is sufficient for architecture:

> **The numeric ID in a Vijay Sales URL cannot be trusted as the identity of the product actually served.** Identity must be read from the rendered page, and the requested-vs-served ID must be compared and flagged on mismatch.

This generalises. The architecture already stores `final_url` alongside `url` in `RawCapture` — that was the right call, and it must now be *enforced*: a stage that compares requested ID to served ID and raises `IDENTITY_REDIRECT` when they differ.

### 6.4 ⚠️ F7 — "Store Display Unit" listing condition — CONFIRMED

Verbatim title of the main product on `/p/243423/`:

> `Store Display Unit - Lenovo LOQ 15 Gaming Laptop (13th Gen Intel Core i5/ 24GB RAM/ 512GB SSD/ 15.6 Inch (39.6 cm) Full HD Display/ 6GB-NVIDIA GeForce RTX 4050 Graphics/ Windows 11/ MS-Office) 83DV018QIN`

Confirmed as the **main product title** (H1), not a badge and not a related item — verified by re-fetching with an explicit instruction to exclude recommended products and to report any title prefix.

Supporting signal on the same page: *"Exclusively available in your nearest Vijay Sales Store"*.

**Why this matters more than it looks.** A store display unit is a demonstration/floor unit — physically used, typically discounted, and not equivalent to new retail stock. Its price (₹88,990 against MRP ₹1,27,000, a 30% discount) is *plausible enough not to trigger a price anomaly*, and the model number and full spec set match a real product perfectly.

Under the architecture as designed, this listing would have passed the G1 condition gate (whose enum is `NEW | RENEWED | REFURBISHED | OPEN_BOX | USED | UNKNOWN` — none of which match "Store Display Unit"), matched on model and specs, and been reported as a legitimate competitor price. **This is precisely the silent-wrong class the architecture exists to prevent, and the current condition list does not catch it.**

### 6.5 Price structure — CONFIRMED, clean

| Label as displayed | Example value | Role |
|---|---|---|
| **VSP** (Vijay Sales Price) | ₹71,990 | ✅ **MOP** |
| MRP | ₹1,04,690 | List price |
| Discount | `31% off` | Derived |
| `(Incl. of all taxes)` | — | Tax qualifier |
| EMI Available | "starting from" | Not MOP |
| Loyalty Points | 540 Points | Not MOP |
| Free Gift | "Portronics Sound Slick V Wireless Soundbar (Worth ₹3,199)" | **Bundle signal** |

Vijay Sales has the **cleanest price model of any platform observed** — a single clearly-labelled selling price (`VSP`) with no personalised tier and no additive fee. No exchange price, bank-offer price, coupon, or cashback appeared on any of the three PDPs.

**The free gift is a bundle signal (G4).** A soundbar worth ₹3,199 bundled with a laptop does not change product identity, but it does affect price comparability. The architecture's `is_bundle`/`bundle_items` fields cover this — the gate must be tuned to *flag* value-added gifts rather than *reject* them (rejecting would discard a valid competitor listing).

### 6.6 Specifications — CONFIRMED, richest of any platform

Verbatim spec rows from `/p/241406/`:

| Spec | Value |
|---|---|
| Processor | `AMD Ryzen 7 7435HS (8C/16T, 3.1/4.5GHz, 4MB L2/16MB L3)` |
| Graphics | `NVIDIA GeForce RTX 3050A 4GB GDDR6` |
| Display | `15.6" FHD (1920x1080) IPS, 300nits, 144Hz, G-SYNC` |
| RAM | `24GB SO-DIMM DDR5-4800` |
| Storage | `512GB SSD M.2 PCIe 4.0x4 NVMe` |
| OS | `Windows 11 Home` |
| Software | `MS Office Home 2024` |
| Camera | `HD 720p with E-shutter` |
| Audio | `2W x2 with Nahimic Audio` |

Every CRITICAL laptop spec in the architecture's schema is directly derivable. These are **compound values** — one row yields several canonical specs (`Processor` → `cpu.brand`, `cpu.family`, `cpu.model`, `cpu.cores`; `Display` → `display.size_in`, `display.resolution`, `display.panel_type`, `display.brightness_nits`, `display.refresh_hz`).

**This validates the `SpecValue`/`SpecField` separation** and confirms that a *compound-row splitter* is required in `normalization/spec_value.py` — the architecture assumed one row → one spec. **Small but real schema-adjacent gap.**

Also note `RTX 3050A` — the "A" suffix denotes a distinct lower-TDP variant, not a typo. `gpu.model` normalisation must **not** strip it. This is the never-strip invariant appearing again, in GPU model names rather than MTMs.

### 6.7 Breadcrumb — CONFIRMED

> `Home > Computers and Laptops > Laptops > Gaming Laptops > Lenovo LOQ 15ARP9`

Four levels plus product leaf, terminating in a **product-type** node (`Gaming Laptops`) — unlike Flipkart, which terminates in a **brand** node (`Lenovo Laptops`). Both must map to the same canonical category. Good, direct input for the accessory gate.

---

## 7. Search Findings

| Platform | Search reachable | Model-number search works | Evidence |
|---|---|---|---|
| **Croma** | ❌ **NOT CONFIRMED** | UNKNOWN | 403 on `/search/?text=323360` |
| **Flipkart** | ✅ CONFIRMED | ❌ **CONFIRMED BROKEN** | 2,135 results; #1 = baby cream |
| **Amazon** | ❌ NOT CONFIRMED | UNKNOWN | 503 ×2 |
| **Reliance Digital** | ❌ Pattern wrong | UNKNOWN | `/search?q=` → rendered 404 |
| **Vijay Sales** | ❌ Pattern wrong | UNKNOWN | `/search/{q}` → HTTP 404 |

**Not one platform's search was successfully exercised end-to-end.** Flipkart's was reachable and demonstrably unreliable; the other four were blocked or had incorrect URL patterns.

**Consequence for the architecture's `QueryPlan`:** the six-strategy plan (Q1–Q6) is entirely unvalidated. Q1 (exact model) is confirmed *harmful* on Flipkart. Whether Q4 (brand + series + specs) performs better is **UNKNOWN**.

**Recommendation:** treat search-URL discovery as an explicit, budgeted M1 task with its own acceptance criterion (correct search URL pattern documented and exercised for all five platforms), not as incidental adapter work.

**Silver lining:** search-engine site queries reliably surfaced correct PDP URLs for Croma, RD, and Vijay Sales. If native search proves hostile, a search-engine-mediated discovery fallback is a viable Plan B — with the significant caveat that it introduces a third-party dependency, staleness, and its own ToS considerations. Flagged as an option, **not** recommended as a primary path.

---

## 8. PDP Findings

| Platform | PDP reachable | Content in initial payload | Requires JS |
|---|---|---|---|
| **Croma** | ❌ 403 ×2 | — | UNKNOWN |
| **Flipkart** | ✅ | Title, breadcrumb, all prices, summary specs | Full spec table: **likely yes** |
| **Amazon** | ❌ 500 | — | UNKNOWN |
| **Reliance Digital** | ⚠️ HTTP 200, **empty shell** | **Nothing** — page title only | ✅ **CONFIRMED YES** |
| **Vijay Sales** | ✅ | **Everything** — title, model, SKU, breadcrumb, prices, full spec table | **CONFIRMED NO** |

### URL patterns established

| Platform | Pattern | ID reliability |
|---|---|---|
| Croma | `/{slug}/p/{6-digit}` | Likely SKU — **unverified** |
| Flipkart | `/{slug}/p/{itemid}?pid={PID}&lid={LID}` | Use **PID**; item ID appears non-unique |
| Reliance Digital | `/product/{slug}-{6-alnum}-{7-digit}` | Use 7-digit; 6-char token repeats across products |
| Vijay Sales | `/p/{sku}/{slug}` | Usually SKU — **but see F10** |

**Cross-cutting conclusion: URL-derived IDs are advisory everywhere.** Croma's is unverified, Flipkart's has three competing ID spaces, RD's has a non-unique component, and Vijay Sales' was observed to disagree with page content. `platform_product_id` must be read from the rendered page, with the URL ID retained only for cross-checking.

---

## 9. Specification Findings

| Platform | Specs obtained | Depth | Interaction needed |
|---|---|---|---|
| **Vijay Sales** | ✅ CONFIRMED | **Rich** — compound rows with cores/threads/clock/cache, memory config, storage interface, panel + nits + refresh + G-SYNC | **None** |
| **Flipkart** | ⚠️ PARTIAL | Summary only (7 fields) | Full table: expander/scroll — **UNKNOWN** |
| **Croma** | ❌ UNKNOWN | Parenthesised title block is a usable fallback | UNKNOWN |
| **Reliance Digital** | ❌ UNKNOWN | — | JS rendering mandatory |
| **Amazon** | ❌ UNKNOWN | — | UNKNOWN |

### Findings that affect the spec schema

1. **Compound spec rows are the norm, not the exception.** One Vijay Sales row (`Processor: AMD Ryzen 7 7435HS (8C/16T, 3.1/4.5GHz, 4MB L2/16MB L3)`) yields four canonical specs. The architecture's implicit one-row-one-spec assumption needs a **compound-row splitter** stage in normalisation.
2. **Dual-unit values confirmed.** RD titles carry `39.62 cm - 15.6 inch`; Vijay Sales carries `15.6 Inch (39.62 cm)` and also `(39.6 cm)` on another listing. Note **39.6 vs 39.62** — the same physical display, two roundings. Converted: 15.590 in vs 15.598 in. The **±0.15 in tolerance decision is vindicated**; an exact comparator would produce MISMATCH on identical hardware.
3. **A GPU spec was found that must not be normalised away.** `RTX 3050A` (Vijay Sales) is a genuinely distinct variant from `RTX 3050` (different TDP/performance). Stripping the `A` would merge two different products. The never-strip invariant applies to `gpu.model`, not just model codes.
4. **Flipkart surfaces GPU TDP** (`115 W`) which no other platform did. Candidate additional spec, `gpu.tdp_w` — valuable because it distinguishes same-name GPU configurations. **Availability elsewhere is UNKNOWN.**

---

## 10. Model Number Findings

### 10.1 Where the model appears, by platform

| Platform | In title | In URL slug | In spec table | Status |
|---|---|---|---|---|
| **Vijay Sales** | ✅ **Yes** (suffix position) | ✅ Yes (lowercased) | ✅ Yes ("Model") | CONFIRMED |
| **Reliance Digital** | ✅ **Yes** (variable position) | ✅ Yes | UNKNOWN | CONFIRMED (title/URL) |
| **Flipkart** | ❌ **No MTM** — series code only | ❌ No MTM | ❌ Not in delivered payload | PARTIALLY CONFIRMED |
| **Croma** | ⚠️ Series code confirmed; MTM UNKNOWN. HP part numbers **do** appear (`2Z326PA#ACJ`) | Slug mirrors title | UNKNOWN | PARTIALLY CONFIRMED |
| **Amazon** | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |

### 10.2 The Lenovo MTM structure — CONFIRMED from 15 real examples

```
83 + {2 alpha: platform code} + {4 alnum: configuration} + {2 alpha: region}
```

| MTM | Series | Source |
|---|---|---|
| `83S000FYIN` | LOQ 15ARP10E | Reference product (user-supplied) |
| `83S000CPIN` | LOQ Essential | Search |
| `83S00000US` | LOQ-E (**US market**) | Search |
| `83DV00L9IN` | LOQ 15IRX9 | Reliance Digital |
| `83DV00B7IN` | LOQ 15IRX9 | Reliance Digital |
| `83DV018NIN` | LOQ 15 | Vijay Sales |
| `83DV018QIN` | LOQ 15 | Vijay Sales |
| `83JC0045IN` | LOQ 15ARP9 | **RD and Vijay Sales — identical** |
| `83JC00JVIN` | LOQ 15ARP9 | Vijay Sales |
| `83GS000PIN` | LOQ (i5 12th) | Reliance Digital |
| `83GS008BIN` | LOQ 15IAX9 | Vijay Sales |
| `83GS003QIN` | LOQ 15IAX9 | Vijay Sales |
| `83LK00AVIN` | LOQ 15IAX9E | Vijay Sales |
| `83LK00D9IN` | LOQ 15IAX9E | Search |
| `83FQ0009IN` | LOQ 2024 | Reliance Digital |

The 2-alpha platform code correlates cleanly with the series: `83DV`→15IRX9, `83JC`→15ARP9, `83GS`→15IAX9, `83LK`→15IAX9E, `83S0`→15ARP10E/Essential.

### 10.3 ⭐ The never-strip invariant — CONFIRMED by real data (F9)

> **MODEL NORMALIZATION MUST NEVER REMOVE CHARACTERS FROM INSIDE AN ALPHANUMERIC RUN.**

Three independent real-world proofs:

**Proof 1 — region code is inside the run and is product-distinguishing:**
```
83S000FYIN   → Lenovo LOQ 15ARP10E, India
83S00000US   → Lenovo LOQ-E,        United States
             ↑↑ different region, different product, both trailing the run
```
Stripping a trailing `IN`/`US` would produce `83S000FY` and `83S000 00`, merging or corrupting two distinct regional SKUs.

**Proof 2 — same prefix, different product:**
```
83S000FYIN   → LOQ 15ARP10E   (Ryzen 5 7535HS, RTX 3050)
83S000CPIN   → LOQ Essential  (different configuration)
```
The first six characters are identical. Only the 4-char configuration block differs. Any truncation-based normalisation merges these.

**Proof 3 — a one-character difference is a real product difference:**
```
83DV018NIN   → LOQ 15, 16 GB RAM, RTX 3050
83DV018QIN   → LOQ 15, 24 GB RAM, RTX 4050
             ↑ N vs Q — different RAM and different GPU
```
Two products differing by a single character in the configuration block have materially different specifications and prices (₹88,990 vs a different tier). **Fuzzy or edit-distance model matching would be catastrophic here** — these are edit-distance 1 apart and are not the same product.

**Verdict: the invariant is not a defensive nicety. It is load-bearing, and the evidence supports it unambiguously.**

### 10.4 Safe vs unsafe transformations — as observed

| Transformation | Verdict | Evidence |
|---|---|---|
| Unicode NFKC normalisation | **Safe** | No counter-example |
| Case folding (`83jc0045in` → `83JC0045IN`) | **Safe — CONFIRMED** | Vijay Sales URL slugs are lowercase; titles uppercase; same product |
| Whitespace collapse | **Safe** | No counter-example |
| Removing a **delimiter-bounded** suffix token (`-IND`, `#ACJ`) | **Conditionally safe** | HP `2Z326PA#ACJ` shows `#` as a real delimiter. **No real `-IN` suffixed example was found — see §15.2** |
| Stripping trailing alpha from inside a run (`83S000FYIN` → `83S000FY`) | ❌ **UNSAFE — CONFIRMED** | Proof 1 above |
| Truncating the configuration block | ❌ **UNSAFE — CONFIRMED** | Proof 2 above |
| Fuzzy / edit-distance matching on MTMs | ❌ **UNSAFE — CONFIRMED** | Proof 3 above (edit distance 1 = different product) |
| Stripping GPU variant suffix (`RTX 3050A` → `RTX 3050`) | ❌ **UNSAFE — CONFIRMED** | §9 finding 3 |

### 10.5 Material description decode — CONFIRMED (F12)

Reference input: `LENOVO 15 LOQE FYIN 7KR5 16GB6X 512+3050`
Resolved product: **Lenovo LOQ 15ARP10E `83S000FYIN`**, AMD Ryzen 5 7535HS, 16 GB DDR5, 512 GB SSD, RTX 3050 6 GB, 15.6" IPS 144 Hz.

| Token | Decodes to | Confidence |
|---|---|---|
| `LENOVO` | Brand: Lenovo | **Certain** |
| `15` | 15.6" display class | **High** |
| `LOQE` | LOQ series, **E** variant → `15ARP10E` | **High** — the trailing `E` matches the series suffix |
| `FYIN` | **Last 4 chars of MTM `83S000FYIN`** | **Certain — direct substring** |
| `7KR5` | Ryzen **5**, 7000-series (7535HS) | **High** |
| `16GB6X` | 16 GB RAM; `6X` ambiguous (DDR5 speed class? 6 GB VRAM?) | **Partial** |
| `512` | 512 GB SSD | **Certain** |
| `3050` | RTX 3050 | **Certain** |

**Architecturally important:** the `FYIN` token is a **deterministic substring of the MTM**. This means the material description can be linked to the model code by pure string operations — no LLM required for the highest-value token. The architecture's L1 (LLM decode) task should be **narrowed**: run a deterministic suffix-matcher first, and invoke the LLM only for genuinely ambiguous tokens like `16GB6X`.

That is a reduction in LLM decision surface, consistent with the design's stated principle. Recommended for M1.

---

## 11. MOP Findings

### 11.1 Price types observed across all platforms

| Price type | Flipkart | Vijay Sales | Croma | RD | Amazon |
|---|---|---|---|---|---|
| Selling price / MOP | ✅ `Selling Price` ₹1,08,990 | ✅ `VSP` ₹71,990 | UNKNOWN | UNKNOWN | UNKNOWN |
| MRP / struck-through | ✅ implied by discount | ✅ `MRP` ₹1,04,690 | UNKNOWN | UNKNOWN | UNKNOWN |
| **"Lowest Price for You"** | ✅ **₹1,04,990** ⚠️ | ❌ | UNKNOWN | UNKNOWN | UNKNOWN |
| EMI | ✅ ₹17,749×6 | ✅ "starting from" | UNKNOWN | UNKNOWN | UNKNOWN |
| Exchange offer | ✅ up to ₹42,600 | ❌ | UNKNOWN | UNKNOWN | UNKNOWN |
| Bank offer | ✅ ₹4,000 off | ❌ | UNKNOWN | UNKNOWN | UNKNOWN |
| **Protect Promise Fee (additive)** | ✅ **+₹306** ⚠️ | ❌ | UNKNOWN | UNKNOWN | UNKNOWN |
| Loyalty points | ❌ | ✅ 540 Points | UNKNOWN | UNKNOWN | UNKNOWN |
| Free gift (bundle) | ❌ | ✅ ₹3,199 soundbar | UNKNOWN | UNKNOWN | UNKNOWN |
| Coupon | Not observed | Not observed | UNKNOWN | UNKNOWN | UNKNOWN |
| Cashback | Not observed | Not observed | UNKNOWN | UNKNOWN | UNKNOWN |

"Not observed" ≠ "does not exist." Coupons and cashback are common on both platforms and were simply absent from the specific listings fetched.

### 11.2 Evidence required to classify a price as MOP

Derived from what was actually observed. A figure may be recorded as `mop` **only if all of the following hold**:

1. **It carries a platform-recognised selling-price label** from a per-platform allowlist:
   - Flipkart: `Selling Price` — and explicitly **not** `Lowest Price for You`
   - Vijay Sales: `VSP` / `Vijay Sales Price`
   - Croma / RD / Amazon: **UNKNOWN — must be established in M1 before those adapters ship**
2. **It is not qualified by a condition.** Any adjacency to *"with exchange"*, *"bank offer"*, *"No Cost EMI"*, *"for you"*, *"with coupon"*, *"card"* disqualifies it.
3. **It is not additive.** `Protect Promise Fee +₹306` is a fee, not a price.
4. **It is a total, not an instalment.** `₹17,749 × 6 months` is not MOP even though it is a rupee figure.
5. **It is consistent with MRP.** `mop ≤ mrp`. A "selling price" above MRP indicates mis-extraction.
6. **The listing condition is NEW.** ⚠️ **F7** — a `Store Display Unit` price is not a comparable retail MOP even when correctly labelled `VSP`.
7. **The pincode context is recorded** — UNKNOWN whether prices vary by pincode on any platform; not testable here.

**If no figure satisfies all seven, `mop` must be `None` and the candidate flagged `MOP_UNRESOLVED`.** The Flipkart page — six monetary figures, exactly one of them MOP — is the strongest possible argument for this discipline.

### 11.3 Schema changes required

The `PriceQuote` model must gain:

```python
personalised_price: Money | None     # F8 — Flipkart "Lowest Price for You"
additive_fees: list[NamedFee]        # F8 — Flipkart "Protect Promise Fee" +₹306
loyalty_points: int | None           # Vijay Sales
free_gift_value: Money | None        # Vijay Sales bundle signal
```

---

## 12. Accessory Collision Findings

### 12.1 Category separation — CONFIRMED viable on Croma

Croma's numeric category IDs cleanly separate products from accessories (F11):

| Product categories | Accessory categories |
|---|---|
| `/c/20` Laptops | `/c/74` Computer & Laptop Accessories |
| `/c/806` Gaming Laptops | `/c/567` Batteries, Chargers & Adapters |
| `/c/855` Windows Laptops | `/c/2001` Laptop Bags |

Brand-scoped accessory categories also exist and are distinct: `/lenovo-laptop-covers/bc/b-0216-557`, `/lenovo-computer-and-laptop-accessories/bc/b-0216-74`.

**Assessment: the accessory gate's primary signal (S1, category) is viable on Croma and is decisive.** Whether the numeric category ID is exposed on the PDP itself (as opposed to on category listing pages) is **UNKNOWN** — 403.

### 12.2 Accessory titles carry their own model codes — CONFIRMED (collision risk)

A Flipkart search for laptop accessories returned pure accessories, several carrying model-like codes:

> 1. `T210 Laptop Sleeve/Cover` — ₹449
> 2. `CASE_BO 15.6 Toploder T210 black-row Laptop Sleeve/Cove...` — ₹526
> 3. `15.6-inch Casual Toploader Briefcase T210 Laptop Sleeve...` — ₹548
> 4. `OFFICE T210 Laptop Sleeve/Cover` — ₹521
> 5. `Cover Pouch MacBook Pro, Waterproof Laptop Sleeve/Cover` — ₹212

**`T210` is a Lenovo accessory model code.** Four of the first fifteen results carry it.

**Two distinct collision risks confirmed:**

1. **Accessories carry their own model codes.** A naive "does the title contain a model-shaped token?" check will fire on accessories. The gate must verify the token matches *the reference product's* model, not merely that a model-shaped token exists.
2. **Accessories carry the product's size/spec tokens.** `15.6-inch` and `15.6` appear in accessory titles — the same token as the laptop's `display.size_in`. A spec-similarity matcher without a category gate could score a ₹548 briefcase as spec-adjacent to a ₹1,08,990 laptop. **The price ratio here is 0.005 — well inside the architecture's S5 signal threshold of 0.15.**

### 12.3 The specific collision the brief asked for — PARTIALLY CONFIRMED

The brief asked for examples where the same product name/model family appears on accessories, remotes, chargers, bags, and replacement parts.

**Found:** bags/sleeves/covers with their own model codes and matching size tokens (§12.2); Croma accessory categories for chargers/adapters and batteries (§12.1); Flipkart's `83S000FYIN` search returned *"laptop batteries"* and *"third-party laptop batteries and accessories"* mixed into laptop results.

**Not found in M0:** a verified single case of an accessory listing containing the *exact reference MTM* (`83S000FYIN`) in its title — e.g. `"Charger for Lenovo LOQ 83S000FYIN"`. Such listings are commonly reported to exist, but **none was directly observed here**, so this is recorded as **NOT CONFIRMED — absence of evidence, not evidence of absence.**

**M1 action:** deliberately construct this test case. The accessory gate's "for {brand} {model}" pattern (signal S2, weight 0.85) is currently designed against a hypothesised listing shape rather than an observed one.

---

## 13. Dynamic Content Findings

| Platform | Static payload sufficient? | Evidence |
|---|---|---|
| **Vijay Sales** | ✅ **Yes — CONFIRMED** | Title, model, SKU, breadcrumb, prices, full spec table all present with no JS/scroll/click |
| **Flipkart** | ⚠️ **Partially** | Title, breadcrumb, all six price figures, summary specs present. Full spec table absent — **expansion likely required, UNKNOWN** |
| **Reliance Digital** | ❌ **No — CONFIRMED** | HTTP 200, page title only. Full client-side rendering |
| **Croma** | UNKNOWN | 403 |
| **Amazon** | UNKNOWN | 503/500 |

**Scrolling, clicking, expander, carousel, and lazy-load behaviour is UNKNOWN for all five platforms** — this channel cannot scroll or click, so none of it could be tested. The architecture's `ScrollPolicy` and `ExpanderSpec` designs are **entirely unvalidated**.

What *is* established: **a browser is mandatory.** Reliance Digital alone settles it — no HTTP-only strategy can ever work there. This is a direct configuration change (§21).

---

## 14. CAPTCHA / Blocking Findings

**Observable behaviour only, as instructed. No circumvention was attempted, and none should be inferred as recommended.**

| Platform | Requests | Observed response | Challenge page? |
|---|---|---|---|
| **Croma** | 4 proxied + 2 curl | **403 Forbidden**, no body | **No** — no CAPTCHA served |
| **Amazon.in** | 3 proxied + 1 curl | **503** ×2, **500** ×1, no body | **No** — the standard robot check did not appear |
| **Flipkart** | 3 proxied | **200**, full content | No |
| **Reliance Digital** | 2 proxied + 1 curl | **200** (empty shell), **200** (404 template) | No |
| **Vijay Sales** | 4 proxied + 1 curl | **200** ×3, **404** ×1 | No |

### Interpretation — stated carefully

- **Croma's 403 is uniform across homepage and PDPs**, which points to an edge/WAF deny keyed on client reputation or TLS/HTTP fingerprint rather than on scraping-sensitive paths. **No interactive challenge was offered** — there is nothing to solve, which means UA rotation alone is unlikely to help.
- **Amazon's 503-on-search / 500-on-product** pattern is consistent with pre-application throttling. The 500 is a **weak data point** — the ASIN used was not verified to exist, so it may simply be a bad URL. **Do not build conclusions on it.**
- **The `curl` result (`000` on all hosts) is not a blocking finding.** It is a sandbox egress limitation and says nothing about the target sites.

### ⚠️ The most important caveat in this section

**These results characterise one specific, weak client** — datacentre egress, no browser, no session, no cookie warm-up, no realistic TLS fingerprint. They are **evidence that a naive HTTP client is blocked**, and **not evidence that a properly configured Playwright browser will be blocked.**

The architecture's bot-defence ratings (`AGGRESSIVE` for Amazon, `MODERATE` for others) are **neither confirmed nor refuted**. Re-testing with a real browser is a **mandatory M1 gate** before any adapter work begins.

---

## 15. Model Variation Examples

### 15.1 Case coverage against the brief's taxonomy

| Case | Description | Found? | Evidence |
|---|---|---|---|
| **A** | Model appears exactly | ✅ **CONFIRMED** | `83JC0045IN` appears identically on **Reliance Digital and Vijay Sales** — a genuine cross-platform exact-model pair |
| **B** | Retailer-specific suffix/prefix | ⚠️ **NOT CONFIRMED for MTMs** | No `-IN`/`-IND` suffixed MTM was observed. A *different* delimiter form was found in HP part numbers: `2Z326PA#ACJ` (Croma). **Prefix found**: `Store Display Unit - ` on Vijay Sales titles |
| **C** | Model embedded in a longer title | ✅ **CONFIRMED, repeatedly** | RD: `Lenovo LOQ 15IRX9 83DV00L9IN Gaming Laptop (...)`; VS: `... MS-Office) 83DV018QIN`. **Position varies** — mid-title on RD, end-of-title on VS |
| **D** | Substantially different model, near-identical specs | ✅ **CONFIRMED** | `83DV018NIN` (16 GB/RTX 3050) vs `83DV018QIN` (24 GB/RTX 4050) — edit distance 1, materially different products. Also `83JC0045IN` (RTX 4050) vs `83JC00JVIN` (RTX 3050A) — same 15ARP9 series, same Ryzen 7 7435HS, same 24 GB/512 GB, **different GPU** |

### 15.2 ⚠️ On the specific `83S000FYIN` → `83S000FYIN-IN` example

The brief asked for real examples of this exact transformation. **It was not found.**

Every one of the fifteen MTMs observed appears in **bare form** — no `-IN`, `-IND`, or `/IN` suffix was attached by any retailer. Retailers vary in *whether* they show the MTM (Flipkart doesn't; RD and VS do) and *where* they place it, but not in its **form**.

**This is an important negative result and should change M1 priorities:**

- The **T2 (`SUFFIX_EQUIVALENT`) tier is currently a solution to a problem not yet observed.** It should be retained — it costs nothing, the evidence base is a single brand in a single category, and the invariant makes it safe — but it should **not** be prioritised in M1.
- **T3 (`EMBEDDED`) is the tier that actually earns its place.** It is confirmed necessary by RD and VS, in two different title positions. It should be the primary focus of model-matching implementation and testing.
- The `#` delimiter in HP part numbers (`2Z326PA#ACJ`) is the **only real delimiter-suffix evidence collected**, and it is from a different brand with a different code structure. The delimiter set must include `#`, and the market-suffix registry needs an HP-specific entry — but this is a **brand-scoped** rule, not a general one.

### 15.3 Cross-platform matching feasibility, per platform

| Reference → Competitor | Model-based match possible? | Basis |
|---|---|---|
| Croma → Vijay Sales | ✅ **Likely** (T0/T3) | Both expose MTM (Croma UNKNOWN but probable) |
| Croma → Reliance Digital | ✅ **Likely** (T3) | RD MTM in title, confirmed |
| Croma → Flipkart | ❌ **Unlikely** | **F1** — no MTM on Flipkart |
| Croma → Amazon | UNKNOWN | No access |

**This is the central strategic finding of M0.** Two of four competitors support model-based identity; one demonstrably does not; one is unknown. The pipeline must be built for a **mixed-capability** competitor set from day one, with Flipkart on a specification-only path.

---

## 16. Price Proximity Observations

### 16.1 Data collected

| Product | Platform | Selling price | MRP | Discount |
|---|---|---|---|---|
| LOQ 15ARP10E `83S000FYIN` (reference) | Third-party aggregator | ₹1,22,990 | — | — |
| LOQ 15ARP9 R7/RTX 4050 | Flipkart | ₹1,08,990 (`Lowest for you` ₹1,04,990) | — | — |
| LOQ 15ARP9 R7/RTX 4060 | Flipkart | ₹1,27,990 | — | — |
| LOQ 2025 R5 7235HS/RTX 3050A | Flipkart | ₹85,990 | — | — |
| LOQ 15IRX9 i5/RTX 4060 | Flipkart | ₹1,39,990 | — | — |
| LOQ 15IRH8 i5/RTX 3050 | Flipkart | ₹1,03,290 / ₹1,20,000 (two listings) | — | — |
| LOQ 15ARP9 `83JC00JVIN` R7/RTX 3050A | Vijay Sales | **₹71,990** | ₹1,04,690 | 31% |
| LOQ 15 `83DV018QIN` i5/RTX 4050 ⚠️ *Store Display Unit* | Vijay Sales | **₹88,990** | ₹1,27,000 | 30% |

### 16.2 What can and cannot be concluded

**Cannot be concluded: whether ±10–20% price proximity is a useful supporting signal.**

The reason is structural, not incidental: **not one confirmed same-model cross-platform pair with both prices was obtained.** `83JC0045IN` appears on both RD and Vijay Sales — the ideal test case — but RD's price could not be read (empty SPA shell) and the Vijay Sales fetch for that ID returned a different product (F10). **The one available natural experiment was defeated by two independent blockers.**

**Three observations that can be made, and that matter:**

1. **Within-series price spread is very wide.** Confirmed LOQ 15ARP9 configurations range ₹71,990 → ₹1,27,990 — a **78% spread within a single series**. Any price-proximity band applied *before* specification matching would be meaningless.
2. **Cross-retailer spread on the same series is large.** Vijay Sales ₹71,990 vs Flipkart ₹1,08,990 for Ryzen 7 15ARP9 configurations (differing in GPU: RTX 3050A vs 4050). Even allowing for the GPU difference, this is far outside ±20%. **A ±20% band would have rejected a legitimate competitor here** had it been treated as a gate rather than a supporting signal.
3. **MRP-to-selling discounts are large and inconsistent** — 30% and 31% at Vijay Sales. MRP is not a stable anchor for comparison.

**Assessment:** these observations *support* the architecture's Rule 5 position — price must be a **weak supporting signal (3 of 100 score points), never an identity gate.** Nothing observed justifies strengthening it, and observation 2 actively warns against it. But this is **corroboration, not validation** — the specific ±10–20% figure remains **UNVALIDATED** and needs same-model pairs from M1.

---

## 17. Evidence Capture Requirements

Derived from what this spike could and could not establish.

### 17.1 What must be captured per page (confirmed necessary)

| Artifact | Why — grounded in an M0 finding |
|---|---|
| Raw HTML | Baseline |
| **Post-JS DOM snapshot** | **F3** — RD content exists only after hydration |
| **Intercepted XHR/JSON** | **F3** — likely the only reliable RD source; DOM scraping an SPA is fragile |
| Final URL after redirects | **F10** — requested ID ≠ served ID on Vijay Sales |
| **Requested-vs-served ID comparison** | **F10** — must be computed and flagged, not merely stored |
| HTTP status + response headers | Croma 403 / Amazon 503 must be distinguishable from an empty page |
| Screenshot | Human adjudication of `requires_review`; evidence for F7-type findings |
| **Full raw title string** | **F7** — `Store Display Unit - ` prefix is only detectable in the untruncated title |
| **Every monetary figure with its adjacent label text** | **F8** — six figures on one Flipkart page, one is MOP |
| Scroll/expander completion report | Needed to distinguish "spec absent" from "spec not yet loaded" |
| Bot-wall detection verdict | Croma/Amazon must fail as *capture* failures, never as empty extractions |

### 17.2 Evidence quality tiers (observed, ranked)

1. **Structured JSON/XHR** — best; existence UNKNOWN on all platforms
2. **Spec table rows** — Vijay Sales confirmed rich; Flipkart partial
3. **Breadcrumb** — Flipkart and Vijay Sales confirmed, different shapes
4. **Title-embedded data** — confirmed everywhere, but marketing-contaminated
5. **URL-derived IDs** — **demoted to advisory only** on the strength of F10

### 17.3 The capture that would have prevented every ambiguity in this document

Every UNKNOWN in §2, §4, §5 traces to one root cause: **no browser.** The single highest-value M1 investment is standing up Playwright and re-running this exact recon script. Nothing else in M1 should precede it.

---

## 18. Architecture Assumptions CONFIRMED

| # | Assumption | Status | Evidence |
|---|---|---|---|
| A1 | Model normalisation must never strip inside an alphanumeric run | ✅ **CONFIRMED** | §10.3 — three independent proofs |
| A2 | Model codes appear embedded in longer competitor titles (T3 tier needed) | ✅ **CONFIRMED** | §10.1 — RD and VS, two different positions |
| A3 | MOP must be distinguished from exchange/bank/EMI/coupon prices | ✅ **CONFIRMED, understated** | §11 — Flipkart has **six** figures; two new types found |
| A4 | Price must be a weak signal, never an identity gate | ✅ **SUPPORTED** | §16.2 — 78% intra-series spread |
| A5 | Category/breadcrumb data can drive accessory rejection | ✅ **CONFIRMED** | §12.1 — Croma numeric category IDs |
| A6 | Accessories collide with products in search results | ✅ **CONFIRMED** | §12.2 — `T210`, `15.6-inch` in accessory titles |
| A7 | Spec normalisation must handle dual units and rounding | ✅ **CONFIRMED** | §9.2 — `39.6` vs `39.62` cm → ±0.15 in tolerance vindicated |
| A8 | Extraction must be separated from capture (pure, replayable) | ✅ **STRONGLY CONFIRMED** | With 3/5 platforms blocked, offline replay is the only way to develop the matcher at all |
| A9 | Three-valued spec logic (UNKNOWN as first-class) is necessary | ✅ **CONFIRMED** | Flipkart lacks MTM; RD renders nothing — UNKNOWN is the *normal* case, not the exception |
| A10 | A brand-scoped model registry is required | ✅ **CONFIRMED** | §2.4 — Lenovo region code is *inside* the run; HP's is after `#`. One global rule cannot serve both |
| A11 | Material description contains a decodable model fragment | ✅ **CONFIRMED** | §10.5 — `FYIN` is a literal substring of `83S000FYIN` |
| A12 | Croma PDP URL is `/{slug}/p/{id}` | ✅ **CONFIRMED** | §2.2 — twelve examples |
| A13 | Classification must be rule-based and auditable, not score-thresholded | ✅ **SUPPORTED** | F7/F8 are both cases where a plausible score would have been confidently wrong |

## 19. Architecture Assumptions REJECTED or WEAKENED

| # | Assumption | Status | Evidence | Required change |
|---|---|---|---|---|
| R1 | `reliance_digital: strategy: http_first` | ❌ **REJECTED** | §5.1 — empty SPA shell | Change to `browser`; make XHR interception primary. **§21.1** |
| R2 | All competitors expose a model number | ❌ **REJECTED** | **F1** — Flipkart exposes none | Flipkart must run a spec-only path; `EXACT_MATCH` unreachable there. **§21.2** |
| R3 | Exact-model search (Q1) is the strongest discovery strategy | ❌ **REJECTED for Flipkart** | **F2** — 2,135 results, #1 baby cream | Per-platform query plans; disable Q1 on Flipkart. **§21.3** |
| R4 | `discovery.early_stop_on_model_hit: true` is a safe optimisation | ❌ **REJECTED** | **F2** — would stop on a garbage result set | Default to `false`; enable only per-platform where model search is verified. **§21.3** |
| R5 | Listing conditions are `NEW / RENEWED / REFURBISHED / OPEN_BOX / USED` | ❌ **INCOMPLETE** | **F7** — `Store Display Unit` | Add `STORE_DISPLAY_UNIT` / `DEMO_UNIT`. **§21.4** |
| R6 | `PriceQuote`'s non-MOP fields are sufficient | ❌ **INCOMPLETE** | **F8** — personalised price + additive fee | Add `personalised_price`, `additive_fees`. **§21.5** |
| R7 | `platform_product_id` can be derived from the URL | ❌ **WEAKENED** | **F10** — VS ID mismatch; RD token non-unique; Flipkart has 3 ID spaces | Read from page; use URL ID for cross-check only. **§21.6** |
| R8 | One spec table row maps to one canonical spec | ❌ **REJECTED** | §9.1 — compound rows are the norm | Add a compound-row splitter. **§21.7** |
| R9 | Croma is reachable for automated capture | ⚠️ **UNVALIDATED** | §2.1 — 403 ×4 | **Must be resolved before M2.** **§21.8** |
| R10 | Amazon is `AGGRESSIVE` but workable | ⚠️ **UNVALIDATED** | §4 — 3/3 blocked | Re-test with browser in M1 |
| R11 | The T2 suffix-equivalent tier addresses a real, observed pattern | ⚠️ **WEAKENED** | §15.2 — no `-IN` suffix found on any MTM | Retain (harmless, safe), but deprioritise vs T3 |
| R12 | ±10–20% price proximity is a useful supporting signal | ⚠️ **UNVALIDATED** | §16.2 — no same-model cross-platform pair obtained | Keep as a 3-point signal; validate in M1 |

---

## 20. Open Questions

### Blocking (must be answered before M2)

| # | Question | Owner | Why blocking |
|---|---|---|---|
| **Q1** | **Does Playwright with a realistic browser profile get past Croma's 403?** | M1 | Croma is the reference product. No Croma = no pipeline. |
| **Q2** | **Is the Croma SKU the numeric `/p/{id}`?** Does `croma.com/.../p/323360` resolve to the reference laptop? | M1 | If yes, search can be bypassed entirely — a major simplification. |
| **Q3** | **Does the Flipkart MTM exist in the expandable spec table?** | M1 | Determines whether Flipkart can ever reach `EXACT_MATCH`. |
| **Q4** | **Does Reliance Digital have a discoverable product JSON API?** | M1 | Determines whether RD extraction is robust or perpetually fragile. |
| **Q5** | **Does Amazon.in respond to a real browser?** | M1 | Determines whether Amazon stays in scope for v1. |

### Important (before the relevant adapter ships)

| # | Question |
|---|---|
| Q6 | What are the correct search URL patterns for Croma, RD, Vijay Sales, Amazon? (All four were wrong or blocked.) |
| Q7 | What price label does Croma use for MOP, and does Croma show exchange/bank/coupon prices? |
| Q8 | Where does Croma display the SKU on the PDP? |
| Q9 | Does pincode change price or availability on any platform? |
| Q10 | Do Croma/Flipkart/RD/Amazon expose JSON-LD `Product` markup? |
| Q11 | What triggered the Vijay Sales `230914` → `243423` substitution (F10)? |
| Q12 | Do accessory listings carrying the *exact reference MTM* exist? (§12.3) |
| Q13 | Is `gpu.tdp_w` available outside Flipkart? |
| Q14 | How does Amazon's twister expose variant ASINs? |

### Deferred

Q15 — Are there same-model cross-platform pairs sufficient to validate the price band?
Q16 — Do Croma titles reliably contain manufacturer part numbers across brands, or only HP?
Q17 — What does `6X` mean in the material description encoding?

---

## 21. Recommended Changes Before M1

Each is the **smallest change** that addresses a specific rejected assumption. No change is proposed for convenience.

### 21.1 — Reliance Digital strategy (fixes R1) · Trivial

```yaml
# config/platforms/reliance_digital.yaml
reliance_digital:
  strategy: browser          # was: http_first  — F3: empty SPA shell
  capabilities:
    supports_http_only: false
    has_json_api: unknown    # Q4 — resolve in M1
    prefer_xhr_over_dom: true
```

### 21.2 — Per-platform model availability (fixes R2) · Small

Add to `AdapterCapabilities`:

```python
exposes_model_number: ModelAvailability   # ALWAYS | SOMETIMES | NEVER | UNKNOWN
model_locations: list[ModelLocation]      # TITLE | SPEC_TABLE | URL_SLUG | JSONLD
max_achievable_classification: MatchClassification
```

Flipkart: `exposes_model_number: NEVER` (pending Q3), `max_achievable_classification: CONFIGURATION_MATCH`.

**Rationale:** makes a real platform limitation explicit and testable, rather than surfacing as a mysterious absence of exact matches. The ranker already handles mixed classifications; this simply tells it what to expect.

### 21.3 — Per-platform query plans (fixes R3, R4) · Small

```yaml
discovery:
  early_stop_on_model_hit: false      # was true — F2
  query_plans:
    flipkart:         {strategies: [Q4, Q5, Q6], disabled: [Q1, Q2], reason: "F2"}
    vijay_sales:      {strategies: [Q1, Q3, Q4]}
    reliance_digital: {strategies: [Q1, Q3, Q4]}
    amazon_in:        {strategies: [Q1, Q3, Q4]}   # unvalidated — Q5
```

### 21.4 — Listing condition enum (fixes R5) · Trivial but high-value

```python
class ListingCondition(StrEnum):
    NEW = "NEW"
    STORE_DISPLAY_UNIT = "STORE_DISPLAY_UNIT"   # F7 — Vijay Sales
    DEMO_UNIT = "DEMO_UNIT"
    RENEWED = "RENEWED"
    REFURBISHED = "REFURBISHED"
    OPEN_BOX = "OPEN_BOX"
    USED = "USED"
    UNKNOWN = "UNKNOWN"
```

Add to `data/registries/listing_conditions.yaml`:
```yaml
store_display_unit:
  title_prefixes: ["Store Display Unit -", "Store Display Unit"]
  title_tokens: ["display unit", "demo unit", "floor model", "display piece"]
  corroborating: ["Exclusively available in your nearest", "store pickup only"]
  gate: REJECT
```

**This is the highest value-per-line change in this document.** It closes a confirmed silent-wrong path.

### 21.5 — PriceQuote extensions (fixes R6) · Small

```python
personalised_price: Money | None    # F8 — "Lowest Price for You"
additive_fees: list[NamedFee]       # F8 — "Protect Promise Fee" +₹306
loyalty_points: int | None
free_gift_value: Money | None
```

And per-platform MOP label allowlists:
```yaml
flipkart:    {mop_labels: ["Selling Price"], never_mop: ["Lowest Price for You", "Protect Promise Fee"]}
vijay_sales: {mop_labels: ["VSP", "Vijay Sales Price"]}
croma:       {mop_labels: []}   # UNKNOWN — Q7 must be answered before Croma adapter ships
```

An empty allowlist means "refuse to extract MOP" — fail-closed, per the architecture's stated preference for rejection over false matching.

### 21.6 — Identity from page, not URL (fixes R7) · Small

- `platform_product_id` is read from the rendered page.
- `url_derived_id` is stored separately.
- A new pipeline check raises `IDENTITY_REDIRECT` when they differ (F10).
- `RawCapture.final_url` comparison becomes mandatory, not advisory.

### 21.7 — Compound spec row splitter (fixes R8) · Small

Add a `CompoundSpecSplitter` stage in `normalization/`, driven by per-field regex rules in `spec_aliases/laptop.yaml`. One raw row may emit multiple `SpecValue`s, each retaining the same `raw_label`/`raw_value` and gaining a `split_rule_id` in its `normalization_trace`.

### 21.8 — 🔴 Add a mandatory M1 access gate (fixes R9, R10) · Process change

**This is the most important recommendation in this document.**

Insert a gate at the head of M1, before any adapter code is written:

> **M1 Gate 0 — Browser Access Verification.**
> Stand up Playwright. Re-run this exact recon against all five platforms with a realistic browser profile. Record status, rendered content, and screenshots.
>
> **Exit criteria:** Croma PDP and Croma SKU search both render (answers Q1, Q2). At least three of four competitors render PDPs.
>
> **If Croma does not render:** halt M1. Do not proceed to M2. Escalate to a scope decision — official Croma data access, a different execution environment, or project re-scoping. **Do not attempt to engineer around a hard block.**

**Rationale:** the architecture's entire value depends on the Croma reference product. Building adapters, matchers, and Excel output on top of an unreachable reference is the most expensive possible failure mode, and M0's job was precisely to surface it before that cost is incurred.

### 21.9 — Commit the architecture document · Trivial

Per §0.1. `docs/architecture/ARCHITECTURE.md` + `CLAUDE.md`, before implementation.

### 21.10 — Narrow the LLM decode task (opportunistic) · Small

Per §10.5: run a deterministic MTM-substring matcher against the material description first; invoke L1 only for genuinely ambiguous tokens. Reduces LLM surface area, consistent with the design's stated principle. Not required, but cheap and directionally correct.

---

## 22. M0 GO / NO-GO

# ⚠️ CONDITIONAL GO

### The decision

**Proceed to M1 — but only into a re-scoped M1 whose first and gating task is browser access verification (§21.8). Do not proceed to M2 until Croma renders.**

### Why GO

1. **The hard part of the architecture is validated.** Model matching — the highest-risk, highest-value component — is confirmed correct by real data. The never-strip invariant has three independent proofs (§10.3), and one of them (edit-distance-1 products with different GPUs and RAM) shows that a conventional fuzzy matcher would have been actively dangerous. **The design's most contested decision is its most vindicated one.**
2. **Every architectural assumption that could be tested, held.** Thirteen assumptions confirmed (§18), including the capture/extraction split, three-valued spec logic, category-driven accessory rejection, and price-as-weak-signal.
3. **The pipeline is proven end-to-end on at least one competitor.** Vijay Sales delivered title, model, SKU, breadcrumb, MOP, MRP, and a full spec table in a single static fetch — every field the canonical schema requires.
4. **M0 did exactly its job.** It surfaced two silent-wrong hazards (F7, F8) and eight assumption failures **before** a line of production code was written. Each has a small, specific remedy (§21). That is precisely the return this milestone was designed to produce.
5. **The blockers are environmental, not architectural.** Nothing observed indicates the design is wrong. Croma's 403 and Amazon's 503 characterise a weak, browserless, datacentre-egress client — not the production browser layer.

### Why CONDITIONAL

1. **🔴 The reference product path is completely unvalidated.** Zero bytes of Croma page content were obtained. Croma SKU search, PDP structure, MOP display, spec location, model location, and pincode behaviour are all **UNKNOWN**. *The pipeline begins at Croma; if Croma is unreachable, nothing downstream matters.*
2. **🔴 Three of five platforms yielded no usable page content** — Croma (403), Amazon (503/500), Reliance Digital (empty shell). Only Vijay Sales was fully readable; only Flipkart was partially readable.
3. **🟠 The highest-value competitor cannot be matched on model.** Flipkart exposes no MTM (F1) and its model search returns confident garbage (F2). This is survivable via `CONFIGURATION_MATCH`, but it must be a deliberate design position with its own tests — not a surprise discovered in M5.
4. **🟠 No DOM-level evidence exists.** Every selector, scroll policy, and expander spec in the architecture remains unvalidated. M1's real cost is higher than the original plan assumed.
5. **🟡 The price-proximity band could not be validated** — the one natural experiment available (`83JC0045IN` on two platforms) was defeated by F3 and F10 simultaneously.

### The condition, stated precisely

> **M1 begins with Gate 0 (§21.8): Playwright access verification across all five platforms.**
>
> - **Croma renders** → apply changes §21.1–§21.10, continue M1 as planned.
> - **Croma does not render** → **halt. Do not start M2.** Escalate to a scope decision: official Croma data access, a different execution environment/network, or project re-scoping. Engineering around a hard block is out of scope and should not be attempted.

### What I would not do

I would **not** weaken the architecture in response to these findings. Every failure mode M0 uncovered — the display unit priced like new stock, the personalised price undercutting the real one, the baby cream ranked first for a laptop model, two laptops one character apart with different GPUs — is an instance of the exact silent-wrong class the design was built to catch. **The findings argue for the architecture, not against it.** The changes in §21 make it *more* conservative, not less.

The one thing M0 genuinely changes is the **cost and sequencing** of M1: browser access is now a gating risk to be retired first, not an implementation detail to be handled along the way.

---

## Appendix A — Complete Request Log

| # | URL | Method | Result |
|---|---|---|---|
| 1 | `croma.com/search/?text=323360` | proxied | **403** |
| 2 | `croma.com/` | proxied | **403** |
| 3 | `croma.com/lenovo-loq-15iax9-.../p/312746` | proxied | **403** |
| 4 | `croma.com/lenovo-loq-15arp9-.../p/316063` | proxied | **403** |
| 5 | `smartprix.com/laptops/lenovo-loq-15arp10e-83s000fyin-...` | proxied | **403** |
| 6 | `flipkart.com/search?q=83S000FYIN` | proxied | **200** — 2,135 results, #1 baby cream |
| 7 | `flipkart.com/search?q=lenovo+loq+laptop+cover+sleeve+bag` | proxied | **200** — 15 accessories |
| 8 | `flipkart.com/lenovo-loq-.../p/itme3c655ce4e3c8?pid=COMHFYZ36W8ZGS8Z` | proxied | **200** — full PDP, no MTM |
| 9 | `amazon.in/s?k=83S000FYIN` | proxied | **503** |
| 10 | `amazon.in/s?k=83JC0045IN` | proxied | **503** |
| 11 | `amazon.in/dp/B0CX5V8QY2` | proxied | **500** (ASIN unverified) |
| 12 | `reliancedigital.in/search?q=83S000FYIN` | proxied | **200** — rendered 404 template |
| 13 | `reliancedigital.in/product/lenovo-loq-15arp9-83jc0045in-...-8674266` | proxied | **200** — **empty SPA shell** |
| 14 | `vijaysales.com/search/83S000FYIN` | proxied | **404** |
| 15 | `vijaysales.com/p/230914/...-83jc0045in` | proxied | **200** — **served SKU 243423** (F10) |
| 16 | `vijaysales.com/p/243423/...-83dv018qin` | proxied | **200** — full PDP, *Store Display Unit* |
| 17 | `vijaysales.com/p/241406/...-83jc00jvin` | proxied | **200** — full PDP, full specs |
| 18–22 | croma.com ×2, reliancedigital.in, vijaysales.com, amazon.in | `curl` | **`000`** — sandbox egress blocked |

Plus five web searches used for URL-shape discovery and MTM cross-referencing.

## Appendix B — Findings Index

| ID | Finding | Section | Status |
|---|---|---|---|
| F1 | Flipkart exposes no MTM | §3.4 | PARTIALLY CONFIRMED |
| F2 | Flipkart model search returns 2,135 irrelevant results | §3.2 | CONFIRMED |
| F3 | Reliance Digital PDP is an empty SPA shell | §5.1 | CONFIRMED |
| F4 | Croma fully inaccessible (403 ×4) | §2.1 | NOT CONFIRMED |
| F5 | Amazon fully inaccessible (503/500) | §4 | NOT CONFIRMED |
| F6 | Vijay Sales fully accessible and rich | §6 | CONFIRMED |
| F7 | "Store Display Unit" listing condition | §6.4 | CONFIRMED |
| F8 | "Lowest Price for You" + additive fee | §3.5 | CONFIRMED |
| F9 | Never-strip invariant validated by real data | §10.3 | CONFIRMED |
| F10 | Vijay Sales URL-ID / page-SKU mismatch | §6.3 | PARTIALLY CONFIRMED |
| F11 | Croma numeric category IDs | §2.3 | CONFIRMED |
| F12 | Material description decode validated | §10.5 | CONFIRMED |

---

**END OF M0 FINDINGS**

*M0 is investigation only. No production scraper, matching engine, ranking engine, Excel integration, or EUP logic was built. No LLM was used for any product decision. M1 has not been started.*
