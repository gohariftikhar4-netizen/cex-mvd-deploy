"""The four arms: output contract, determinism, and structural guarantees."""

import pytest

from forja.bench.corpusgen.generator import generate
from forja.llm import OfflineDeterministicClient
from forja.pipeline import constraints
from forja.runlog import NullLogger
from forja.schemas import Job, load_candidates_v2
from forja.workflows import WORKFLOWS
from forja.workflows.common import claim_supported


@pytest.fixture(scope="module")
def world():
    job_dicts, _ = generate(size=500)
    jobs = [Job.from_dict(d) for d in job_dicts]
    candidates = load_candidates_v2()
    return jobs, {c.id: c for c in candidates}


@pytest.mark.parametrize("arm", ["b0", "b1", "b2", "b3"])
def test_output_contract(world, arm):
    jobs, cands = world
    cand = cands["cand_omar"]
    logger = NullLogger()
    out = WORKFLOWS[arm](cand, jobs, logger, OfflineDeterministicClient(logger))
    assert out["workflow"] == arm and out["candidate_id"] == cand.id
    recs = out["recommendations"]
    assert len(recs) <= 10
    assert [r["rank"] for r in recs] == list(range(1, len(recs) + 1))
    known = {j.id for j in jobs}
    assert all(r["job_id"] in known for r in recs)
    assert len(out["extended"]) <= 50
    assert len(set(out["extended"])) == len(out["extended"])
    for r in recs:
        for c in r.get("claims", []):
            assert set(c) >= {"claim", "source", "quote"}


@pytest.mark.parametrize("arm", ["b0", "b2", "b3"])
def test_arms_are_deterministic_offline(world, arm):
    jobs, cands = world
    cand = cands["cand_geir"]
    logger = NullLogger()
    client = OfflineDeterministicClient(logger)
    o1 = WORKFLOWS[arm](cand, jobs, logger, client)
    o2 = WORKFLOWS[arm](cand, jobs, logger, client)
    assert [r["job_id"] for r in o1["recommendations"]] == \
           [r["job_id"] for r in o2["recommendations"]]
    assert o1["extended"] == o2["extended"]


@pytest.mark.parametrize("arm", ["b2", "b3"])
def test_deterministic_arms_pass_structured_constraints(world, arm):
    """B2/B3 guarantee: nothing in the list violates a constraint VISIBLE in
    structured fields (text-only facts are the measured residual risk)."""
    jobs, cands = world
    jobs_by_id = {j.id: j for j in jobs}
    logger = NullLogger()
    client = OfflineDeterministicClient(logger)
    for cand_id in ("cand_solveig", "cand_lin", "cand_tarik", "cand_lene"):
        out = WORKFLOWS[arm](cands[cand_id], jobs, logger, client)
        for r in out["recommendations"]:
            report = constraints.check(cands[cand_id], jobs_by_id[r["job_id"]])
            assert report.passed, (arm, cand_id, r["job_id"],
                                   [v.dimension for v in report.violations])


def test_b3_claims_all_verify(world):
    jobs, cands = world
    jobs_by_id = {j.id: j for j in jobs}
    logger = NullLogger()
    client = OfflineDeterministicClient(logger)
    for cand_id in ("cand_marius", "cand_marta", "cand_yusuf"):
        out = WORKFLOWS["b3"](cands[cand_id], jobs, logger, client)
        for r in out["recommendations"]:
            for c in r["claims"]:
                assert claim_supported(c, cands[cand_id], jobs_by_id[r["job_id"]]), \
                    (cand_id, r["job_id"], c)


def test_softpref_rejects_fabricated_quotes(world):
    """The soft-preference gate: a fit score whose quote is not in the
    candidate's own text must be ignored."""
    import json as _json
    from forja.llm import ModelResult
    from forja.pipeline.profiling import build_profile
    from forja.pipeline.matching import score_job
    from forja.pipeline.softpref import apply_soft_preferences

    jobs, cands = world
    cand = cands["cand_kari"]
    logger = NullLogger()
    profile = build_profile(cand, logger)
    some_jobs = [j for j in jobs if j.work_mode == "remote"][:5] or jobs[:5]
    jobs_by_id = {j.id: j for j in some_jobs}
    matches = [score_job(profile, j, 0.5, 1.0) for j in some_jobs]

    class FabricatingClient:
        name = "fake"
        model = "fake"

        def complete(self, *, task, system, user, json_schema=None,
                     max_tokens=0, tags=None):
            payload = {"preferences": [
                {"job_id": some_jobs[0].id, "fit": 1.0,
                 "claim": "passer perfekt", "quote": "jeg elsker nattarbeid"},
            ]}
            return ModelResult(text=_json.dumps(payload), parsed_json=payload,
                               latency_s=0, input_tokens=None, output_tokens=None,
                               client_name="fake", model="fake")

    before = {m.job_id: m.total_score for m in matches}
    adjusted = apply_soft_preferences(profile, matches, jobs_by_id, logger,
                                      FabricatingClient())
    after = {m.job_id: m.total_score for m in adjusted}
    assert after == before  # fabricated quote -> zero influence
    decision = [d for d in logger.decisions if d["stage"] == "softpref.applied"][0]
    assert decision["rejected"] and decision["accepted"] == {}
