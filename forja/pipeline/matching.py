"""Structured matching: deterministic scoring with machine-checkable evidence.

Weights and thresholds below were fixed from domain reasoning BEFORE the
benchmark was first run, and per EDGE.md §6 must never be tuned against the
gold labels. Change them only with a written domain justification in the
commit message.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import taxonomy
from ..schemas import Job
from .profiling import CandidateProfile

# Component weights (sum to 1.0).
W_MUST_HAVE = 0.35
W_NICE_TO_HAVE = 0.10
W_EXPERIENCE = 0.10
W_LOCATION = 0.10
W_SECTOR = 0.10
W_SALARY = 0.10
W_RETRIEVAL = 0.15

# Recommendation bars (see recommend.py): a job below either bar is not worth
# a candidate's time even if it ranks in the top 10 by score.
MIN_MUST_HAVE_COVERAGE = 0.34
MIN_TOTAL_SCORE = 0.40


@dataclass(frozen=True)
class EvidenceItem:
    """One machine-checkable claim supporting a recommendation."""

    type: str            # skill_match | transferable_skill | experience | location | sector | salary | retrieval
    claim: str           # human-readable, shown to the advisor
    candidate_ref: str   # dotted path into the candidate/profile record ("" if n/a)
    candidate_value: str
    job_ref: str         # dotted path into the job record ("" if n/a)
    job_value: str

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "claim": self.claim,
            "candidate_ref": self.candidate_ref,
            "candidate_value": self.candidate_value,
            "job_ref": self.job_ref,
            "job_value": self.job_value,
        }


@dataclass(frozen=True)
class MatchComponent:
    name: str
    score: float   # in [0, 1]
    weight: float
    evidence: tuple[EvidenceItem, ...] = ()

    @property
    def contribution(self) -> float:
        return self.score * self.weight

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "score": round(self.score, 4),
            "weight": self.weight,
            "contribution": round(self.contribution, 4),
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class MatchResult:
    job_id: str
    total_score: float
    must_have_coverage: float
    components: tuple[MatchComponent, ...]
    missing_must_haves: tuple[str, ...]        # no direct match and no transfer
    partially_covered: tuple[str, ...] = ()    # covered only via transferable skill

    def all_evidence(self) -> list[EvidenceItem]:
        return [e for c in self.components for e in c.evidence]

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "total_score": round(self.total_score, 4),
            "must_have_coverage": round(self.must_have_coverage, 4),
            "components": [c.to_dict() for c in self.components],
            "missing_must_haves": list(self.missing_must_haves),
            "partially_covered": list(self.partially_covered),
        }


def _skill_coverage(profile: CandidateProfile, skills: tuple[str, ...],
                    kind: str) -> tuple[float, list[EvidenceItem], list[str], list[str]]:
    """Credit per required skill: 1.0 direct, transfer weight if transferable, else 0.

    V2: job-side skills are free strings (the corpus is not taxonomy-bound).
    Each is normalized through the alias map before lookup; a string the
    taxonomy cannot place is honestly counted as missing — vocabulary drift is
    a measured weakness of this deterministic matcher, not something to paper
    over."""
    if not skills:
        return 1.0, [], [], []
    held = profile.skill_set
    evidence: list[EvidenceItem] = []
    missing: list[str] = []
    partial: list[str] = []
    credit = 0.0
    for raw_skill in skills:
        skill = taxonomy.normalize_skill(raw_skill) or raw_skill
        if skill in held:
            credit += 1.0
            evidence.append(EvidenceItem(
                type="skill_match",
                claim=f"Kandidaten har etterspurt kompetanse: {raw_skill} "
                      f"(kilde: {profile.effective_skills[skill]}).",
                candidate_ref=f"profile.effective_skills.{skill}",
                candidate_value=profile.effective_skills[skill],
                job_ref=f"requirements.{kind}",
                job_value=raw_skill,
            ))
            continue
        paths = taxonomy.transfer_paths(held, skill)
        if paths:
            source, weight, rationale = paths[0]
            credit += weight
            partial.append(raw_skill)
            evidence.append(EvidenceItem(
                type="transferable_skill",
                claim=f"Delvis dekning av {raw_skill} via overførbar kompetanse "
                      f"{source} (vekt {weight}): {rationale}.",
                candidate_ref=f"profile.effective_skills.{source}",
                candidate_value=profile.effective_skills[source],
                job_ref=f"requirements.{kind}",
                job_value=raw_skill,
            ))
        else:
            missing.append(raw_skill)
    return credit / len(skills), evidence, missing, partial


def score_job(profile: CandidateProfile, job: Job, retrieval_score: float,
              max_retrieval_score: float) -> MatchResult:
    candidate = profile.candidate
    components: list[MatchComponent] = []

    # Must-have skills.
    must_cov, must_ev, missing, partial = _skill_coverage(
        profile, job.requirements.must_have_skills, "must_have_skills")
    components.append(MatchComponent("must_have_skills", must_cov, W_MUST_HAVE, tuple(must_ev)))

    # Nice-to-have skills.
    nice_cov, nice_ev, _, _ = _skill_coverage(
        profile, job.requirements.nice_to_have_skills, "nice_to_have_skills")
    components.append(MatchComponent("nice_to_have_skills", nice_cov, W_NICE_TO_HAVE, tuple(nice_ev)))

    # Experience.
    years = candidate.total_experience_years()
    req_years = job.requirements.min_years_experience
    exp_score = 1.0 if req_years <= 0 else min(years / req_years, 1.0)
    components.append(MatchComponent(
        "experience", exp_score, W_EXPERIENCE,
        (EvidenceItem(
            type="experience",
            claim=f"Kandidaten har {years} års erfaring; stillingen ber om minst {req_years}.",
            candidate_ref="work_history",
            candidate_value=f"{years} år",
            job_ref="requirements.min_years_experience",
            job_value=str(req_years),
        ),),
    ))

    # Location quality (hard eligibility is already guaranteed upstream;
    # this scores how convenient the eligible option is).
    if job.work_mode == "remote":
        loc_score, loc_desc = 0.85, "stillingen er fjernarbeid"
    else:
        minutes = taxonomy.commute_minutes(candidate.location_city, job.location_city)
        if minutes <= taxonomy.CITY_INTERNAL_MINUTES:
            loc_score, loc_desc = 1.0, f"samme by ({job.location_city})"
        elif minutes <= candidate.hard_constraints.max_commute_minutes:
            loc_score, loc_desc = 0.75, f"pendlebart, ca. {minutes} min"
        else:
            loc_score, loc_desc = 0.5, f"krever flytting til {taxonomy.CITY_COUNTY[job.location_city]}"
    components.append(MatchComponent(
        "location", loc_score, W_LOCATION,
        (EvidenceItem(
            type="location",
            claim=f"Arbeidssted: {loc_desc}.",
            candidate_ref="location_city",
            candidate_value=candidate.location_city,
            job_ref="location_city",
            job_value=f"{job.location_city} ({job.work_mode})",
        ),),
    ))

    # Sector preference (soft).
    if job.sector in candidate.preferred_sectors:
        sector_score, sector_desc = 1.0, "foretrukket sektor"
    elif job.sector in candidate.avoided_sectors:
        sector_score, sector_desc = 0.0, "sektor kandidaten ønsker seg bort fra"
    else:
        sector_score, sector_desc = 0.5, "nøytral sektor"
    components.append(MatchComponent(
        "sector", sector_score, W_SECTOR,
        (EvidenceItem(
            type="sector",
            claim=f"Sektor {job.sector}: {sector_desc}.",
            candidate_ref="preferred_sectors",
            candidate_value=",".join(candidate.preferred_sectors) or "ingen oppgitt",
            job_ref="sector",
            job_value=job.sector,
        ),),
    ))

    # Salary attractiveness (floor compliance is already guaranteed upstream).
    floor = candidate.hard_constraints.min_salary_nok
    if job.salary_nok_max is None:
        sal_score, sal_desc = 0.5, "lønn ikke oppgitt i annonsen (må avklares)"
    elif floor is None:
        sal_score, sal_desc = 0.7, f"oppgitt lønn {job.salary_nok_min}–{job.salary_nok_max} NOK"
    else:
        # Salaries are stated as full-time equivalents (see constraints.py).
        if job.salary_nok_max >= floor * 1.10:
            sal_score, sal_desc = 1.0, "oppgitt lønn ligger klart over kandidatens nedre grense"
        else:
            sal_score, sal_desc = 0.7, "oppgitt lønn ligger nær kandidatens nedre grense"
    components.append(MatchComponent(
        "salary", sal_score, W_SALARY,
        (EvidenceItem(
            type="salary",
            claim=f"Lønnsvurdering: {sal_desc}.",
            candidate_ref="hard_constraints.min_salary_nok",
            candidate_value=str(floor),
            job_ref="salary_nok_min/salary_nok_max",
            job_value=f"{job.salary_nok_min}–{job.salary_nok_max}",
        ),),
    ))

    # Retrieval similarity, normalized against the best score in this
    # candidate's shortlist so the component is comparable across candidates.
    rel = retrieval_score / max_retrieval_score if max_retrieval_score > 0 else 0.0
    components.append(MatchComponent(
        "retrieval_similarity", rel, W_RETRIEVAL,
        (EvidenceItem(
            type="retrieval",
            claim=f"Tekstlig likhet mellom profil og annonse: {retrieval_score:.3f} "
                  f"(normalisert {rel:.2f}).",
            candidate_ref="profile.query_text",
            candidate_value="tf-idf query",
            job_ref="description",
            job_value="tf-idf document",
        ),),
    ))

    total = sum(c.contribution for c in components)
    return MatchResult(
        job_id=job.id,
        total_score=round(total, 6),
        must_have_coverage=round(must_cov, 6),
        components=tuple(components),
        missing_must_haves=tuple(missing),
        partially_covered=tuple(partial),
    )
