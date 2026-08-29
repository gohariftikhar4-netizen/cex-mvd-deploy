"""Gap analysis: what stands between the candidate and this job, and what to
do about it. Deterministic, derived only from structured fields."""

from __future__ import annotations

from dataclasses import dataclass

from .. import taxonomy
from ..schemas import Job
from .matching import MatchResult
from .profiling import CandidateProfile


@dataclass(frozen=True)
class Gap:
    kind: str        # missing_skill | partial_skill | experience_shortfall | nice_to_have | salary_undisclosed
    detail: str
    next_action: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail, "next_action": self.next_action}


def analyze(profile: CandidateProfile, job: Job, match: MatchResult) -> list[Gap]:
    gaps: list[Gap] = []
    candidate = profile.candidate

    for skill in match.missing_must_haves:
        gaps.append(Gap(
            kind="missing_skill",
            detail=f"Stillingen krever {skill}; kandidaten mangler dette uten "
                   f"dokumentert overførbar kompetanse.",
            next_action=f"Vurder kort kurs/sertifisering i {skill}, eller dokumenter "
                        f"tilsvarende erfaring i CV-en før søknad.",
        ))

    for skill in match.partially_covered:
        normalized = taxonomy.normalize_skill(skill) or skill
        paths = taxonomy.transfer_paths(profile.skill_set, normalized)
        source, _w, rationale = paths[0]
        gaps.append(Gap(
            kind="partial_skill",
            detail=f"{skill} dekkes delvis via {source}.",
            next_action=f"Fremhev i søknaden at {rationale}.",
        ))

    years = candidate.total_experience_years()
    req = job.requirements.min_years_experience
    if req > 0 and years < req:
        gaps.append(Gap(
            kind="experience_shortfall",
            detail=f"Stillingen ber om {req} års erfaring; kandidaten har {years}.",
            next_action="Adresser gapet aktivt i søknaden: vis konkrete resultater "
                        "som kompenserer for kortere fartstid.",
        ))

    missing_nice = [
        s for s in job.requirements.nice_to_have_skills
        if (taxonomy.normalize_skill(s) or s) not in profile.skill_set
        and not taxonomy.transfer_paths(profile.skill_set, taxonomy.normalize_skill(s) or s)
    ]
    if missing_nice:
        gaps.append(Gap(
            kind="nice_to_have",
            detail=f"Ønsket, men ikke påkrevd: {', '.join(missing_nice)}.",
            next_action="Ikke diskvalifiserende — nevn relatert erfaring der den finnes.",
        ))

    if candidate.hard_constraints.min_salary_nok is not None and job.salary_nok_max is None:
        gaps.append(Gap(
            kind="salary_undisclosed",
            detail="Annonsen oppgir ikke lønn; kandidaten har en nedre lønnsgrense.",
            next_action=f"Avklar lønnsnivå tidlig i prosessen (kandidatens grense: "
                        f"{candidate.hard_constraints.min_salary_nok} NOK i 100 %).",
        ))

    return gaps
