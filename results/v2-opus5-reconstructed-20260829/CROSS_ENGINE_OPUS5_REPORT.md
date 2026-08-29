> **CROSS-ENGINE CONFIRMATION ARTIFACT — NOT the canonical V2 report.**
> The canonical, accepted V2 verdict lives in `RED_TEAM_V2.md` (GLM-5.3
> Flash engine) and is frozen. This document reports the same frozen
> criteria applied to the deterministically reconstructed Claude Opus 5
> run (no new API calls; replayed from logged responses). Both engines
> return FAIL -> KILL for the B3 architecture.

# RED_TEAM_V2.md — Benchmark V2 red-team report

_Generated 2026-08-29 by `python -m forja.bench.report`. Verdict rules are frozen in [HYPOTHESIS_V2.md](HYPOTHESIS_V2.md); this report never answers a question the evidence cannot support._

## Evidence available

- Quality results: LIVE run `opus5-reconstructed-20260829` — 1000 jobs, 19 candidates, model `claude-opus-5`
- Measured human time: **none — no review sessions have been run**
- Adversarial suite: none

| Metric | B0 | B1 | B2 | B3 |
|---|---|---|---|---|
| P@10 | 0.8211 | 0.84 | 0.88 | 0.7533 |
| R@10 | 0.6616 | 0.6432 | 0.6608 | 0.5833 |
| R@50 | 0.8068 | 0.7623 | 0.9325 | 0.9345 |
| NDCG@10 | 0.8852 | 0.8973 | 0.9342 | 0.8228 |
| Violation rate | 0.0906 | 0.0 | 0.0333 | 0.1133 |
| Unsupported evidence | 0.0 | 0.0126 | 0.0 | 0.0 |
| Opportunity loss | 0.1922 | 0.1863 | 0.1475 | 0.201 |
| Cost (USD) | 7.46 | 10.59 | 3.47 | 1.49 |

Cases per arm: {'B0': 19, 'B1': 15, 'B2': 15, 'B3': 15}. Arms with unequal case counts are not strictly paired; the B3-vs-B2 comparison uses the full paired set.

## Q1. Does Forja beat B2?

Quality non-inferiority of B3 vs B2: **FAILS**.
Failing margins: mean_precision_at_10: 0.7533 vs 0.88; mean_recall_at_10: 0.5833 vs 0.6608; mean_ndcg_at_10: 0.8228 vs 0.9342; violation rate 0.1133 (baseline 0.0333); opportunity loss 0.201 vs 0.1475
**UNANSWERED.** Requires measured human review sessions (`python -m forja.bench.review start ...`), ≥20 completed cases per arm from ≥2 reviewers. Quality alone cannot answer Q1: the primary metric is human active minutes.

## Q2. By how much?

See the table above for quality deltas; HAM deltas are unmeasured — **UNANSWERED.** Requires measured human review sessions (`python -m forja.bench.review start ...`), ≥20 completed cases per arm from ≥2 reviewers.

## Q3. Is the improvement statistically and economically meaningful?

**UNANSWERED.** Requires measured human review sessions (`python -m forja.bench.review start ...`), ≥20 completed cases per arm from ≥2 reviewers.
Economics context (secondary): per-candidate model cost — B0 $0.39, B1 $0.71, B2 $0.23, B3 $0.10

## Q4. Which parts of Forja generate the improvement?

By construction, B2 and B3 share the constraint engine and the retrieval index, so any live B3−B2 delta isolates: structured matching with transferable-skill credit, machine-checkable evidence with gap analysis and next actions, the bounded soft-preference stage, and the final constraint gate.
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

**FAIL — the frozen criteria recommend KILL for the current architecture.** B3 is materially worse than the strongest baseline (B2) on quality point estimates:

- mean_precision_at_10: 0.7533 vs 0.88 (>0.10 worse)
- mean_ndcg_at_10: 0.8228 vs 0.9342 (>0.10 worse)
- violation rate 0.1133 vs 0.0333 (excess >0.01)

Per HYPOTHESIS_V2.md, FAIL triggers on 'materially worse matching/recall' regardless of human-time measurement, and the pre-committed mapping is: FAIL → recommend KILL. Since the baseline that wins here (B2) shares Forja's constraint engine and retrieval, the verbatim pre-committed sentence applies: **the current architecture does not demonstrate a defensible edge.** Sunk engineering cost carries zero weight in this decision.

Scope of the verdict: it attaches to this run's shared engine (see run metadata) and this corpus. Measuring HAM could not rescue a PASS: the PASS criteria require non-inferior quality, which is already violated.
