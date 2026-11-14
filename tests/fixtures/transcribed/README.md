# Transcribed fixtures — NOT captures

**These files are weaker evidence than a capture, and must never be presented as one.**

## What these are

Product listings **transcribed verbatim from M0 recon findings**. Titles, model
numbers, specification rows and prices are quoted from real competitor pages that
were fetched live on 2026-08-13 — but the HTML itself was not preserved, so what
survives is the recon document's quotation of it, not the page.

Every record carries a `provenance` block citing the exact M0 section it came from.

## What these are not

They are **not** content-addressed capture artifacts. Compare with
`tests/fixtures/croma/pdp_323360/`, which is a real `CaptureStore` — 707,724 bytes of
HTML whose SHA-256 and `capture_id` are re-derived on every read. Nothing here is
verifiable that way, because there is nothing to verify against.

## What they are sufficient for

Testing **comparison logic**: does a model code comparison respect the never-strip
invariant, does a Store Display Unit get rejected, does a compound spec row reduce to
the same canonical values as a pre-split one. The data is real; only its packaging is
second-hand.

## What they are NOT sufficient for

Testing **extraction**. No claim about where a field lives on a page, how it is
labelled, or whether it is present in the initial payload can be validated here.
That is exactly what M3b exists to establish.

## Retirement

Each record is replaced by a real capture as its platform passes its M3b gate. When
`platform` has a committed capture fixture, the transcribed record for it should be
deleted, not kept as a duplicate.

| Platform | M3b gate | Transcribed records | Status |
|---|---|---|---|
| Flipkart | M3b-1 | 1 | awaiting gate |
| Vijay Sales | M3b-2 | 3 | awaiting gate |
| Reliance Digital | M3b-3 | 2 | awaiting gate |
| Amazon | M3b-4 | 0 | no data obtained in M0 |
