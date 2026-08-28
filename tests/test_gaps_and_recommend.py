from forja.pipeline.gaps import analyze
from forja.pipeline.matching import score_job
from forja.pipeline.profiling import build_profile
from forja.pipeline.recommend import MAX_RECOMMENDATIONS, assemble
from forja.runlog import NullLogger


def _prof(make_candidate, **kw):
    return build_profile(make_candidate(**kw), NullLogger())


def test_gap_analysis_names_missing_skill_and_action(make_candidate, make_job):
    prof = _prof(make_candidate)
    job = make_job(requirements={"must_have_skills": ["python", "go"], "min_years_experience": 10})
    match = score_job(prof, job, 0.5, 1.0)
    gaps = analyze(prof, job, match)
    kinds = {g.kind for g in gaps}
    assert "missing_skill" in kinds and "experience_shortfall" in kinds
    for g in gaps:
        assert g.detail and g.next_action


def test_salary_undisclosed_gap(make_candidate, make_job):
    prof = _prof(make_candidate, hard_constraints={"min_salary_nok": 600000})
    job = make_job()  # no salary info
    match = score_job(prof, job, 0.5, 1.0)
    assert "salary_undisclosed" in {g.kind for g in analyze(prof, job, match)}


def test_assemble_ranks_caps_and_carries_evidence(make_candidate, make_job):
    prof = _prof(make_candidate)
    jobs = [
        make_job(id=f"job_9{i:02d}", title=f"Utvikler {i}",
                 description=f"python utvikler stilling {i}")
        for i in range(15)
    ]
    jobs_by_id = {j.id: j for j in jobs}
    matches = [score_job(prof, j, 0.5, 1.0) for j in jobs]
    recs = assemble(prof, matches, jobs_by_id, NullLogger())
    assert 0 < len(recs) <= MAX_RECOMMENDATIONS
    assert [r.rank for r in recs] == list(range(1, len(recs) + 1))
    scores = [r.score for r in recs]
    assert scores == sorted(scores, reverse=True)
    for r in recs:
        assert r.evidence, "recommendation without evidence"
        assert r.constraint_report["passed"] is True
        assert r.next_actions


def test_final_gate_blocks_constraint_violation(make_candidate, make_job):
    """Defense in depth: even if a violating job reaches assembly (e.g. an
    LLM-influenced upstream bug), it must be dropped and logged."""
    prof = _prof(make_candidate, hard_constraints={"cannot_work_shifts": ["night"]})
    ok_job = make_job(id="job_920", description="python utvikler dagtid")
    bad_job = make_job(id="job_921", shifts=["night"], description="python utvikler natt")
    jobs_by_id = {j.id: j for j in (ok_job, bad_job)}
    matches = [score_job(prof, j, 0.5, 1.0) for j in (ok_job, bad_job)]

    logger = NullLogger()
    recs = assemble(prof, matches, jobs_by_id, logger)

    assert [r.job_id for r in recs] == ["job_920"]
    blocked = [d for d in logger.decisions if d["stage"] == "recommend.final_gate_blocked"]
    assert len(blocked) == 1 and blocked[0]["job_id"] == "job_921"
    assert blocked[0]["violations"][0]["dimension"] == "shifts"


def test_low_coverage_job_not_recommended(make_candidate, make_job):
    prof = _prof(make_candidate)  # python, postgresql
    stranger = make_job(id="job_930",
                        requirements={"must_have_skills": ["frisering", "matlaging", "renhold"]})
    matches = [score_job(prof, stranger, 0.9, 1.0)]
    recs = assemble(prof, matches, {"job_930": stranger}, NullLogger())
    assert recs == []
