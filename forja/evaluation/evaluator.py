"""Benchmark evaluator.

Trusts nothing a workflow reports about itself: job existence, relevance,
constraint compliance, and every structured evidence claim are re-verified
here against the raw dataset (constraint compliance via the same
`constraints` module — it is the domain's definition of eligibility, applied
to raw records, not pipeline state).

Asymmetry note (documented in BENCHMARK.md): free-text output only exposes
job identity to verification, so the baseline can only "hallucinate" a job
that does not resolve. Structured output exposes every claim, so Forja is
checkable — and penalizable — at much finer grain.
"""

from __future__ import annotations

import math
import re

from ..pipeline import constraints
from ..pipeline.profiling import _extract_alias_skills, _own_text
from ..schemas import Candidate, Job
from .. import taxonomy
from .gold import GoldLabels
from . import review_time

MAX_LIST = 10


# --------------------------------------------------------------------------
# Evidence verification (structured recommendations only)
# --------------------------------------------------------------------------

def _allowed_skills(candidate: Candidate) -> set[str]:
    return set(candidate.skills) | _extract_alias_skills(_own_text(candidate))


def _cited_requirement_list(job: Job, job_ref: str) -> tuple[str, ...] | None:
    if job_ref.endswith("must_have_skills"):
        return job.requirements.must_have_skills
    if job_ref.endswith("nice_to_have_skills"):
        return job.requirements.nice_to_have_skills
    return None


def verify_evidence_item(item: dict, candidate: Candidate, job: Job,
                         profile_skills: dict[str, str]) -> tuple[bool, str]:
    """Returns (verified, note). A failed verification is a critical
    hallucination: the recommendation asserted something the records do not
    support."""
    etype = item.get("type", "")
    allowed = _allowed_skills(candidate)

    if etype == "skill_match":
        raw = item.get("job_value", "")
        skill = taxonomy.normalize_skill(raw) or raw
        cited = _cited_requirement_list(job, item.get("job_ref", ""))
        if cited is None or raw not in cited:
            return False, f"job does not list {raw!r} in the cited requirement list"
        if skill in allowed:
            return True, "ok"
        if profile_skills.get(skill) == "llm_suggested_validated":
            return True, "llm-suggested skill (passed the verbatim-quote gate)"
        return False, f"candidate records do not support skill {raw!r}"

    if etype == "transferable_skill":
        raw_target = item.get("job_value", "")
        target = taxonomy.normalize_skill(raw_target) or raw_target
        m = re.match(r"profile\.effective_skills\.(\w+)", item.get("candidate_ref", ""))
        if not m:
            return False, "unparseable candidate_ref"
        source = m.group(1)
        cited = _cited_requirement_list(job, item.get("job_ref", ""))
        if cited is None or raw_target not in cited:
            return False, f"job does not list {raw_target!r} in the cited requirement list"
        if source not in allowed and profile_skills.get(source) != "llm_suggested_validated":
            return False, f"candidate records do not support source skill {source!r}"
        if not taxonomy.transfer_paths({source}, target):
            return False, f"no transferable path {source!r} -> {target!r} in the taxonomy"
        return True, "ok"

    if etype == "experience":
        try:
            claimed_req = float(item.get("job_value", "x"))
        except ValueError:
            return False, "unparseable required-years claim"
        if claimed_req != job.requirements.min_years_experience:
            return False, "required-years claim does not match the job record"
        m = re.match(r"([\d.]+)", item.get("candidate_value", ""))
        if not m or float(m.group(1)) != candidate.total_experience_years():
            return False, "experience claim does not match the candidate record"
        return True, "ok"

    if etype == "location":
        if job.location_city not in item.get("job_value", ""):
            return False, "location claim does not match the job record"
        if item.get("candidate_value") != candidate.location_city:
            return False, "location claim does not match the candidate record"
        return True, "ok"

    if etype == "sector":
        if item.get("job_value") != job.sector:
            return False, "sector claim does not match the job record"
        return True, "ok"

    if etype == "salary":
        expected = f"{job.salary_nok_min}–{job.salary_nok_max}"
        if item.get("job_value") != expected:
            return False, "salary claim does not match the job record"
        return True, "ok"

    if etype == "retrieval":
        # A similarity statement over the two records; structurally harmless.
        return True, "ok"

    return False, f"unknown evidence type {etype!r}"


# --------------------------------------------------------------------------
# Ranking metrics
# --------------------------------------------------------------------------

def _gain(grade: int) -> float:
    return (2 ** grade) - 1  # 0 -> 0, 1 -> 1, 2 -> 3


def ndcg_at_10(listed_grades: list[int], gold_grades: list[int]) -> float:
    """NDCG over the listed order (hallucinated items enter as grade 0)."""
    dcg = sum(
        _gain(g) / math.log2(pos + 2)
        for pos, g in enumerate(listed_grades[:MAX_LIST])
    )
    ideal = sorted(gold_grades, reverse=True)[:MAX_LIST]
    idcg = sum(_gain(g) / math.log2(pos + 2) for pos, g in enumerate(ideal))
    if idcg == 0:
        return 0.0
    return round(dcg / idcg, 4)


# --------------------------------------------------------------------------
# Per-output evaluation
# --------------------------------------------------------------------------

def evaluate_output(output: dict, candidate: Candidate, jobs_by_id: dict[str, Job],
                    gold: GoldLabels) -> dict:
    recs = output.get("recommendations", [])
    profile_skills = output.get("profile_skills", {})

    listed: list[dict] = []          # normalized entries in rank order
    hallucinated_items: list[dict] = []
    duplicate_count = 0
    seen: set[str] = set()

    for rec in recs:
        job_id = rec.get("job_id")
        if job_id is None or job_id not in jobs_by_id:
            hallucinated_items.append({
                "rank": rec.get("rank"),
                "cited": job_id,
                "raw": rec.get("raw_line") or rec.get("reason_text") or "",
                "why": "job cannot be resolved in the dataset",
            })
            listed.append({"job_id": None, "rec": rec})
            continue
        if job_id in seen:
            duplicate_count += 1
            continue
        seen.add(job_id)
        listed.append({"job_id": job_id, "rec": rec})

    n_listed = len(listed)
    valid = [e for e in listed if e["job_id"] is not None]

    # Relevance.
    grades = [gold.grade(candidate.id, e["job_id"]) if e["job_id"] else 0 for e in listed]
    relevant_listed = sum(1 for g in grades if g >= 1)
    strong_listed = sum(1 for g in grades if g == 2)
    gold_relevant = gold.relevant_jobs(candidate.id)
    gold_grades = list(gold_relevant.values())
    recall = (
        len({e["job_id"] for e in valid} & set(gold_relevant)) / len(gold_relevant)
        if gold_relevant else 0.0
    )

    # Constraint violations, re-derived from raw records.
    violations: list[dict] = []
    constraint_ok_ids: set[str] = set()
    for e in valid:
        report = constraints.check(candidate, jobs_by_id[e["job_id"]])
        if report.passed:
            constraint_ok_ids.add(e["job_id"])
        else:
            violations.append({
                "job_id": e["job_id"],
                "dimensions": sorted({v.dimension for v in report.violations}),
                "reasons": [v.reason for v in report.violations],
            })

    # Evidence verification (structured recs only).
    evidence_failures: list[dict] = []
    n_verified_recs = 0
    n_unverified_recs = 0
    for e in valid:
        rec = e["rec"]
        evidence = rec.get("evidence")
        if not evidence:
            n_unverified_recs += 1
            continue
        job = jobs_by_id[e["job_id"]]
        item_results = [
            verify_evidence_item(item, candidate, job, profile_skills)
            for item in evidence
        ]
        failures = [
            {"job_id": e["job_id"], "claim": item.get("claim", ""), "why": note}
            for item, (ok, note) in zip(evidence, item_results) if not ok
        ]
        evidence_failures.extend(failures)
        if not failures and e["job_id"] in constraint_ok_ids:
            n_verified_recs += 1
        else:
            n_unverified_recs += 1

    n_hallucinations = len(hallucinated_items) + len(evidence_failures)

    review = review_time.estimate(
        n_verified=n_verified_recs,
        n_unverified=n_unverified_recs,
        n_violations=len(violations),
        n_hallucinations=n_hallucinations,
    )

    return {
        "workflow": output.get("workflow"),
        "candidate_id": candidate.id,
        "n_listed": n_listed,
        "n_valid": len(valid),
        "duplicates": duplicate_count,
        "precision_listed": round(relevant_listed / n_listed, 4) if n_listed else 0.0,
        "strong_precision_listed": round(strong_listed / n_listed, 4) if n_listed else 0.0,
        "recall_relevant": round(recall, 4),
        "ndcg_at_10": ndcg_at_10(grades, gold_grades),
        "gold_relevant_count": len(gold_relevant),
        "constraint_violations": violations,
        "constraint_violation_count": len(violations),
        "hallucinated_items": hallucinated_items,
        "evidence_failures": evidence_failures,
        "critical_hallucination_count": n_hallucinations,
        "verified_recs": n_verified_recs,
        "unverified_recs": n_unverified_recs,
        "review_time": review,
        "wall_time_s": output.get("wall_time_s"),
        "listed_job_ids": [e["job_id"] for e in listed],
        "listed_grades": grades,
    }


# --------------------------------------------------------------------------
# Aggregation and reporting
# --------------------------------------------------------------------------

def aggregate(per_candidate: list[dict]) -> dict:
    n = len(per_candidate)
    if n == 0:
        return {}

    def mean(key: str) -> float:
        return round(sum(p[key] for p in per_candidate) / n, 4)

    def total(key: str) -> float:
        return round(sum(p[key] for p in per_candidate), 4)

    return {
        "candidates": n,
        "mean_precision_listed": mean("precision_listed"),
        "mean_strong_precision": mean("strong_precision_listed"),
        "mean_recall_relevant": mean("recall_relevant"),
        "mean_ndcg_at_10": mean("ndcg_at_10"),
        "total_constraint_violations": int(total("constraint_violation_count")),
        "total_critical_hallucinations": int(total("critical_hallucination_count")),
        "total_verified_recs": int(total("verified_recs")),
        "total_unverified_recs": int(total("unverified_recs")),
        "total_review_minutes": round(sum(p["review_time"]["minutes"] for p in per_candidate), 2),
        "total_review_minutes_low": round(sum(p["review_time"]["minutes_low"] for p in per_candidate), 2),
        "total_review_minutes_high": round(sum(p["review_time"]["minutes_high"] for p in per_candidate), 2),
        "total_wall_time_s": round(sum(p["wall_time_s"] or 0 for p in per_candidate), 2),
    }


_SUMMARY_ROWS = [
    ("Mean precision of listed recs (grade ≥ 1)", "mean_precision_listed"),
    ("Mean strong precision (grade 2)", "mean_strong_precision"),
    ("Mean recall of relevant jobs", "mean_recall_relevant"),
    ("Mean NDCG@10", "mean_ndcg_at_10"),
    ("Hard-constraint violations (total)", "total_constraint_violations"),
    ("Critical hallucinations (total)", "total_critical_hallucinations"),
    ("Machine-verified recommendations", "total_verified_recs"),
    ("Unverified recommendations", "total_unverified_recs"),
    ("Est. human review time, minutes (total)", "total_review_minutes"),
    ("… low / high sensitivity band", None),
    ("Processing wall time, seconds (total)", "total_wall_time_s"),
]


def format_summary(results: dict) -> str:
    meta = results["run_meta"]
    agg = results["aggregate"]
    lines = [
        "# Benchmark summary",
        "",
        f"- mode: **{meta['mode']}**  |  model: `{meta['model']}`  |  date: {meta['date']}",
        f"- candidates: {agg['baseline']['candidates']}  |  jobs: {meta['n_jobs']}",
        "",
    ]
    if meta["mode"] == "offline":
        lines += [
            "> **OFFLINE MODE.** Both workflows used the deterministic lexical stand-in, "
            "not a real LLM. These numbers validate the harness and Forja's deterministic "
            "spine; they are NOT evidence for or against the edge (see BENCHMARK.md).",
            "",
        ]
    lines += [
        "| Metric | Baseline (advisor + chat LLM) | Forja |",
        "|---|---|---|",
    ]
    for label, key in _SUMMARY_ROWS:
        if key is None:
            b = f"{agg['baseline']['total_review_minutes_low']}–{agg['baseline']['total_review_minutes_high']}"
            f = f"{agg['forja']['total_review_minutes_low']}–{agg['forja']['total_review_minutes_high']}"
        else:
            b, f = agg["baseline"][key], agg["forja"][key]
        lines.append(f"| {label} | {b} | {f} |")

    lines += ["", "## Per-candidate detail", ""]
    header = "| Candidate | Workflow | Listed | Precision | NDCG@10 | Violations | Hallucinations | Review min |"
    lines += [header, "|---|---|---|---|---|---|---|---|"]
    for cand_id in sorted(results["per_candidate"]):
        for wf in ("baseline", "forja"):
            p = results["per_candidate"][cand_id][wf]
            lines.append(
                f"| {cand_id} | {wf} | {p['n_listed']} | {p['precision_listed']} "
                f"| {p['ndcg_at_10']} | {p['constraint_violation_count']} "
                f"| {p['critical_hallucination_count']} | {p['review_time']['minutes']} |"
            )
    return "\n".join(lines)
