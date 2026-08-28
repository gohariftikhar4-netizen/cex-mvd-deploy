"""Model for estimated human review time — the economic core of the benchmark.

THIS IS A MODEL, NOT A MEASUREMENT. Parameters are stated assumptions about
how long a competent advisor needs before forwarding a recommendation to a
candidate. Every reported figure carries a low/high band (parameters halved /
doubled) so no conclusion can hide behind a point estimate. See BENCHMARK.md
for the justification and the sensitivity discussion; per EDGE.md §5, review
-time conclusions count only if they survive scrutiny of these assumptions.

Assumptions (per recommendation):
- VERIFIED: every claim carries a machine-checked pointer into the records
  and the hard-constraint pass was re-verified by the evaluator. The advisor
  spot-checks rather than re-derives.                       -> 0.75 min
- UNVERIFIED: free-text claim. The advisor must re-read the ad, cross-check
  the CV, and check every hard constraint by hand.          -> 4.0 min
- CONSTRAINT VIOLATION reaching the advisor: discovery, removal, and damage
  to trust in the remaining list (extra scrutiny).          -> +6.0 min
- HALLUCINATED item (job that does not exist / cannot be resolved): the
  advisor searches for a nonexistent posting before giving up. -> +8.0 min
"""

from __future__ import annotations

VERIFIED_REC_MIN = 0.75
UNVERIFIED_REC_MIN = 4.0
VIOLATION_PENALTY_MIN = 6.0
HALLUCINATION_PENALTY_MIN = 8.0

SENSITIVITY_LOW = 0.5
SENSITIVITY_HIGH = 2.0


def estimate(n_verified: int, n_unverified: int, n_violations: int,
             n_hallucinations: int) -> dict:
    def total(factor: float) -> float:
        return round(factor * (
            n_verified * VERIFIED_REC_MIN
            + n_unverified * UNVERIFIED_REC_MIN
            + n_violations * VIOLATION_PENALTY_MIN
            + n_hallucinations * HALLUCINATION_PENALTY_MIN
        ), 2)

    return {
        "minutes": total(1.0),
        "minutes_low": total(SENSITIVITY_LOW),
        "minutes_high": total(SENSITIVITY_HIGH),
        "inputs": {
            "verified_recs": n_verified,
            "unverified_recs": n_unverified,
            "violations": n_violations,
            "hallucinations": n_hallucinations,
        },
    }
