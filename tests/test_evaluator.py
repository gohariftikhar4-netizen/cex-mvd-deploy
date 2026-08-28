"""Evaluator metrics against hand-computed values."""

import math

import pytest

from forja.evaluation.evaluator import evaluate_output, ndcg_at_10, verify_evidence_item
from forja.evaluation.gold import GoldLabels
from forja.evaluation import review_time
from forja.schemas import Candidate, Job
from tests.conftest import candidate_dict, job_dict


@pytest.fixture
def world(make_candidate):
    cand = make_candidate(hard_constraints={"cannot_work_shifts": ["night"]})
    job_a = Job.from_dict(job_dict(id="job_950", description="python utvikler"))
    job_c = Job.from_dict(job_dict(id="job_951", shifts=["night"], description="nattjobb"))
    gold = GoldLabels(
        rubric="test",
        labels={"cand_test": {"job_950": 2, "job_952": 1}},
        notes={},
    )
    jobs_by_id = {"job_950": job_a, "job_951": job_c,
                  "job_952": Job.from_dict(job_dict(id="job_952"))}
    return cand, jobs_by_id, gold


def test_metrics_hand_computed(world):
    cand, jobs_by_id, gold = world
    output = {
        "workflow": "baseline",
        "candidate_id": "cand_test",
        "recommendations": [
            {"job_id": "job_950", "rank": 1, "reason_text": "bra"},
            {"job_id": "job_951", "rank": 2, "reason_text": "natt"},   # violates shifts
            {"job_id": None, "rank": 3, "raw_line": "3. Fantasijobb"},  # hallucinated
        ],
        "wall_time_s": 1.5,
    }
    m = evaluate_output(output, cand, jobs_by_id, gold)

    assert m["n_listed"] == 3 and m["n_valid"] == 2
    assert m["precision_listed"] == pytest.approx(1 / 3, abs=1e-4)
    assert m["recall_relevant"] == 0.5  # found job_950 of {job_950, job_952}
    # DCG = 3/log2(2) = 3; IDCG = 3 + 1/log2(3)
    expected_ndcg = 3 / (3 + 1 / math.log2(3))
    assert m["ndcg_at_10"] == pytest.approx(expected_ndcg, abs=1e-3)
    assert m["constraint_violation_count"] == 1
    assert m["constraint_violations"][0]["job_id"] == "job_951"
    assert m["critical_hallucination_count"] == 1
    # Free-text recs are unverified: 2 * 4.0 + 1 violation * 6.0 + 1 hallucination * 8.0
    assert m["review_time"]["minutes"] == pytest.approx(2 * 4.0 + 6.0 + 8.0)
    assert m["wall_time_s"] == 1.5


def test_ndcg_perfect_and_empty():
    assert ndcg_at_10([2, 1], [2, 1]) == 1.0
    assert ndcg_at_10([], [2, 1]) == 0.0
    assert ndcg_at_10([1], []) == 0.0  # no relevant jobs exist -> 0 by convention


def test_verified_structured_rec_counts_and_costs_less(world):
    cand, jobs_by_id, gold = world
    good_evidence = [{
        "type": "skill_match",
        "claim": "Kandidaten har python.",
        "candidate_ref": "profile.effective_skills.python",
        "candidate_value": "declared",
        "job_ref": "requirements.must_have_skills",
        "job_value": "python",
    }]
    output = {
        "workflow": "forja",
        "candidate_id": "cand_test",
        "recommendations": [
            {"job_id": "job_950", "rank": 1, "evidence": good_evidence},
        ],
        "profile_skills": {"python": "declared"},
        "wall_time_s": 0.1,
    }
    m = evaluate_output(output, cand, jobs_by_id, gold)
    assert m["verified_recs"] == 1 and m["unverified_recs"] == 0
    assert m["critical_hallucination_count"] == 0
    assert m["review_time"]["minutes"] == pytest.approx(review_time.VERIFIED_REC_MIN)


def test_fabricated_evidence_is_critical_hallucination(world):
    cand, jobs_by_id, gold = world
    fabricated = [{
        "type": "skill_match",
        "claim": "Kandidaten har frisering.",
        "candidate_ref": "profile.effective_skills.frisering",
        "candidate_value": "declared",
        "job_ref": "requirements.must_have_skills",
        "job_value": "frisering",  # job does not require it, candidate lacks it
    }]
    output = {
        "workflow": "forja",
        "candidate_id": "cand_test",
        "recommendations": [{"job_id": "job_950", "rank": 1, "evidence": fabricated}],
        "profile_skills": {"python": "declared"},
        "wall_time_s": 0.1,
    }
    m = evaluate_output(output, cand, jobs_by_id, gold)
    assert m["critical_hallucination_count"] == 1
    assert m["evidence_failures"]
    assert m["verified_recs"] == 0 and m["unverified_recs"] == 1


def test_verify_evidence_item_transfer_path():
    cand = Candidate.from_dict(candidate_dict(skills=["undervisning"]))
    job = Job.from_dict(job_dict(requirements={"must_have_skills": ["formidling"]}))
    ok, _ = verify_evidence_item({
        "type": "transferable_skill",
        "claim": "x",
        "candidate_ref": "profile.effective_skills.undervisning",
        "candidate_value": "declared",
        "job_ref": "requirements.must_have_skills",
        "job_value": "formidling",
    }, cand, job, {"undervisning": "declared"})
    assert ok
    bad, note = verify_evidence_item({
        "type": "transferable_skill",
        "claim": "x",
        "candidate_ref": "profile.effective_skills.undervisning",
        "candidate_value": "declared",
        "job_ref": "requirements.must_have_skills",
        "job_value": "sveising",  # no such transfer path, not required by job
    }, cand, job, {"undervisning": "declared"})
    assert not bad


def test_review_time_sensitivity_band():
    est = review_time.estimate(n_verified=2, n_unverified=1, n_violations=0, n_hallucinations=0)
    assert est["minutes"] == pytest.approx(2 * 0.75 + 4.0)
    assert est["minutes_low"] == pytest.approx(est["minutes"] * 0.5)
    assert est["minutes_high"] == pytest.approx(est["minutes"] * 2.0)
