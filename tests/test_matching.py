from forja.pipeline.matching import score_job
from forja.pipeline.profiling import build_profile
from forja.runlog import NullLogger


def _profile(make_candidate, **kw):
    return build_profile(make_candidate(**kw), NullLogger())


def test_components_carry_evidence(make_candidate, make_job):
    job = make_job(requirements={"must_have_skills": ["python"],
                                 "nice_to_have_skills": ["postgresql"]})
    result = score_job(_profile(make_candidate), job, 0.5, 1.0)
    assert 0 <= result.total_score <= 1
    for component in result.components:
        assert component.evidence, f"component {component.name} has no evidence"
    for item in result.all_evidence():
        d = item.to_dict()
        assert d["claim"] and d["type"]
    # Overall evidence is never empty, even for a job with no skill lists:
    # experience/location/sector/salary/retrieval always contribute items.
    bare = score_job(_profile(make_candidate),
                     make_job(requirements={"must_have_skills": []}), 0.5, 1.0)
    assert bare.all_evidence()


def test_direct_match_beats_missing_skill(make_candidate, make_job):
    prof = _profile(make_candidate)  # python, postgresql
    full = score_job(prof, make_job(requirements={"must_have_skills": ["python", "postgresql"]}), 0.5, 1.0)
    half = score_job(prof, make_job(requirements={"must_have_skills": ["python", "go"]}), 0.5, 1.0)
    assert full.total_score > half.total_score
    assert full.must_have_coverage == 1.0
    assert half.missing_must_haves == ("go",)


def test_transferable_skill_gives_partial_credit_with_rationale(make_candidate, make_job):
    prof = _profile(make_candidate, skills=["undervisning"])
    job = make_job(requirements={"must_have_skills": ["formidling"]})
    result = score_job(prof, job, 0.5, 1.0)
    assert 0 < result.must_have_coverage < 1.0
    assert result.partially_covered == ("formidling",)
    transfer_evidence = [e for e in result.all_evidence() if e.type == "transferable_skill"]
    assert transfer_evidence and "undervisning" in transfer_evidence[0].claim


def test_empty_must_haves_full_coverage(make_candidate, make_job):
    result = score_job(_profile(make_candidate), make_job(requirements={"must_have_skills": []}), 0.0, 1.0)
    assert result.must_have_coverage == 1.0


def test_experience_component(make_candidate, make_job):
    prof = _profile(make_candidate)  # 5 years
    short = score_job(prof, make_job(requirements={"min_years_experience": 10}), 0.5, 1.0)
    ok = score_job(prof, make_job(requirements={"min_years_experience": 3}), 0.5, 1.0)
    exp = {c.name: c.score for c in short.components}["experience"]
    assert exp == 0.5  # 5/10
    assert {c.name: c.score for c in ok.components}["experience"] == 1.0


def test_avoided_sector_scores_zero(make_candidate, make_job):
    prof = _profile(make_candidate, avoided_sectors=["teknologi"])
    result = score_job(prof, make_job(), 0.5, 1.0)
    assert {c.name: c.score for c in result.components}["sector"] == 0.0
