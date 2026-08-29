"""Review-event schema and human-active-time computation (HYPOTHESIS_V2.md).

Event stream (JSONL, one dict per line):
  {"ts": <unix float>, "event": <type>, "case_id": "...", ...payload}

Event types:
  review_started, recommendation_opened, recommendation_rejected,
  recommendation_modified, external_research_started,
  external_research_finished, case_approved

Active time for a case = sum of gaps between consecutive events from
review_started to case_approved, where each gap is capped at IDLE_CAP_S
(gaps longer than the cap count as idle and contribute 0). A case counts
only when it reaches case_approved.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

IDLE_CAP_S = 120.0

EVENT_TYPES = (
    "review_started",
    "recommendation_opened",
    "recommendation_rejected",
    "recommendation_modified",
    "external_research_started",
    "external_research_finished",
    "case_approved",
)


def load_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def case_metrics(events: list[dict], case_id: str) -> dict | None:
    """Metrics for one case; None if the case never reached case_approved."""
    ev = sorted((e for e in events if e.get("case_id") == case_id),
                key=lambda e: e["ts"])
    if not any(e["event"] == "case_approved" for e in ev):
        return None
    start = next((e["ts"] for e in ev if e["event"] == "review_started"), None)
    end = next(e["ts"] for e in ev if e["event"] == "case_approved")
    if start is None:
        return None

    window = [e for e in ev if start <= e["ts"] <= end]
    active = 0.0
    for a, b in zip(window, window[1:]):
        active += min(b["ts"] - a["ts"], IDLE_CAP_S)

    research_s = 0.0
    research_open: float | None = None
    for e in window:
        if e["event"] == "external_research_started":
            research_open = e["ts"]
        elif e["event"] == "external_research_finished" and research_open is not None:
            research_s += min(e["ts"] - research_open, IDLE_CAP_S * 5)
            research_open = None

    count = lambda t: sum(1 for e in window if e["event"] == t)  # noqa: E731
    return {
        "case_id": case_id,
        "active_minutes": round(active / 60.0, 3),
        "research_minutes": round(research_s / 60.0, 3),
        "recommendations_opened": count("recommendation_opened"),
        "recommendations_rejected": count("recommendation_rejected"),
        "recommendations_modified": count("recommendation_modified"),
        "external_searches": count("external_research_started"),
        "reverifications": sum(1 for e in window
                               if e["event"] == "recommendation_opened"
                               and e.get("reverify")),
    }


def bootstrap_ratio(b3_by_key: dict[str, float], base_by_key: dict[str, float],
                    n: int = 10_000, seed: int = 7) -> dict | None:
    """Paired bootstrap 95% CI of mean(B3)/mean(baseline) over shared keys
    (candidate ids). Returns None with fewer than 3 pairs."""
    keys = sorted(set(b3_by_key) & set(base_by_key))
    if len(keys) < 3:
        return None
    rng = random.Random(seed)
    pairs = [(b3_by_key[k], base_by_key[k]) for k in keys]
    point = (sum(p[0] for p in pairs) / len(pairs)) / max(
        sum(p[1] for p in pairs) / len(pairs), 1e-9)
    ratios = []
    for _ in range(n):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        num = sum(p[0] for p in sample) / len(sample)
        den = max(sum(p[1] for p in sample) / len(sample), 1e-9)
        ratios.append(num / den)
    ratios.sort()
    return {
        "n_pairs": len(keys),
        "point": round(point, 4),
        "ci95_low": round(ratios[int(0.025 * n)], 4),
        "ci95_high": round(ratios[int(0.975 * n)], 4),
    }
