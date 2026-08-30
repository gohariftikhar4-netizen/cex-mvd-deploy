"""Match Engine v1 — the production matching engine, hardened on real data.

Provenance: this starts as a faithful copy of the frozen Benchmark V2 arm
`forja/workflows/b2_production.py`, which won the V2 comparison. That file
stays FROZEN so the V2 benchmark remains reproducible; all real-world
hardening happens here instead.

Status: B2 is the PROVISIONAL production baseline pending real-world
hardening and independent human validation. MATCHING EDGE: NOT CONFIRMED.
"""

from .engine import run_match_engine

__all__ = ["run_match_engine"]
