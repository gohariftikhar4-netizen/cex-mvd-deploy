"""B2 — competent production baseline.

What a competent AI engineer builds in a sprint: deterministic hard-constraint
filtering over the structured fields, lexical/semantic retrieval, one LLM
rerank call with structured evidence, and simple deterministic verification
(constraint recheck + quote check on claims). Shares the constraint engine and
retrieval index with B3 by design, so the B2-vs-B3 comparison isolates Forja's
additional machinery rather than component quality.
"""

from __future__ import annotations

import time

from ..baseline import render_candidate
from ..llm import ModelClient
from ..pipeline import constraints, retrieval
from ..runlog import RunLogger
from ..schemas import Candidate, Job
from .common import (
    CLAIM_INSTRUCTION, FINALISTS, RANK_SCHEMA, build_output, claim_supported,
    jobs_prompt, normalize_items,
)

_SYSTEM = (
    "Du er en karriereveileder. Stillingene du får er allerede filtrert mot "
    "kandidatens absolutte krav (så langt strukturert data rekker). Ranger de "
    "beste stillingene for kandidaten, best først, med score 0–100. Vær også "
    "oppmerksom på krav som bare fremgår av annonseteksten. " + CLAIM_INSTRUCTION
)


def run_b2(candidate: Candidate, jobs: list[Job], logger: RunLogger,
           client: ModelClient) -> dict:
    start = time.perf_counter()
    tags = {"workflow": "b2", "candidate_id": candidate.id}

    # 1. Deterministic hard-constraint filter (structured view).
    eligible, reports = constraints.filter_eligible(candidate, jobs)
    logger.log_decision("b2.filtered", candidate_id=candidate.id,
                        total=len(jobs), eligible=len(eligible))

    # 2. Retrieval shortlist.
    index = retrieval.build_index(eligible)
    shortlist_ids = [job_id for job_id, _ in
                     retrieval.retrieve(index, render_candidate(candidate), FINALISTS)]
    jobs_by_id = {j.id: j for j in eligible}
    shortlist = [jobs_by_id[i] for i in shortlist_ids]

    # 3. One LLM rerank with structured evidence.
    ranked: list[dict] = []
    if shortlist:
        prompt = jobs_prompt(candidate, shortlist) + (
            "\n\nRanger inntil 50 av stillingene over for kandidaten."
        )
        result = client.complete(task="v2.rerank", system=_SYSTEM, user=prompt,
                                 json_schema=RANK_SCHEMA, max_tokens=12000, tags=tags)
        ranked = normalize_items(result.parsed_json, set(shortlist_ids))
        ranked.sort(key=lambda x: (-x["score"], x["job_id"]))

    # 4. Simple verification: recheck constraints; drop unsupported claims.
    verified: list[dict] = []
    dropped_claims = 0
    for item in ranked:
        job = jobs_by_id[item["job_id"]]
        if not constraints.check(candidate, job).passed:
            logger.log_decision("b2.verification_dropped_item",
                                candidate_id=candidate.id, job_id=job.id)
            continue
        kept_claims = [c for c in item["claims"] if claim_supported(c, candidate, job)]
        dropped_claims += len(item["claims"]) - len(kept_claims)
        verified.append({**item, "claims": kept_claims})

    # Pad the extended list with the rest of the retrieval order (recall).
    listed = {i["job_id"] for i in verified}
    for job_id in shortlist_ids:
        if len(verified) >= 50:
            break
        if job_id not in listed:
            verified.append({"job_id": job_id, "score": 0.0, "claims": []})

    logger.log_decision("b2.final", candidate_id=candidate.id,
                        ranked=[i["job_id"] for i in verified[:50]],
                        dropped_claims=dropped_claims)
    return build_output("b2", candidate, verified, time.perf_counter() - start,
                        notes={"dropped_unsupported_claims": dropped_claims})
