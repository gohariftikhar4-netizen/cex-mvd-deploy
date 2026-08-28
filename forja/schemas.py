"""Structured candidate and job schemas.

Plain dataclasses + strict validation (stdlib only). `from_dict` rejects
unknown keys and vocabulary violations so hand-authored dataset errors fail
loudly at load time instead of corrupting benchmark results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import taxonomy


class SchemaError(ValueError):
    """Raised when a candidate/job record violates the schema."""


def _require_keys(d: dict, required: set[str], optional: set[str], where: str) -> None:
    keys = set(d)
    missing = required - keys
    unknown = keys - required - optional
    problems = []
    if missing:
        problems.append(f"missing keys {sorted(missing)}")
    if unknown:
        problems.append(f"unknown keys {sorted(unknown)}")
    if problems:
        raise SchemaError(f"{where}: " + "; ".join(problems))


def _check_vocab(values: list[str], vocab: set[str] | list[str], where: str) -> None:
    vocab_set = set(vocab)
    bad = [v for v in values if v not in vocab_set]
    if bad:
        raise SchemaError(f"{where}: values not in vocabulary: {bad}")


def _check_city(city: str, where: str) -> None:
    if city not in taxonomy.CITY_COUNTY:
        raise SchemaError(f"{where}: unknown city {city!r}")


# --------------------------------------------------------------------------
# Candidate
# --------------------------------------------------------------------------

WORK_MODES = ["onsite", "hybrid", "remote"]


@dataclass(frozen=True)
class HardConstraints:
    """Non-negotiable employment constraints. Only `forja.pipeline.constraints`
    may interpret these; no LLM output can add, remove, or relax them."""

    max_commute_minutes: int
    willing_to_relocate: bool
    relocation_counties: tuple[str, ...]  # empty + willing => anywhere
    cannot_work_shifts: tuple[str, ...]
    physical_limitations: tuple[str, ...]
    min_salary_nok: int | None  # full-time-equivalent floor; None = no floor
    percent_min: int  # acceptable position size range, e.g. 60–80 %
    percent_max: int
    no_overnight_travel: bool

    @staticmethod
    def from_dict(d: dict, where: str) -> "HardConstraints":
        _require_keys(
            d,
            required={
                "max_commute_minutes", "willing_to_relocate", "relocation_counties",
                "cannot_work_shifts", "physical_limitations", "min_salary_nok",
                "percent_min", "percent_max", "no_overnight_travel",
            },
            optional=set(),
            where=where,
        )
        _check_vocab(d["cannot_work_shifts"], taxonomy.SHIFT_TYPES, f"{where}.cannot_work_shifts")
        _check_vocab(d["physical_limitations"], taxonomy.PHYSICAL_LIMITATIONS, f"{where}.physical_limitations")
        _check_vocab(d["relocation_counties"], set(taxonomy.CITY_COUNTY.values()), f"{where}.relocation_counties")
        if not (0 < d["percent_min"] <= d["percent_max"] <= 100):
            raise SchemaError(f"{where}: invalid percent range {d['percent_min']}–{d['percent_max']}")
        if d["min_salary_nok"] is not None and d["min_salary_nok"] <= 0:
            raise SchemaError(f"{where}: min_salary_nok must be positive or null")
        if d["max_commute_minutes"] <= 0:
            raise SchemaError(f"{where}: max_commute_minutes must be positive")
        return HardConstraints(
            max_commute_minutes=int(d["max_commute_minutes"]),
            willing_to_relocate=bool(d["willing_to_relocate"]),
            relocation_counties=tuple(d["relocation_counties"]),
            cannot_work_shifts=tuple(d["cannot_work_shifts"]),
            physical_limitations=tuple(d["physical_limitations"]),
            min_salary_nok=d["min_salary_nok"],
            percent_min=int(d["percent_min"]),
            percent_max=int(d["percent_max"]),
            no_overnight_travel=bool(d["no_overnight_travel"]),
        )


@dataclass(frozen=True)
class WorkHistoryEntry:
    title: str
    employer: str
    years: float
    description: str


@dataclass(frozen=True)
class EducationEntry:
    degree: str
    field: str
    institution: str
    year: int
    recognized_in_norway: bool


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    location_city: str
    work_authorization: str  # taxonomy.WORK_AUTH_STATUSES
    languages: dict[str, str]  # language -> CEFR level
    driving_licenses: tuple[str, ...]
    certifications: tuple[str, ...]
    education: tuple[EducationEntry, ...]
    work_history: tuple[WorkHistoryEntry, ...]
    skills: tuple[str, ...]
    hard_constraints: HardConstraints
    preferred_sectors: tuple[str, ...]
    avoided_sectors: tuple[str, ...]
    free_text: str

    @property
    def is_citizen(self) -> bool:
        return self.work_authorization == "citizen"

    def total_experience_years(self) -> float:
        return round(sum(w.years for w in self.work_history), 1)

    def language_level(self, language: str) -> str:
        return self.languages.get(language, "none")

    @staticmethod
    def from_dict(d: dict) -> "Candidate":
        where = f"candidate[{d.get('id', '?')}]"
        _require_keys(
            d,
            required={
                "id", "name", "location_city", "work_authorization", "languages",
                "driving_licenses", "certifications", "education", "work_history",
                "skills", "hard_constraints", "preferred_sectors", "avoided_sectors",
                "free_text",
            },
            optional=set(),
            where=where,
        )
        _check_city(d["location_city"], f"{where}.location_city")
        if d["work_authorization"] not in taxonomy.WORK_AUTH_STATUSES:
            raise SchemaError(f"{where}: unknown work_authorization {d['work_authorization']!r}")
        for lang, level in d["languages"].items():
            if level not in taxonomy.CEFR_ORDER:
                raise SchemaError(f"{where}.languages[{lang}]: unknown level {level!r}")
        _check_vocab(d["driving_licenses"], taxonomy.DRIVING_LICENSE_CLASSES, f"{where}.driving_licenses")
        _check_vocab(d["certifications"], taxonomy.CERTIFICATIONS, f"{where}.certifications")
        _check_vocab(d["skills"], taxonomy.SKILLS, f"{where}.skills")
        _check_vocab(d["preferred_sectors"], taxonomy.SECTORS, f"{where}.preferred_sectors")
        _check_vocab(d["avoided_sectors"], taxonomy.SECTORS, f"{where}.avoided_sectors")

        education = []
        for i, e in enumerate(d["education"]):
            _require_keys(
                e,
                required={"degree", "field", "institution", "year", "recognized_in_norway"},
                optional=set(),
                where=f"{where}.education[{i}]",
            )
            education.append(EducationEntry(
                degree=e["degree"], field=e["field"], institution=e["institution"],
                year=int(e["year"]), recognized_in_norway=bool(e["recognized_in_norway"]),
            ))
        history = []
        for i, w in enumerate(d["work_history"]):
            _require_keys(
                w,
                required={"title", "employer", "years", "description"},
                optional=set(),
                where=f"{where}.work_history[{i}]",
            )
            if w["years"] <= 0:
                raise SchemaError(f"{where}.work_history[{i}]: years must be positive")
            history.append(WorkHistoryEntry(
                title=w["title"], employer=w["employer"],
                years=float(w["years"]), description=w["description"],
            ))

        return Candidate(
            id=d["id"],
            name=d["name"],
            location_city=d["location_city"],
            work_authorization=d["work_authorization"],
            languages=dict(d["languages"]),
            driving_licenses=tuple(d["driving_licenses"]),
            certifications=tuple(d["certifications"]),
            education=tuple(education),
            work_history=tuple(history),
            skills=tuple(d["skills"]),
            hard_constraints=HardConstraints.from_dict(d["hard_constraints"], f"{where}.hard_constraints"),
            preferred_sectors=tuple(d["preferred_sectors"]),
            avoided_sectors=tuple(d["avoided_sectors"]),
            free_text=d["free_text"],
        )

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Job
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class JobRequirements:
    must_have_skills: tuple[str, ...]
    nice_to_have_skills: tuple[str, ...]
    min_years_experience: float
    certifications_required: tuple[str, ...]  # legally/absolutely required only
    driving_license_required: str | None
    norwegian_min_level: str | None
    english_min_level: str | None
    requires_norwegian_citizenship: bool  # e.g. security clearance
    physical_demands: tuple[str, ...]

    @staticmethod
    def from_dict(d: dict, where: str) -> "JobRequirements":
        _require_keys(
            d,
            required={
                "must_have_skills", "nice_to_have_skills", "min_years_experience",
                "certifications_required", "driving_license_required",
                "norwegian_min_level", "english_min_level",
                "requires_norwegian_citizenship", "physical_demands",
            },
            optional=set(),
            where=where,
        )
        _check_vocab(d["must_have_skills"], taxonomy.SKILLS, f"{where}.must_have_skills")
        _check_vocab(d["nice_to_have_skills"], taxonomy.SKILLS, f"{where}.nice_to_have_skills")
        _check_vocab(d["certifications_required"], taxonomy.CERTIFICATIONS, f"{where}.certifications_required")
        _check_vocab(d["physical_demands"], taxonomy.PHYSICAL_DEMANDS, f"{where}.physical_demands")
        lic = d["driving_license_required"]
        if lic is not None and lic not in taxonomy.DRIVING_LICENSE_CLASSES:
            raise SchemaError(f"{where}: unknown driving license {lic!r}")
        for key in ("norwegian_min_level", "english_min_level"):
            if d[key] is not None and d[key] not in taxonomy.CEFR_ORDER:
                raise SchemaError(f"{where}.{key}: unknown level {d[key]!r}")
        if d["min_years_experience"] < 0:
            raise SchemaError(f"{where}: min_years_experience must be >= 0")
        return JobRequirements(
            must_have_skills=tuple(d["must_have_skills"]),
            nice_to_have_skills=tuple(d["nice_to_have_skills"]),
            min_years_experience=float(d["min_years_experience"]),
            certifications_required=tuple(d["certifications_required"]),
            driving_license_required=lic,
            norwegian_min_level=d["norwegian_min_level"],
            english_min_level=d["english_min_level"],
            requires_norwegian_citizenship=bool(d["requires_norwegian_citizenship"]),
            physical_demands=tuple(d["physical_demands"]),
        )


@dataclass(frozen=True)
class Job:
    id: str
    title: str
    employer: str
    sector: str
    location_city: str  # for remote jobs: the employer's registered city
    work_mode: str  # onsite | hybrid | remote
    percent_position: int
    shifts: tuple[str, ...]  # shift coverage the role requires
    salary_nok_min: int | None
    salary_nok_max: int | None
    requires_overnight_travel: bool
    requirements: JobRequirements
    description: str  # the ad text shown to workflows

    @staticmethod
    def from_dict(d: dict) -> "Job":
        where = f"job[{d.get('id', '?')}]"
        _require_keys(
            d,
            required={
                "id", "title", "employer", "sector", "location_city", "work_mode",
                "percent_position", "shifts", "salary_nok_min", "salary_nok_max",
                "requires_overnight_travel", "requirements", "description",
            },
            optional=set(),
            where=where,
        )
        if d["sector"] not in taxonomy.SECTORS:
            raise SchemaError(f"{where}: unknown sector {d['sector']!r}")
        _check_city(d["location_city"], f"{where}.location_city")
        if d["work_mode"] not in WORK_MODES:
            raise SchemaError(f"{where}: unknown work_mode {d['work_mode']!r}")
        _check_vocab(d["shifts"], taxonomy.SHIFT_TYPES, f"{where}.shifts")
        if not d["shifts"]:
            raise SchemaError(f"{where}: shifts must not be empty (use ['day'])")
        if not (0 < int(d["percent_position"]) <= 100):
            raise SchemaError(f"{where}: percent_position out of range")
        lo, hi = d["salary_nok_min"], d["salary_nok_max"]
        if (lo is None) != (hi is None):
            raise SchemaError(f"{where}: salary bounds must both be set or both null")
        if lo is not None and not (0 < lo <= hi):
            raise SchemaError(f"{where}: invalid salary range")
        return Job(
            id=d["id"],
            title=d["title"],
            employer=d["employer"],
            sector=d["sector"],
            location_city=d["location_city"],
            work_mode=d["work_mode"],
            percent_position=int(d["percent_position"]),
            shifts=tuple(d["shifts"]),
            salary_nok_min=lo,
            salary_nok_max=hi,
            requires_overnight_travel=bool(d["requires_overnight_travel"]),
            requirements=JobRequirements.from_dict(d["requirements"], f"{where}.requirements"),
            description=d["description"],
        )

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"


def load_candidates(path: Path | None = None) -> list[Candidate]:
    path = path or DATA_DIR / "candidates.json"
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    candidates = [Candidate.from_dict(c) for c in raw]
    _ensure_unique_ids([c.id for c in candidates], "candidate")
    return candidates


def load_jobs(path: Path | None = None) -> list[Job]:
    path = path or DATA_DIR / "jobs.json"
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    jobs = [Job.from_dict(j) for j in raw]
    _ensure_unique_ids([j.id for j in jobs], "job")
    return jobs


def _ensure_unique_ids(ids: list[str], kind: str) -> None:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            raise SchemaError(f"duplicate {kind} id: {i}")
        seen.add(i)
