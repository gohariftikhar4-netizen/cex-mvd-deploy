import pytest

from forja.schemas import Candidate, Job, SchemaError, load_candidates, load_jobs
from tests.conftest import candidate_dict, job_dict


def test_dataset_loads_and_validates():
    candidates = load_candidates()
    jobs = load_jobs()
    assert len(candidates) == 10
    assert len(jobs) >= 60
    assert all(c.id.startswith("cand_") for c in candidates)
    assert all(j.id.startswith("job_") for j in jobs)


def test_unknown_key_rejected():
    bad = candidate_dict()
    bad["favourite_colour"] = "blue"
    with pytest.raises(SchemaError, match="unknown keys"):
        Candidate.from_dict(bad)


def test_missing_key_rejected():
    bad = candidate_dict()
    del bad["skills"]
    with pytest.raises(SchemaError, match="missing keys"):
        Candidate.from_dict(bad)


def test_unknown_skill_rejected():
    with pytest.raises(SchemaError, match="vocabulary"):
        Candidate.from_dict(candidate_dict(skills=["python", "not_a_skill"]))


def test_unknown_city_rejected():
    with pytest.raises(SchemaError, match="unknown city"):
        Candidate.from_dict(candidate_dict(location_city="Atlantis"))


def test_bad_cefr_level_rejected():
    with pytest.raises(SchemaError, match="unknown level"):
        Candidate.from_dict(candidate_dict(languages={"norwegian": "B7"}))


def test_invalid_percent_range_rejected():
    with pytest.raises(SchemaError, match="percent"):
        Candidate.from_dict(candidate_dict(hard_constraints={"percent_min": 80, "percent_max": 60}))


def test_job_salary_bounds_must_be_paired():
    with pytest.raises(SchemaError, match="salary"):
        Job.from_dict(job_dict(salary_nok_min=500000, salary_nok_max=None))


def test_job_inverted_salary_rejected():
    with pytest.raises(SchemaError, match="salary"):
        Job.from_dict(job_dict(salary_nok_min=600000, salary_nok_max=500000))


def test_job_empty_shifts_rejected():
    with pytest.raises(SchemaError, match="shifts"):
        Job.from_dict(job_dict(shifts=[]))


def test_candidate_experience_sum():
    c = Candidate.from_dict(candidate_dict())
    assert c.total_experience_years() == 5.0
