"""Structural integrity of the benchmark dataset and gold labels.

These tests check that the labels are internally consistent with the frozen
labeling rubric — they do NOT check agreement with any workflow's output
(that would be optimizing against the benchmark)."""

from forja.evaluation.gold import load_labels
from forja.pipeline import constraints
from forja.schemas import load_candidates, load_jobs


def _world():
    candidates = {c.id: c for c in load_candidates()}
    jobs = {j.id: j for j in load_jobs()}
    gold = load_labels()
    return candidates, jobs, gold


def test_labels_reference_existing_records():
    candidates, jobs, gold = _world()
    for cand_id in gold.candidate_ids():
        assert cand_id in candidates, f"label for unknown candidate {cand_id}"
        for job_id in gold.relevant_jobs(cand_id):
            assert job_id in jobs, f"label references unknown job {job_id}"
    for cand_id, notes in gold.notes.items():
        assert cand_id in candidates
        for job_id in notes:
            assert job_id in jobs, f"note references unknown job {job_id}"


def test_every_candidate_is_labeled():
    candidates, _, gold = _world()
    assert set(gold.candidate_ids()) == set(candidates)


def test_relevant_jobs_violate_no_hard_constraint():
    """Rubric invariant: grade >= 1 requires zero hard-constraint violations."""
    candidates, jobs, gold = _world()
    for cand_id in gold.candidate_ids():
        for job_id, grade in gold.relevant_jobs(cand_id).items():
            report = constraints.check(candidates[cand_id], jobs[job_id])
            assert report.passed, (
                f"{cand_id} -> {job_id} labeled grade {grade} but violates: "
                f"{[v.reason for v in report.violations]}"
            )


def test_every_candidate_has_enough_relevant_jobs_and_traps():
    candidates, jobs, gold = _world()
    for cand_id in candidates:
        relevant = gold.relevant_jobs(cand_id)
        assert len(relevant) >= 3, f"{cand_id} has only {len(relevant)} relevant jobs"
        assert any(g == 2 for g in relevant.values()), f"{cand_id} has no grade-2 job"
        traps = [
            job_id for job_id, note in gold.notes.get(cand_id, {}).items()
            if note.startswith("TRAP")
        ]
        assert len(traps) >= 2, f"{cand_id} has only {len(traps)} documented traps"


def test_documented_traps_actually_violate_constraints():
    """Every note marked TRAP must be a real hard-constraint violation, and
    must not carry a positive grade."""
    candidates, jobs, gold = _world()
    for cand_id, notes in gold.notes.items():
        for job_id, note in notes.items():
            if not note.startswith("TRAP"):
                continue
            assert gold.grade(cand_id, job_id) == 0, f"TRAP {cand_id}->{job_id} has grade > 0"
            report = constraints.check(candidates[cand_id], jobs[job_id])
            assert not report.passed, (
                f"{cand_id} -> {job_id} documented as TRAP but violates nothing"
            )


def test_no_candidate_relevant_set_is_trivially_the_whole_market():
    """Sanity: gold-relevant jobs are a small minority of the market, so
    precision is a meaningful metric."""
    candidates, jobs, gold = _world()
    for cand_id in candidates:
        assert len(gold.relevant_jobs(cand_id)) <= len(jobs) * 0.15
