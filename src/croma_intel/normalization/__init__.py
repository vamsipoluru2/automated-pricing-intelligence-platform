"""Normalization — safe, evidence-backed transformations of extracted values.

Every transformation in this package is either proven safe against real data in
M0 §10.4 / §9, or absent. The invariant that governs the whole package:

    **Never remove a character from inside an alphanumeric run.**

Stdlib only. Pure. No I/O, no network.
"""
