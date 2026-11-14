"""Pipeline — orchestration and selection.

Sits above ``matching/``: it composes discovery, extraction, classification and
ranking into a run. Ranking lives here rather than in ``matching/`` so that the M3a
guard keeping ordering out of the classifier stays intact.
"""
