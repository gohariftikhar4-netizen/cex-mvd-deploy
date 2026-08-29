"""B1 — strong LLM baseline.

B0 plus everything a careful prompt engineer would add: an explicit
hard-constraint checklist in every call, a self-critique pass over the draft
ranking, and a second-pass verification that can strike items. This is the
realistic zero-build competitor at its best.
"""

from __future__ import annotations

import time

from ..llm import ModelClient
from ..runlog import RunLogger
from ..schemas import Candidate, Job
from .b0_frontier import rank_stage
from .common import (
    RANK_SCHEMA, VERDICT_SCHEMA, build_output, constraint_checklist,
    jobs_prompt, normalize_items,
)

_CRITIQUE_SYSTEM = (
    "Du er en kritisk kvalitetskontrollør for karriereanbefalinger. Du får en "
    "kandidat, en sjekkliste med absolutte krav, stillingsannonsene og et "
    "utkast til rangering. Finn feil: brutte krav, svak begrunnelse, bedre "
    "rekkefølge. Returner en KOMPLETT revidert rangering (samme format). "
    "Returner tom liste hvis utkastet er korrekt."
)

_VERIFY_SYSTEM = (
    "Du er en kontrollør. For hver stilling i listen: gå gjennom sjekklisten "
    "punkt for punkt mot annonsen (også detaljer som bare står i teksten). "
    "Sett keep=false for enhver stilling som bryter ett eneste absolutt krav, "
    "med en kort begrunnelse."
)


def run_b1(candidate: Candidate, jobs: list[Job], logger: RunLogger,
           client: ModelClient) -> dict:
    start = time.perf_counter()
    checklist = constraint_checklist(candidate)
    jobs_by_id = {j.id: j for j in jobs}
    tags = {"workflow": "b1", "candidate_id": candidate.id}

    ranked = rank_stage("b1", candidate, jobs, client, extra_context=checklist)

    # Self-critique over the draft (finalist jobs re-rendered for grounding).
    if ranked:
        draft_ids = [i["job_id"] for i in ranked[:50]]
        draft_jobs = [jobs_by_id[j] for j in draft_ids]
        draft_text = "\n".join(
            f"{n}. {i['job_id']} (score {i['score']})"
            for n, i in enumerate(ranked[:50], start=1))
        prompt = (jobs_prompt(candidate, draft_jobs, extra=checklist)
                  + "\n\nUTKAST TIL RANGERING:\n" + draft_text
                  + "\n\nReturner den reviderte, komplette rangeringen "
                    "(eller tom liste hvis utkastet står seg).")
        result = client.complete(task="v2.critique", system=_CRITIQUE_SYSTEM,
                                 user=prompt, json_schema=RANK_SCHEMA,
                                 max_tokens=12000, tags=tags)
        revised = normalize_items(result.parsed_json, set(draft_ids))
        if revised:
            revised.sort(key=lambda x: (-x["score"], x["job_id"]))
            logger.log_decision("b1.critique_revised", candidate_id=candidate.id,
                                before=draft_ids, after=[i["job_id"] for i in revised])
            ranked = revised

    # Second-pass verification: strike anything that breaks the checklist.
    struck: list[dict] = []
    if ranked:
        check_ids = [i["job_id"] for i in ranked[:50]]
        check_jobs = [jobs_by_id[j] for j in check_ids]
        prompt = (jobs_prompt(candidate, check_jobs, extra=checklist)
                  + "\n\nGi en verdict (keep true/false) for hver av stillingene over.")
        result = client.complete(task="v2.verify", system=_VERIFY_SYSTEM,
                                 user=prompt, json_schema=VERDICT_SCHEMA,
                                 max_tokens=8000, tags=tags)
        verdicts = {v.get("job_id"): v for v in (result.parsed_json or {}).get("verdicts", [])}
        kept = []
        for item in ranked:
            v = verdicts.get(item["job_id"])
            if v is not None and v.get("keep") is False:
                struck.append({"job_id": item["job_id"], "reason": v.get("reason", "")})
            else:
                kept.append(item)
        ranked = kept

    logger.log_decision("b1.final", candidate_id=candidate.id,
                        ranked=[i["job_id"] for i in ranked[:50]], struck=struck)
    return build_output("b1", candidate, ranked, time.perf_counter() - start,
                        notes={"struck_by_verification": struck})
