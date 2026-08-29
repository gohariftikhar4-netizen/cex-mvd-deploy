"""B3 — Forja: the full pipeline (deterministic constraint spine, retrieval,
structured matching, evidence, gap analysis, bounded soft-preference stage,
final constraint gate), adapted to the shared V2 output contract.

Deterministic evidence is mapped into the same claim format the LLM arms use
(claim + source + verbatim quote), so the unsupported-evidence metric treats
every arm identically."""

from __future__ import annotations

import time

from ..llm import ModelClient
from ..pipeline import run_forja
from ..runlog import RunLogger
from ..schemas import Candidate, Job
from .common import MAX_EXTENDED, build_output


def _evidence_to_claims(evidence: list[dict], job: Job) -> list[dict]:
    """Map deterministic EvidenceItems to {claim, source, quote} with quotes
    that appear verbatim in the canonical record renders."""
    claims: list[dict] = []
    for e in evidence:
        etype = e.get("type")
        if etype in ("skill_match", "transferable_skill"):
            claims.append({"claim": e["claim"], "source": "job", "quote": e["job_value"]})
        elif etype == "experience":
            # Only meaningful (and quotable) when the job actually asks for
            # experience; the render prints "minst {n} års erfaring" then.
            n = job.requirements.min_years_experience
            if n:
                claims.append({"claim": e["claim"], "source": "job",
                               "quote": f"minst {n:g} års erfaring"})
        elif etype == "location":
            claims.append({"claim": e["claim"], "source": "job", "quote": job.location_city})
        elif etype == "sector":
            claims.append({"claim": e["claim"], "source": "job", "quote": job.sector})
        elif etype == "salary":
            quote = (str(job.salary_nok_max) if job.salary_nok_max is not None
                     else "ikke oppgitt")
            claims.append({"claim": e["claim"], "source": "job", "quote": quote})
        # retrieval similarity is internal bookkeeping, not an advisor-facing claim
    return claims[:6]


def run_b3(candidate: Candidate, jobs: list[Job], logger: RunLogger,
           client: ModelClient) -> dict:
    start = time.perf_counter()
    out = run_forja(candidate, jobs, logger, model_client=client,
                    extended_k=MAX_EXTENDED, use_soft_pref=True)
    jobs_by_id = {j.id: j for j in jobs}

    ranked = [
        {
            "job_id": rec["job_id"],
            "score": rec["score"],
            "claims": _evidence_to_claims(rec["evidence"], jobs_by_id[rec["job_id"]]),
            "gaps": rec["gaps"],
            "next_actions": rec["next_actions"],
            "constraint_report": rec["constraint_report"],
        }
        for rec in out["recommendations"]
    ]
    # Extend beyond the emitted top-10 with the ranked eligible tail.
    listed = {r["job_id"] for r in ranked}
    for job_id in out.get("extended", []):
        if job_id not in listed:
            ranked.append({"job_id": job_id, "score": 0.0, "claims": []})

    result = build_output("b3", candidate, ranked, time.perf_counter() - start,
                          notes={"profile_skills": out["profile_skills"]})
    # Preserve the full Forja recommendation payload for the review console.
    result["forja_detail"] = out["recommendations"]
    return result
