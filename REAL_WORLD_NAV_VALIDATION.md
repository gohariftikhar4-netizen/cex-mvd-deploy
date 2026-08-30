# REAL_WORLD_NAV_VALIDATION.md — external validation on real Norwegian job ads

**Track:** External validation (separate from the frozen Benchmark V2).
**Date:** 2026-08-29/30. **Status:** exploratory / descriptive.

> **This document changes nothing about Benchmark V2.** HYPOTHESIS_V2.md, the
> V2 thresholds, gold labels, scoring, B2/B3 prompts, the V2 datasets and the
> RED_TEAM_V2 verdict (B3 → KILL) are untouched and remain valid as the
> preregistered result they were.
>
> **Nothing here is ground truth.** No human has labelled a single real
> candidate–ad pair yet. Every B2 observation below is *descriptive*, and every
> "conflict" is a *suspicion raised by a pattern*, not a confirmed error. The
> blinded labelling batch that would turn these into evidence is prepared
> (§E) but not yet reviewed.

---

## A. What NAV officially provides

Full verified detail: [`forja/external/nav/NAV_SOURCE.md`](forja/external/nav/NAV_SOURCE.md).
Every fact there was confirmed against live endpoints or official docs; nothing
was assumed.

| Item | Verified value |
|---|---|
| Service | **NAV Job Vacancy Feed** (`pam-stilling-feed`) — the feed behind arbeidsplassen.nav.no |
| Docs | https://navikt.github.io/pam-stilling-feed/ |
| Base URL | `https://pam-stilling-feed.nav.no` |
| Endpoints | `GET /api/v1/feed` → `/api/v1/feed/{next_id}`; detail `GET /api/v1/feedentry/{uuid}`; token `GET /api/publicToken` |
| Auth | `Authorization: Bearer <JWT>`. The public-token endpoint returns **prose**, not a bare token — the JWT must be extracted. Public token rotates irregularly; a stable private token is issued on request to `nav.team.arbeidsplassen@nav.no`. |
| Terms | https://arbeidsplassen.nav.no/vilkar-api — **statistical/analytical use is explicitly permitted**; republished ads must be removed when they go inactive; personal data must be deleted when no longer needed. |

**Feed semantics (verified, and consequential):**

- The feed is an **event log, not a catalogue**. Each item is a state change;
  the same ad reappears whenever it is updated. Deduplication is the
  consumer's job.
- **Inactive ads are content-masked by NAV.** A detail fetch for a stopped ad
  returns only `{uuid, status, sistEndret}` — no title, employer or
  description. Verified directly: 263 of 2,263 detail fetches (11.6%) came
  back masked, and an ad listed ACTIVE on 24 Aug returned INACTIVE on 29 Aug.
  **Consequence: historical corpora cannot be rebuilt retroactively. Ad
  content must be captured close to publication.**
- Fields on an active ad: `title, jobtitle, description (HTML), published,
  expires, updated, applicationDue, applicationUrl, link, source, employer{},
  workLocations[], occupationCategories[], categoryList[] (ESCO/JANZZ/STYRK08
  with confidence scores), contactList[], engagementtype, extent, starttime,
  positioncount, sector`.
- **`contactList` is personal data.** Our adapter drops it by default; the
  stored corpus contains none.
- Transport note (environment-specific, not NAV's fault): HTTP/2 through this
  proxy fails; the adapter pins HTTP/1.1.

**The single most important structural fact:** NAV publishes **no
machine-readable requirement fields**. There is no field for required
certification, licence, language level, shift pattern, physical demand,
authorization or experience. Those requirements exist **only inside the
free-text HTML description**.

---

## B. What we observed in the real corpus

**Snapshot:** `nav_data/snapshot_20260829` — **2,000 ads**, captured
2026-08-29 21:41–22:00 UTC (anchor `If-Modified-Since: Tue, 25 Aug 2026`),
7,484 feed entries listed, 2,263 detail fetches, 263 masked-inactive skipped.
Reproducible via the recorded manifest; raw NAV payload preserved per ad for
traceability.

**Coverage** — 13 occupation families, all 15 counties + Svalbard:
Helse og sosial 863, Utdanning 344, Salg og service 303, Kontor og økonomi
211, Håndverkere 168, Reiseliv og mat 98, Transport og lager 78, Industri 71,
Bygg og anlegg 70, IT 53, Kultur 34, Sikkerhet 30, Natur og miljø 26.
Oslo 351, Vestland 281, Akershus 256, Rogaland 192, Innlandet 190, …
Heltid 1380 / Deltid 620; Fast 1303 / Vikariat 383 / other 314;
Offentlig 1027 / Privat 832. Sources: IMPORTAPI 1611, Stillingsregistrering
373, AMEDIA 14, EURES 2.

### B.1 Where requirements actually live (% of 2,000 ads)

| Dimension | **text-only** | ambiguous | both | structured-only | absent |
|---|---:|---:|---:|---:|---:|
| authorization | **34.8%** | 13.0% | 0.0% | 0.0% | 52.2% |
| shift/rotation | **17.3%** | 1.5% | 0.0% | 0.0% | 81.2% |
| working hours / night | **16.8%** | 4.8% | 0.0% | 0.0% | 78.5% |
| experience | **15.1%** | 62.5% | 0.0% | 0.0% | 22.4% |
| education | **14.4%** | 34.4% | 0.0% | 0.0% | 51.2% |
| language | **12.7%** | 39.5% | 0.0% | 0.0% | 47.9% |
| driver's licence | **6.4%** | 19.5% | 0.0% | 0.0% | 74.1% |
| certification | **3.7%** | 12.3% | 0.0% | 0.0% | 84.0% |
| physical | **2.8%** | 1.8% | 0.0% | 0.0% | 95.4% |
| travel | **1.4%** | 2.5% | 0.0% | 0.0% | 96.0% |
| geography | 0.1% | 0.1% | 41.6% | 57.6% | 0.5% |
| employment type | 0.0% | 0.0% | 53.4% | 46.5% | 0.1% |
| extent (part-time) | 0.0% | 0.0% | 51.2% | 48.8% | 0.0% |

**Read the `structured-only` column: it is 0.0% for ten of thirteen
dimensions.** Only geography, employment type and extent have any structured
representation at all.

**Method bounds (stated so the numbers are not over-read):** text-only rates
are computed by deterministic Norwegian regex over NAV's own description HTML
and are therefore **lower bounds** — a phrasing the patterns miss is counted
as "not specified". The `ambiguous` column is an **upper bound**: it flags a
requirement co-occurring with a hedge somewhere in the ad, and hedge
attribution is not verified. Observed failure of that heuristic: *"Førerkort
klasse B er et krav"* (hard) sitting beside *"Personlig egnethet vektlegges"*
(hedge) gets counted ambiguous.

### B.2 Consequence for the deterministic constraint spine

Running the **unmodified** V2 constraint engine against 400 real ads × 5
candidates, counting which dimensions can still fire:

| Functional (3/12) | Structurally dead (9/12) |
|---|---|
| `location_commute` (1,493 fires) | certifications, driving_license, language_norwegian, language_english, |
| `percent_position` (593) | overnight_travel, physical, salary, shifts, work_authorization |
| `deadline` (30) | |

**Forja's deterministic hard-constraint spine enforces 3 of 12 dimensions on
real NAV data.** The other nine have no structured field to read. This is the
central real-world finding of this track.

### B.3 Data-quality facts that break naive assumptions

- **Structured/text contradiction: ~5.5%** of ads — `extent=Heltid` while the
  text says "50 % stilling" (36 ads), `extent=Deltid` while text says 100%
  (23), `engagementtype=Fast` while text says "vikariat/midlertidig" (51).
- **`applicationDue` is not always a date: 325 ads (16.3%)** carry free text
  such as *"Snarest"*. Our V2 schema assumes ISO dates.
- **5.0%** of ads in a fresh feed already have a past application deadline.
- **Descriptions are 5.3× longer than our synthetic ads** (median 2,932 chars
  vs 557; p90 5,245 vs 657).
- **46.8% of ads carry at least one constraint type V2 never modelled**:
  politiattest/vandel **34.1%**, lønn etter tariff 11.5%, weekend-frequency
  ("hver 3. helg") 8.8%, MRSA/tuberculosis screening 3.3%,
  taushetsplikt/clearance 3.2%, own car 2.0%, vaccination 0.7%.

---

## C. What B2 does well on real ads

Run: `nav_data/validation_20260829` — B2 only (B3 archived, not run), 8
candidates chosen to stress text-borne dimensions, 400-ad subsample,
`z-ai/glm-5.3-flash` via OpenRouter route `deepinfra/fp8`, **cost $0.051**.

1. **It runs end-to-end on real data without adaptation.** All 8 candidates
   produced complete, well-formed, schema-valid top-10 lists. No crashes, no
   malformed output, no hallucinated job IDs.
2. **It reads the raw text and reasons about it.** B2's own claims frequently
   quote genuine requirement sentences from the ad body — the exact material
   the structured fields omit. This is why "raw job text available to the LLM"
   was made non-negotiable in MATCH_ENGINE_V1.md, and real data supports that.
3. **Its simple verification step measurably works.** Of the raw model
   claims, **5 of 89 (5.6%) quoted text that does not appear verbatim** in the
   records; after B2's verification step, **0 of 74 claims on emitted
   recommendations fail** the check. The cheap deterministic filter caught
   every fabricated quote.
4. **It is conservative where it is blind.** For the hardest candidate
   (Solveig — day-only, 6 eligible ads of 400 after filtering) it returned a
   short list rather than padding.

---

## D. What B2 fails on

1. **It recommends jobs whose text conflicts with a hard constraint.**
   Suspected-conflict rate across 80 recommendations, by measurement
   strictness:

   | Estimate | Rate | Method |
   |---|---:|---|
   | Raw detector | **63/80 (79%)** | broad patterns — **upper bound**, contains false positives |
   | Evidence-filtered | **54/80 (68%)** | conflict evidence must contain a real domain term |
   | Strictest independent patterns | **~15% of ranked items** | narrow, high-precision patterns only |

   The honest statement is: **somewhere between ~15% and ~79%, and we cannot
   narrow it without human labels.** Measured false-positive rates in the
   detector itself: shifts 22% (e.g. *"Hjul**skift**"* — wheel change —
   matching a shift pattern, the same substring-bug class fixed in V1),
   certification/authorization 37% weak evidence, language 0%.

   Unambiguous true positives do exist. Ingrid (nurse, **cannot work nights**)
   was recommended four ads explicitly requiring *"tredelt turnus"* /
   *"turnus vil inneholde noen nattevakter"*. Marta (**pending** Norwegian
   authorization) was recommended an ad stating *"Kvalifikasjonskrav: Norsk
   autorisasjon som helsefagarbeider"*.

2. **Evidence verification checks provenance, not relevance.** 100% of emitted
   claims quote real text — yet a claim asserting *"Dagtid uten krav om
   skiftarbeid"* was supported by the quote *"Nøyaktig og effektiv. Ha
   arbeidslyst og godt humør."* The quote is verbatim and the claim is
   unfounded. **The verification mechanism cannot detect this class of error**,
   which means the "machine-checkable evidence" property is weaker in
   production than V2 measured it to be.

3. **It ranks jobs its own reasoning rejects.** In several cases B2's claim
   text says the job breaks the candidate's constraint — one literally reads
   *"bryter kandidatens absolutte krav – må avvises"* — and the job is still
   ranked (in that instance #3). Detected in 10 of 39 raw ranked items (26%).
   The rerank stage has no mechanism to act on its own negative finding.

4. **Text-borne dimensions are pervasive, so the blind spot is not rare.**
   78 of 80 recommendations (97.5%) touch at least one text-borne dimension:
   experience 88%, language 71%, authorization 62%, driver's licence 51%,
   night work 45%, shift rotation 42%, education 40%.

5. **Adapter/geography weakness (ours, not B2's).** 46 of 80 recommendations
   used a fallback city because NAV's `workLocations` lacked a city we could
   map; 6.2% of ads have no usable city at all. Commute filtering — one of the
   three surviving deterministic dimensions — is therefore softer on real data
   than in V2.

---

## E. What requires human labelling

Prepared and ready: `nav_data/labeling_20260829` — **144 blinded pairs**
across the 8 candidates (B2-selected pairs plus 8 decoys per candidate so
reviewers cannot infer machine selection). Verified blinding: each record
contains exactly `{pair_uid, candidate, job}` — **no rank, score, system
identity, or machine-generated conflict flag**. Grading rubric is the frozen
0/1/2 scale; multiple independent reviewers per pair are supported.

Human adjudication is required to answer:

1. **Are the suspected conflicts real?** This decides whether B2's true
   violation rate is ~15% or ~79% — a decision-grade difference.
2. **Is a hedged requirement ("ønskelig med fagbrev") disqualifying?** 62.5%
   of ads hedge experience; no automated rule can settle this.
3. **Which text-only requirements are genuinely hard** vs. employer
   boilerplate (politiattest appears in 34.1% of ads — is it a filter or a
   formality?).
4. **Is a short conservative list better or worse** than a longer list with
   caveats, for an advisor's actual workflow?

---

## F. What remains unproven

- **B2's real accuracy.** No gold exists for real ads; every number in §C–D is
  descriptive. Nothing here confirms or refutes B2's fitness beyond "it runs
  and behaves plausibly".
- **Whether the conflicts are B2 errors or detector artifacts** (§D.1).
- **Engine sensitivity.** Only `glm-5.3-flash` was run against real ads
  (budget: $0.051 of $0.45 remaining). Opus 5 was not.
- **Scale.** 400 ads per candidate, 8 candidates, one snapshot, 96-hour
  window. Seasonality and regional effects are unmeasured.
- **Deduplication.** The feed is an event log; repeated ads were not
  deduplicated in this snapshot.
- **Everything about human active minutes.** No advisor has used this output.

---

## G. Is the V2 synthetic benchmark representative?

**No — it was materially easier than reality, in one specific and important
direction.**

| Property | Synthetic V2 | Real NAV | Effect |
|---|---|---|---|
| Ads with machine-readable requirements | **100%** | **0%** | V2 gave the deterministic spine work it does not have in production |
| Median description length | 557 chars | **2,932 chars** (5.3×) | Real text reasoning is a much larger job |
| Constraint dimensions the spine can enforce | 12/12 | **3/12** | V2 overstated deterministic safety |
| Structured/text contradiction | not modelled | **~5.5%** | unmodelled failure mode |
| Non-date deadline values | not modelled | **16.3%** | schema assumption breaks |
| Constraint types outside the taxonomy | 0% | **46.8%** | real ads carry requirements we never represented |

Two honest consequences, pulling in opposite directions:

1. **It strengthens the Match Engine v1 decision.** V2 chose B2 largely
   because its rerank reads raw ad text. Real data shows that is not merely an
   advantage but the **only** route to nine of twelve constraint dimensions.
   Had the synthetic corpus been realistic, B2 would have beaten B3 by more,
   not less.
2. **It weakens the claim that the deterministic spine is a major safety
   asset.** On real ads it enforces three dimensions. The V2 "zero violations
   from structurally-visible constraints" guarantee is largely vacuous in
   production, because in production the constraints are not structurally
   visible.

**The V2 KILL verdict for B3 is unaffected** — if anything the real-data
asymmetry would have widened B2's margin. But V2's absolute safety numbers
should not be quoted as production expectations.

---

## H. Bottom line

**Is B2 a credible base engine for the next phase? Provisionally yes, with one
named unresolved risk.** It runs unmodified on real Norwegian ads, reads the
text that carries the requirements, and its cheap verification demonstrably
removes fabricated quotes. That is a working foundation.

The unresolved risk is **constraint conflicts in emitted recommendations**,
somewhere between ~15% and ~79%, unresolvable without the human labelling
batch now prepared. Until those labels exist, B2 should be treated as a
**credible base engine with an unquantified safety gap**, not a validated one.

**No product claims are made here, and no tuning was performed to improve any
number in this document.**
