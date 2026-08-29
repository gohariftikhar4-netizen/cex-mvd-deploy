# RED_TEAM_V2.md — Benchmark V2 red-team report

_Generated 2026-08-29 by `python -m forja.bench.report`. Verdict rules are frozen in [HYPOTHESIS_V2.md](HYPOTHESIS_V2.md); this report never answers a question the evidence cannot support._

## Evidence available

- Quality results: LIVE run `merged-glm53flash-20260829` — 1000 jobs, 24 candidates, model `z-ai/glm-5.3-flash`
- Measured human time: **none — no review sessions have been run**
- Adversarial suite: mode live, {'INFO': 14, 'PASS': 21, 'FAIL': 1}

| Metric | B0 | B1 | B2 | B3 |
|---|---|---|---|---|
| P@10 | 0.3 | 0.44 | 0.8083 | 0.7333 |
| R@10 | 0.1092 | 0.2262 | 0.6665 | 0.6181 |
| R@50 | 0.1092 | 0.2262 | 0.9446 | 0.9444 |
| NDCG@10 | 0.2904 | 0.5244 | 0.9357 | 0.8186 |
| Violation rate | 0.4 | 0.0 | 0.0833 | 0.1375 |
| Unsupported evidence | 0.358 | 0.211 | 0.0 | 0.0 |
| Opportunity loss | 0.7718 | 0.6453 | 0.1137 | 0.1887 |
| Cost (USD) | 0.07 | 0.07 | 0.05 | 0.01 |

Cases per arm: {'B0': 5, 'B1': 5, 'B2': 24, 'B3': 24}. Arms with unequal case counts are not strictly paired; the B3-vs-B2 comparison uses the full paired set.

## Q1. Does Forja beat B2?

Quality non-inferiority of B3 vs B2: **FAILS**.
Failing margins: mean_precision_at_10: 0.7333 vs 0.8083; mean_ndcg_at_10: 0.8186 vs 0.9357; violation rate 0.1375 (baseline 0.0833); opportunity loss 0.1887 vs 0.1137
**UNANSWERED.** Requires measured human review sessions (`python -m forja.bench.review start ...`), ≥20 completed cases per arm from ≥2 reviewers. Quality alone cannot answer Q1: the primary metric is human active minutes.

## Q2. By how much?

See the table above for quality deltas; HAM deltas are unmeasured — **UNANSWERED.** Requires measured human review sessions (`python -m forja.bench.review start ...`), ≥20 completed cases per arm from ≥2 reviewers.

## Q3. Is the improvement statistically and economically meaningful?

**UNANSWERED.** Requires measured human review sessions (`python -m forja.bench.review start ...`), ≥20 completed cases per arm from ≥2 reviewers.
Economics context (secondary): per-candidate model cost — B0 $0.01, B1 $0.01, B2 $0.00, B3 $0.00

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

**FAIL — the frozen criteria recommend KILL for the current architecture.** B3 is materially worse than the strongest baseline (B2) on quality point estimates:

- mean_ndcg_at_10: 0.8186 vs 0.9357 (>0.10 worse)
- violation rate 0.1375 vs 0.0833 (excess >0.01)

Per HYPOTHESIS_V2.md, FAIL triggers on 'materially worse matching/recall' regardless of human-time measurement, and the pre-committed mapping is: FAIL → recommend KILL. Since the baseline that wins here (B2) shares Forja's constraint engine and retrieval, the verbatim pre-committed sentence applies: **the current architecture does not demonstrate a defensible edge.** Sunk engineering cost carries zero weight in this decision.

Scope of the verdict: it attaches to this run's shared engine (see run metadata) and this corpus. Measuring HAM could not rescue a PASS: the PASS criteria require non-inferior quality, which is already violated.
