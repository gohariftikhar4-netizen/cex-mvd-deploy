# MATCH_ENGINE_V1.md — the frozen matching architecture

**Status: FROZEN as of 2026-08-29.** Benchmark V2 tested whether Forja's
elaborate matching architecture (B3) beats a competent production baseline
(B2) and it does not — on two independent model engines. Matching is now a
**solved, frozen component**. Do not build more matching AI (see §4).

## 1. Why this is frozen — the evidence

Benchmark V2 ran four arms on the same 1,000-job slice, same corpus, same
gold, same scoring. B2 = deterministic hard-constraint filter → retrieval →
**one** structured LLM rerank with raw job text → machine-checkable evidence
→ simple deterministic verification. B3 = the full Forja pipeline (B2's spine
plus structured matching, transferable-skill scoring, gap analysis, a bounded
soft-preference LLM stage, and a final constraint gate).

**B3 lost to B2 on every quality and safety axis, on both engines:**

| Paired B3 − B2 (95% CI) | Claude Opus 5 (n=15) | GLM-5.3 Flash (n=24) |
|---|---|---|
| Precision@10 | −0.127 [−0.193, −0.073] | −0.075 [−0.129, −0.025] |
| NDCG@10 | −0.111 [−0.177, −0.047] | −0.117 [−0.169, −0.069] |
| Strong precision@10 | −0.067 [−0.140, 0.000] | −0.079 [−0.133, −0.029] |
| Recall@50 | +0.002 [−0.011, +0.017] (tie) | −0.000 (tie) |
| Constraint violation rate | +0.080 worse [+0.033, +0.133] | +0.054 worse [+0.025, +0.083] |
| Unsupported evidence | 0.000 (both clean) | 0.000 (both clean) |

Direction agrees on all six metrics across both engines. Recall ties because
B2 and B3 share the retrieval index. The decisive gap is that B2's single
rerank call reads the **raw job text**, catching text-only constraint traps
(the ~40% of ads whose structured parse is incomplete) that B3's
deterministic spine cannot see — while B3's extra ranking machinery actively
*hurts* ordering. A stronger engine (Opus 5) lifted the pure-LLM arms (B0/B1)
dramatically but did **not** rescue B3 relative to B2: the elaborate
architecture is not what a better model rewards.

Per the frozen HYPOTHESIS_V2.md decision mapping, both engines return
**FAIL → KILL** for the B3 architecture. Sunk engineering effort carried zero
weight in this decision.

**Verdicts:**
- **MATCHING EDGE: NOT CONFIRMED.**
- **B3 ARCHITECTURE: ARCHIVE.** (`forja/pipeline/matching.py`'s scoring,
  `gaps.py`, `softpref.py`, and the B3 orchestration are retained in-tree for
  provenance and V2 reproducibility, but are not the basis of the product
  match engine and receive no further investment.)

## 2. Match Engine v1 — the specification (this is B2)

The production match engine is exactly the B2 architecture, no more:

1. **Deterministic hard-constraint filter** (`forja/pipeline/constraints.py`,
   unchanged): the sole authority on eligibility, applied to structured
   fields before anything ranks. This is the one piece of Forja's spine that
   earns its place — it is cheap, exact, and auditable.
2. **Retrieval** (`forja/pipeline/retrieval.py`): shortlist eligible jobs by
   similarity to the candidate. (Lexical TF-IDF today; an embedding index is
   a drop-in behind the same interface if retrieval ever becomes the
   bottleneck — measure first.)
3. **Raw job text available to the LLM**: the rerank prompt contains the
   actual ad text, not just parsed fields. This is what lets the model catch
   requirements that never made it into structured data. It is the single
   most important design choice and is non-negotiable.
4. **One strong structured rerank stage**: a single LLM call returns a ranked
   list with scores, constrained by a JSON schema. One call — not a
   map-reduce, not a critique loop, not a multi-stage pipeline.
5. **Machine-checkable evidence**: every recommendation carries claims with
   verbatim quotes into the candidate/job records; a claim whose quote is not
   found is a defect the scorer counts.
6. **Simple verification**: re-run the deterministic constraint check on each
   recommended job and drop unsupported claims. Nothing more elaborate.

Reference implementation: `forja/workflows/b2_production.py`. When Match
Engine v1 is extracted into product code, that file is the source of truth
for the algorithm.

## 3. What was archived and why it did not help

- **Structured skill matching with transferable-skill weights**
  (`matching.py`): added ranking noise without beating a rerank that reads
  the text; taxonomy coverage gaps hurt more than the weights helped.
- **Gap analysis / next actions** (`gaps.py`): useful *product surface*, but
  not a matching-quality lever — revisit only as a workflow feature in V3,
  never as a matcher.
- **Bounded soft-preference LLM stage** (`softpref.py`): the capped,
  quote-gated preference adjustment did not move ranking quality enough to
  justify a second model call.
- **Final constraint gate** (`recommend.py`): harmless defense-in-depth, but
  B2's simple post-filter achieves the same safety at lower complexity. The
  gate's *idea* (re-check before emit) survives in B2's verification step.

## 4. Standing rule: stop building matching AI

Matching is frozen. **Do not add matching modules, prompt variants, or
ranking stages** in pursuit of a matching edge. There is no evidence one
exists, and two engines agree. Reopen matching only if **new external data**
(real advisor usage, real candidate outcomes, a real corpus) shows a concrete
matching failure that B2 cannot handle — not on a hunch, and never by tuning
against this benchmark.

The edge search now moves off matching entirely and onto the unit economics
of the whole employment-support workflow. See `BENCHMARK_V3_SPEC.md`.
