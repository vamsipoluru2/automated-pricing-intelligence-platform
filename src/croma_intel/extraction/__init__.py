"""Extraction layer — pure functions over immutable captures.

Architectural rule 1: nothing in this package may touch the network, import
Playwright, or perform I/O. Given the same capture, extraction must produce the same
result forever, which is what makes the replay guarantee testable.
"""
