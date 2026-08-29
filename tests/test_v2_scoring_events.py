"""Scoring math (frozen definitions) + review-event active-time computation."""

import json

import pytest

from forja.bench.events import bootstrap_ratio, case_metrics
from forja.bench.goldgen import GoldV2
from forja.bench.score_v2 import evaluate_case
from forja.schemas import Candidate
from tests.conftest import candidate_dict, job_dict
from forja.schemas import Job


@pytest.fixture
def gold(tmp_path):
    cand = Candidate.from_dict(candidate_dict(hard_constraints={"cannot_work_shifts": ["night"]}))

    def truth(city="Oslo", shifts=("day",), musts=("python",)):
        return {
            "family": "backend_dev", "title": "Utvikler", "employer": "X",
            "sector": "teknologi", "city": city, "work_mode": "onsite",
            "percent": 100, "shifts": list(shifts), "salary": None,
            "overnight": False, "license": None, "certs": [],
            "norwegian": None, "english": None, "citizenship": False,
            "physical": [], "min_years": 0, "min_years_hard": False,
            "musts": list(musts), "nice": [], "deadline": None,
        }

    manifest = {
        "version": "t", "seed": 0, "size": 4, "benchmark_date": "2026-09-01",
        "jobs": {
            "job_20001": {"family": "backend_dev", "strata": ["planted_strong:cand_test"],
                          "truth": truth(),
                          "relations": {"cand_test": {"relation": "strong", "grade": 2}},
                          "text_only_facts": [], "injection": False},
            "job_20002": {"family": "backend_dev", "strata": ["planted_near:cand_test"],
                          "truth": truth(),
                          "relations": {"cand_test": {"relation": "near", "grade": 1}},
                          "text_only_facts": [], "injection": False},
            "job_20003": {"family": "backend_dev", "strata": ["trap:shifts:cand_test"],
                          "truth": truth(shifts=("day", "night")),
                          "relations": {"cand_test": {"relation": "trap:shifts", "grade": 0}},
                          "text_only_facts": [], "injection": False},
            "job_20004": {"family": "frisor", "strata": ["filler"], "truth": truth(),
                          "relations": {}, "text_only_facts": [], "injection": False},
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return cand, GoldV2(path, [cand])


def _jobs():
    return {jid: Job.from_dict(job_dict(id=jid))
            for jid in ("job_20001", "job_20002", "job_20003", "job_20004")}


def test_metrics_hand_computed(gold):
    cand, g = gold
    jobs = _jobs()
    output = {
        "workflow": "bx", "candidate_id": "cand_test",
        "recommendations": [
            {"job_id": "job_20001", "rank": 1, "claims": [
                {"claim": "har python", "source": "job", "quote": "python"}]},
            {"job_id": "job_20003", "rank": 2, "claims": [
                {"claim": "tull", "source": "job", "quote": "denne teksten finnes ikke"}]},
            {"job_id": "job_20004", "rank": 3, "claims": []},
        ],
        "extended": ["job_20001", "job_20003", "job_20004", "job_20002"],
        "wall_time_s": 1.0,
    }
    m = evaluate_case(output, cand, g, jobs, set(jobs))
    # P@10 divides by 10: short lists are not rewarded.
    assert m["precision_at_10"] == pytest.approx(0.1)     # only job_20001 relevant
    assert m["recall_at_10"] == pytest.approx(0.5)        # 1 of 2 relevant found
    assert m["recall_at_50"] == pytest.approx(1.0)        # extended catches both
    assert m["violation_rate"] == pytest.approx(1 / 3, abs=1e-3)  # the night trap
    assert m["violations"][0]["job_id"] == "job_20003"
    assert m["unsupported_evidence_rate"] == pytest.approx(0.5)
    assert m["opportunity_loss"] == pytest.approx(0.0)    # the only grade-2 is listed
    assert m["false_negative_rate"] == pytest.approx(0.0)
    assert m["ndcg_at_10"] is not None


def test_conservative_short_list_caps_precision(gold):
    cand, g = gold
    jobs = _jobs()
    output = {"workflow": "bx", "candidate_id": "cand_test",
              "recommendations": [{"job_id": "job_20001", "rank": 1, "claims": []}],
              "extended": ["job_20001"], "wall_time_s": 0.1}
    m = evaluate_case(output, cand, g, jobs, set(jobs))
    assert m["precision_at_10"] == pytest.approx(0.1)  # 1 relevant / 10, not /1
    assert m["recall_at_50"] == pytest.approx(0.5)
    assert m["false_negative_rate"] == pytest.approx(0.5)


# ---------------- events ----------------

def _ev(ts, event, case_id="case_001", **payload):
    return {"ts": ts, "event": event, "case_id": case_id, **payload}


def test_active_time_caps_idle_gaps():
    events = [
        _ev(0, "review_started"),
        _ev(30, "recommendation_opened", n=1),
        _ev(1000, "recommendation_opened", n=2),   # 970s gap -> capped at 120
        _ev(1030, "recommendation_rejected", n=2),
        _ev(1090, "case_approved"),
    ]
    m = case_metrics(events, "case_001")
    assert m["active_minutes"] == pytest.approx((30 + 120 + 30 + 60) / 60, abs=0.01)
    assert m["recommendations_rejected"] == 1


def test_unapproved_case_does_not_count():
    events = [_ev(0, "review_started"), _ev(10, "recommendation_opened", n=1)]
    assert case_metrics(events, "case_001") is None


def test_reverification_and_research_tracking():
    events = [
        _ev(0, "review_started"),
        _ev(10, "recommendation_opened", n=1, reverify=True),
        _ev(20, "external_research_started"),
        _ev(80, "external_research_finished"),
        _ev(90, "case_approved"),
    ]
    m = case_metrics(events, "case_001")
    assert m["reverifications"] == 1
    assert m["external_searches"] == 1
    assert m["research_minutes"] == pytest.approx(1.0)


def test_bootstrap_ratio_sanity():
    b3 = {f"c{i}": 2.0 for i in range(10)}
    base = {f"c{i}": 4.0 for i in range(10)}
    r = bootstrap_ratio(b3, base)
    assert r["point"] == pytest.approx(0.5)
    assert r["ci95_low"] == pytest.approx(0.5) and r["ci95_high"] == pytest.approx(0.5)
    assert bootstrap_ratio({"a": 1.0}, {"a": 2.0}) is None  # too few pairs
