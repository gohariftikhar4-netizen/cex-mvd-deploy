"""Blind labeling export/import + the adversarial suite (offline)."""

import csv
import json
from pathlib import Path

import pytest

from forja.bench import run_v2
from forja.bench.adversarial import run_suite
from forja.bench.corpusgen.generator import write_corpus
from forja.bench.labeling import export_batch, import_answers, pair_uid


@pytest.fixture(scope="module")
def small_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("v2run")
    data_dir = root / "benchmark_data"
    write_corpus(data_dir, size=450)
    run_dir = run_v2.run("offline", ["b2", "b3"],
                         ["cand_ingrid", "cand_omar"], data_dir,
                         slice_name="full", out_dir=root / "runs")
    return run_dir


def test_export_is_blind(small_run, tmp_path):
    batch = tmp_path / "batch"
    export_batch(small_run, per_candidate=10, out_dir=batch)
    lines = (batch / "pairs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) > 10
    for line in lines:
        rec = json.loads(line)
        # Only the pair id and the two renders — no workflow, no grade, no gold.
        assert set(rec) == {"pair_uid", "candidate", "job"}
    instructions = (batch / "instructions.md").read_text(encoding="utf-8")
    assert "2 = sterk match" in instructions


def test_import_merges_multiple_reviewers_and_flags_disputes(small_run, tmp_path):
    batch = tmp_path / "batch"
    export_batch(small_run, per_candidate=8, out_dir=batch)
    key = json.loads((batch / "pair_key.json").read_text())
    uids = sorted(key)[:6]

    def write_answers(path, reviewer, grades):
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["pair_uid", "grade", "rationale", "reviewer_id"])
            for uid, g in zip(uids, grades):
                w.writerow([uid, g, "test", reviewer])

    a1 = tmp_path / "anna.csv"
    a2 = tmp_path / "bo.csv"
    write_answers(a1, "anna", [2, 1, 0, 0, 2, 1])
    write_answers(a2, "bo",   [2, 1, 0, 1, 2, 2])  # two disagreements

    result = import_answers(batch, [a1, a2])
    assert result["reviewers"] == ["anna", "bo"]
    pairwise = result["agreement"]["anna|bo"]
    assert pairwise["pairs"] == 6
    assert pairwise["exact_agreement"] == pytest.approx(4 / 6, abs=0.01)
    # Even splits (1 vs 1) require adjudication.
    assert len(result["disputes_needing_adjudication"]) == 2
    labels = json.loads((batch / "labels_human.json").read_text())
    assert labels["result"]["consolidated"] == 4


def test_pair_uid_is_stable():
    assert pair_uid("cand_x", "job_1") == pair_uid("cand_x", "job_1")
    assert pair_uid("cand_x", "job_1") != pair_uid("cand_x", "job_2")


def test_adversarial_suite_offline_has_no_failures():
    report = run_suite(mode="offline")
    fails = {
        (name, arm): outcome
        for name, case in report["cases"].items()
        for arm, outcome in case.items()
        if outcome["status"] == "FAIL"
    }
    assert not fails, fails
    # The deterministic guarantees are actually exercised, not vacuously skipped.
    assert report["summary"].get("PASS", 0) >= 10
    assert report["summary"].get("LIVE_ONLY", 0) >= 5
