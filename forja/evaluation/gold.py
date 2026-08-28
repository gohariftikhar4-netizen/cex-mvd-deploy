"""Gold relevance labels.

Format of forja/data/labels.json:
{
  "rubric": "<the labeling rules, human-readable>",
  "labels": {"<candidate_id>": {"<job_id>": 1 | 2, ...}, ...},
  "notes":  {"<candidate_id>": {"<job_id>": "<rationale>", ...}, ...}
}

Grade semantics (the rubric in the file is authoritative prose):
  2 = strong match: eligible on every hard constraint AND core occupation/skill
      fit — a competent advisor would put it at the top of the list.
  1 = worth pursuing: eligible AND a credible partial or transferable-skill
      path — a competent advisor would include it with caveats.
  0 = not worth the candidate's time (default for every unlisted pair);
      by definition includes every job that violates a hard constraint.

Per EDGE.md §6, matching logic must never be tuned against these labels.
Labels change only to fix a demonstrable labeling error, with the rationale
recorded in `notes` and in the commit message.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..schemas import DATA_DIR


class GoldLabels:
    def __init__(self, rubric: str, labels: dict[str, dict[str, int]],
                 notes: dict[str, dict[str, str]]):
        self.rubric = rubric
        self._labels = labels
        self.notes = notes

    def grade(self, candidate_id: str, job_id: str) -> int:
        return self._labels.get(candidate_id, {}).get(job_id, 0)

    def relevant_jobs(self, candidate_id: str, min_grade: int = 1) -> dict[str, int]:
        return {
            job_id: grade
            for job_id, grade in self._labels.get(candidate_id, {}).items()
            if grade >= min_grade
        }

    def candidate_ids(self) -> list[str]:
        return sorted(self._labels)


def load_labels(path: Path | None = None) -> GoldLabels:
    path = path or DATA_DIR / "labels.json"
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    for cand, jobs in raw["labels"].items():
        for job_id, grade in jobs.items():
            if grade not in (1, 2):
                raise ValueError(
                    f"labels[{cand}][{job_id}] = {grade}; only 1 and 2 may be "
                    f"stored (0 is the implicit default)"
                )
    return GoldLabels(
        rubric=raw["rubric"],
        labels=raw["labels"],
        notes=raw.get("notes", {}),
    )
