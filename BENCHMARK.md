# BENCHMARK.md — Benchmark V2: methodology, status, and limitations

Read in this order: [EDGE.md](EDGE.md) (the original frozen hypothesis) →
[HYPOTHESIS_V2.md](HYPOTHESIS_V2.md) (the frozen V2 pre-registration:
verdict thresholds, metric definitions, statistical bar, decision mapping) →
this document (how the machinery implements it) →
[RED_TEAM_V2.md](RED_TEAM_V2.md) (the current honest answers).

**Status of V1:** the V1 offline benchmark (Appendix A) demonstrated the
harness and Forja's deterministic guarantees, **not** an economic edge. Its
headline numbers are declared invalid for product decisions. V2 exists to
answer the only question that matters: does Forja beat a competent
production-grade AI baseline on *measured human work*, at non-inferior
quality?

## 1. The four arms

All arms use `claude-opus-5` for every model call (override `FORJA_MODEL`),
so the comparison measures architecture, not model quality.

| Arm | What it is | LLM calls per candidate (slice-1000) |
|---|---|---|
| **B0** | Frontier LLM baseline: profile + jobs in, structured output out. Corpus exceeds one context window, so it map-reduces: rank each chunk, then rank the union of finalists. | ~21 |
| **B1** | Strong LLM baseline: B0 + explicit hard-constraint checklist in every call + self-critique pass + second-pass verification that strikes items. The realistic zero-build competitor at its best. | ~23 |
| **B2** | Competent production baseline (what a good engineer builds in a sprint): deterministic hard-constraint filter → TF-IDF retrieval → one LLM rerank with evidence claims → simple deterministic verification (constraint recheck + quote check). | 1 |
| **B3** | Forja: profiling (gated LLM enrichment) → deterministic constraint filter → retrieval → structured matching with machine-checkable evidence → bounded LLM soft-preference stage → gap analysis + next actions → final constraint gate. | 2 |

B2 and B3 **share** the constraint engine and retrieval index by design: any
live B3−B2 delta isolates Forja's additional machinery (structured matching,
evidence/gaps, soft preferences, gate, orchestration) — the red-team Q4/Q5
question. Every arm emits the same contract: top-10 recommendations with
claims (`claim` + `source` + verbatim `quote`), plus an extended top-50 for
recall metrics.

## 2. Dataset

### Candidates (24)

`forja/data/candidates.json` (10, from V1) + `candidates_v2.json` (14 new),
loaded via `load_candidates_v2()`. The V2 additions carry deliberate
ambiguity: pending/expired credentials (nurse awaiting autorisasjon, driver
with lapsed YSK, offshore worker with lapsed GSK), foreign education under
recognition, B1/A2 Norwegian, an incomplete CV (skills only in prose), a
day-only single parent, contradictory salary anchors ("says 900k, floor
700k"), a career changer whose obvious occupation is not his goal, an
overqualified postdoc, temporary physical limitations, and down-shifters.
Schemas distinguish hard constraints (structured, absolute) from soft
preferences and uncertainty (`constraint_notes`, free text).

### Job corpus (generated, 10,000; scalable to 20,000)

`python3 -m forja.bench.corpusgen.generator --size 10000` deterministically
generates `benchmark_data/` from ~50 occupation families **not aligned with
Forja's taxonomy** (job skill strings are free text: "spring boot", "gerica
journalsystem", "pleie- og omsorgsarbeid"). Per candidate the generator
plants: strong and near matches, transferable-skill opportunities, a
single-dimension trap per applicable constraint (shifts, license, legally
required certification, language, citizenship, salary, percent, overnight,
physical, commute, deadline), **text-only traps** (the violated fact appears
only in the ad prose), near-identical pairs where only one satisfies
constraints, misleading titles, goal-mismatch jobs (eligible but against
stated goals), and experience traps. Corpus-wide: prompt-injection ads, stale
deadlines, ambiguous language, and ~40% of ads with incomplete structured
parses (`structured_completeness` partial/minimal — the structured fields are
an imperfect PARSE; the text is authoritative).

The generated corpus is not committed (regenerate byte-identically from the
seed; `corpus_meta.json` carries the sha256). Committed tests enforce the
structural invariants (every planted relation truth-consistent, every trap a
real violation, text-only traps invisible to the structured view).

### Gold and leakage separation

Ground truth lives in `benchmark_data/manifest.json`: the full requirement
set each ad was generated from, plus per-candidate relations/grades assigned
at generation time (rubric in `corpusgen/archetypes.py`). **Workflows never
see it**: matching (phase 1, `run_v2`) and scoring (phase 2, `score_v2`) are
separate programs, and AST-level tests forbid the workflow arms and the
phase-1 runner from importing the gold side at all. Constraint violations are
judged against manifest truth, never against any workflow's parser.
Additionally, `forja.bench.labeling` exports candidate–job pairs for **blind
human labeling** — reviewers see only the two records (never which workflow
selected the pair), multiple reviewers label independently, agreement
(exact + weighted kappa) is computed, and consolidated human labels can
validate the generator gold (`--validate-gold`).

## 3. Metrics (definitions frozen in HYPOTHESIS_V2.md)

Quality: P@10 (÷10 — a conservative 3-item list caps at 0.3), R@10, R@50,
NDCG@10, truth-judged violation rate, hallucination rate, unsupported
evidence rate (claims whose quote does not appear in the cited record —
identical check for every arm), opportunity loss (grade-2 missed from
top-10), false-negative rate (gold-relevant absent from top-50). Capped
recall variants are reported as diagnostics only.

**Primary economic metric: human active minutes per completed candidate case
(HAM)** — measured, not estimated. The V1 modeled review time is removed from
primary results. `python3 -m forja.bench.review start` runs a blinded console
(arms shuffled behind neutral case ids) that logs `review_started`,
`recommendation_opened` (with re-verification flag), `recommendation_rejected`,
`recommendation_modified`, `external_research_started/finished`,
`case_approved`. Active time sums inter-event gaps capped at 120 s. The
report computes per-arm HAM, correction/rejection/re-verification counts,
external searches, and the paired-bootstrap HAM ratio of B3 vs each baseline
against the frozen statistical bar. Cost, tokens, calls, and latency are
recorded per arm (secondary).

## 4. Adversarial evaluation

`python3 -m forja.bench.adversarial` runs hostile cases per arm: misleading
skill substrings ("varelogistikk"→"gis", "reactor"→"react"), false
certificates asserted in CV text, CV/job terminology mismatch, conflicting
constraints, near-identical pairs, prompt-injection ad text, stale postings,
and (live-only) fabricated-claim measurement. Deterministic guarantees are
asserted (PASS/FAIL); known trade-offs are recorded as INFO; LLM-arm behavior
is LIVE_ONLY until a live run. Every outcome lands in the report and the
decision log.

## 5. How to run

```bash
python3 -m pytest tests/ -q                                  # 116 tests, offline
python3 -m forja.bench.corpusgen.generator --size 10000      # build benchmark_data/
python3 -m forja.bench.run_v2 --mode offline --slice 1000    # phase 1 (no gold)
python3 -m forja.bench.score_v2 runs_v2/<run_id>             # phase 2 (gold)
python3 -m forja.bench.adversarial                           # hostile cases
python3 -m forja.bench.review start --run runs_v2/<run_id> --reviewer <name>
python3 -m forja.bench.review report --session review_sessions/<id>
python3 -m forja.bench.labeling export --run runs_v2/<run_id> --out labeling_batches/b1
python3 -m forja.bench.report --results ... --review ... --adversarial ...   # RED_TEAM_V2.md

# The decisive experiment (needs ANTHROPIC_API_KEY):
python3 -m forja.bench.run_v2 --mode live --slice 1000
```

**Projected live cost** (slice-1000 × 24 candidates, `claude-opus-5`,
measured prompt volumes): B0 ≈ $52, B1 ≈ $56, B2 ≈ $3, B3 < $1 — ≈ $115
total, treat as a lower bound (×1.5 with realistic output lengths). The full
10k corpus multiplies B0/B1 by ~10 (≈ $1,100–1,700) while B2/B3 stay under
$10 — the cost asymmetry of retrieval-first architectures is itself a
benchmark result. Start with `--slice 1000` and a candidate subset.

## 6. Current status (2026-08-29) — LIVE RESULTS EXIST

- **The live experiment has run.** Engine: `z-ai/glm-5.3-flash` via OpenRouter
  (pinned route `deepinfra/fp8`) for every arm — this is the model OpenRouter
  reveals as the former "Stealth Ox Alpha". Note the engine deviates from the
  pre-registered `claude-opus-5`; the verdict formally attaches to this
  engine. 1,000-job slice; B2/B3 on all 24 candidates, B0/B1 on the first 5
  (canonical order, credit-limited). Total live cost: **$0.20** (92 calls,
  3.66M input tokens of which 1.81M cache-served, 0.13M output).
- **Verdict per the frozen criteria: FAIL → KILL recommendation for the
  current architecture.** B3 is materially worse than B2 (which shares its
  constraint engine and retrieval): paired NDCG@10 delta −0.117
  (95% CI [−0.168, −0.069], B3 better in 2/24 candidates), P@10 −0.075
  (CI [−0.129, −0.025]), truth-judged violation rate +0.054 worse
  (CI [+0.025, +0.083]); Recall@50 ties (shared retrieval). B2's one LLM
  rerank call reads ad text the deterministic spine cannot, catching
  text-only constraint traps (9 vs 15 slipped) AND ranking better. See
  RED_TEAM_V2.md and `results/v2-glm53flash-20260829/`.
- Live adversarial suite (all four arms): 21 PASS / 1 FAIL (B0 recommended a
  stale posting) / 14 INFO. Live LLM arms resisted the prompt-injection ad
  and the false-certificate CV in the small-world tests.
- Still unmeasured: human active minutes (moot for a PASS — quality
  non-inferiority is already violated — but required before trusting any HAM
  claims elsewhere). Not yet run: the same benchmark on `claude-opus-5`
  (partial data for 14 candidates × 4 arms exists in the aborted Anthropic
  run's full call logs, reconstructable by deterministic replay).

## 7. Threats to validity

1. **Generator-derived gold shares authorship with the corpus.** Relations
   are family+eligibility rules set at generation time. All arms face the
   same gold, so arm *comparisons* are fair; absolute numbers are only as
   good as the generator's notion of relevance. Mitigation: the blind human
   labeling pathway with agreement stats and `--validate-gold`; run it on a
   sample before trusting absolute levels.
2. **Family-level relevance is coarse.** A candidate's primary-family job is
   graded relevant when truth-eligible even if its specific skill mix is
   odd; per-pair human labels are finer.
3. **Relocating candidates have huge relevant sets** (hundreds of eligible
   jobs nationwide), which caps uncapped Recall@K for everyone; comparisons
   remain valid, capped diagnostics are reported.
4. **Offline mode cannot evaluate LLM arms.** The lexical stand-in is shared
   plumbing validation only. Every arm-vs-arm claim requires the live run.
5. **HAM depends on reviewer behavior.** Blinding hides the arm label but not
   the artifact (richer artifacts ARE the treatment); reviewer variance is
   addressed by the paired design, ≥2 reviewers, and the bootstrap bar.
6. **Synthetic Norwegian text.** Template-generated ads are cleaner than real
   ones (even with injected ambiguity); real-ad parsing remains out of scope.
7. **Cost figures are projections** from offline prompt volumes at cached
   prices; the live run measures them properly.

---

## Appendix A — V1 (superseded)

V1 (10 candidates, 82 hand-written jobs, hand-authored labels, modeled review
time) remains runnable: `python3 -m pytest tests/ -q` covers it and
`python3 -m forja.run_benchmark --mode offline` reproduces it. Its
methodology and red-team assessment live in the git history (commit
`d7eb8b1`). Its conclusions stand: harness validity yes, economic edge not
demonstrated. V2 supersedes it everywhere they disagree.
