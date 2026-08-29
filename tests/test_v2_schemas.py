import datetime as dt

import pytest

from forja.schemas import (
    BENCHMARK_TODAY, Candidate, Certification, Job, SchemaError,
    load_candidates_v2,
)
from tests.conftest import candidate_dict, job_dict


def test_certification_string_backcompat():
    c = Certification.from_record("fagbrev_elektriker", "t")
    assert c.status == "valid"


def test_expired_and_pending_certs_are_not_valid(make_job):
    cand = Candidate.from_dict(candidate_dict(certifications=[
        {"id": "ysk_gods", "status": "expired"},
        {"id": "truckforerbevis_t1_t4", "status": "valid"},
        {"id": "hpr_autorisasjon_sykepleier", "status": "pending"},
    ]))
    assert cand.valid_certification_ids == {"truckforerbevis_t1_t4"}
    from forja.pipeline import constraints
    job = make_job(requirements={"certifications_required": ["ysk_gods"]})
    report = constraints.check(cand, job)
    assert {v.dimension for v in report.violations} == {"certifications"}
    assert "expired" in report.violations[0].reason


def test_unknown_cert_status_rejected():
    with pytest.raises(SchemaError, match="status"):
        Certification.from_record({"id": "ysk_gods", "status": "sortof"}, "t")


def test_deadline_semantics(make_candidate, make_job):
    from forja.pipeline import constraints
    past = (BENCHMARK_TODAY - dt.timedelta(days=3)).isoformat()
    future = (BENCHMARK_TODAY + dt.timedelta(days=3)).isoformat()
    stale = make_job(application_deadline=past)
    assert stale.deadline_passed()
    report = constraints.check(make_candidate(), stale)
    assert {v.dimension for v in report.violations} == {"deadline"}
    assert not make_job(application_deadline=future).deadline_passed()
    assert not make_job().deadline_passed()


def test_out_of_vocabulary_job_skills_allowed():
    job = Job.from_dict(job_dict(requirements={
        "must_have_skills": ["spring boot", "next.js", "gerica journalsystem"]}))
    assert "spring boot" in job.requirements.must_have_skills
    with pytest.raises(SchemaError, match="malformed"):
        Job.from_dict(job_dict(requirements={"must_have_skills": ["Python!"]}))


def test_empty_shifts_unverified_for_shift_restricted_candidate(make_candidate, make_job):
    from forja.pipeline import constraints
    cand = make_candidate(hard_constraints={"cannot_work_shifts": ["night"]})
    job = make_job(shifts=[])
    report = constraints.check(cand, job)
    assert report.passed and "shifts" in report.unverified
    # No restrictions -> nothing to verify.
    report2 = constraints.check(make_candidate(), job)
    assert "shifts" not in report2.unverified


def test_v2_candidate_set_loads():
    cands = load_candidates_v2()
    assert len(cands) == 24
    by_id = {c.id: c for c in cands}
    # ambiguity features present
    assert by_id["cand_omar"].valid_certification_ids == {"truckforerbevis_t1_t4"}
    assert by_id["cand_marta"].valid_certification_ids == frozenset()
    assert by_id["cand_thomas"].skills == ()  # incomplete CV by design
    assert by_id["cand_solveig"].hard_constraints.cannot_work_shifts == (
        "evening", "night", "weekend")
    assert by_id["cand_lin"].language_level("norwegian") == "A2"
    assert all(c.constraint_notes for c in cands[10:]), "V2 candidates carry notes"
