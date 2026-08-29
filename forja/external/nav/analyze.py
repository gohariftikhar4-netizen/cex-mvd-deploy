"""Step 4: describe where constraints actually live in real NAV ads.

    python3 -m forja.external.nav.analyze --snapshot nav_data/snapshot_<date>

For each constraint dimension, classify every ad as:
  structured_only  — the fact is in a structured field, text is silent
  text_only        — the fact appears ONLY in the free-text description
  both             — present in both (agreeing or not; disagreement flagged)
  ambiguous        — the text hedges ("noe kveldsarbeid kan forekomme")
  not_specified    — neither

This is DESCRIPTIVE measurement of the corpus, computed by deterministic
Norwegian pattern matching over NAV's own HTML description. It is not a
matcher, not gold, and makes no claim about candidate fit.

Recall/precision of the patterns themselves is unvalidated — the counts are
lower bounds on text-borne constraints (a phrasing the patterns miss is
counted as not_specified, never as absent-in-reality). Human review is
required before treating any number here as ground truth; see
`--sample-out` which exports matched snippets for exactly that purpose.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .ingest import load_snapshot

# --------------------------------------------------------------------------
# Text normalization
# --------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def plain_text(html_str: str | None) -> str:
    if not html_str:
        return ""
    text = _TAG.sub(" ", html_str)
    text = html.unescape(text)
    return _WS.sub(" ", text).strip()


def fold(text: str) -> str:
    return text.lower()


# --------------------------------------------------------------------------
# Constraint dimensions: Norwegian surface patterns.
# Each dimension: positive patterns (the requirement is stated) and optional
# hedge patterns (stated but conditional/vague).
# --------------------------------------------------------------------------

DIMENSIONS: dict[str, dict] = {
    "working_hours_night": {
        "patterns": [r"\bnattevakt\w*", r"\bnattarbeid\w*", r"\bnattskift\w*",
                     r"\bnatt\b(?=[^.]{0,40}(vakt|skift|tjeneste|arbeid))",
                     r"\bturnus\w*", r"\bskiftarbeid\w*", r"\b3-delt\b",
                     r"\btredelt\b", r"\bdøgnkontinuerlig\w*", r"\brotasjon\w*",
                     r"\bhelgearbeid\w*", r"\bhver tredje helg\b", r"\bkveldsvakt\w*"],
        "hedges": [r"kan forekomme", r"må påregnes", r"noe kveld", r"noe helg",
                   r"etter avtale", r"kan bli aktuelt", r"i perioder"],
        "structured_fields": ["extent"],
    },
    "drivers_license": {
        "patterns": [r"\bførerkort\w*", r"\bsertifikat\b", r"\bklasse\s?[bcde]\b",
                     r"\bkl\.\s?[bcde]\b", r"\bfører kort\b", r"\bdriving licen\w*"],
        "hedges": [r"fordel", r"ønskelig", r"gjerne"],
        "structured_fields": [],
    },
    "certification": {
        "patterns": [r"\bfagbrev\w*", r"\bsvennebrev\w*", r"\bsertifisering\w*",
                     r"\btruckførerbevis\w*", r"\bmaskinførerbevis\w*",
                     r"\badr\b", r"\byrkessjåfør\w*", r"\bysk\b", r"\bkompetansebevis\w*",
                     r"\bg4\b", r"\bg11\b", r"\bfse\b", r"\bkurs\w* kreves"],
        "hedges": [r"fordel", r"ønskelig", r"gjerne", r"eller tilsvarende"],
        "structured_fields": [],
    },
    "authorization": {
        "patterns": [r"\bautorisasjon\w*", r"\bautorisert\b", r"\bhpr\b",
                     r"\bgodkjenning fra\b", r"\bhelsepersonell\w*",
                     r"\bpolitiattest\w*", r"\bvandelsattest\w*",
                     r"\bsikkerhetsklarer\w*", r"\btaushetserklær\w*"],
        "hedges": [r"må fremlegges", r"vil bli innhentet", r"før tiltredelse"],
        "structured_fields": [],
    },
    "education": {
        "patterns": [r"\bbachelor\w*", r"\bmaster\w*", r"\bhøyskole\w*", r"\bhøgskole\w*",
                     r"\buniversitet\w*", r"\bvideregående\b", r"\bfagskole\w*",
                     r"\butdanning\w*", r"\bstudiepoeng\b", r"\bcand\.\w+",
                     r"\bingeniør\w* utdanning", r"\brelevant utdanning\b"],
        "hedges": [r"eller tilsvarende", r"fordel", r"ønskelig",
                   r"realkompetanse", r"eller annen relevant"],
        "structured_fields": [],
    },
    "language": {
        "patterns": [r"\bnorsk\w*\b(?=[^.]{0,50}(språk|muntlig|skriftlig|beherske|krav|gode|god))",
                     r"\bbeherske\w* norsk\b", r"\bskandinavisk\w*",
                     r"\bengelsk\w*\b(?=[^.]{0,50}(språk|muntlig|skriftlig|beherske|krav|gode|god))",
                     r"\bspråkkrav\w*", r"\bb1\b", r"\bb2\b", r"\bc1\b",
                     r"\bnorskprøve\w*", r"\bbergenstest\w*"],
        "hedges": [r"fordel", r"ønskelig", r"gjerne"],
        "structured_fields": [],
    },
    "travel": {
        "patterns": [r"\breisevirksomhet\w*", r"\breising\b", r"\bovernatting\w*",
                     r"\bborteperiod\w*", r"\bpendl\w*", r"\bmå påregne reis\w*",
                     r"\breisedøgn\w*", r"\butstasjoner\w*"],
        "hedges": [r"noe reis", r"kan forekomme", r"må påregnes", r"i perioder"],
        "structured_fields": [],
    },
    "geography": {
        "patterns": [r"\barbeidssted\w*", r"\bstasjonert\b", r"\boppmøte\w*",
                     r"\bhjemmekontor\w*", r"\bfjernarbeid\w*", r"\bremote\b",
                     r"\bhybrid\w*", r"\bbosatt i\b", r"\bnærhet til\b"],
        "hedges": [r"mulighet for", r"etter avtale", r"delvis"],
        "structured_fields": ["workLocations"],
    },
    "physical": {
        "patterns": [r"\bfysisk krevende\b", r"\btunge løft\w*", r"\bløfte\w*",
                     r"\bgod fysikk\b", r"\bfysisk form\b", r"\bstå\w* mye\b",
                     r"\barbeid i høyden\b", r"\bhelseattest\w*", r"\bsyn\w* krav"],
        "hedges": [r"kan forekomme", r"tidvis", r"noe"],
        "structured_fields": [],
    },
    "experience": {
        "patterns": [r"\berfaring\w*", r"\b\d+\s*(års|år)\s+erfaring\b",
                     r"\bminimum \d+ år\b", r"\bnyutdannet\w*", r"\bpraksis\w*"],
        "hedges": [r"fordel", r"ønskelig", r"gjerne", r"ikke et krav",
                   r"nyutdannede oppfordres", r"eller tilsvarende"],
        "structured_fields": [],
    },
    "employment_type": {
        "patterns": [r"\bfast stilling\w*", r"\bvikariat\w*", r"\bmidlertidig\w*",
                     r"\bengasjement\w*", r"\bprosjektstilling\w*", r"\btilkalling\w*",
                     r"\bsesong\w*", r"\bprøvetid\w*"],
        "hedges": [r"mulighet for fast", r"med mulighet", r"kan bli"],
        "structured_fields": ["engagementtype"],
    },
    "extent_parttime": {
        "patterns": [r"\bdeltid\w*", r"\bheltid\w*", r"\b\d{2,3}\s?%\s?stilling",
                     r"\bstillingsprosent\w*", r"\b\d{2,3}\s?prosent\b",
                     r"\bredusert stilling\b"],
        "hedges": [r"etter avtale", r"fleksibel", r"kan diskuteres"],
        "structured_fields": ["extent"],
    },
    "shift_rotation": {
        "patterns": [r"\bturnus\w*", r"\bskiftordning\w*", r"\b2-skift\b",
                     r"\b3-skift\b", r"\brotasjon\w*", r"\b\d+-\d+ rotasjon\b",
                     r"\bvaktordning\w*", r"\bberedskapsvakt\w*"],
        "hedges": [r"kan forekomme", r"må påregnes", r"etter behov"],
        "structured_fields": [],
    },
}

_COMPILED = {
    dim: {"patterns": [re.compile(p, re.I) for p in spec["patterns"]],
          "hedges": [re.compile(h, re.I) for h in spec.get("hedges", [])],
          "structured_fields": spec.get("structured_fields", [])}
    for dim, spec in DIMENSIONS.items()
}


def _structured_present(ad: dict, dim: str) -> bool:
    """Is this dimension decidable from NAV's STRUCTURED fields alone?"""
    fields = _COMPILED[dim]["structured_fields"]
    if not fields:
        return False
    for f in fields:
        if f == "extent":
            v = (ad.get("extent") or "").strip()
            if dim == "extent_parttime" and v:
                return True
            # 'extent' says Heltid/Deltid — it never states night/shift work
        elif f == "engagementtype":
            if (ad.get("engagementtype") or "").strip():
                return True
        elif f == "workLocations":
            locs = ad.get("work_locations") or []
            if locs and any((l or {}).get("city") or (l or {}).get("municipal") for l in locs):
                return True
    return False


def classify_ad(ad: dict) -> dict:
    text = plain_text(ad.get("description_html"))
    low = fold(text)
    result = {}
    for dim, spec in _COMPILED.items():
        hits = []
        for pat in spec["patterns"]:
            for m in pat.finditer(low):
                s = max(0, m.start() - 60)
                e = min(len(text), m.end() + 60)
                hits.append(text[s:e].strip())
                break  # one snippet per pattern is enough
        in_text = bool(hits)
        hedged = in_text and any(h.search(low) for h in spec["hedges"])
        in_struct = _structured_present(ad, dim)
        if in_text and in_struct:
            state = "both"
        elif in_text:
            state = "ambiguous" if hedged else "text_only"
        elif in_struct:
            state = "structured_only"
        else:
            state = "not_specified"
        result[dim] = {"state": state, "hedged": hedged,
                       "snippets": hits[:3]}
    return result


def structured_completeness(ad: dict) -> dict:
    """Which key structured fields are actually populated?"""
    checks = {
        "extent": bool((ad.get("extent") or "").strip()),
        "engagementtype": bool((ad.get("engagementtype") or "").strip()),
        "sector": bool((ad.get("sector") or "").strip()),
        "positioncount": ad.get("positioncount") is not None,
        "starttime": bool((ad.get("starttime") or "").strip()),
        "application_due": bool((ad.get("application_due") or "").strip()),
        "work_location_city": bool(
            any((l or {}).get("city") for l in (ad.get("work_locations") or []))),
        "occupation_categories": bool(ad.get("occupation_categories")),
        "employer_name": bool(ad.get("employer_name")),
        "description_nonempty": len(plain_text(ad.get("description_html"))) > 200,
    }
    return checks


def analyze(snapshot_dir: Path, sample_out: Path | None = None) -> dict:
    ads = load_snapshot(snapshot_dir)
    manifest = json.loads((Path(snapshot_dir) / "manifest.json").read_text())

    dim_counts = {d: Counter() for d in DIMENSIONS}
    completeness = Counter()
    occ_l1 = Counter()
    occ_l2 = Counter()
    counties = Counter()
    extents = Counter()
    engagements = Counter()
    sectors = Counter()
    sources = Counter()
    desc_lengths = []
    samples = defaultdict(list)
    missing_fields_per_ad = []

    for ad in ads:
        cls = classify_ad(ad)
        for dim, info in cls.items():
            dim_counts[dim][info["state"]] += 1
            if sample_out and info["snippets"] and len(samples[dim]) < 12:
                samples[dim].append({
                    "uuid": ad.get("uuid"), "title": ad.get("title"),
                    "state": info["state"], "snippet": info["snippets"][0],
                    "structured_extent": ad.get("extent"),
                    "structured_engagementtype": ad.get("engagementtype"),
                })
        comp = structured_completeness(ad)
        n_missing = sum(1 for v in comp.values() if not v)
        missing_fields_per_ad.append(n_missing)
        for field, ok in comp.items():
            if ok:
                completeness[field] += 1
        for oc in (ad.get("occupation_categories") or []):
            occ_l1[oc.get("level1")] += 1
            occ_l2[oc.get("level2")] += 1
        for loc in (ad.get("work_locations") or []):
            counties[(loc or {}).get("county")] += 1
        extents[(ad.get("extent") or "(blank)")] += 1
        engagements[(ad.get("engagementtype") or "(blank)")] += 1
        sectors[(ad.get("sector") or "(blank)")] += 1
        sources[(ad.get("source") or "(blank)")] += 1
        desc_lengths.append(len(plain_text(ad.get("description_html"))))

    n = len(ads)

    def pct(c):
        return round(100.0 * c / n, 1) if n else 0.0

    dim_summary = {}
    for dim, counts in dim_counts.items():
        total_present = counts["text_only"] + counts["both"] + counts["ambiguous"]
        dim_summary[dim] = {
            "text_only_pct": pct(counts["text_only"]),
            "both_pct": pct(counts["both"]),
            "ambiguous_pct": pct(counts["ambiguous"]),
            "structured_only_pct": pct(counts["structured_only"]),
            "not_specified_pct": pct(counts["not_specified"]),
            "present_anywhere_pct": pct(total_present + counts["structured_only"]),
            "text_borne_pct": pct(counts["text_only"] + counts["ambiguous"]),
            "counts": dict(counts),
        }

    desc_lengths.sort()
    report = {
        "snapshot": str(snapshot_dir),
        "snapshot_manifest": {k: manifest.get(k) for k in
                              ("snapshot_started_utc", "if_modified_since_anchor",
                               "ads_captured", "entries_listed", "token_fingerprint")},
        "n_ads": n,
        "description_length_chars": {
            "median": desc_lengths[n // 2] if n else 0,
            "p10": desc_lengths[int(n * 0.1)] if n else 0,
            "p90": desc_lengths[int(n * 0.9)] if n else 0,
        },
        "structured_field_population_pct": {k: pct(v) for k, v in completeness.items()},
        "ads_with_all_key_fields_pct": pct(sum(1 for m in missing_fields_per_ad if m == 0)),
        "mean_missing_key_fields": round(sum(missing_fields_per_ad) / n, 2) if n else 0,
        "constraint_provenance": dim_summary,
        "coverage": {
            "occupation_level1": dict(occ_l1.most_common(25)),
            "occupation_level2_top": dict(occ_l2.most_common(20)),
            "counties": dict(counties.most_common(25)),
            "extent": dict(extents),
            "engagementtype": dict(engagements.most_common(12)),
            "sector": dict(sectors),
            "source_systems": dict(sources.most_common(15)),
        },
        "method_caveats": [
            "Deterministic Norwegian regex over NAV's own description HTML.",
            "Pattern recall is unvalidated: a phrasing the patterns miss is "
            "counted as not_specified, so text-borne percentages are LOWER BOUNDS.",
            "'ambiguous' = a requirement pattern co-occurring with a hedge phrase "
            "anywhere in the ad; hedge attribution to the specific requirement is "
            "not verified.",
            "No claim about candidate fit is made here. Not gold. Descriptive only.",
        ],
    }

    if sample_out:
        Path(sample_out).write_text(
            json.dumps(dict(samples), ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--sample-out", default=None,
                   help="write matched snippets for human verification")
    args = p.parse_args(argv)
    report = analyze(Path(args.snapshot),
                     Path(args.sample_out) if args.sample_out else None)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
