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
         "conflicts": [{"dimension": "shifts",
                        "quote": "Tredelt turnus med nattevakter",
                        "explanation": "kandidaten kan ikke jobbe natt"}]},
        {"job_id": "job_800", "score": 10, "claims": [],
         "hard_constraint_conflict": False, "conflicts": []},
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
         "hard_constraint_conflict": True,
         "conflicts": [{"dimension": "shifts", "quote": "Tredelt turnus med nattevakter",
                        "explanation": "natt"}]},
    ]}
    out = run_match_engine(cand, jobs, NullLogger(), ScriptedClient(payload))
    assert [r["job_id"] for r in out["recommendations"]] == []


def test_rejection_is_logged_and_auditable():
    cand, jobs = _world()
    payload = {"items": [
        {"job_id": "job_801", "score": 88, "claims": [],
         "hard_constraint_conflict": True,
         "conflicts": [{"dimension": "shifts", "quote": "Tredelt turnus med nattevakter",
                        "explanation": "natt"}]},
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


# --- P1b: the fix must not cause OVER-rejection -------------------------
# Measured regression on real NAV data: enforcing every declared conflict
# collapsed recommendations from 10 to 0-2 per candidate (cand_lin: 0).
# Two systematic causes, both fixed below.

def test_conflict_with_unverifiable_quote_does_not_reject():
    """A conflict must prove the requirement exists in the ad. If its quote is
    not in the ad text, the requirement is unsubstantiated and must NOT cause
    a rejection — that is how the model's misreadings became lost jobs."""
    cand, jobs = _world()
    payload = {"items": [
        {"job_id": "job_800", "score": 70, "claims": [],
         "hard_constraint_conflict": True,
         "conflict_dimensions": ["authorization"],
         "conflicts": [{"dimension": "authorization",
                        "quote": "krever autorisasjon som sykepleier",   # NOT in job_800
                        "explanation": "modellen tror det står et krav her"}]},
    ]}
    logger = NullLogger()
    out = run_match_engine(cand, jobs, logger, ScriptedClient(payload))
    ids = [r["job_id"] for r in out["recommendations"]]
    assert "job_800" in ids, "rejected on evidence that does not exist in the ad"
    assert any("unverified_conflict" in d["stage"] for d in logger.decisions), \
        "an unverifiable conflict must still be logged for review"


def test_llm_cannot_reject_on_deterministically_owned_dimension():
    """Location/extent/deadline are enforced deterministically from structured
    data. A model verdict on those dimensions must be ignored — on real NAV
    data the model re-judged geography and destroyed 126 recommendations.

    (Semantic misuse of an owned dimension — e.g. citing a city name under
    'travel' — is an ENTAILMENT problem and is handled in P3, not here.)"""
    cand, jobs = _world()
    payload = {"items": [
        {"job_id": "job_800", "score": 60, "claims": [],
         "hard_constraint_conflict": True,
         "conflicts": [{"dimension": "position_extent",
                        "quote": "Python-utvikler i Oslo. Ren dagtid.",
                        "explanation": "feil stillingsprosent"}]},
    ]}
    out = run_match_engine(cand, jobs, NullLogger(), ScriptedClient(payload))
    assert "job_800" in [r["job_id"] for r in out["recommendations"]], \
        "a location statement was accepted as a travel conflict"


def test_real_conflict_still_rejects_after_the_over_rejection_fix():
    """Regression guard: tightening must not reintroduce the P1 bug."""
    cand, jobs = _world()
    payload = {"items": [
        {"job_id": "job_801", "score": 95, "claims": [],
         "hard_constraint_conflict": True,
         "conflict_dimensions": ["shifts"],
         "conflicts": [{"dimension": "shifts",
                        "quote": "Tredelt turnus med nattevakter",  # verbatim in job_801
                        "explanation": "kandidaten kan ikke jobbe natt"}]},
    ]}
    out = run_match_engine(cand, jobs, NullLogger(), ScriptedClient(payload))
    assert "job_801" not in [r["job_id"] for r in out["recommendations"]]
