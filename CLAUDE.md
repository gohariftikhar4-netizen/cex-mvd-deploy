# CLAUDE.md

Guidance for AI assistants working in this repository.

## Repository overview

This repo contains **two unrelated projects**. Do not mix them, share code
between them, or "refactor" one to resemble the other.

1. **Forja Work benchmark harness** (`forja/`, `tests/`, `EDGE.md`,
   `BENCHMARK.md`) — the active focus. A rigorous benchmark testing whether an
   AI-native employment workflow beats a competent human using a
   general-purpose LLM. Python 3.11, stdlib only (the `anthropic` SDK is an
   optional runtime dependency for live mode; `pytest` for tests).
2. **CEX microstructure recorder** (`cex_microstructure_mvd/`) — a frozen,
   self-contained deployment artifact that records public crypto market data
   on a VPS. Touch it only when explicitly asked.

There is no CI configured. Run tests locally before every push.

## Read this first

- **`EDGE.md` is frozen. Never edit it.** It states the hypothesis this repo
  exists to test, the frozen metric set, and the standing rules. If it seems
  wrong, say so to the founder — do not change the file.
- **`BENCHMARK.md`** documents methodology, current results, and threats to
  validity. Update its results/red-team sections when a new benchmark run
  changes the picture; keep its honesty: never soften the limitations.

## Forja Work

### Commands

```bash
python3 -m pytest tests/ -q                      # full test suite (fast, offline)
python3 -m forja.run_benchmark --mode offline    # deterministic harness-validation run
python3 -m forja.run_benchmark --mode live       # real LLM calls (needs credentials)
python3 -m forja.run_benchmark                   # auto: live if credentials, else offline
```

- Live mode uses the official `anthropic` SDK, zero-arg client (resolves
  `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / CLI profile). Default model
  `claude-opus-5`; override with `FORJA_MODEL`.
- Run artifacts land in `runs/<run_id>/` (git-ignored): `results.json`,
  `outputs.json`, `summary.md`, `model_calls.jsonl`, `decisions.jsonl`.

### Architecture and dependency rules

```
forja/
  taxonomy.py         controlled vocabularies: skills/aliases, transferable-skill map,
                      CEFR levels, license implications, city/commute table, sectors
  schemas.py          Candidate/Job dataclasses; strict validation (unknown keys rejected)
  textutil.py         deterministic tokenization
  runlog.py           JSONL logging (model_calls + decisions)
  llm.py              THE LLM BOUNDARY: ModelClient protocol, AnthropicClient,
                      OfflineDeterministicClient (neutral lexical stand-in)
  baseline.py         baseline workflow: one-shot advisor prompt + lenient parser
  pipeline/
    __init__.py       run_forja(): profile → filter → retrieve → match → gaps → recommend
    profiling.py      skills with provenance; LLM suggestions gated by verbatim-quote check
    constraints.py    SOLE AUTHORITY on hard constraints (11 dimensions), pure/deterministic
    retrieval.py      TF-IDF cosine retrieval (deterministic)
    matching.py       scoring with machine-checkable EvidenceItems; frozen weights/bars
    gaps.py           gap analysis + next actions (deterministic templates)
    recommend.py      ranking + DEFENSE-IN-DEPTH final constraint gate
  evaluation/
    gold.py           gold labels loader (grades 1/2 stored; 0 implicit)
    evaluator.py      re-verifies everything against raw records; trusts no workflow
    review_time.py    review-time economic model (published assumptions + sensitivity band)
  data/               candidates.json (10), jobs.json (82), labels.json (gold + rationales)
  run_benchmark.py    CLI entry point
```

**Dependency direction (enforce in review):** deterministic modules
(`pipeline/*`, `evaluation/*`, `schemas`, `taxonomy`, `textutil`) must never
import `llm.py`. Only `profiling.py` (via injected `ModelClient`),
`baseline.py`, and `run_benchmark.py` may trigger model calls. Everything else
must run byte-identically with no network.

### Non-negotiable invariants (from EDGE.md — tests enforce them)

1. **Hard constraints are never overridden by an LLM.**
   `forja/pipeline/constraints.py` is the only place eligibility is decided.
   The filter runs before ranking AND `recommend.py` re-checks every
   recommendation at emission (`final_gate_blocked` log on trip). Never add a
   code path that lets model output bypass, reorder, or soften these checks.
2. **Every recommendation carries evidence** — `EvidenceItem`s with dotted refs
   into the candidate/job records. The evaluator independently verifies each
   claim; a claim that fails verification counts as a critical hallucination.
   Never emit evidence that is not derived from a record lookup.
3. **LLM proposes, deterministic code disposes.** Model suggestions (e.g.
   profile enrichment) are accepted only after deterministic validation
   (known-vocabulary + verbatim-quote-in-source checks). Rejections are logged,
   never silently dropped.
4. **Log everything.** Every model call (full prompt + full response) goes to
   `model_calls.jsonl`; every intermediate decision to `decisions.jsonl`.
   New pipeline stages must log their decisions with candidate/job ids.
5. **Never optimize against the benchmark answers.** Do not tune scoring
   weights, thresholds, taxonomy weights, or prompts by peeking at
   `labels.json` or at per-pair benchmark results. Changes to matching logic
   need a written domain justification in the commit message. Gold labels
   change only to fix a demonstrable labeling error — document the error in
   `labels.json` `notes` and the commit message. (Precedent: fixing the
   substring-match extraction bug was legitimate because it produced false
   evidence; leaving Tarik→job_038 as a scoring miss was equally deliberate.)
6. **Scope guardrails:** no UI, no authentication, no billing, no production
   SaaS features, nothing unrelated to proving or disproving the edge.
7. **Determinism:** offline mode must be fully reproducible — stable sorts with
   id tie-breakers everywhere; no randomness; no wall-clock in logic (only in
   timing measurements).

### Dataset conventions (editing `forja/data/`)

- Validation is strict: unknown JSON keys, out-of-vocabulary skills/certs/
  cities/sectors, bad CEFR levels, or unpaired salary bounds fail loudly at
  load. Extend `taxonomy.py` first if a new term is genuinely needed.
- Salaries are **full-time-equivalent** NOK figures (Norwegian convention),
  compared directly against candidate floors.
- The commute table in `taxonomy.py` must list every genuinely commutable city
  pair; any unlisted pair is "not commutable" by definition.
- Job `certifications_required` holds only legally/absolutely required
  credentials; preferences belong in `nice_to_have_skills`.
- Trap jobs should violate **exactly one** hard constraint where possible, and
  every trap gets a `TRAP`-prefixed note in `labels.json`.
  `tests/test_data_integrity.py` enforces: every grade ≥ 1 pair passes all
  constraints; every TRAP note actually violates; every candidate keeps ≥ 3
  relevant jobs, ≥ 1 grade-2, and ≥ 2 traps. Run it after any data edit.
- Age is deliberately absent from structured fields (it may appear in free
  text): age must never become a filtering or scoring input.

### Testing expectations

77 tests in `tests/`. Every new pipeline capability needs: a unit test, a
determinism check if it touches ordering, and — if it can affect what gets
recommended — a test proving constraint violations still cannot escape.
`tests/test_end_to_end.py` asserts zero violations and zero evidence
hallucinations for Forja over the real dataset; keep that green, and treat any
red there as a release blocker, not a flaky test.

## CEX microstructure recorder (`cex_microstructure_mvd/`)

Persistent recorder for public OKX + Coinbase market data (trades/BBO/L2/
funding/OI) into partitioned parquet via systemd, with daily quality reports.
See `cex_microstructure_mvd/README_DEPLOY.md` for the full deploy/verify flow.

Hard guardrails (by design — do not "fix"): public market data only, no API
keys, no authentication, no order placement, no trading, no backtests, no
secrets read or written. Deployment is `sudo bash
cex_microstructure_mvd/scripts/bootstrap.sh` on a Debian/Ubuntu VPS; units are
hardened (`ProtectSystem=strict` etc.) and flush buffers on SIGTERM. The
package has no automated test suite — recorders were smoke-tested against live
feeds; validate changes with `scripts/daily_quality_report.py --date all`
(expect `VERDICT: PASS`).

## Git conventions

- Work happens on `claude/`-prefixed feature branches pushed with
  `git push -u origin <branch>`; do not push to `main` directly.
- Run the full test suite before any push. Never commit `runs/`,
  `__pycache__/`, or credentials.
- Commit messages: imperative summary line; for changes to matching/scoring/
  labels, include the domain justification required by the invariants above.
