"""Profiling: provenance tracking + the 'LLM proposes, code disposes' gate."""

import json

from forja.llm import ModelResult
from forja.pipeline.profiling import (
    PROVENANCE_DECLARED, PROVENANCE_EXTRACTED, PROVENANCE_LLM, build_profile,
)
from forja.runlog import NullLogger


class FakeEnrichmentClient:
    """Returns a fixed set of suggestions: one verifiable, three not."""

    name = "fake"
    model = "fake-1"

    def __init__(self, suggestions):
        self._suggestions = suggestions
        self.calls = 0

    def complete(self, *, task, system, user, json_schema=None, max_tokens=4096):
        self.calls += 1
        payload = {"suggested_skills": self._suggestions, "notes": "test"}
        return ModelResult(
            text=json.dumps(payload), parsed_json=payload, latency_s=0.0,
            input_tokens=None, output_tokens=None, client_name=self.name, model=self.model,
        )


def test_declared_and_extracted_provenance(make_candidate):
    cand = make_candidate(free_text="Jeg har jobbet mye med k8s og feilsøking av systemer.")
    profile = build_profile(cand, NullLogger())
    assert profile.effective_skills["python"] == PROVENANCE_DECLARED
    # "k8s" is a known alias -> extracted from the candidate's own text.
    assert profile.effective_skills["kubernetes"] == PROVENANCE_EXTRACTED


def test_llm_suggestions_are_gated(make_candidate):
    cand = make_candidate(free_text="Jeg ledet et lite team på fire utviklere i to år.")
    client = FakeEnrichmentClient([
        # Accepted: known skill + quote appears verbatim in the text.
        {"skill": "teamledelse", "evidence_quote": "ledet et lite team"},
        # Rejected: quote not in the candidate's text (fabricated evidence).
        {"skill": "salg", "evidence_quote": "solgte mest i fylket"},
        # Rejected: not a known taxonomy skill.
        {"skill": "quantum_welding", "evidence_quote": "ledet et lite team"},
        # Rejected: already present as declared.
        {"skill": "python", "evidence_quote": "ledet et lite team"},
    ])
    logger = NullLogger()
    profile = build_profile(cand, logger, model_client=client)

    assert profile.effective_skills["teamledelse"] == PROVENANCE_LLM
    assert "salg" not in profile.effective_skills
    assert "quantum_welding" not in profile.effective_skills
    assert profile.effective_skills["python"] == PROVENANCE_DECLARED

    decision = [d for d in logger.decisions if d["stage"] == "profiling"][0]
    assert len(decision["llm_accepted"]) == 1
    assert len(decision["llm_rejected"]) == 3
    reasons = {r["rejected_because"] for r in decision["llm_rejected"]}
    assert "evidence quote not found in candidate text" in reasons
    assert "unknown skill" in reasons


def test_extraction_requires_word_boundaries(make_candidate):
    """Regression: 'varelogistikk' must not grant 'gis'; substrings of longer
    words are never skill evidence."""
    cand = make_candidate(skills=["python"],
                          free_text="Ansvar for varelogistikk og innkjøp i varehuset.")
    profile = build_profile(cand, NullLogger())
    assert "gis" not in profile.effective_skills
    assert profile.effective_skills["varelogistikk"] == PROVENANCE_EXTRACTED


def test_extraction_folds_norwegian_letters(make_candidate):
    cand = make_candidate(skills=["python"],
                          free_text="Jeg jobbet mye med truckkjøring og feilsøking.")
    profile = build_profile(cand, NullLogger())
    assert profile.effective_skills["truckkjoring"] == PROVENANCE_EXTRACTED
    assert profile.effective_skills["feilsoking_elektro"] == PROVENANCE_EXTRACTED


def test_offline_profile_without_client_is_deterministic(make_candidate):
    cand = make_candidate()
    p1 = build_profile(cand, NullLogger())
    p2 = build_profile(cand, NullLogger())
    assert p1.effective_skills == p2.effective_skills
    assert p1.query_text == p2.query_text
