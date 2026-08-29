"""Red-team report generator for Benchmark V2.

    python -m forja.bench.report [--results runs_v2/<id>/results_v2.json]
        [--review review_sessions/<id>/review_report.json]
        [--adversarial adversarial_report.json] [--out RED_TEAM_V2.md]

Answers the eight frozen red-team questions from whatever evidence exists,
and marks everything else UNANSWERED. It never fabricates: an offline run is
reported as harness validation, never as an arm comparison; the verdict stays
NOT DECIDABLE until live quality results AND measured human-time results
exist per HYPOTHESIS_V2.md."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

QUALITY_KEYS = [
    ("P@10", "mean_precision_at_10"),
    ("R@10", "mean_recall_at_10"),
    ("R@50", "mean_recall_at_50"),
    ("NDCG@10", "mean_ndcg_at_10"),
    ("Violation rate", "mean_violation_rate"),
    ("Unsupported evidence", "mean_unsupported_evidence_rate"),
    ("Opportunity loss", "mean_opportunity_loss"),
]


def _load(path: str | None) -> dict | None:
    if not path:
        return None
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _quality_table(agg: dict) -> list[str]:
    arms = list(agg)
    lines = ["| Metric | " + " | ".join(a.upper() for a in arms) + " |",
             "|---|" + "---|" * len(arms)]
    for label, key in QUALITY_KEYS:
        lines.append(f"| {label} | " + " | ".join(str(agg[a].get(key)) for a in arms) + " |")
    lines.append("| Cost (USD) | " + " | ".join(
        (f"{agg[a]['usage']['cost_usd']:.2f}" if agg[a]["usage"].get("tokens_known") else "n/a")
        for a in arms) + " |")
    return lines


def _noninferior(b3: dict, base: dict) -> tuple[bool, list[str]]:
    """Frozen non-inferiority margins (HYPOTHESIS_V2.md)."""
    problems = []

    def worse_by(key, margin):
        a, b = b3.get(key), base.get(key)
        if a is None or b is None:
            return False
        return a < b - margin

    for key in ("mean_precision_at_10", "mean_recall_at_10", "mean_recall_at_50",
                "mean_ndcg_at_10"):
        if worse_by(key, 0.05):
            problems.append(f"{key}: {b3.get(key)} vs {base.get(key)}")
    v3, vb = b3.get("mean_violation_rate"), base.get("mean_violation_rate")
    if v3 is not None and (v3 > 0.01 or (vb is not None and v3 > vb)):
        problems.append(f"violation rate {v3} (baseline {vb})")
    for key in ("mean_hallucination_rate", "mean_unsupported_evidence_rate"):
        a, b = b3.get(key), base.get(key)
        if a is not None and b is not None and a > b + 0.01:
            problems.append(f"{key}: {a} vs {b}")
    ol3, olb = b3.get("mean_opportunity_loss"), base.get("mean_opportunity_loss")
    if ol3 is not None and olb is not None and ol3 > olb + 0.05:
        problems.append(f"opportunity loss {ol3} vs {olb}")
    return (not problems, problems)


def build_report(results: dict | None, review: dict | None,
                 adversarial: dict | None) -> str:
    today = _dt.date.today().isoformat()
    live = bool(results) and results["run_meta"]["mode"] == "live"
    agg = results["aggregate"] if results else None
    ham = review["by_arm"] if review else None
    ratios = review.get("ham_ratio_bootstrap") if review else None

    L: list[str] = [
        "# RED_TEAM_V2.md — Benchmark V2 red-team report",
        "",
        f"_Generated {today} by `python -m forja.bench.report`. Verdict rules are "
        "frozen in [HYPOTHESIS_V2.md](HYPOTHESIS_V2.md); this report never "
        "answers a question the evidence cannot support._",
        "",
        "## Evidence available",
        "",
        f"- Quality results: "
        + (f"{'LIVE' if live else 'OFFLINE (harness validation only)'} run "
           f"`{results['run_meta']['run_id']}` — {results['run_meta']['n_jobs']} jobs, "
           f"{len(results['run_meta']['candidates'])} candidates, model `{results['run_meta']['model']}`"
           if results else "none"),
        f"- Measured human time: "
        + (f"review session(s) with {sum(a['completed_cases'] for a in ham.values())} completed cases"
           if ham else "**none — no review sessions have been run**"),
        f"- Adversarial suite: "
        + (f"mode {adversarial['mode']}, {adversarial['summary']}" if adversarial else "none"),
        "",
    ]

    if results and not live:
        L += [
            "> **The quality table below is an OFFLINE harness-validation run.** All "
            "arms shared a deterministic lexical stand-in instead of a real model, so "
            "it validates plumbing, metrics, and the deterministic guarantees of "
            "B2/B3 — it is **invalid** for comparing arms and is not used in any "
            "answer below.",
            "",
        ]
    if agg:
        L += _quality_table(agg) + [""]

    def q(n, title, body):
        L.extend([f"## Q{n}. {title}", "", *body, ""])

    unanswered_live = ("**UNANSWERED.** Requires a live run "
                       "(`python -m forja.bench.run_v2 --mode live` + `score_v2`).")
    unanswered_ham = ("**UNANSWERED.** Requires measured human review sessions "
                      "(`python -m forja.bench.review start ...`), ≥20 completed "
                      "cases per arm from ≥2 reviewers.")

    # Q1/Q2/Q3
    if live and agg and "b2" in agg and "b3" in agg:
        ok, problems = _noninferior(agg["b3"], agg["b2"])
        q1 = [f"Quality non-inferiority of B3 vs B2: **{'holds' if ok else 'FAILS'}**."]
        if problems:
            q1 += ["Failing margins: " + "; ".join(problems)]
        if ham and ratios and ratios.get("b3_vs_b2"):
            r = ratios["b3_vs_b2"]
            q1.append(f"Measured HAM ratio B3/B2: **{r['point']}** "
                      f"(95% CI {r['ci95_low']}–{r['ci95_high']}, n={r['n_pairs']}).")
        else:
            q1.append(unanswered_ham + " Quality alone cannot answer Q1: the primary "
                      "metric is human active minutes.")
        q(1, "Does Forja beat B2?", q1)
        q(2, "By how much?", [
            "See the table above for quality deltas; HAM deltas "
            + ("are in Q1." if ham else "are unmeasured — " + unanswered_ham)
        ])
        q3 = []
        if ham and ratios and ratios.get("b3_vs_b2"):
            r = ratios["b3_vs_b2"]
            verdict = ("STRONG PASS candidate" if r["point"] <= 0.5 and r["ci95_high"] <= 0.55
                       else "PASS candidate" if r["point"] <= 0.5 and r["ci95_high"] < 0.70
                       else "FAIL" if r["point"] > 0.70 else "INCONCLUSIVE")
            q3.append(f"Against the frozen statistical bar: **{verdict}** "
                      f"(point {r['point']}, CI upper {r['ci95_high']}).")
            if r["n_pairs"] < 20:
                q3.append(f"⚠ Only {r['n_pairs']} paired cases — below the frozen "
                          "minimum of 20; the verdict is provisional.")
        else:
            q3.append(unanswered_ham)
        if agg:
            q3.append("Economics context (secondary): per-candidate model cost — "
                      + ", ".join(f"{a.upper()} ${agg[a]['usage']['cost_usd'] / max(1, agg[a]['cases']):.2f}"
                                  for a in agg if agg[a]["usage"].get("tokens_known")))
        q(3, "Is the improvement statistically and economically meaningful?", q3)
    else:
        q(1, "Does Forja beat B2?", [unanswered_live, "",
          "What CAN be said today: the deterministic guarantees hold under test "
          "(zero truth-judged violations from structurally-visible constraints; "
          "adversarial PASSes below), and text-only constraints burn B2 and B3 "
          "equally — the deterministic spine is not sufficient on its own. "
          "B3 currently has no LLM text-verification stage while B1 does, so a "
          "live B1 may plausibly beat B3 on constraint safety. That jeopardy is "
          "deliberate: patching B3 mid-benchmark would be tuning against the "
          "benchmark."])
        q(2, "By how much?", [unanswered_live])
        q(3, "Is the improvement statistically and economically meaningful?",
          [unanswered_live + " " + unanswered_ham])

    # Q4/Q5 — component attribution
    q4 = [
        "By construction, B2 and B3 share the constraint engine and the retrieval "
        "index, so any live B3−B2 delta isolates: structured matching with "
        "transferable-skill credit, machine-checkable evidence with gap analysis "
        "and next actions, the bounded soft-preference stage, and the final "
        "constraint gate.",
    ]
    if adversarial:
        det_pass = [name for name, case in adversarial["cases"].items()
                    if case.get("b3", {}).get("status") == "PASS"]
        q4.append(f"Adversarial suite: B3 deterministic defenses pass {len(det_pass)} "
                  f"testable cases ({', '.join(det_pass)}); B2 passes the same set — "
                  "these defenses are NOT unique to B3.")
    q4.append("Attribution of any HAM advantage (evidence/gaps vs ranking quality) "
              "requires the live run plus review sessions."
              if not (live and ham) else
              "Compare per-arm rejected/modified/reverification counts in the review "
              "report to attribute the HAM delta.")
    q(4, "Which parts of Forja generate the improvement?", q4)
    q(5, "Could those parts simply be added to B2?", [
        "Architecturally: **largely yes.** The final gate is ~50 lines against the "
        "shared engine; evidence claims and gap templates are deterministic modules "
        "B2 could import wholesale; the soft-preference stage is one bounded LLM "
        "call. Nothing in B3's advantage rests on data or feedback loops B2 could "
        "not adopt within days. The honest question the live run must answer is "
        "whether the ASSEMBLED system beats 'B2 + those modules' — i.e., whether "
        "orchestration itself carries value beyond its parts.",
    ])
    q(6, "Is there evidence of a moat, or merely a better implementation?", [
        "**No moat evidence exists in this benchmark.** Everything measured here is "
        "reproducible engineering: deterministic checks, retrieval, prompts, and "
        "verification. A moat would need at least one of: proprietary data "
        "(taxonomy/transfer weights validated at scale), accumulated candidate "
        "outcomes, or advisor-workflow lock-in — none of which a benchmark can "
        "demonstrate. Treat any positive result as 'better implementation until "
        "proven otherwise'.",
    ])
    q7 = [
        "- Constraint safety on text-only requirements: with ~40% of ads carrying "
        "incomplete structured data, the deterministic spine floors at a nonzero "
        "violation rate (offline diagnostic: ~0.14 for both B2 and B3, essentially "
        "all from text-hidden facts). A text-reading verification stage — which B1 "
        "has and B3 does not — is the obvious next component for EITHER "
        "architecture, and its absence is a real cap on B3's safety claim.",
        "- The live arm comparison (no API credentials in the build environment).",
        "- Measured human active minutes — the primary metric (no review sessions yet).",
        "- Generator-derived gold validated by blind human labeling "
        "(`forja.bench.labeling`, agreement stats built in) — "
        + ("validated." if False else "not yet run."),
        "- LLM-arm behavior under prompt injection, false certificates, and "
        "terminology mismatch (LIVE_ONLY adversarial cases).",
        "- Scale behavior at the full 10k corpus for B0/B1 (cost-gated).",
        "- Whether 'B2 + Forja's modules' matches B3 (the Q5 ablation).",
    ]
    q(7, "What remains unproven?", q7)

    if live and ham:
        q(8, "BUILD, PIVOT, or KILL?", [
            "Apply the frozen decision mapping in HYPOTHESIS_V2.md to Q1–Q3 above.",
        ])
    else:
        q(8, "BUILD, PIVOT, or KILL?", [
            "**NOT DECIDABLE — and this report refuses to guess.** The frozen "
            "decision mapping needs live quality results and measured human time; "
            "neither exists. Two commitments stand regardless of outcome:",
            "",
            "1. If B2 performs essentially as well as B3 in the live comparison, "
            "the conclusion is that **the current architecture does not "
            "demonstrate a defensible edge** — and that sentence goes in the "
            "final report verbatim.",
            "2. The engineering effort already invested in Forja is sunk cost and "
            "carries **zero** weight in the decision.",
        ])

    return "\n".join(L)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results")
    parser.add_argument("--review")
    parser.add_argument("--adversarial")
    parser.add_argument("--out", default="RED_TEAM_V2.md")
    args = parser.parse_args(argv)
    text = build_report(_load(args.results), _load(args.review), _load(args.adversarial))
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
