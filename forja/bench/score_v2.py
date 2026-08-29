"""Benchmark V2 phase 2: score a phase-1 run against generator gold.

    python -m forja.bench.score_v2 runs_v2/<run_id>

Loads the run's outputs, the corpus manifest (ground truth), and the frozen
metric definitions from HYPOTHESIS_V2.md. Writes results_v2.json and
summary_v2.md into the run directory. This phase — and only this phase —
touches gold."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from ..schemas import load_candidates_v2
from ..workflows.common import claim_supported
from .goldgen import GoldV2
from .run_v2 import load_corpus

MAX_TOP = 10


def _gain(grade: int) -> float:
    return (2 ** grade) - 1


def _ndcg(listed_grades: list[int], gold_grades: list[int]) -> float | None:
    ideal = sorted(gold_grades, reverse=True)[:MAX_TOP]
    idcg = sum(_gain(g) / math.log2(i + 2) for i, g in enumerate(ideal))
    if idcg == 0:
        return None
    dcg = sum(_gain(g) / math.log2(i + 2) for i, g in enumerate(listed_grades[:MAX_TOP]))
    return round(dcg / idcg, 4)


def evaluate_case(output: dict, cand, gold: GoldV2, jobs_by_id: dict,
                  slice_ids: set[str]) -> dict:
    top = [r for r in output.get("recommendations", [])][:MAX_TOP]
    top_ids = [r.get("job_id") for r in top]
    extended_ids = list(dict.fromkeys(output.get("extended", [])))[:50]

    relevant = gold.relevant(cand.id, min_grade=1, within=slice_ids)
    strong = {j for j, g in relevant.items() if g == 2}

    grades = [gold.grade(cand.id, j) if j in jobs_by_id else 0 for j in top_ids]
    hits10 = {j for j in top_ids if j in relevant}
    hits50 = {j for j in extended_ids if j in relevant}

    # Constraint safety and staleness, judged against generator ground truth.
    violations = []
    for j in top_ids:
        ok, dims = gold.truth_check(cand.id, j)
        if not ok:
            violations.append({"job_id": j, "dimensions": dims,
                               "strata": gold.strata(j)})
    hallucinated = [j for j in top_ids if j not in jobs_by_id]

    # Evidence support (uniform across arms: quote must appear in the record).
    n_claims = n_unsupported = 0
    for rec in top:
        job = jobs_by_id.get(rec.get("job_id"))
        if job is None:
            continue
        for claim in rec.get("claims", []):
            n_claims += 1
            if not claim_supported(claim, cand, job):
                n_unsupported += 1

    n_rel = len(relevant)
    n_strong = len(strong)
    metrics = {
        "workflow": output.get("workflow"),
        "candidate_id": cand.id,
        "n_listed": len(top_ids),
        "n_extended": len(extended_ids),
        "precision_at_10": round(sum(1 for g in grades if g >= 1) / MAX_TOP, 4),
        "strong_precision_at_10": round(sum(1 for g in grades if g == 2) / MAX_TOP, 4),
        "recall_at_10": round(len(hits10) / n_rel, 4) if n_rel else None,
        "recall_at_50": round(len(hits50) / n_rel, 4) if n_rel else None,
        "recall_at_10_capped": round(len(hits10) / min(MAX_TOP, n_rel), 4) if n_rel else None,
        "recall_at_50_capped": round(len(hits50) / min(50, n_rel), 4) if n_rel else None,
        "ndcg_at_10": _ndcg(grades, list(relevant.values())),
        "violation_rate": round(len(violations) / max(1, len(top_ids)), 4),
        "violations": violations,
        "hallucination_rate": round(len(hallucinated) / max(1, len(top_ids)), 4),
        "hallucinated": hallucinated,
        "unsupported_evidence_rate": (round(n_unsupported / n_claims, 4)
                                      if n_claims else None),
        "n_claims": n_claims,
        "opportunity_loss": (round(1 - len({j for j in top_ids if j in strong}) / n_strong, 4)
                             if n_strong else None),
        "false_negative_rate": (round(1 - len(hits50) / n_rel, 4) if n_rel else None),
        "gold_relevant": n_rel,
        "gold_strong": n_strong,
        "wall_time_s": output.get("wall_time_s"),
        "listed_grades": grades,
        "top_ids": top_ids,
    }
    return metrics


_MEAN_KEYS = [
    "precision_at_10", "strong_precision_at_10", "recall_at_10", "recall_at_50",
    "recall_at_10_capped", "recall_at_50_capped", "ndcg_at_10",
    "violation_rate", "hallucination_rate", "unsupported_evidence_rate",
    "opportunity_loss", "false_negative_rate",
]


def aggregate(cases: list[dict], usage: dict, workflow: str) -> dict:
    agg: dict = {"cases": len(cases)}
    for key in _MEAN_KEYS:
        vals = [c[key] for c in cases if c[key] is not None]
        agg[f"mean_{key}"] = round(sum(vals) / len(vals), 4) if vals else None
    agg["total_violations"] = sum(len(c["violations"]) for c in cases)
    agg["total_hallucinations"] = sum(len(c["hallucinated"]) for c in cases)
    agg["total_claims"] = sum(c["n_claims"] for c in cases)
    agg["total_wall_time_s"] = round(sum(c["wall_time_s"] or 0 for c in cases), 2)
    # trap diagnostics: which strata fooled this arm
    fooled: dict[str, int] = {}
    for c in cases:
        for v in c["violations"]:
            for s in v["strata"]:
                stem = s.split(":")[0]
                fooled[stem] = fooled.get(stem, 0) + 1
    agg["violations_by_strata"] = dict(sorted(fooled.items()))
    # usage
    u = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0,
         "cost_usd": 0.0, "latency_s": 0.0}
    known = True
    for c in cases:
        entry = usage.get(f"{workflow}::{c['candidate_id']}")
        if not entry:
            continue
        u["model_calls"] += entry["model_calls"]
        u["latency_s"] = round(u["latency_s"] + entry["latency_s"], 2)
        if entry.get("tokens_known"):
            u["input_tokens"] += entry["input_tokens"]
            u["output_tokens"] += entry["output_tokens"]
            u["cost_usd"] = round(u["cost_usd"] + entry["cost_usd"], 4)
        else:
            known = False
    u["tokens_known"] = known
    agg["usage"] = u
    return agg


def score_run(run_dir: Path) -> dict:
    meta = json.loads((run_dir / "run_meta.json").read_text())
    outputs = json.loads((run_dir / "outputs_v2.json").read_text(encoding="utf-8"))
    usage = json.loads((run_dir / "usage.json").read_text())
    data_dir = Path(meta["data_dir"])

    candidates = {c.id: c for c in load_candidates_v2()}
    jobs = load_corpus(data_dir, meta["slice"])
    jobs_by_id = {j.id: j for j in jobs}
    slice_ids = set(jobs_by_id)
    gold = GoldV2(data_dir / "manifest.json", list(candidates.values()))

    per_case: dict[str, dict[str, dict]] = {}
    for cand_id, arms in outputs.items():
        per_case[cand_id] = {}
        for wf, output in arms.items():
            per_case[cand_id][wf] = evaluate_case(
                output, candidates[cand_id], gold, jobs_by_id, slice_ids)

    workflows = meta["workflows"]
    agg = {
        wf: aggregate([per_case[c][wf] for c in per_case if wf in per_case[c]],
                      usage, wf)
        for wf in workflows
    }

    results = {
        "run_meta": meta,
        "gold_meta": gold.meta,
        "per_case": per_case,
        "aggregate": agg,
        "human_active_minutes": {
            "status": "NOT MEASURED",
            "note": "The primary economic metric requires blinded human review "
                    "sessions (forja.bench.review). No modeled substitute is "
                    "reported here per HYPOTHESIS_V2.md.",
        },
    }
    (run_dir / "results_v2.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = format_summary(results)
    (run_dir / "summary_v2.md").write_text(summary, encoding="utf-8")
    print(summary)
    return results


_ROWS = [
    ("P@10 (grade ≥ 1)", "mean_precision_at_10"),
    ("Strong P@10 (grade 2)", "mean_strong_precision_at_10"),
    ("Recall@10", "mean_recall_at_10"),
    ("Recall@50", "mean_recall_at_50"),
    ("Recall@50 (capped, diagnostic)", "mean_recall_at_50_capped"),
    ("NDCG@10", "mean_ndcg_at_10"),
    ("Violation rate (truth-judged)", "mean_violation_rate"),
    ("Hallucination rate", "mean_hallucination_rate"),
    ("Unsupported evidence rate", "mean_unsupported_evidence_rate"),
    ("Opportunity loss (grade-2 missed)", "mean_opportunity_loss"),
    ("False-negative rate (top-50)", "mean_false_negative_rate"),
    ("Total violations", "total_violations"),
]


def format_summary(results: dict) -> str:
    meta = results["run_meta"]
    agg = results["aggregate"]
    arms = list(agg)
    lines = [
        "# Benchmark V2 summary",
        "",
        f"- mode: **{meta['mode']}** | model: `{meta['model']}` | date: {meta['date']} "
        f"| jobs: {meta['n_jobs']} (slice {meta['slice']}) | candidates: {len(meta['candidates'])}",
        "",
    ]
    if meta["mode"] == "offline":
        lines += [
            "> **OFFLINE MODE — harness validation only.** Every arm used the same "
            "deterministic lexical stand-in instead of a real model, so LLM-arm "
            "quality (B0/B1, and the rerank/soft-preference stages of B2/B3) is NOT "
            "meaningfully measured here. Do not use this table to compare arms.",
            "",
        ]
    lines.append("| Metric | " + " | ".join(a.upper() for a in arms) + " |")
    lines.append("|---|" + "---|" * len(arms))
    for label, key in _ROWS:
        vals = [str(agg[a].get(key)) for a in arms]
        lines.append(f"| {label} | " + " | ".join(vals) + " |")
    lines.append("| Model calls | " + " | ".join(str(agg[a]["usage"]["model_calls"]) for a in arms) + " |")
    lines.append("| Cost (USD, live only) | " + " | ".join(
        (f"{agg[a]['usage']['cost_usd']:.2f}" if agg[a]["usage"]["tokens_known"] else "n/a")
        for a in arms) + " |")
    lines.append("| Wall time (s) | " + " | ".join(
        str(agg[a]["total_wall_time_s"]) for a in arms) + " |")
    lines += [
        "",
        "**Human active minutes per completed case: NOT MEASURED** — requires "
        "blinded review sessions (`python -m forja.bench.review`). The verdict "
        "in HYPOTHESIS_V2.md cannot be evaluated without it.",
        "",
        "Violations by strata (which trap types fooled each arm):",
        "",
    ]
    for a in arms:
        lines.append(f"- {a.upper()}: {agg[a]['violations_by_strata'] or '{}'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    args = parser.parse_args(argv)
    score_run(Path(args.run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
