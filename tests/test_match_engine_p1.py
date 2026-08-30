"""P1 — hard-constraint conflicts must be structurally unable to be recommended.

Root cause being fixed (measured on real NAV output, 77 strong-evidence
conflicts): the rerank schema had NO machine-readable constraint field, so
87% of conflicts were never elicited (class A) and 4% were stated in prose
and then ignored by the system (class C). These tests pin the contract:
a declared conflict is a REJECT that nothing can override.
"""

import json

import pytest

from forja.llm import ModelResult
from forja.match_engine import run_match_engine
from forja.runlog import NullLogger
from forja.schemas import Candidate, Job
from tests.conftest import candidate_dict, job_dict


class ScriptedClient:
    """Returns a fixed rerank payload so we test ENFORCEMENT, not the model."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, *, task, system, user, json_schema=None, max_tokens=4096,
                 tags=None, cached_prefix=None):
        self.calls += 1
        if task == "forja.profile_enrichment":
            p = {"suggested_skills": [], "notes": ""}
            return ModelResult(json.dumps(p), p, 0.0, None, None, self.name, self.model)
        return ModelResult(json.dumps(self.payload), self.payload, 0.0, None, None,
                           self.name, self.model)


def _world():
    cand = Candidate.from_dict(candidate_dict(
        hard_constraints={"cannot_work_shifts": ["night"]}))
    # Both jobs pass the STRUCTURED filter; the conflict is only in the text.
    ok = Job.from_dict(job_dict(id="job_800", description="Python-utvikler i Oslo. Ren dagtid."))
    bad = Job.from_dict(job_dict(id="job_801",
                                 description="Python-utvikler i Oslo. Tredelt turnus med nattevakter."))
    return cand, [ok, bad]


def test_declared_conflict_is_rejected():
    """If the model declares a hard-constraint conflict, the job MUST NOT be
    recommended — this is the class-C failure seen on real NAV data."""
    cand, jobs = _world()
    payload = {"items": [
        {"job_id": "job_801", "score": 99, "claims": [],
         "hard_constraint_conflict": True,
         "conflict_dimensions": ["shifts"],
         "conflict_evidence": [{"dimension": "shifts", "quote": "Tredelt turnus med nattevakter"}]},
        {"job_id": "job_800", "score": 10, "claims": [],
         "hard_constraint_conflict": False, "conflict_dimensions": [], "conflict_evidence": []},
    ]}
    out = run_match_engine(cand, jobs, NullLogger(), ScriptedClient(payload))
    ids = [r["job_id"] for r in out["recommendations"]]
    assert "job_801" not in ids, "declared hard conflict was recommended anyway"
    assert "job_800" in ids


def test_score_cannot_override_conflict():
    """A conflicting job with the top score must still be rejected: no rank,
    score or preference may outrank a hard conflict."""
    cand, jobs = _world()
    payload = {"items": [
        {"job_id": "job_801", "score": 100, "claims": [],
         "hard_constraint_conflict": True, "conflict_dimensions": ["shifts"],
         "conflict_evidence": [{"dimension": "shifts", "quote": "nattevakter"}]},
    ]}
    out = run_match_engine(cand, jobs, NullLogger(), ScriptedClient(payload))
    assert [r["job_id"] for r in out["recommendations"]] == []


def test_rejection_is_logged_and_auditable():
    cand, jobs = _world()
    payload = {"items": [
        {"job_id": "job_801", "score": 88, "claims": [],
         "hard_constraint_conflict": True, "conflict_dimensions": ["shifts"],
         "conflict_evidence": [{"dimension": "shifts", "quote": "nattevakter"}]},
    ]}
    logger = NullLogger()
    run_match_engine(cand, jobs, logger, ScriptedClient(payload))
    rejects = [d for d in logger.decisions if "reject" in d["stage"]]
    assert rejects, "rejection must be logged for audit"
    assert any("job_801" == d.get("job_id") for d in rejects)


def test_missing_conflict_field_is_treated_as_unverified_not_safe():
    """Fail-safe: a model that omits the verdict must not be assumed clean."""
    cand, jobs = _world()
    payload = {"items": [{"job_id": "job_801", "score": 50, "claims": []}]}
    out = run_match_engine(cand, jobs, NullLogger(), ScriptedClient(payload))
    recs = out["recommendations"]
    if recs:
        assert recs[0].get("constraint_verdict") == "unverified", \
            "a missing verdict must be surfaced as unverified, never silently trusted"
