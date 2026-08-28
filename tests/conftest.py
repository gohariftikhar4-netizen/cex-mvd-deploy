"""Shared builders: minimal valid candidate/job dicts with overridable fields."""

from __future__ import annotations

import pytest

from forja.schemas import Candidate, Job


def candidate_dict(**overrides) -> dict:
    base = {
        "id": "cand_test",
        "name": "Test Person",
        "location_city": "Oslo",
        "work_authorization": "citizen",
        "languages": {"norwegian": "native", "english": "B2"},
        "driving_licenses": ["B"],
        "certifications": [],
        "education": [],
        "work_history": [
            {"title": "Utvikler", "employer": "Firma AS", "years": 5,
             "description": "Backend-utvikling i Python."}
        ],
        "skills": ["python", "postgresql"],
        "hard_constraints": {
            "max_commute_minutes": 45,
            "willing_to_relocate": False,
            "relocation_counties": [],
            "cannot_work_shifts": [],
            "physical_limitations": [],
            "min_salary_nok": None,
            "percent_min": 100,
            "percent_max": 100,
            "no_overnight_travel": False,
        },
        "preferred_sectors": [],
        "avoided_sectors": [],
        "free_text": "Testkandidat.",
    }
    hc_overrides = overrides.pop("hard_constraints", {})
    base["hard_constraints"].update(hc_overrides)
    base.update(overrides)
    return base


def job_dict(**overrides) -> dict:
    base = {
        "id": "job_900",
        "title": "Utvikler",
        "employer": "Testfirma",
        "sector": "teknologi",
        "location_city": "Oslo",
        "work_mode": "onsite",
        "percent_position": 100,
        "shifts": ["day"],
        "salary_nok_min": None,
        "salary_nok_max": None,
        "requires_overnight_travel": False,
        "requirements": {
            "must_have_skills": ["python"],
            "nice_to_have_skills": [],
            "min_years_experience": 0,
            "certifications_required": [],
            "driving_license_required": None,
            "norwegian_min_level": None,
            "english_min_level": None,
            "requires_norwegian_citizenship": False,
            "physical_demands": [],
        },
        "description": "Vi søker utvikler med python-erfaring.",
    }
    req_overrides = overrides.pop("requirements", {})
    base["requirements"].update(req_overrides)
    base.update(overrides)
    return base


@pytest.fixture
def make_candidate():
    return lambda **kw: Candidate.from_dict(candidate_dict(**kw))


@pytest.fixture
def make_job():
    return lambda **kw: Job.from_dict(job_dict(**kw))
