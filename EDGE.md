# EDGE.md — The Forja Work Edge Hypothesis

> **Provenance note (not part of the hypothesis).** The founding brief for Forja Work
> instructed: *"Read EDGE.md first and treat it as frozen."* No EDGE.md existed in this
> repository or its git history at that time (2026-08-28). This file was therefore
> reconstructed directly from the founder's written brief, adding only the minimal
> operationalization needed to make the hypothesis testable. **From the commit that
> introduces this file onward, it is frozen: do not edit it.** If the founder's original
> EDGE.md surfaces, it replaces this file wholesale in a dedicated commit; the benchmark
> must then be re-read against it.

---

## 1. The claim under test

**An AI-native employment workflow (Forja) has a real economic advantage over a
competent human advisor using a general-purpose LLM.**

That is the entire bet. Forja Work is only worth building as a product if this claim
survives honest measurement. Nothing in this repository exists to make Forja look good;
everything exists to find out whether the claim is true.

"AI-native workflow" means: structured data models, deterministic hard-constraint
enforcement, retrieval, structured matching with evidence, gap analysis, and ranked
recommendations — with LLM reasoning confined to the places it adds value and excluded
from the places it causes harm.

"Competent human using a general-purpose LLM" means: a skilled advisor who pastes a
candidate's profile and the available job listings into a general chat LLM and asks for
ranked recommendations with reasons. This is the realistic zero-build alternative; it is
free to adopt and improves every time frontier models improve.

## 2. Why the edge might exist

1. **Hard employment constraints are not a language problem.** Shift bans, driving-license
   classes, legally required authorizations (autorisasjon, fagbrev), language-level
   requirements, commute limits, position percentage, right-to-work — these are boolean
   facts. A chat LLM enforces them probabilistically; a deterministic filter enforces
   them absolutely. Every violated constraint that reaches a candidate destroys trust and
   wastes advisor time.
2. **Verifiable evidence changes the economics of review.** A free-text recommendation
   must be re-checked by a human end to end. A recommendation whose every claim carries a
   machine-checkable pointer into the candidate and job records can be spot-checked in
   seconds. The product is not the ranking; it is the *reduction in human verification
   cost per trustworthy recommendation*.
3. **Structured intermediates compound.** Profiles, gap analyses, and match evidence are
   reusable state. A chat transcript is not.

## 3. Why the edge might NOT exist

1. Frontier LLMs are already very good at CV–job matching in a single prompt, and get
   better for free.
2. The deterministic scaffolding may add engineering cost without measurably beating the
   single-prompt baseline on outcomes that matter.
3. If the baseline's error rate is already low, verification savings may be too small to
   support a business.

This document deliberately records both directions. A benchmark that cannot find the
second list is not a benchmark.

## 4. What must be measured (frozen metric set)

For each candidate, each workflow produces up to 10 ranked job recommendations with
reasons. The evaluator measures, per the founding brief:

1. **Top-10 relevance** — how many recommended jobs are genuinely worth the candidate's
   pursuit (against a gold-labeled dataset).
2. **Constraint violations** — recommendations that break a hard employment constraint.
   The acceptable number in a trustworthy product is zero.
3. **Critical hallucinations** — recommended jobs that do not exist in the dataset, or
   evidence claims that fail verification against the records.
4. **Ranking quality** — whether the strongest matches rank highest (graded, e.g. NDCG).
5. **Processing time** — wall-clock time to produce recommendations.
6. **Estimated human review time** — modeled cost for a human advisor to verify the
   output before it reaches a candidate. This is the economic core.

## 5. Falsification criteria

The edge claim is **supported** only if, on the benchmark, the Forja workflow
simultaneously:

- produces **zero hard-constraint violations** while the baseline produces more than zero;
- matches or beats the baseline on Top-10 relevance and ranking quality;
- produces **zero critical hallucinations** in structured evidence;
- yields a materially lower estimated human review time per candidate (the review-time
  model and its assumptions must be published with the results).

The edge claim is **weakened or refuted** if the baseline matches Forja on violations and
relevance, or if Forja's advantage exists only under review-time assumptions that do not
survive scrutiny. In that case the honest conclusion is that the edge is not yet
demonstrated — and that conclusion must be reported as such, not argued away.

## 6. Standing rules (frozen, from the founding brief)

1. Do not build a production SaaS until the edge is demonstrated. No UI, no
   authentication, no billing.
2. Do not optimize against the benchmark answers manually. Scoring logic changes must be
   justified by domain reasoning, never by peeking at gold labels.
3. Keep deterministic business logic separate from LLM reasoning.
4. Every recommendation must contain evidence explaining why it was made.
5. Hard employment constraints must never be overridden by an LLM.
6. Log model calls and intermediate decisions so failures can be inspected.
7. Prefer simple architecture over unnecessary frameworks.
8. Build nothing unrelated to proving or disproving the edge.
9. Do not alter this file.
