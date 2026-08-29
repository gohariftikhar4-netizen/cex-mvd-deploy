# HYPOTHESIS_V2.md — Benchmark V2 pre-registration (FROZEN)

> Frozen 2026-08-29, before any V2 workflow was implemented and before any V2
> result existed. Per the founder's V2 brief: **do not optimize thresholds
> after seeing results.** Edits to this file after the freeze commit are
> forbidden except to fix typos that do not alter any number or definition.
> V1's offline headline numbers are declared **invalid for product decisions**.

## Primary hypothesis

**Forja (B3) reduces measured human active work per candidate by at least 50%
versus a competent production-grade AI-assisted workflow, while maintaining
non-inferior match quality, opportunity recall, ranking quality, and
constraint safety.**

| Verdict | Criterion |
|---|---|
| **STRONG PASS** | ≥ 2× measured advisor throughput versus the strongest baseline, with non-inferior quality |
| **PASS** | ≥ 50% reduction in human active minutes per completed case versus the strongest baseline, with non-inferior quality |
| **FAIL** | < 30% reduction in measured human active time versus the strongest baseline, **or** materially worse matching/recall |
| Between 30% and 50% | INCONCLUSIVE — the primary hypothesis is not met; report as such |

## Arms

- **B0** — Frontier LLM baseline: Claude Opus 5, candidate profile + available
  jobs, structured output.
- **B1** — Strong LLM baseline: B0 + explicit hard-constraint checklist +
  structured schema + self-critique + second-pass verification.
- **B2** — Competent production baseline: deterministic hard-constraint
  filtering + semantic retrieval + Claude Opus 5 reranking + structured
  evidence + simple verification. Represents what a competent AI engineer
  builds quickly.
- **B3** — Forja: deterministic constraint spine, retrieval, structured
  matching, evidence, gap analysis, verification, final constraint gate,
  orchestration.

All arms use the same underlying model (`claude-opus-5`) wherever a model is
used, so the comparison measures architecture, not model quality.

## Operational definitions (frozen)

**Human active minutes per completed candidate case (HAM)** — the primary
economic metric. Measured from review-console event logs
(`review_started`, `recommendation_opened`, `recommendation_rejected`,
`recommendation_modified`, `external_research_started`,
`external_research_finished`, `case_approved`). Active time = the sum of
inter-event gaps ≤ 120 seconds between `review_started` and `case_approved`;
gaps > 120 s count as idle (0). A case is completed only at `case_approved`.
Reviewers are blind to which arm produced the case. Modeled/estimated review
time is **removed from primary results** and may appear only as a clearly
labeled secondary appendix.

**Throughput** — completed cases per active hour = 60 / mean HAM.

**Strongest baseline** — the arm among B0/B1/B2 with the lowest mean HAM among
arms that meet the quality floors below; if none meets them, the baseline arm
with the best composite quality. B3-vs-each-arm comparisons are reported
regardless.

**Quality metrics** (per arm, evaluated against gold):

- Precision@10 = relevant (grade ≥ 1) in the top 10 ÷ **10** (a short list is
  not rewarded: recommending 3 jobs caps P@10 at 0.3).
- Recall@10 and Recall@50 = share of the candidate's gold-relevant jobs
  present in the top 10 / top 50.
- NDCG@10 (gains 0/1/3, log2 discount).
- Hard-constraint violation rate = violating recommendations ÷ recommendations
  listed, judged against **generator ground truth**, not against any
  workflow's own parser.
- Critical hallucination rate = unresolvable/nonexistent recommendations ÷ listed.
- Unsupported evidence rate = evidence claims failing verification against the
  records ÷ evidence claims made.
- Opportunity loss = share of grade-2 gold jobs absent from the top 10.
- False-negative rate = share of gold-relevant jobs absent from the top 50.

**Non-inferiority** (B3 vs a baseline arm) — all of:
P@10, R@10, R@50, NDCG@10 each no more than **0.05 absolute** below the arm;
violation rate ≤ the arm's and ≤ **0.01**; critical hallucination rate and
unsupported evidence rate ≤ the arm's + **0.01**; opportunity loss ≤ the arm's
+ **0.05**.

**Materially worse matching/recall** (triggers FAIL) — any of: P@10, R@10,
R@50, or NDCG@10 more than **0.10 absolute** below the strongest baseline;
violation rate exceeding it by > **0.01**; unsupported evidence rate exceeding
it by > **0.02**.

**Quality floors for baseline arms** (to qualify as "strongest baseline"):
within the non-inferiority margins above relative to the best-quality baseline
arm on P@10, R@50, and violation rate.

## Statistical bar (frozen)

Comparisons are paired per candidate case. The primary claim requires ≥ 20
completed cases per arm reviewed by ≥ 2 independent reviewers. Compute the
paired bootstrap (10,000 resamples) 95% CI of the HAM ratio B3 ÷ strongest
baseline:

- **PASS** requires point estimate ≤ 0.50 and CI upper bound < 0.70.
- **STRONG PASS** requires point estimate ≤ 0.50 and CI upper bound ≤ 0.55.
- **FAIL** is declared when the point estimate is > 0.70, or a materially-worse
  quality condition holds on point estimates.

Cost, tokens, model calls, and latency are recorded and reported per arm but
are secondary: they inform the economics narrative, not the verdict.

## Decision mapping (pre-committed)

- PASS/STRONG PASS **and** the ablation shows the advantage comes from parts
  B2 cannot trivially absorb → a BUILD case exists.
- PASS but the advantage disappears when the responsible components are added
  to B2 → PIVOT: the value is the components, not the architecture.
- FAIL → recommend KILL for the current architecture. Sunk engineering effort
  is explicitly not a reason to continue.
- If B2 performs essentially as well as B3, the report must state plainly that
  the current architecture does not demonstrate a defensible edge.

## Leakage rules (restated, enforced by tests)

Dataset generation, gold labeling, matching, and scoring are separated. No
matching workflow may read gold labels or the generator manifest. Gold labels
derive from generator ground truth and from blind human labeling (reviewers
never see which workflow selected a pair; multiple independent labels per pair
are supported). Forja must not be tuned against individual labeled examples.
