"""The Forja workflow: profile → hard filter → retrieve → match → gaps → recommend.

Stage order is a guarantee, not an implementation detail: hard-constraint
filtering runs BEFORE any ranking, and the final gate re-checks constraints
AFTER ranking, so no LLM-influenced step can introduce an ineligible job.
"""

from __future__ import annotations

import time

from ..llm import ModelClient
from ..runlog import RunLogger
from ..schemas import Candidate, Job
from . import constraints, retrieval
from .matching import score_job
from .profiling import build_profile
from .recommend import assemble

RETRIEVAL_SHORTLIST_K = 30


def run_forja(
    candidate: Candidate,
    jobs: list[Job],
    logger: RunLogger,
    model_client: ModelClient | None = None,
) -> dict:
    """Run the full Forja workflow for one candidate. Returns the normalized
    workflow output consumed by the evaluator."""
    start = time.perf_counter()
    jobs_by_id = {j.id: j for j in jobs}

    # 1. Candidate profiling (optionally LLM-enriched; validated deterministically).
    profile = build_profile(candidate, logger, model_client)

    # 2. Hard constraint filtering — before anything ranks or reasons.
    eligible, reports = constraints.filter_eligible(candidate, jobs)
    for report in reports:
        if not report.passed:
            logger.log_decision(
                "constraints.excluded",
                candidate_id=candidate.id,
                job_id=report.job_id,
                violations=[v.to_dict() for v in report.violations],
            )
    logger.log_decision(
        "constraints.summary",
        candidate_id=candidate.id,
        total_jobs=len(jobs),
        eligible=len(eligible),
        excluded=len(jobs) - len(eligible),
    )

    # 3. Semantic retrieval over eligible jobs only.
    index = retrieval.build_index(eligible)
    shortlist = retrieval.retrieve(index, profile.query_text, RETRIEVAL_SHORTLIST_K)
    logger.log_decision(
        "retrieval.shortlist",
        candidate_id=candidate.id,
        shortlist=[{"job_id": j, "score": s} for j, s in shortlist],
    )

    # 4. Structured matching on the shortlist.
    max_retrieval = max((s for _, s in shortlist), default=0.0)
    matches = [
        score_job(profile, jobs_by_id[job_id], score, max_retrieval)
        for job_id, score in shortlist
    ]
    for m in matches:
        logger.log_decision("matching.scored", candidate_id=candidate.id, **m.to_dict())

    # 5 + 6. Gap analysis + ranked recommendations with evidence and next
    # actions (assemble runs the final constraint gate).
    recommendations = assemble(profile, matches, jobs_by_id, logger)

    wall_time = time.perf_counter() - start
    return {
        "workflow": "forja",
        "candidate_id": candidate.id,
        "recommendations": [r.to_dict() for r in recommendations],
        "profile_skills": profile.effective_skills,
        "wall_time_s": round(wall_time, 4),
    }
