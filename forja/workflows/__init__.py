"""Benchmark V2 competing workflows.

B0 — frontier LLM baseline (profile + jobs, structured output)
B1 — strong LLM baseline (B0 + constraint checklist + self-critique + verification pass)
B2 — competent production baseline (deterministic filter + retrieval + LLM rerank
     + structured evidence + simple verification)
B3 — Forja (deterministic constraint spine, retrieval, structured matching,
     evidence, gap analysis, soft-preference stage, final constraint gate)

All arms use the same model client and the same normalized output contract.

LEAKAGE RULE (tested): nothing in this package may import gold labels, the
corpus manifest, or anything under forja.bench that touches them.
"""

from .b0_frontier import run_b0
from .b1_strong import run_b1
from .b2_production import run_b2
from .b3_forja import run_b3

WORKFLOWS = {
    "b0": run_b0,
    "b1": run_b1,
    "b2": run_b2,
    "b3": run_b3,
}
