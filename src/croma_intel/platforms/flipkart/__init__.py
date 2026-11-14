"""Flipkart adapter — M3b-1.

Discovery, capture and extraction for Flipkart only. Everything here produces the
platform-neutral ``CandidateProduct``; no Flipkart-specific concept is permitted to
reach ``domain/``, ``normalization/`` or ``matching/``.

M0 F1/F2 shape this adapter: Flipkart publishes no manufacturer model number in its
titles, and searching for one returns a large, confidently wrong result set. The
adapter is therefore specification-first by design, not as a degraded fallback.
"""
