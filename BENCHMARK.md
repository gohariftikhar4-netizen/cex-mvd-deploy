# BENCHMARK.md — Methodology, Results, and Limitations

This document describes the Forja Work benchmark harness: what it measures, how,
and — most importantly — what it cannot yet prove. Read [EDGE.md](EDGE.md)
(frozen) first; this benchmark exists solely to test the claim stated there.

## 1. What is being compared

Two workflows produce up to 10 ranked job recommendations per candidate from the
same inputs (one candidate record + all 82 job records):

**Baseline — "competent advisor using a general-purpose LLM"**
([`forja/baseline.py`](forja/baseline.py)). The candidate's full profile —
including an explicit list of their absolute constraints — and every job ad are
rendered into one chat prompt asking for up to 10 ranked recommendations with
job IDs and reasons. Output is free text, parsed leniently. This is deliberately
a good-faith baseline (complete information, clear instructions), but it is a
*single-shot prompt*: it does not model an advisor iterating over several chat
turns. That is a real limitation (§6.5).

**Forja — the AI-native workflow** ([`forja/pipeline/`](forja/pipeline/)):
candidate profiling → deterministic hard-constraint filtering → TF-IDF semantic
retrieval → structured matching with machine-checkable evidence → gap analysis →
ranked recommendations with next actions. LLM reasoning is confined to profile
enrichment (suggestions validated by deterministic code against verbatim quotes)
and is structurally unable to affect constraint enforcement: the constraint
filter runs before any ranking, and a final gate re-checks every recommendation
before emission ([`forja/pipeline/recommend.py`](forja/pipeline/recommend.py)).

## 2. Dataset

Synthetic but deliberately realistic Norwegian data
([`forja/data/`](forja/data/)):

- **10 candidates** with diverse backgrounds and constraint structures: a nurse
  who cannot work nights, a laid-off senior developer with a salary floor and no
  driver's license, a Syrian-educated structural engineer at Norwegian B1 with
  pending NOKUT recognition, a retail manager without a degree, a fresh marine
  biology graduate willing to relocate anywhere, an electrician with a lifting
  restriction, a burned-out teacher who needs remote work from Tromsø, an EEA
  warehouse worker without a car license, a controller restricted to 60–80 %
  positions, and a 58-year-old CE truck driver who can no longer travel
  overnight.
- **82 job listings** in three deliberate strata:
  - *relevant jobs* (43 labeled pairs across candidates, grades 1–2),
  - *trap jobs*: attractive listings that violate **exactly one** hard
    constraint where possible (night shifts, license class, legally required
    certification, language level, citizenship/clearance, position percentage,
    salary floor, commute, physical demands, overnight travel) — 25 documented
    traps,
  - *distractors* from unrelated occupations with real requirements of their
    own.
- **Gold labels** ([`forja/data/labels.json`](forja/data/labels.json)): graded
  0/1/2 per (candidate, job) pair with written rationales. The rubric is in the
  file. Grade ≥ 1 requires zero hard-constraint violations *by definition* —
  in employment, an ineligible job is irrelevant regardless of skill fit.

Conventions: salaries are stated as full-time equivalents (Norwegian practice);
commute times come from a fixed city-pair table in
[`forja/taxonomy.py`](forja/taxonomy.py); skills, certifications, and language
levels use controlled vocabularies.

**Labeling discipline.** Labels were authored before the first benchmark run and
are never edited to improve any workflow's numbers (EDGE.md §6.2). Automated
integrity tests ([`tests/test_data_integrity.py`](tests/test_data_integrity.py))
verify rubric invariants only — never agreement with a workflow. During
development exactly one code change was made after seeing benchmark output: a
word-boundary fix in deterministic skill extraction ("varelogistikk" had falsely
granted the skill "gis" as a substring). That was a correctness bug producing
false evidence — the kind of failure this system exists to prevent — not metric
tuning; a genuine matcher misjudgment surfaced in the same run (Tarik →
job_038) was left in place and still costs Forja precision.

## 3. Metrics

Computed by [`forja/evaluation/evaluator.py`](forja/evaluation/evaluator.py),
which re-verifies everything against raw records and trusts nothing a workflow
reports about itself:

| Metric | Definition |
|---|---|
| Precision (listed) | share of listed items with grade ≥ 1; hallucinated items count against it |
| Strong precision | share with grade 2 |
| Recall | share of the candidate's gold-relevant jobs that appear in the list |
| NDCG@10 | graded ranking quality (gains 0/1/3, log2 discount, normalized) |
| Constraint violations | listed jobs that violate a hard constraint, re-derived from raw records via the same constraint engine (it is the domain's definition of eligibility) |
| Critical hallucinations | items that cannot be resolved to a real job, plus structured evidence claims that fail verification against the records |
| Processing time | wall-clock per workflow |
| Estimated human review time | modeled, see below |

**Review-time model** ([`forja/evaluation/review_time.py`](forja/evaluation/review_time.py)):
0.75 min per machine-verified recommendation (all evidence claims verified by
the evaluator AND constraint pass re-confirmed), 4 min per unverified (free-text)
recommendation, +6 min per constraint violation reaching the advisor, +8 min per
hallucination. **These parameters are assumptions, not measurements.** Every
reported figure carries a ×0.5 / ×2 sensitivity band, and per EDGE.md §5 the
review-time conclusion only counts if it survives scrutiny of these assumptions.
Note the structural asymmetry: free-text output can never earn "verified"
status, so this metric partially embeds the thesis it tests (§6.4).

## 4. Modes, logging, reproducibility

- `python -m forja.run_benchmark --mode offline` — both workflows run against a
  deterministic lexical stand-in for the LLM
  ([`forja/llm.py`](forja/llm.py) → `OfflineDeterministicClient`): it ranks job
  blocks by token overlap with the candidate section of the *same prompt text* a
  real model would receive, with no constraint awareness and no domain
  knowledge. Offline results validate the harness and Forja's deterministic
  spine. **They are not evidence about the edge** (§6.2).
- `python -m forja.run_benchmark --mode live` — real calls through the official
  `anthropic` SDK (default model `claude-opus-5`, override with `FORJA_MODEL`).
  Refusal fallbacks are deliberately disabled: in a benchmark, a refusal must
  surface as a logged failure of the named model, not a silent model switch.
- Every run writes `results.json`, `outputs.json`, `summary.md`,
  `model_calls.jsonl` (every prompt and response, in full) and
  `decisions.jsonl` (every constraint exclusion, retrieval shortlist, score
  breakdown, and final-gate action) under `runs/<run_id>/`, so any failure can
  be traced to the exact call or decision that caused it.

## 5. Current results — offline harness validation run (2026-08-28)

10 candidates × 82 jobs, mode `offline`, 77 automated tests passing.

| Metric | Baseline (lexical stand-in) | Forja |
|---|---|---|
| Mean precision (grade ≥ 1) | 0.42 | 0.98 |
| Mean strong precision (grade 2) | 0.29 | 0.67 |
| Mean recall of relevant jobs | 0.98 | 1.00 |
| Mean NDCG@10 | 0.87 | 0.98 |
| Hard-constraint violations (total) | 53 | **0** |
| Critical hallucinations (total) | 0 | **0** |
| Machine-verified recommendations | 0 | 44 |
| Est. human review time (total, min) | 718 (359–1436) | 33 (17–66) |
| Processing wall time (total, s) | 0.04 | 0.10 |

What this run legitimately shows:

- The harness works end to end and is deterministic (verified by tests).
- Forja's structural guarantees hold on real data: zero constraint violations
  and zero evidence hallucinations, independently re-verified by the evaluator;
  a test injects a violating job past the ranking stage and confirms the final
  gate drops and logs it.
- Forja's known misses are visible and instructive: it recommended a laboratory
  job to the electrician on partial skill-transfer credit (honest matcher
  misjudgment, grade 0, kept), and for the burned-out teacher it ranked the
  classroom job #1 because "wants out of the classroom" is a free-text nuance
  the deterministic spine does not model — precisely the kind of judgment the
  LLM stages exist to add in live mode.

What this run does **not** show: anything about a real LLM baseline. The 53
violations belong to a deliberately naive lexical ranker, not to Claude-class
models, which read constraint lists and would catch many of these. Do not quote
the offline table as evidence of the edge.

## 6. Threats to validity (read before believing anything above)

1. **Same-author circularity.** The gold labels, the taxonomy (including
   transferable-skill weights), and the matching logic were authored by the same
   entity. Forja's near-perfect offline precision against these labels is close
   to definitionally favorable and must be read as an upper bound, not a
   finding. Independent labeling (ideally by practicing Norwegian employment
   advisors) is required before any relevance claim is made publicly.
2. **The decisive experiment has not run.** No API credentials were available in
   the build environment, so no live run against a real LLM baseline exists yet.
   The edge claim is therefore currently **untested**, not supported.
3. **Constraint-fit is partially definitional.** Grade ≥ 1 requires constraint
   eligibility, and Forja enforces exactly that predicate with the same engine
   the evaluator uses. The genuinely contested ground is ranking quality among
   eligible jobs and whether a strong LLM baseline also achieves ~zero
   violations when properly prompted. The benchmark measures both, but only a
   live run can answer the second.
4. **The review-time model embeds part of the thesis.** Free-text output cannot
   earn "verified" status by construction, so the review-time gap partly follows
   from the parameter structure. The parameters are published, the sensitivity
   band is reported, and the claim should be validated by timing real advisors
   before it is used commercially.
5. **Simplifications.** Jobs arrive pre-parsed into structured requirements
   (real ads are messy prose — parsing is a large share of the real problem and
   is assumed solved here); retrieval is lexical TF-IDF, not embeddings; the
   baseline is single-shot rather than iterative; n=10 candidates gives no
   statistical power; traps are clean single-dimension violations while real
   ads are ambiguous; all data is synthetic and Norway-specific.
6. **Verification asymmetry.** Structured output exposes every claim to
   checking (and penalty); free text only exposes job identity. This currently
   *helps* the baseline's hallucination count and *hurts* its verified count.

## 7. How to run

```bash
python3 -m pytest tests/ -q          # 77 tests, no network needed
python3 -m forja.run_benchmark --mode offline   # harness validation
ANTHROPIC_API_KEY=... python3 -m forja.run_benchmark --mode live  # the real experiment
```

## 8. Red-team assessment (2026-08-28)

**Does the current system demonstrate the edge claimed in EDGE.md? No — not
yet.** Here is the honest position:

**Demonstrated:** an architecture in which hard employment constraints are
structurally impossible for an LLM to override (filter before ranking + final
gate + independent evaluator re-check, all tested); recommendations whose every
claim is machine-checkable, with fabricated evidence detected and counted; full
call/decision logging; a deterministic, reproducible harness with labeled traps
that measurably punish constraint violations. These are real engineering
properties of the product idea, and the offline run proves the deterministic
spine delivers them on this dataset.

**Not demonstrated:** the economic advantage over a competent human using a
general-purpose LLM — the actual claim. The comparison that matters (live
frontier-model baseline vs. Forja with live LLM stages) has not been executed.
Given how good frontier models are at reading an explicit constraint list in a
prompt, a realistic expectation is that a strong live baseline commits *far*
fewer than 53 violations — possibly close to zero on this small, clean dataset —
which would collapse the headline gap to (a) ranking quality among eligible
jobs, (b) verification cost, where Forja's advantage rests on a published but
unvalidated time model, and (c) robustness at scale (hundreds of ads exceed a
single prompt's practical limits — a dimension this benchmark does not yet
test).

**The single most likely way the edge is real** is the verification-economics
argument (evidence that can be spot-checked instead of re-derived), and the
single most likely way it is illusory is that "LLM + a two-line checklist
prompt + a human skim" is already good enough at realistic volumes. The current
benchmark cannot distinguish these outcomes.

**Required before any product decision:** (1) a live run of both workflows;
(2) an additional stronger baseline — the same LLM given the constraint
checklist and asked to self-verify, since that is the real zero-build
competitor; (3) independent relabeling of at least a sample of pairs;
(4) scale-up of the job set past single-prompt capacity; (5) timing data from
real advisors to replace the review-time assumptions. Until then, treat every
number in §5 as harness validation, not as evidence that Forja Work should be
built.
