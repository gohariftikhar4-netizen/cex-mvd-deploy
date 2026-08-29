"""Gold derivation from the generator manifest (EVAL SIDE ONLY).

Grades come from generation-time relations (see corpusgen.archetypes rubric).
Constraint violations are judged against generator GROUND TRUTH — the full
requirement set each ad was generated from — via the same domain constraint
engine, applied to the truth view rather than to any workflow's parse.
Workflows must never import this module (enforced by tests)."""

from __future__ import annotations

import json
from pathlib import Path

from ..pipeline import constraints
from ..schemas import Candidate
from .corpusgen.generator import truth_job


class GoldV2:
    def __init__(self, manifest_path: Path, candidates: list[Candidate]):
        raw = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        self.meta = {k: raw[k] for k in ("version", "seed", "size", "benchmark_date")}
        self.jobs: dict[str, dict] = raw["jobs"]
        self.candidates = {c.id: c for c in candidates}
        self._truth_cache: dict[tuple[str, str], tuple[bool, list[str]]] = {}

    # -- relevance ---------------------------------------------------------

    def grade(self, cand_id: str, job_id: str) -> int:
        rel = self.jobs.get(job_id, {}).get("relations", {}).get(cand_id)
        return rel["grade"] if rel else 0

    def relevant(self, cand_id: str, min_grade: int = 1,
                 within: set[str] | None = None) -> dict[str, int]:
        out = {}
        for job_id, m in self.jobs.items():
            if within is not None and job_id not in within:
                continue
            rel = m.get("relations", {}).get(cand_id)
            if rel and rel["grade"] >= min_grade:
                out[job_id] = rel["grade"]
        return out

    # -- constraint truth --------------------------------------------------

    def truth_check(self, cand_id: str, job_id: str) -> tuple[bool, list[str]]:
        """(passes, violated_dimensions) judged against generator ground truth."""
        key = (cand_id, job_id)
        if key not in self._truth_cache:
            entry = self.jobs.get(job_id)
            if entry is None:
                self._truth_cache[key] = (False, ["nonexistent_job"])
            else:
                report = constraints.check(self.candidates[cand_id],
                                           truth_job(entry["truth"], job_id))
                self._truth_cache[key] = (report.passed,
                                          sorted({v.dimension for v in report.violations}))
        return self._truth_cache[key]

    def strata(self, job_id: str) -> list[str]:
        return self.jobs.get(job_id, {}).get("strata", [])
