"""Baseline workflow: a competent advisor using a general-purpose LLM.

Operationalization: the advisor pastes the candidate's full profile and ALL
job ads into one chat prompt and asks for up to 10 ranked recommendations
with reasons, explicitly asking the model to respect the candidate's
constraints and to cite job IDs. The output is free text, parsed leniently —
exactly the artifact a human would have to verify by hand.

This is deliberately a strong, good-faith baseline (clear instructions,
complete information, ID citation requested), not a strawman. Its known
limitation — it models a single-shot prompt, not an iterative chat session —
is documented in BENCHMARK.md.
"""

from __future__ import annotations

import re
import time

from .llm import CANDIDATE_SECTION, JOB_BLOCK_PREFIX, JOBS_SECTION, ModelClient
from .runlog import RunLogger
from .schemas import Candidate, Job

_SYSTEM = (
    "Du er en erfaren norsk karriereveileder. Du hjelper en kandidat med å "
    "finne stillinger som er verdt å søke på. Vær konkret og ærlig."
)


def render_candidate(candidate: Candidate) -> str:
    hc = candidate.hard_constraints
    lines = [
        f"Navn: {candidate.name}",
        f"Bosted: {candidate.location_city}",
        f"Arbeidstillatelse: {candidate.work_authorization}",
        "Språk: " + ", ".join(f"{lang} ({lvl})" for lang, lvl in candidate.languages.items()),
        "Førerkort: " + (", ".join(candidate.driving_licenses) or "ingen"),
        "Sertifiseringer/autorisasjoner: " + (", ".join(
            c.id if c.status == "valid" else f"{c.id} [{c.status.upper()}]"
            for c in candidate.certifications
        ) or "ingen"),
        "Utdanning: " + "; ".join(
            f"{e.degree} i {e.field}, {e.institution} ({e.year})"
            + ("" if e.recognized_in_norway else " [IKKE godkjent i Norge ennå]")
            for e in candidate.education
        ),
        "Arbeidserfaring:",
    ]
    for w in candidate.work_history:
        lines.append(f"  - {w.title}, {w.employer} ({w.years} år): {w.description}")
    lines += [
        "Kompetanser: " + ", ".join(candidate.skills),
        "",
        "ABSOLUTTE KRAV (må aldri brytes):",
        f"  - Maks pendletid: {hc.max_commute_minutes} min én vei"
        + (f"; kan flytte til: {', '.join(hc.relocation_counties) or 'hvor som helst'}"
           if hc.willing_to_relocate else "; kan IKKE flytte"),
        f"  - Kan ikke jobbe skift: {', '.join(hc.cannot_work_shifts) or 'ingen begrensning'}",
        f"  - Fysiske begrensninger: {', '.join(hc.physical_limitations) or 'ingen'}",
        f"  - Nedre lønnsgrense (100 %): {hc.min_salary_nok or 'ingen'}",
        f"  - Stillingsprosent: {hc.percent_min}–{hc.percent_max} %",
        f"  - Overnattingsreiser: {'NEI' if hc.no_overnight_travel else 'ok'}",
        "",
        f"Ønskede sektorer: {', '.join(candidate.preferred_sectors) or 'ingen preferanse'}",
        f"Vil bort fra: {', '.join(candidate.avoided_sectors) or '—'}",
        "",
        "Kandidatens egen beskrivelse:",
        candidate.free_text,
    ]
    if candidate.constraint_notes:
        lines += ["", "Merknader om ønsker/begrensninger:", candidate.constraint_notes]
    return "\n".join(lines)


def render_job(job: Job) -> str:
    req = job.requirements
    lines = [
        f"{JOB_BLOCK_PREFIX}{job.id}]",
        f"Tittel: {job.title}",
        f"Arbeidsgiver: {job.employer} ({job.sector})",
        f"Sted: {job.location_city} — {job.work_mode}, {job.percent_position} % stilling",
        f"Skift: {', '.join(job.shifts)}"
        + ("; reisevirksomhet med overnatting" if job.requires_overnight_travel else ""),
        "Lønn: " + (f"{job.salary_nok_min}–{job.salary_nok_max} NOK"
                    if job.salary_nok_min else "ikke oppgitt"),
    ]
    if job.application_deadline:
        lines.append(f"Søknadsfrist: {job.application_deadline}")
    reqs = []
    if req.must_have_skills:
        reqs.append("krav: " + ", ".join(req.must_have_skills))
    if req.nice_to_have_skills:
        reqs.append("ønskelig: " + ", ".join(req.nice_to_have_skills))
    if req.min_years_experience:
        reqs.append(f"minst {req.min_years_experience:g} års erfaring")
    if req.certifications_required:
        reqs.append("påkrevd sertifisering: " + ", ".join(req.certifications_required))
    if req.driving_license_required:
        reqs.append(f"førerkort klasse {req.driving_license_required}")
    if req.norwegian_min_level:
        reqs.append(f"norsk minst {req.norwegian_min_level}")
    if req.english_min_level:
        reqs.append(f"engelsk minst {req.english_min_level}")
    if req.requires_norwegian_citizenship:
        reqs.append("krever norsk statsborgerskap (sikkerhetsklarering)")
    if req.physical_demands:
        reqs.append("fysiske krav: " + ", ".join(req.physical_demands))
    if reqs:
        lines.append("Kvalifikasjoner: " + "; ".join(reqs))
    lines.append(job.description)
    return "\n".join(lines)


def build_prompt(candidate: Candidate, jobs: list[Job]) -> str:
    parts = [
        "Under finner du en kandidatprofil og alle tilgjengelige stillingsannonser.",
        "Anbefal opptil 10 stillinger kandidaten bør søke på, rangert fra best til",
        "dårligst. Respekter kandidatens absolutte krav. For hver anbefaling:",
        "oppgi stillings-ID-en (f.eks. job_012) og en kort begrunnelse.",
        "Nummerer listen 1–10. Ta bare med stillinger som faktisk er verdt å søke på.",
        "",
        CANDIDATE_SECTION,
        render_candidate(candidate),
        "",
        JOBS_SECTION,
    ]
    parts += [render_job(j) for j in jobs]
    return "\n\n".join(parts)


# One numbered line, e.g. "3. job_017 – god match ..." or "3) Sykepleier (job_017): ..."
_LINE_RE = re.compile(r"^\s*(\d{1,2})[.)]\s*(.+)$")
_JOB_ID_RE = re.compile(r"\bjob_\d{3}\b")


def parse_response(text: str, jobs: list[Job]) -> list[dict]:
    """Extract ranked recommendations from advisor-style free text.

    Returns [{job_id: str | None, rank, reason_text, raw_line}]. job_id is the
    cited ID if it exists in the dataset; otherwise a fuzzy title match is
    attempted; otherwise None (an unresolvable — hallucinated — item).
    """
    known_ids = {j.id for j in jobs}
    titles = {j.id: j.title.lower() for j in jobs}
    out: list[dict] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        body = m.group(2).strip()
        ids = _JOB_ID_RE.findall(body)
        job_id: str | None = None
        for cand_id in ids:
            if cand_id in known_ids:
                job_id = cand_id
                break
        if job_id is None:
            low = body.lower()
            matches = [jid for jid, title in titles.items() if title and title in low]
            if len(matches) == 1:
                job_id = matches[0]
        if job_id is not None and job_id in seen:
            continue  # duplicate mention of an already-ranked job
        if job_id is not None:
            seen.add(job_id)
        out.append({
            "job_id": job_id,
            "rank": len(out) + 1,
            "reason_text": body,
            "raw_line": line.strip(),
        })
        if len(out) >= 10:
            break
    return out


def run_baseline(
    candidate: Candidate,
    jobs: list[Job],
    logger: RunLogger,
    model_client: ModelClient,
) -> dict:
    start = time.perf_counter()
    prompt = build_prompt(candidate, jobs)
    result = model_client.complete(
        task="baseline.advise",
        system=_SYSTEM,
        user=prompt,
        max_tokens=4000,
    )
    recommendations = parse_response(result.text, jobs)
    logger.log_decision(
        "baseline.parsed",
        candidate_id=candidate.id,
        recommendations=recommendations,
    )
    wall_time = time.perf_counter() - start
    return {
        "workflow": "baseline",
        "candidate_id": candidate.id,
        "recommendations": recommendations,
        "raw_response": result.text,
        "wall_time_s": round(wall_time, 4),
    }
