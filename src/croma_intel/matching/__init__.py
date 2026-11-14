"""Matching — gates, specification comparison, and the decision table.

Pure and offline. Nothing here touches the network, imports a browser, or consults
an LLM. Given the same two candidate records it returns the same verdict forever,
which is what makes matching auditable rather than merely plausible.

Order of operations is fixed by architectural rule 5: gates run to completion before
any identity comparison, and scoring happens last and only within a classification
already decided by the table.
"""
