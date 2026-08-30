"""Match Engine v1 — the production matching engine, hardened on real data.

Provenance: starts from the frozen Benchmark V2 arm B2
(`forja/workflows/b2_production.py`), which won the V2 comparison and stays
frozen so that benchmark remains reproducible. All real-world hardening
lives here.

Shape (deliberately unchanged from B2 — simple, not an agent pipeline):

    deterministic structured filter   (only where NAV data is reliable)
  → retrieval shortlist
  → ONE LLM rerank that reads the RAW ad text and returns a machine-readable
    hard-constraint verdict per job
  → DETERMINISTIC enforcement of that verdict + evidence verification

P1 hardening: `hard_constraint_conflict` is an explicit output field. When it
is true the job is REJECTED before ranking is applied. No score, rank or
preference can override it, and a missing verdict is treated as UNVERIFIED
rather than silently assumed safe.
"""

from __future__ import annotations

import time

from ..baseline import render_candidate
from ..llm import ModelClient
from ..pipeline import constraints, retrieval
from ..runlog import RunLogger
from ..schemas import Candidate, Job
from ..workflows.common import (
    CLAIM_INSTRUCTION, FINALISTS, build_output, claim_supported, jobs_prompt,
)
from .schema import (CONSTRAINT_INSTRUCTION, CONSTRAINT_DIMENSIONS,
                     DETERMINISTIC_DIMENSIONS, RANK_SCHEMA_V1)

VERDICT_CLEAR = "clear"
VERDICT_CONFLICT = "conflict"
VERDICT_UNVERIFIED = "unverified"

_SYSTEM = (
    "Du er en karriereveileder. Du får en kandidatprofil og stillingsannonser "
    "med full annonsetekst. Ranger de beste stillingene for kandidaten, best "
    "først, med score 0–100.\n\n"
    + CONSTRAINT_INSTRUCTION + "\n\n" + CLAIM_INSTRUCTION
)


def _normalize(parsed: dict | None, known_ids: set[str]) -> list[dict]:
    """Sanitize the rerank payload, PRESERVING the constraint verdict.

    A missing/malformed verdict becomes UNVERIFIED — never silently 'clear'.
    """
    items: list[dict] = []
    seen: set[str] = set()
    for raw in (parsed or {}).get("items", []):
        job_id = raw.get("job_id")
        if job_id not in known_ids or job_id in seen:
            continue
        seen.add(job_id)
        flag = raw.get("hard_constraint_conflict")
        if flag is True:
            verdict = VERDICT_CONFLICT
        elif flag is False:
            verdict = VERDICT_CLEAR
        else:
            verdict = VERDICT_UNVERIFIED
        conflicts = [
            c for c in (raw.get("conflicts") or [])
            if isinstance(c, dict) and c.get("dimension") in CONSTRAINT_DIMENSIONS
        ]
        # Declared conflicts without any conflict detail are still conflicts.
        items.append({
            "job_id": job_id,
            "score": float(raw.get("score", 0.0) or 0.0),
            "constraint_verdict": verdict,
            "conflicts": conflicts,
            "claims": [c for c in (raw.get("claims") or [])
                       if isinstance(c, dict) and c.get("claim")],
        })
    return items


def _quote_in_ad(quote: str, job: Job) -> bool:
    """The conflict must prove the requirement EXISTS in this ad."""
    q = " ".join((quote or "").lower().split())
    if len(q) < 8:
        return False
    hay = " ".join(f"{job.title} {job.description}".lower().split())
    return q in hay


def substantiated_conflicts(item: dict, job: Job) -> tuple[list[dict], list[dict]]:
    """Split declared conflicts into substantiated vs unsubstantiated.

    A conflict counts only when (a) its dimension is one the model owns — the
    deterministic filter's dimensions are ignored — and (b) its quote appears
    verbatim in the ad text.
    """
    good, bad = [], []
    for c in item.get("conflicts", []):
        dim = c.get("dimension")
        if dim in DETERMINISTIC_DIMENSIONS or dim not in CONSTRAINT_DIMENSIONS:
            bad.append({**c, "rejected_because": "dimension owned by the deterministic filter"})
        elif not _quote_in_ad(c.get("quote", ""), job):
            bad.append({**c, "rejected_because": "quote not found verbatim in the ad"})
        else:
            good.append(c)
    return good, bad


def enforce_constraint_verdicts(items: list[dict], jobs_by_id: dict[str, Job],
                                candidate_id: str,
                                logger: RunLogger) -> tuple[list[dict], list[dict]]:
    """DETERMINISTIC gate: a SUBSTANTIATED conflict can never be recommended.

    Returns (kept, rejected). Score and rank are never consulted — this runs
    before ordering and is not overridable downstream. A declared conflict
    whose evidence does not hold up is NOT a rejection (that over-rejection
    collapsed real-world recommendations to 0-2 per candidate); it is logged
    for review instead.
    """
    kept, rejected = [], []
    for it in items:
        job = jobs_by_id.get(it["job_id"])
        if it["constraint_verdict"] != VERDICT_CONFLICT or job is None:
            kept.append(it)
            continue
        good, bad = substantiated_conflicts(it, job)
        if bad:
            logger.log_decision(
                "me1.unverified_conflict", candidate_id=candidate_id,
                job_id=it["job_id"],
                discarded=[{"dimension": c.get("dimension"),
                            "quote": (c.get("quote") or "")[:160],
                            "why": c["rejected_because"]} for c in bad])
        if good:
            rejected.append({**it, "conflicts": good})
            logger.log_decision(
                "me1.hard_constraint_reject",
                candidate_id=candidate_id, job_id=it["job_id"],
                score=it["score"],
                dimensions=sorted({c["dimension"] for c in good}),
                evidence=[{"dimension": c["dimension"], "quote": c.get("quote", "")}
                          for c in good])
        else:
            # Conflict declared but nothing substantiated it -> keep, flagged.
            kept.append({**it, "constraint_verdict": VERDICT_UNVERIFIED})
    return kept, rejected


def run_match_engine(candidate: Candidate, jobs: list[Job], logger: RunLogger,
                     client: ModelClient) -> dict:
    start = time.perf_counter()
    tags = {"workflow": "match_engine_v1", "candidate_id": candidate.id}

    # 1. Deterministic structured filter — only the dimensions real data
    #    actually supports (on NAV: location, extent, engagement type, deadline).
    eligible, _reports = constraints.filter_eligible(candidate, jobs)
    logger.log_decision("me1.filtered", candidate_id=candidate.id,
                        total=len(jobs), eligible=len(eligible))

    # 2. Retrieval shortlist.
    index = retrieval.build_index(eligible)
    shortlist_ids = [job_id for job_id, _ in
                     retrieval.retrieve(index, render_candidate(candidate), FINALISTS)]
    jobs_by_id = {j.id: j for j in eligible}
    shortlist = [jobs_by_id[i] for i in shortlist_ids]

    # 3. ONE rerank over the RAW ad text, returning a constraint verdict.
    ranked: list[dict] = []
    rejected: list[dict] = []
    if shortlist:
        prompt = jobs_prompt(candidate, shortlist) + (
            "\n\nVurder ALLE stillingene over. Returner inntil 50 stillinger "
            "rangert best først, hver med hard_constraint_conflict-vurdering."
        )
        result = client.complete(task="me1.rerank", system=_SYSTEM, user=prompt,
                                 json_schema=RANK_SCHEMA_V1, max_tokens=16000,
                                 tags=tags)
        items = _normalize(result.parsed_json, set(shortlist_ids))

        # 4a. HARD GATE — before any ordering. Score cannot override.
        items, rejected = enforce_constraint_verdicts(
            items, jobs_by_id, candidate.id, logger)
        items.sort(key=lambda x: (-x["score"], x["job_id"]))

        # 4b. Deterministic re-check of the structured constraints, and
        #     evidence verification (quote must exist in the cited record).
        for it in items:
            job = jobs_by_id[it["job_id"]]
            if not constraints.check(candidate, job).passed:
                logger.log_decision("me1.structured_recheck_reject",
                                    candidate_id=candidate.id, job_id=job.id)
                continue
            kept_claims = [c for c in it["claims"]
                           if claim_supported(c, candidate, job)]
            dropped = len(it["claims"]) - len(kept_claims)
            if dropped:
                logger.log_decision("me1.dropped_unsupported_claims",
                                    candidate_id=candidate.id, job_id=job.id,
                                    dropped=dropped)
            ranked.append({**it, "claims": kept_claims})

    logger.log_decision("me1.final", candidate_id=candidate.id,
                        ranked=[i["job_id"] for i in ranked[:50]],
                        rejected_hard_conflict=[i["job_id"] for i in rejected])
    out = build_output("match_engine_v1", candidate, ranked,
                       time.perf_counter() - start,
                       notes={"rejected_hard_conflict": [
                           {"job_id": i["job_id"], "score": i["score"],
                            "dimensions": sorted({c["dimension"] for c in i["conflicts"]})}
                           for i in rejected]})
    # Preserve the verdict on each emitted recommendation for auditability.
    by_id = {i["job_id"]: i for i in ranked}
    for rec in out["recommendations"]:
        src = by_id.get(rec["job_id"], {})
        rec["constraint_verdict"] = src.get("constraint_verdict", VERDICT_UNVERIFIED)
        rec["conflicts"] = src.get("conflicts", [])
    return out
