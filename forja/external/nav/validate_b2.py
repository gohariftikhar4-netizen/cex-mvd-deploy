"""Step 5: exploratory validation of B2 against real NAV ads.

    python3 -m forja.external.nav.validate_b2 --snapshot nav_data/snapshot_<date> \
        --provider openrouter --model z-ai/glm-5.3-flash --provider-route deepinfra/fp8 \
        --candidates cand_solveig,cand_omar,... --out nav_data/validation_<date>

B2 ONLY. B3 is archived and is not run, resurrected, or tuned here.

This produces DESCRIPTIVE observations, not scored results: there is no gold
for real ads. Specifically it measures, per candidate:

- does B2 return a sensible, complete, well-formed list at all;
- how many recommendations carry claims whose quotes actually appear in the
  NAV record (the same machine check the frozen scorer uses);
- how many recommended ads contain text-borne constraint signals that the
  deterministic filter could not see (from the Step-4 analyzer), i.e. the
  candidate-blind exposure surface;
- of those, how many plausibly CONFLICT with the candidate's hard constraints
  — flagged as SUSPECTED, never as confirmed error, because only a human can
  adjudicate real ad text.

Everything flagged is exported for blinded human labeling (Step 6).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

from ...llm import AnthropicClient, OpenRouterClient
from ...runlog import RunLogger
from ...schemas import Candidate, load_candidates_v2
from ...workflows.b2_production import run_b2
from ...match_engine import run_match_engine

ENGINES = {"b2": run_b2, "match_engine_v1": run_match_engine}
from ...workflows.common import claim_supported
from .analyze import classify_ad, plain_text
from .ingest import load_snapshot
from .to_jobs import nav_ad_to_job

# Candidate hard-constraint -> which analyzer dimensions could contradict it.
_CONFLICT_MAP = {
    "cannot_work_shifts": ["working_hours_night", "shift_rotation"],
    "no_overnight_travel": ["travel"],
    "physical_limitations": ["physical"],
    "driving_licenses": ["drivers_license"],
    "certifications": ["certification", "authorization"],
    "languages": ["language"],
    "percent_range": ["extent_parttime"],
}

_NIGHT = re.compile(r"natt|turnus|skift|døgnkontinuerlig|rotasjon|3-delt|tredelt", re.I)
_WEEKEND = re.compile(r"helg", re.I)
_EVENING = re.compile(r"kveld", re.I)


def _suspected_conflicts(cand: Candidate, ad: dict, cls: dict) -> list[dict]:
    """Candidate-specific SUSPICIONS from ad text. Never authoritative."""
    out = []
    text = plain_text(ad.get("description_html"))
    hc = cand.hard_constraints

    for shift in hc.cannot_work_shifts:
        pat = {"night": _NIGHT, "weekend": _WEEKEND, "evening": _EVENING}.get(shift)
        if pat and pat.search(text):
            m = pat.search(text)
            out.append({"dimension": "shifts", "candidate_constraint": f"cannot work {shift}",
                        "evidence": text[max(0, m.start() - 70):m.end() + 70].strip()})
    if hc.no_overnight_travel and cls["travel"]["state"] in ("text_only", "both", "ambiguous"):
        out.append({"dimension": "overnight_travel",
                    "candidate_constraint": "no overnight travel",
                    "evidence": (cls["travel"]["snippets"] or [""])[0]})
    for lim in hc.physical_limitations:
        if cls["physical"]["state"] in ("text_only", "both", "ambiguous"):
            out.append({"dimension": "physical", "candidate_constraint": lim,
                        "evidence": (cls["physical"]["snippets"] or [""])[0]})
    if cls["drivers_license"]["state"] in ("text_only", "both", "ambiguous") \
            and not cand.driving_licenses:
        out.append({"dimension": "driving_license",
                    "candidate_constraint": "no driving licence",
                    "evidence": (cls["drivers_license"]["snippets"] or [""])[0]})
    if cls["certification"]["state"] in ("text_only", "both") \
            or cls["authorization"]["state"] in ("text_only", "both"):
        snippet = ((cls["certification"]["snippets"] or []) +
                   (cls["authorization"]["snippets"] or []) + [""])[0]
        out.append({"dimension": "certification_or_authorization",
                    "candidate_constraint": "holds: " +
                    (", ".join(sorted(cand.valid_certification_ids)) or "none valid"),
                    "evidence": snippet})
    # Language: only flag when the candidate is below B2 Norwegian.
    from ... import taxonomy
    if not taxonomy.meets_language_level(cand.language_level("norwegian"), "B2") \
            and cls["language"]["state"] in ("text_only", "both", "ambiguous"):
        out.append({"dimension": "language",
                    "candidate_constraint": f"norwegian {cand.language_level('norwegian')}",
                    "evidence": (cls["language"]["snippets"] or [""])[0]})
    return out


def validate(snapshot_dir: Path, candidate_ids: list[str], out_dir: Path,
             provider: str, model: str | None, provider_route: str | None,
             max_ads: int, engine: str = "b2") -> dict:
    ads = load_snapshot(snapshot_dir)[:max_ads]
    candidates = {c.id: c for c in load_candidates_v2()}
    chosen = [candidates[c] for c in candidate_ids]

    out_dir.mkdir(parents=True, exist_ok=True)
    logger = RunLogger(out_dir)
    client = (OpenRouterClient(logger, model=model, provider_route=provider_route)
              if provider == "openrouter" else AnthropicClient(logger))

    # Precompute the analyzer view once (candidate-independent).
    cls_by_uuid = {ad["uuid"]: classify_ad(ad) for ad in ads}

    results: dict[str, dict] = {}
    for cand in chosen:
        jobs, notes_by_id, ad_by_id = [], {}, {}
        for i, ad in enumerate(ads):
            job, notes = nav_ad_to_job(ad, f"nav_{i:05d}", cand.location_city)
            jobs.append(job)
            notes_by_id[job.id] = notes
            ad_by_id[job.id] = ad
        print(f"  {cand.id}: running B2 over {len(jobs)} real ads ...", flush=True)
        out = ENGINES[engine](cand, jobs, logger, client)

        recs = []
        for rec in out["recommendations"]:
            job_id = rec["job_id"]
            ad = ad_by_id[job_id]
            job = next(j for j in jobs if j.id == job_id)
            cls = cls_by_uuid[ad["uuid"]]
            claims = rec.get("claims", [])
            unsupported = [c for c in claims if not claim_supported(c, cand, job)]
            text_borne = sorted(d for d, info in cls.items()
                                if info["state"] in ("text_only", "ambiguous"))
            recs.append({
                "rank": rec["rank"], "job_id": job_id,
                "nav_uuid": ad["uuid"], "title": ad.get("title"),
                "employer": ad.get("employer_name"),
                "nav_city": (ad.get("work_locations") or [{}])[0].get("city"),
                "score": rec.get("score"),
                # Carry the engine's machine-readable verdict into the report
                # so the audit trail is complete (Match Engine v1 only; the
                # frozen B2 arm has no such field and reports None).
                "constraint_verdict": rec.get("constraint_verdict"),
                "declared_conflicts": rec.get("conflicts", []),
                "n_claims": len(claims), "n_unsupported_claims": len(unsupported),
                "unsupported_claims": unsupported,
                "text_borne_dimensions": text_borne,
                "suspected_conflicts": _suspected_conflicts(cand, ad, cls),
                "adaptation_notes": notes_by_id[job_id],
            })

        n = len(recs) or 1
        results[cand.id] = {
            "candidate_id": cand.id,
            "n_recommendations": len(recs),
            "n_extended": len(out.get("extended", [])),
            "wall_time_s": out.get("wall_time_s"),
            "recommendations": recs,
            "observations": {
                "recs_with_unsupported_claims": sum(1 for r in recs if r["n_unsupported_claims"]),
                "unsupported_claim_rate": round(
                    sum(r["n_unsupported_claims"] for r in recs) /
                    max(1, sum(r["n_claims"] for r in recs)), 4),
                "recs_with_any_text_borne_dimension": sum(1 for r in recs if r["text_borne_dimensions"]),
                "recs_with_suspected_conflict": sum(1 for r in recs if r["suspected_conflicts"]),
                "suspected_conflict_rate": round(
                    sum(1 for r in recs if r["suspected_conflicts"]) / n, 4),
                "geography_fallback_used": sum(
                    1 for r in recs if r["adaptation_notes"]["geography_fallback_used"]),
            },
        }

    usage_total = 0.0
    for c in logger.model_calls:
        usage_total += c.get("reported_cost_usd") or 0.0

    report = {
        "kind": "EXPLORATORY / DESCRIPTIVE — no gold labels exist for real NAV ads",
        "snapshot": str(snapshot_dir),
        "generated_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "engine": {"provider": client.name, "model": client.model,
                   "route": provider_route},
        "arm": f"{engine} only (B3 is archived; not run)",
        "n_ads": len(ads),
        "candidates": candidate_ids,
        "cost_usd": round(usage_total, 4),
        "per_candidate": results,
        "caveats": [
            "No ground truth: nothing here is scored as correct or incorrect.",
            "'suspected_conflicts' are pattern-based suspicions from ad text and "
            "REQUIRE human adjudication; they are not confirmed B2 errors.",
            "Structured requirement fields are empty by design (NAV publishes "
            "none), so the deterministic filter can only enforce location, "
            "extent, engagement type and deadline.",
        ],
    }
    (out_dir / "b2_validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"n_ads": report["n_ads"], "cost_usd": report["cost_usd"],
                      "per_candidate": {k: v["observations"]
                                        for k, v in results.items()}},
                     ensure_ascii=False, indent=2))
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--candidates", required=True)
    p.add_argument("--provider", choices=["anthropic", "openrouter"], default="openrouter")
    p.add_argument("--model", default=None)
    p.add_argument("--provider-route", default=None)
    p.add_argument("--max-ads", type=int, default=400)
    p.add_argument("--engine", choices=sorted(ENGINES), default="b2")
    args = p.parse_args(argv)
    validate(Path(args.snapshot),
             [c.strip() for c in args.candidates.split(",") if c.strip()],
             Path(args.out), args.provider, args.model, args.provider_route,
             args.max_ads, args.engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
