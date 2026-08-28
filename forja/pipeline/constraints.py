"""Hard employment-constraint checking. Pure and deterministic.

This module is the ONLY authority on hard constraints in the system. It never
imports LLM code, and nothing an LLM produces can add, remove, or relax a
check here. The pipeline applies it twice: as a pre-filter before any model
sees a job, and as a final gate on assembled recommendations (defense in
depth — see forja/pipeline/recommend.py).

Every violation carries machine-checkable evidence: the candidate field and
job field that conflict, with their values. `unverified` marks dimensions the
data cannot decide (e.g. salary not disclosed in the ad) — an unverified
dimension is NOT a violation, but is surfaced to the human reviewer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import taxonomy
from ..schemas import Candidate, Job

# The fixed set of dimensions this engine checks, in check order.
DIMENSIONS = [
    "work_authorization",
    "location_commute",
    "shifts",
    "physical",
    "driving_license",
    "certifications",
    "language_norwegian",
    "language_english",
    "percent_position",
    "salary",
    "overnight_travel",
]


@dataclass(frozen=True)
class Violation:
    dimension: str
    reason: str            # human-readable, verbatim in logs and evidence
    candidate_ref: str     # dotted path into the candidate record
    candidate_value: str
    job_ref: str           # dotted path into the job record
    job_value: str

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "reason": self.reason,
            "candidate_ref": self.candidate_ref,
            "candidate_value": self.candidate_value,
            "job_ref": self.job_ref,
            "job_value": self.job_value,
        }


@dataclass(frozen=True)
class ConstraintReport:
    candidate_id: str
    job_id: str
    violations: tuple[Violation, ...]
    unverified: tuple[str, ...]  # dimensions the data cannot decide

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "job_id": self.job_id,
            "passed": self.passed,
            "checked_dimensions": DIMENSIONS,
            "violations": [v.to_dict() for v in self.violations],
            "unverified": list(self.unverified),
        }


def check(candidate: Candidate, job: Job) -> ConstraintReport:
    """Check every hard-constraint dimension of `candidate` against `job`."""
    hc = candidate.hard_constraints
    violations: list[Violation] = []
    unverified: list[str] = []

    # 1. Work authorization / citizenship requirement.
    if job.requirements.requires_norwegian_citizenship and not candidate.is_citizen:
        violations.append(Violation(
            dimension="work_authorization",
            reason="Stillingen krever norsk statsborgerskap (sikkerhetsklarering); "
                   "kandidaten er ikke norsk statsborger.",
            candidate_ref="work_authorization",
            candidate_value=candidate.work_authorization,
            job_ref="requirements.requires_norwegian_citizenship",
            job_value="true",
        ))

    # 2. Location / commute / relocation. Remote jobs are reachable from
    # anywhere in Norway.
    if job.work_mode != "remote":
        minutes = taxonomy.commute_minutes(candidate.location_city, job.location_city)
        commutable = minutes <= hc.max_commute_minutes
        if not commutable:
            job_county = taxonomy.CITY_COUNTY[job.location_city]
            relocatable = hc.willing_to_relocate and (
                not hc.relocation_counties or job_county in hc.relocation_counties
            )
            if not relocatable:
                violations.append(Violation(
                    dimension="location_commute",
                    reason=(
                        f"Pendletid {candidate.location_city}–{job.location_city} er "
                        f"ca. {minutes} min (grense {hc.max_commute_minutes} min), og "
                        f"kandidaten kan ikke flytte til {job_county}."
                        if minutes < taxonomy.NOT_COMMUTABLE else
                        f"{job.location_city} er ikke pendlebart fra "
                        f"{candidate.location_city}, og kandidaten kan ikke flytte "
                        f"til {job_county}."
                    ),
                    candidate_ref="hard_constraints.max_commute_minutes",
                    candidate_value=str(hc.max_commute_minutes),
                    job_ref="location_city",
                    job_value=f"{job.location_city} ({job.work_mode})",
                ))

    # 3. Shifts: the job's required shift coverage must avoid every shift the
    # candidate cannot work.
    blocked = sorted(set(job.shifts) & set(hc.cannot_work_shifts))
    if blocked:
        violations.append(Violation(
            dimension="shifts",
            reason=f"Stillingen krever skift {blocked}; kandidaten kan ikke jobbe {blocked}.",
            candidate_ref="hard_constraints.cannot_work_shifts",
            candidate_value=",".join(hc.cannot_work_shifts),
            job_ref="shifts",
            job_value=",".join(job.shifts),
        ))

    # 4. Physical demands vs documented limitations.
    for demand in job.requirements.physical_demands:
        for limitation in hc.physical_limitations:
            if taxonomy.limitation_blocks_demand(limitation, demand):
                violations.append(Violation(
                    dimension="physical",
                    reason=f"Stillingen krever {demand}; kandidaten har begrensningen {limitation}.",
                    candidate_ref="hard_constraints.physical_limitations",
                    candidate_value=limitation,
                    job_ref="requirements.physical_demands",
                    job_value=demand,
                ))

    # 5. Driving license class (implied classes count).
    required_license = job.requirements.driving_license_required
    if required_license is not None:
        held = taxonomy.expand_licenses(list(candidate.driving_licenses))
        if required_license not in held:
            violations.append(Violation(
                dimension="driving_license",
                reason=f"Stillingen krever førerkort klasse {required_license}; "
                       f"kandidaten har {sorted(held) or 'ingen'}.",
                candidate_ref="driving_licenses",
                candidate_value=",".join(candidate.driving_licenses) or "none",
                job_ref="requirements.driving_license_required",
                job_value=required_license,
            ))

    # 6. Legally required certifications/authorizations.
    missing_certs = [
        c for c in job.requirements.certifications_required
        if c not in candidate.certifications
    ]
    for cert in missing_certs:
        violations.append(Violation(
            dimension="certifications",
            reason=f"Stillingen krever {cert}; kandidaten mangler denne autorisasjonen/sertifiseringen.",
            candidate_ref="certifications",
            candidate_value=",".join(candidate.certifications) or "none",
            job_ref="requirements.certifications_required",
            job_value=cert,
        ))

    # 7 + 8. Language requirements.
    for dim, language, required in (
        ("language_norwegian", "norwegian", job.requirements.norwegian_min_level),
        ("language_english", "english", job.requirements.english_min_level),
    ):
        if required is None:
            continue
        level = candidate.language_level(language)
        if not taxonomy.meets_language_level(level, required):
            violations.append(Violation(
                dimension=dim,
                reason=f"Stillingen krever {language} på nivå {required}; "
                       f"kandidaten er på nivå {level}.",
                candidate_ref=f"languages.{language}",
                candidate_value=level,
                job_ref=f"requirements.{'norwegian' if language == 'norwegian' else 'english'}_min_level",
                job_value=required,
            ))

    # 9. Position percentage must fall inside the candidate's acceptable range.
    if not (hc.percent_min <= job.percent_position <= hc.percent_max):
        violations.append(Violation(
            dimension="percent_position",
            reason=f"Stillingen er {job.percent_position} %; kandidaten kan jobbe "
                   f"{hc.percent_min}–{hc.percent_max} %.",
            candidate_ref="hard_constraints.percent_min/percent_max",
            candidate_value=f"{hc.percent_min}-{hc.percent_max}",
            job_ref="percent_position",
            job_value=str(job.percent_position),
        ))

    # 10. Salary floor. Job salaries are stated as full-time equivalents
    # (Norwegian convention: årslønn i 100 % stilling), so the comparison is
    # direct. Undisclosed salary is unverified, never a violation.
    if hc.min_salary_nok is not None:
        if job.salary_nok_max is None:
            unverified.append("salary")
        else:
            if job.salary_nok_max < hc.min_salary_nok:
                violations.append(Violation(
                    dimension="salary",
                    reason=f"Stillingens øvre lønn er {job.salary_nok_max} NOK (100 %-ekvivalent); "
                           f"kandidatens nedre grense er {hc.min_salary_nok} NOK.",
                    candidate_ref="hard_constraints.min_salary_nok",
                    candidate_value=str(hc.min_salary_nok),
                    job_ref="salary_nok_max",
                    job_value=str(job.salary_nok_max),
                ))

    # 11. Overnight travel.
    if hc.no_overnight_travel and job.requires_overnight_travel:
        violations.append(Violation(
            dimension="overnight_travel",
            reason="Stillingen krever reisevirksomhet med overnatting; "
                   "kandidaten kan ikke ha overnattingsreiser.",
            candidate_ref="hard_constraints.no_overnight_travel",
            candidate_value="true",
            job_ref="requires_overnight_travel",
            job_value="true",
        ))

    return ConstraintReport(
        candidate_id=candidate.id,
        job_id=job.id,
        violations=tuple(violations),
        unverified=tuple(unverified),
    )


def filter_eligible(candidate: Candidate, jobs: list[Job]) -> tuple[list[Job], list[ConstraintReport]]:
    """Split jobs into eligible ones and the full per-job reports.

    Returns (eligible_jobs, all_reports). Reports for excluded jobs carry the
    violations as evidence for the decision log.
    """
    reports = [check(candidate, job) for job in jobs]
    eligible = [job for job, report in zip(jobs, reports) if report.passed]
    return eligible, reports
