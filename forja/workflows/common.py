"""Shared machinery for the V2 workflow arms: prompt rendering, structured
output schemas, chunking, claim verification, and the normalized output
contract. No gold labels, no manifest — ever."""

from __future__ import annotations

import re

from ..baseline import render_candidate, render_job
from ..llm import CANDIDATE_SECTION, JOBS_SECTION, ModelClient
from ..schemas import Candidate, Job

MAX_TOP = 10
MAX_EXTENDED = 50
# claude-opus-5 has a 1M-token context window: a benchmark slice of a few
# thousand ads fits in ONE call, which is the purest reading of the B0 spec
# ("candidate profile + available jobs"). Map-reduce only kicks in beyond it.
CHUNK_CHAR_BUDGET = 2_600_000  # ≈ 650k tokens of rendered job blocks per call
CHUNK_KEEP = 15                # candidates kept per chunk (map-reduce mode)
FINALISTS = 60                 # merged shortlist size before the final ranking

_CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "claim": {"type": "string"},
        "source": {"type": "string", "enum": ["candidate", "job"]},
        "quote": {"type": "string"},
    },
    "required": ["claim", "source", "quote"],
    "additionalProperties": False,
}

RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "score": {"type": "number"},
                    "claims": {"type": "array", "items": _CLAIM_SCHEMA},
                },
                "required": ["job_id", "score", "claims"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "keep": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["job_id", "keep", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}

PREF_SCHEMA = {
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

CLAIM_INSTRUCTION = (
    "For hver anbefalt stilling: oppgi 1–3 korte påstander (claims) som "
    "begrunner den. Hver påstand MÅ ha et ordrett sitat (quote) fra enten "
    "kandidatprofilen (source=candidate) eller stillingsannonsen (source=job). "
    "Ikke skriv påstander du ikke kan sitere belegg for."
)


def jobs_prompt(candidate: Candidate, jobs: list[Job], extra: str = "") -> str:
    parts = []
    if extra:
        parts.append(extra)
    parts += [CANDIDATE_SECTION, render_candidate(candidate), "", JOBS_SECTION]
    parts += [render_job(j) for j in jobs]
    return "\n\n".join(parts)


def jobs_prompt_parts(candidate: Candidate, jobs: list[Job], extra: str = "",
                      instruction: str = "") -> tuple[str, str]:
    """(stable_prefix, volatile_suffix) with the jobs corpus FIRST so the
    prefix is identical across candidates and can be prompt-cached. The
    candidate profile and any per-candidate context go in the suffix (long-
    context best practice also favors query-after-documents)."""
    prefix = "\n\n".join([JOBS_SECTION] + [render_job(j) for j in jobs])
    suffix_parts = []
    if extra:
        suffix_parts.append(extra)
    suffix_parts += [CANDIDATE_SECTION, render_candidate(candidate)]
    if instruction:
        suffix_parts.append(instruction)
    return prefix, "\n\n".join(suffix_parts)


def chunk_jobs(jobs: list[Job], char_budget: int = CHUNK_CHAR_BUDGET) -> list[list[Job]]:
    chunks: list[list[Job]] = []
    current: list[Job] = []
    used = 0
    for job in jobs:
        size = len(render_job(job)) + 2
        if current and used + size > char_budget:
            chunks.append(current)
            current, used = [], 0
        current.append(job)
        used += size
    if current:
        chunks.append(current)
    return chunks


def constraint_checklist(candidate: Candidate) -> str:
    """Explicit hard-constraint checklist (B1). States exactly what must be
    verified per job — the strongest realistic prompt-side defense."""
    hc = candidate.hard_constraints
    lines = [
        "SJEKKLISTE FOR ABSOLUTTE KRAV — kontroller HVERT punkt for HVER stilling.",
        "En stilling som bryter ett eneste punkt skal IKKE anbefales:",
        f"1. Pendletid maks {hc.max_commute_minutes} min fra {candidate.location_city}"
        + (" (kan flytte: " + (", ".join(hc.relocation_counties) or "hvor som helst") + ")"
           if hc.willing_to_relocate else " — kandidaten kan IKKE flytte")
        + ". Remote-stillinger er alltid OK geografisk.",
        f"2. Kan IKKE jobbe skift: {', '.join(hc.cannot_work_shifts) or '(ingen begrensning)'}. "
        "Sjekk også skiftinformasjon som bare står i annonseteksten.",
        f"3. Fysiske begrensninger: {', '.join(hc.physical_limitations) or '(ingen)'}.",
        f"4. Nedre lønnsgrense (100 %-ekvivalent): {hc.min_salary_nok or '(ingen)'} — "
        "oppgitt lønn under grensen diskvalifiserer; uoppgitt lønn er OK men skal noteres.",
        f"5. Stillingsprosent må være {hc.percent_min}–{hc.percent_max} %.",
        f"6. Overnattingsreiser: {'IKKE mulig' if hc.no_overnight_travel else 'OK'}.",
        "7. Førerkort: kandidaten har "
        + (", ".join(candidate.driving_licenses) or "ingen førerkort")
        + " — stillinger som krever andre klasser diskvalifiseres.",
        "8. Gyldige sertifiseringer/autorisasjoner: "
        + (", ".join(sorted(candidate.valid_certification_ids)) or "ingen")
        + ". Utløpte eller ventende autorisasjoner teller IKKE som gyldige.",
        "9. Språk: " + ", ".join(f"{k} {v}" for k, v in candidate.languages.items())
        + " — språkkrav over kandidatens nivå diskvalifiserer.",
        "10. Norsk statsborgerskap: " + ("ja" if candidate.is_citizen else "NEI — "
        "stillinger med krav om sikkerhetsklarering/statsborgerskap diskvalifiseres."),
        "11. Søknadsfrist må ikke ha utløpt (i dag er 2026-09-01).",
    ]
    return "\n".join(lines)


def normalize_items(parsed: dict | None, known_ids: set[str]) -> list[dict]:
    """Sanitize a RANK_SCHEMA response: drop unknown/duplicate job ids, coerce
    fields. Unknown ids are kept in the returned list's `dropped` companion via
    the second element."""
    items: list[dict] = []
    seen: set[str] = set()
    for raw in (parsed or {}).get("items", []):
        job_id = raw.get("job_id")
        if job_id not in known_ids or job_id in seen:
            continue
        seen.add(job_id)
        claims = [c for c in raw.get("claims", [])
                  if isinstance(c, dict) and c.get("claim")]
        items.append({"job_id": job_id,
                      "score": float(raw.get("score", 0.0)),
                      "claims": claims})
    return items


_WS = re.compile(r"\s+")


def _fold(text: str) -> str:
    return _WS.sub(" ", text.lower()).strip()


def claim_supported(claim: dict, candidate: Candidate, job: Job) -> bool:
    """A claim is supported iff its quote appears verbatim (case/whitespace
    folded) in the canonical render of the cited source record."""
    quote = _fold(claim.get("quote", ""))
    if len(quote) < 2:
        return False
    source = claim.get("source")
    if source == "candidate":
        haystack = _fold(render_candidate(candidate))
    elif source == "job":
        haystack = _fold(render_job(job))
    else:
        return False
    return quote in haystack


def build_output(workflow: str, candidate: Candidate, ranked: list[dict],
                 wall_time_s: float, notes: dict | None = None) -> dict:
    """Normalized output contract shared by all arms. `ranked` is the final
    ordering (best first) of {job_id, score, claims} dicts."""
    top = ranked[:MAX_TOP]
    return {
        "workflow": workflow,
        "candidate_id": candidate.id,
        "recommendations": [
            {"job_id": item["job_id"], "rank": i + 1,
             "score": round(item.get("score", 0.0), 4),
             "claims": item.get("claims", [])}
            for i, item in enumerate(top)
        ],
        "extended": [item["job_id"] for item in ranked[:MAX_EXTENDED]],
        "wall_time_s": round(wall_time_s, 4),
        "notes": notes or {},
    }
