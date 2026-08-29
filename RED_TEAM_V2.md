# RED_TEAM_V2.md — Benchmark V2 red-team report

_Generated 2026-08-29 by `python -m forja.bench.report`. Verdict rules are frozen in [HYPOTHESIS_V2.md](HYPOTHESIS_V2.md); this report never answers a question the evidence cannot support._

## Evidence available

- Quality results: OFFLINE (harness validation only) run `20260829T150955Z-v2-offline` — 1000 jobs, 24 candidates, model `offline-lexical-v1`
- Measured human time: **none — no review sessions have been run**
- Adversarial suite: mode offline, {'INFO': 10, 'PASS': 14, 'LIVE_ONLY': 12}

> **The quality table below is an OFFLINE harness-validation run.** All arms shared a deterministic lexical stand-in instead of a real model, so it validates plumbing, metrics, and the deterministic guarantees of B2/B3 — it is **invalid** for comparing arms and is not used in any answer below.

| Metric | B0 | B1 | B2 | B3 |
|---|---|---|---|---|
| P@10 | 0.325 | 0.325 | 0.7083 | 0.7375 |
| R@10 | 0.2603 | 0.2603 | 0.5893 | 0.6225 |
| R@50 | 0.7329 | 0.7329 | 0.9335 | 0.9399 |
| NDCG@10 | 0.3598 | 0.3598 | 0.7831 | 0.8041 |
| Violation rate | 0.6292 | 0.6292 | 0.1417 | 0.1375 |
| Unsupported evidence | 0.0 | 0.0 | 0.0 | 0.0 |
| Opportunity loss | 0.6375 | 0.6375 | 0.2082 | 0.19 |
| Cost (USD) | n/a | n/a | n/a | n/a |

## Q1. Does Forja beat B2?

**UNANSWERED.** Requires a live run (`python -m forja.bench.run_v2 --mode live` + `score_v2`).

What CAN be said today: the deterministic guarantees hold under test (zero truth-judged violations from structurally-visible constraints; adversarial PASSes below), and text-only constraints burn B2 and B3 equally — the deterministic spine is not sufficient on its own. B3 currently has no LLM text-verification stage while B1 does, so a live B1 may plausibly beat B3 on constraint safety. That jeopardy is deliberate: patching B3 mid-benchmark would be tuning against the benchmark.

## Q2. By how much?

**UNANSWERED.** Requires a live run (`python -m forja.bench.run_v2 --mode live` + `score_v2`).

## Q3. Is the improvement statistically and economically meaningful?

**UNANSWERED.** Requires a live run (`python -m forja.bench.run_v2 --mode live` + `score_v2`). **UNANSWERED.** Requires measured human review sessions (`python -m forja.bench.review start ...`), ≥20 completed cases per arm from ≥2 reviewers.

## Q4. Which parts of Forja generate the improvement?

By construction, B2 and B3 share the constraint engine and the retrieval index, so any live B3−B2 delta isolates: structured matching with transferable-skill credit, machine-checkable evidence with gap analysis and next actions, the bounded soft-preference stage, and the final constraint gate.
Adversarial suite: B3 deterministic defenses pass 7 testable cases (substring_skill, false_certificate_in_text, near_identical_pair, prompt_injection_in_description, irrelevant_substring, stale_job, conflicting_constraints); B2 passes the same set — these defenses are NOT unique to B3.
Attribution of any HAM advantage (evidence/gaps vs ranking quality) requires the live run plus review sessions.

## Q5. Could those parts simply be added to B2?

Architecturally: **largely yes.** The final gate is ~50 lines against the shared engine; evidence claims and gap templates are deterministic modules B2 could import wholesale; the soft-preference stage is one bounded LLM call. Nothing in B3's advantage rests on data or feedback loops B2 could not adopt within days. The honest question the live run must answer is whether the ASSEMBLED system beats 'B2 + those modules' — i.e., whether orchestration itself carries value beyond its parts.

## Q6. Is there evidence of a moat, or merely a better implementation?

**No moat evidence exists in this benchmark.** Everything measured here is reproducible engineering: deterministic checks, retrieval, prompts, and verification. A moat would need at least one of: proprietary data (taxonomy/transfer weights validated at scale), accumulated candidate outcomes, or advisor-workflow lock-in — none of which a benchmark can demonstrate. Treat any positive result as 'better implementation until proven otherwise'.

## Q7. What remains unproven?

- Constraint safety on text-only requirements: with ~40% of ads carrying incomplete structured data, the deterministic spine floors at a nonzero violation rate (offline diagnostic: ~0.14 for both B2 and B3, essentially all from text-hidden facts). A text-reading verification stage — which B1 has and B3 does not — is the obvious next component for EITHER architecture, and its absence is a real cap on B3's safety claim.
- The live arm comparison (no API credentials in the build environment).
- Measured human active minutes — the primary metric (no review sessions yet).
- Generator-derived gold validated by blind human labeling (`forja.bench.labeling`, agreement stats built in) — not yet run.
- LLM-arm behavior under prompt injection, false certificates, and terminology mismatch (LIVE_ONLY adversarial cases).
- Scale behavior at the full 10k corpus for B0/B1 (cost-gated).
- Whether 'B2 + Forja's modules' matches B3 (the Q5 ablation).

## Q8. BUILD, PIVOT, or KILL?

**NOT DECIDABLE — and this report refuses to guess.** The frozen decision mapping needs live quality results and measured human time; neither exists. Two commitments stand regardless of outcome:

1. If B2 performs essentially as well as B3 in the live comparison, the conclusion is that **the current architecture does not demonstrate a defensible edge** — and that sentence goes in the final report verbatim.
2. The engineering effort already invested in Forja is sunk cost and carries **zero** weight in the decision.
