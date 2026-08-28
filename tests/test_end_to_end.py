"""End-to-end: full offline benchmark over the real dataset."""

import json

from forja import run_benchmark
from forja.evaluation.evaluator import evaluate_output
from forja.evaluation.gold import load_labels
from forja.llm import OfflineDeterministicClient
from forja.pipeline import run_forja
from forja.runlog import NullLogger
from forja.schemas import load_candidates, load_jobs


def test_forja_pipeline_structural_guarantees():
    """For every real candidate: recommendations exist, carry evidence, and
    contain zero hard-constraint violations and zero evidence hallucinations
    (verified independently by the evaluator)."""
    candidates = load_candidates()
    jobs = load_jobs()
    jobs_by_id = {j.id: j for j in jobs}
    gold = load_labels()

    for cand in candidates:
        out = run_forja(cand, jobs, NullLogger())
        assert out["recommendations"], f"no recommendations for {cand.id}"
        assert len(out["recommendations"]) <= 10
        for rec in out["recommendations"]:
            assert rec["evidence"], f"{cand.id}: rec {rec['job_id']} has no evidence"
            assert rec["next_actions"]
        metrics = evaluate_output(out, cand, jobs_by_id, gold)
        assert metrics["constraint_violation_count"] == 0, metrics["constraint_violations"]
        assert metrics["critical_hallucination_count"] == 0, metrics["evidence_failures"]


def test_forja_pipeline_is_deterministic():
    candidates = load_candidates()
    jobs = load_jobs()
    cand = candidates[0]
    out1 = run_forja(cand, jobs, NullLogger())
    out2 = run_forja(cand, jobs, NullLogger())
    ids1 = [r["job_id"] for r in out1["recommendations"]]
    ids2 = [r["job_id"] for r in out2["recommendations"]]
    assert ids1 == ids2
    assert [r["score"] for r in out1["recommendations"]] == [r["score"] for r in out2["recommendations"]]


def test_offline_benchmark_run_produces_artifacts(tmp_path):
    results = run_benchmark.run("offline", tmp_path)

    assert results["run_meta"]["mode"] == "offline"
    agg = results["aggregate"]
    assert set(agg) == {"baseline", "forja"}
    assert agg["forja"]["total_constraint_violations"] == 0
    assert agg["forja"]["total_critical_hallucinations"] == 0
    assert agg["baseline"]["candidates"] == 10

    run_dirs = list(tmp_path.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    for name in ("results.json", "outputs.json", "summary.md", "model_calls.jsonl", "decisions.jsonl"):
        assert (run_dir / name).exists(), f"missing artifact {name}"

    # Model calls are logged in full (prompt + response present on every line).
    calls = [json.loads(line) for line in (run_dir / "model_calls.jsonl").read_text().splitlines()]
    # Per candidate: one baseline call + one profiling-enrichment call.
    assert len(calls) == 20
    assert {c["task"] for c in calls} == {"baseline.advise", "forja.profile_enrichment"}
    assert all(c["prompt"] and c["response"] for c in calls)

    # Decision log covers constraint exclusions and final recommendations.
    stages = {json.loads(line)["stage"] for line in (run_dir / "decisions.jsonl").read_text().splitlines()}
    assert {"constraints.excluded", "constraints.summary", "retrieval.shortlist",
            "matching.scored", "recommend.final", "profiling", "baseline.parsed"} <= stages

    # Offline summary carries the harness-validation warning.
    assert "OFFLINE MODE" in (run_dir / "summary.md").read_text()


def test_offline_client_rejects_unknown_task():
    client = OfflineDeterministicClient(NullLogger())
    try:
        client.complete(task="unknown.task", system="", user="")
        raise AssertionError("expected ForjaModelError")
    except Exception as e:
        assert "no handler" in str(e)
