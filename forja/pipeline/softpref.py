"""Soft-preference stage (V2): LLM-scored preference fit, deterministically
bounded.

The deterministic matcher cannot read nuance like "vil ut av klasserommet"
or "sier 900', mener 700'". This stage lets the model score preference fit
for the ALREADY-ELIGIBLE shortlist, under strict rules:

- influence is capped: final = (1 - WEIGHT) * deterministic + WEIGHT * fit;
- a fit score is used ONLY if its supporting quote appears verbatim in the
  candidate's own free text / notes (LLM proposes, code disposes);
- eligibility is untouched — the final constraint gate still runs after this.
"""

from __future__ import annotations

import dataclasses

from ..llm import JOB_BLOCK_PREFIX, ModelClient
from ..runlog import RunLogger
from ..schemas import Job
from .matching import MatchResult
from .profiling import CandidateProfile

WEIGHT = 0.2
TOP_K = 15

_PREF_SCHEMA = {
    "type": "object",
    "properties": {
        "preferences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "fit": {"type": "number"},
                    "claim": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["job_id", "fit", "claim", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["preferences"],
    "additionalProperties": False,
}

_SYSTEM = (
    "Du vurderer hvor godt stillinger passer en kandidats UTTALTE ønsker og "
    "mål (ikke kvalifikasjoner — de er allerede vurdert). Gi fit 0.0–1.0 per "
    "stilling: 1.0 = midt i kandidatens uttalte retning, 0.0 = direkte i "
    "strid med uttalte ønsker. Begrunn hver score med et ORDRETT sitat fra "
    "kandidatens egen tekst. Ikke vurder harde krav — kun ønsker og retning."
)


def apply_soft_preferences(
    profile: CandidateProfile,
    matches: list[MatchResult],
    jobs_by_id: dict[str, Job],
    logger: RunLogger,
    client: ModelClient,
) -> list[MatchResult]:
    candidate = profile.candidate
    ranked = sorted(matches, key=lambda m: (-m.total_score, m.job_id))
    head, tail = ranked[:TOP_K], ranked[TOP_K:]
    if not head:
        return ranked

    own_text = candidate.free_text + "\n" + candidate.constraint_notes
    blocks = []
    for m in head:
        job = jobs_by_id[m.job_id]
        blocks.append(f"{JOB_BLOCK_PREFIX}{job.id}]\n{job.title} – {job.employer} "
                      f"({job.sector}, {job.location_city}, {job.work_mode})\n"
                      f"{job.description[:400]}")
    user = (
        "Kandidatens egen tekst:\n" + own_text
        + "\n\n=== KANDIDAT ===\n(se over)\n\n=== STILLINGSANNONSER ===\n"
        + "\n\n".join(blocks)
    )
    result = client.complete(task="v2.soft_pref", system=_SYSTEM, user=user,
                             json_schema=_PREF_SCHEMA, max_tokens=4000,
                             tags={"workflow": "b3", "candidate_id": candidate.id})

    folded_text = " ".join(own_text.lower().split())
    accepted: dict[str, dict] = {}
    rejected: list[dict] = []
    for pref in (result.parsed_json or {}).get("preferences", []):
        job_id = pref.get("job_id")
        fit = pref.get("fit")
        quote = " ".join(str(pref.get("quote", "")).lower().split())
        if job_id not in {m.job_id for m in head}:
            rejected.append({**pref, "rejected_because": "unknown job id"})
        elif not isinstance(fit, (int, float)) or not (0.0 <= fit <= 1.0):
            rejected.append({**pref, "rejected_because": "fit out of range"})
        elif quote and quote not in folded_text:
            rejected.append({**pref, "rejected_because": "quote not found in candidate text"})
        else:
            accepted[job_id] = {"fit": float(fit), "claim": pref.get("claim", ""),
                                "quote": pref.get("quote", "")}

    adjusted: list[MatchResult] = []
    for m in head:
        pref = accepted.get(m.job_id)
        if pref is None:
            adjusted.append(m)
            continue
        new_score = round((1 - WEIGHT) * m.total_score + WEIGHT * pref["fit"], 6)
        adjusted.append(dataclasses.replace(m, total_score=new_score))
    logger.log_decision(
        "softpref.applied",
        candidate_id=candidate.id,
        accepted={k: v["fit"] for k, v in sorted(accepted.items())},
        rejected=rejected,
        weight=WEIGHT,
    )
    result_list = adjusted + tail
    result_list.sort(key=lambda m: (-m.total_score, m.job_id))
    return result_list
