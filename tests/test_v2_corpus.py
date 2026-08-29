"""Corpus generator invariants (structural only — never agreement with any
workflow, per the leakage rules)."""

import pytest

from forja.bench.corpusgen.generator import (
    build_slices, corpus_checksum, generate, truth_job,
)
from forja.pipeline import constraints
from forja.schemas import Job, load_candidates_v2

SIZE = 700


@pytest.fixture(scope="module")
def world():
    jobs, manifest = generate(size=SIZE)
    candidates = {c.id: c for c in load_candidates_v2()}
    return jobs, manifest, candidates


def test_generation_is_deterministic(world):
    jobs, _, _ = world
    jobs2, _ = generate(size=SIZE)
    assert corpus_checksum(jobs) == corpus_checksum(jobs2)


def test_every_job_validates(world):
    jobs, manifest, _ = world
    assert len(jobs) == SIZE
    parsed = [Job.from_dict(d) for d in jobs]
    assert len({j.id for j in parsed}) == SIZE
    assert set(manifest) == {j.id for j in parsed}


def test_planted_coverage_per_candidate(world):
    _, manifest, candidates = world
    for cand_id in candidates:
        grades = [m["relations"][cand_id]["grade"]
                  for m in manifest.values() if cand_id in m["relations"]]
        relevant = [g for g in grades if g >= 1]
        assert len(relevant) >= 3, f"{cand_id}: only {len(relevant)} relevant jobs"
        assert any(g == 2 for g in grades), f"{cand_id}: no grade-2 job"
        traps = [m for m in manifest.values()
                 if cand_id in m["relations"]
                 and m["relations"][cand_id]["relation"].startswith("trap")]
        assert len(traps) >= 2, f"{cand_id}: only {len(traps)} traps"


def test_relations_match_ground_truth(world):
    """Relevant relations must be truth-eligible; trap relations (except the
    experience pseudo-trap) must truth-violate."""
    _, manifest, candidates = world
    for job_id, m in manifest.items():
        for cand_id, rel in m["relations"].items():
            report = constraints.check(candidates[cand_id], truth_job(m["truth"], job_id))
            relation = rel["relation"]
            if rel["grade"] >= 1:
                assert report.passed, (
                    f"{cand_id}->{job_id} grade {rel['grade']} but violates "
                    f"{[v.dimension for v in report.violations]}")
            elif relation.startswith("trap") and "experience" not in relation:
                assert not report.passed, f"{cand_id}->{job_id} tagged {relation} but eligible"


def test_textonly_traps_hide_the_fact_from_structured_fields(world):
    """The whole point of a text-only trap: the workflow-visible structured
    parse passes the constraint engine, while ground truth violates."""
    jobs_by_id = {d["id"]: d for d in world[0]}
    _, manifest, candidates = world
    found = 0
    for job_id, m in manifest.items():
        tags = [s for s in m["strata"] if s.startswith("trap_textonly:")]
        if not tags:
            continue
        found += 1
        _, dim, cand_id = tags[0].split(":")
        cand = candidates[cand_id]
        visible = Job.from_dict(jobs_by_id[job_id])
        visible_report = constraints.check(cand, visible)
        truth_report = constraints.check(cand, truth_job(m["truth"], job_id))
        assert not truth_report.passed
        assert dim not in {v.dimension for v in visible_report.violations}, (
            f"{job_id}: dimension {dim} leaked into the structured view")
    assert found >= 10


def test_adversarial_strata_present(world):
    _, manifest, _ = world
    stems = {s.split(":")[0] for m in manifest.values() for s in m["strata"]}
    for needed in ("planted_strong", "trap", "trap_textonly", "near_duplicate_ok",
                   "near_duplicate_trap", "misleading_title", "prompt_injection",
                   "filler", "obvious_not_best"):
        assert needed in stems, f"missing stratum {needed}"
    injected = [m for m in manifest.values() if m["injection"]]
    assert len(injected) == 3


def test_slices_contain_all_planted(world):
    jobs, manifest, _ = world
    slices = build_slices(jobs, manifest, seed=1, sizes=(650,))
    core = {jid for jid, m in manifest.items() if "filler" not in m["strata"]}
    assert core <= set(slices["650"])
    assert len(slices["650"]) == 650
