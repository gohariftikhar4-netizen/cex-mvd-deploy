"""B0 — frontier LLM baseline.

Claude Opus 5, candidate profile + available jobs, structured output. Nothing
else: no checklist, no critique, no deterministic machinery. The corpus does
not fit one context window at benchmark scale, so the natural operationalization
is map-reduce: rank each chunk, then rank the union of chunk finalists.
"""

from __future__ import annotations

import time

from ..llm import ModelClient
from ..runlog import RunLogger
from ..schemas import Candidate, Job
from .common import (
    CHUNK_KEEP, CLAIM_INSTRUCTION, FINALISTS, RANK_SCHEMA, build_output,
    chunk_jobs, jobs_prompt, jobs_prompt_parts, normalize_items,
)

_SYSTEM = (
    "Du er en erfaren norsk karriereveileder. Du får en kandidatprofil og en "
    "liste stillingsannonser. Velg og ranger stillingene som er verdt å søke "
    "på for kandidaten, med de beste først. Respekter kandidatens absolutte "
    "krav. " + CLAIM_INSTRUCTION
)

_FINAL_INSTRUCTION = (
    "Gi den ENDELIGE rangeringen for kandidaten over: inntil 50 stillinger, "
    "best først, med score 0–100 og belagte påstander. Ta bare med stillinger "
    "som faktisk er verdt å søke på."
)


def rank_stage(workflow: str, candidate: Candidate, jobs: list[Job],
               client: ModelClient, extra_context: str = "") -> list[dict]:
    """Shared B0/B1 ranking. With a 1M-token window the whole slice usually
    fits one call (jobs prefix prompt-cached across candidates); larger
    corpora fall back to map-reduce with per-chunk cached prefixes."""
    known = {j.id for j in jobs}
    jobs_by_id = {j.id: j for j in jobs}
    tags = {"workflow": workflow, "candidate_id": candidate.id}
    chunks = chunk_jobs(jobs)

    if len(chunks) == 1:
        prefix, suffix = jobs_prompt_parts(candidate, chunks[0],
                                           extra=extra_context,
                                           instruction=_FINAL_INSTRUCTION)
        result = client.complete(task="v2.merge", system=_SYSTEM, user=suffix,
                                 cached_prefix=prefix, json_schema=RANK_SCHEMA,
                                 max_tokens=16000, tags=tags)
        ranked = normalize_items(result.parsed_json, known)
        ranked.sort(key=lambda x: (-x["score"], x["job_id"]))
        return ranked

    collected: list[dict] = []
    for chunk in chunks:
        prefix, suffix = jobs_prompt_parts(
            candidate, chunk, extra=extra_context,
            instruction=(f"Velg de inntil {CHUNK_KEEP} beste stillingene fra "
                         "listen over for denne kandidaten. Sett score 0–100."))
        result = client.complete(task="v2.rank_chunk", system=_SYSTEM, user=suffix,
                                 cached_prefix=prefix, json_schema=RANK_SCHEMA,
                                 max_tokens=8000, tags=tags)
        items = normalize_items(result.parsed_json, {j.id for j in chunk})
        items.sort(key=lambda x: (-x["score"], x["job_id"]))
        collected.extend(items[:CHUNK_KEEP])

    collected.sort(key=lambda x: (-x["score"], x["job_id"]))
    finalists = collected[:FINALISTS]
    if not finalists:
        return []

    finalist_jobs = [jobs_by_id[i["job_id"]] for i in finalists]
    prompt = jobs_prompt(candidate, finalist_jobs, extra=extra_context) + (
        "\n\nDette er finalistene fra en grovsortering. " + _FINAL_INSTRUCTION
    )
    result = client.complete(task="v2.merge", system=_SYSTEM, user=prompt,
                             json_schema=RANK_SCHEMA, max_tokens=16000, tags=tags)
    ranked = normalize_items(result.parsed_json, known)
    ranked.sort(key=lambda x: (-x["score"], x["job_id"]))
    return ranked


def run_b0(candidate: Candidate, jobs: list[Job], logger: RunLogger,
           client: ModelClient) -> dict:
    start = time.perf_counter()
    ranked = rank_stage("b0", candidate, jobs, client)
    logger.log_decision("b0.final", candidate_id=candidate.id,
                        ranked=[i["job_id"] for i in ranked[:50]])
    return build_output("b0", candidate, ranked, time.perf_counter() - start)
