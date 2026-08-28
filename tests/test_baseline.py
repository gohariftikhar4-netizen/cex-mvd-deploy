from forja.baseline import build_prompt, parse_response, run_baseline
from forja.llm import CANDIDATE_SECTION, JOBS_SECTION, OfflineDeterministicClient
from forja.runlog import NullLogger
from forja.schemas import Job
from tests.conftest import job_dict


def _jobs():
    return [
        Job.from_dict(job_dict(id="job_940", title="Backend-utvikler",
                               description="python postgresql utvikler")),
        Job.from_dict(job_dict(id="job_941", title="Frisør",
                               requirements={"must_have_skills": ["frisering"]},
                               description="frisør salong")),
    ]


def test_prompt_contains_sections_and_constraints(make_candidate):
    cand = make_candidate(hard_constraints={"cannot_work_shifts": ["night"], "min_salary_nok": 600000})
    prompt = build_prompt(cand, _jobs())
    assert CANDIDATE_SECTION in prompt and JOBS_SECTION in prompt
    assert "[STILLING job_940]" in prompt
    assert "night" in prompt and "600000" in prompt  # constraints are visible to the model


def test_parse_numbered_list_with_ids():
    text = "Anbefalinger:\n1. job_940 – sterk match på python.\n2. job_941 – mindre relevant."
    recs = parse_response(text, _jobs())
    assert [r["job_id"] for r in recs] == ["job_940", "job_941"]
    assert recs[0]["rank"] == 1 and "python" in recs[0]["reason_text"]


def test_parse_flags_hallucinated_id_as_unresolved():
    text = "1. job_999 – finnes ikke.\n2. job_940 – ekte."
    recs = parse_response(text, _jobs())
    assert recs[0]["job_id"] is None
    assert recs[1]["job_id"] == "job_940"


def test_parse_falls_back_to_unique_title_match():
    text = "1. Frisør hos salongen – bra for deg."
    recs = parse_response(text, _jobs())
    assert recs[0]["job_id"] == "job_941"


def test_parse_dedupes_repeated_jobs():
    text = "1. job_940 – bra.\n2. job_940 – samme igjen.\n3. job_941 – ok."
    recs = parse_response(text, _jobs())
    assert [r["job_id"] for r in recs] == ["job_940", "job_941"]


def test_run_baseline_offline_end_to_end(make_candidate):
    logger = NullLogger()
    out = run_baseline(make_candidate(), _jobs(), logger, OfflineDeterministicClient(logger))
    assert out["workflow"] == "baseline"
    assert out["recommendations"]
    # The lexically obvious match should rank first for a python candidate.
    assert out["recommendations"][0]["job_id"] == "job_940"
    assert len(logger.model_calls) == 1
    call = logger.model_calls[0]
    assert call["task"] == "baseline.advise" and call["prompt"] and call["response"]


def test_offline_baseline_is_deterministic(make_candidate):
    logger = NullLogger()
    client = OfflineDeterministicClient(logger)
    cand = make_candidate()
    r1 = run_baseline(cand, _jobs(), logger, client)
    r2 = run_baseline(cand, _jobs(), logger, client)
    assert [x["job_id"] for x in r1["recommendations"]] == [x["job_id"] for x in r2["recommendations"]]
