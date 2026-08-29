"""Step 6: export blinded candidate–ad pairs from real NAV data for
independent human labeling.

    python3 -m forja.external.nav.label_export --validation nav_data/validation_<date> \
        --snapshot nav_data/snapshot_<date> --out nav_data/labeling_<date>

NO FAKE GOLD. Nothing B2 produced is treated as correct. The reviewer sees
only the candidate record and the real NAV ad, never:
  - that a machine selected the pair,
  - the rank or score it was given,
  - which system produced it,
  - any suspected-conflict flag we computed.

Decoy pairs (ads B2 did NOT recommend) are mixed in at a fixed ratio so a
reviewer cannot infer selection from the mere presence of a pair, and so the
resulting labels can measure both false positives and misses.

Two label tasks per pair, because real ads need both:
  1. RELEVANCE  0/1/2 on the same frozen V2 rubric wording.
  2. ELIGIBILITY yes/no/unclear — does the ad text state any requirement the
     candidate cannot meet? This is what V2's synthetic corpus made trivial
     and real ads do not.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from pathlib import Path

from ...baseline import render_candidate
from ...schemas import load_candidates_v2
from .analyze import plain_text
from .ingest import load_snapshot

RUBRIC = """# Blindmerking av kandidat–stilling-par (ekte NAV-annonser)

Du vurderer ETT par: én kandidat og én ekte stillingsannonse fra NAV.
Du får ikke vite hvordan paret ble valgt ut, og skal ikke forsøke å gjette det.

## Oppgave 1 — RELEVANS (grade)
  2 = sterk match: annonsen bryter INGEN av kandidatens absolutte krav, og
      kjernekompetansen treffer så godt at du ville satt den øverst.
  1 = verdt å søke: ingen brudd på absolutte krav, og en troverdig hel eller
      delvis/overførbar vei inn — du ville tatt den med, med forbehold.
  0 = ikke verdt kandidatens tid: bryter minst ett absolutt krav ELLER er
      faglig/målmessig feil.

## Oppgave 2 — KVALIFISERBARHET (eligibility)
  ja      = ingenting i annonseteksten utelukker kandidaten
  nei     = annonsen stiller et krav kandidaten IKKE oppfyller
            (skriv hvilket i 'blocking_requirement')
  uklart  = annonsen er for vag til å avgjøre
            (skriv hva som er uklart i 'blocking_requirement')

## Oppgave 3 — HVOR STOD KRAVET? (requirement_location)
  strukturert / fritekst / begge / ingen
  Dette er det viktigste enkeltspørsmålet for oss: står det avgjørende kravet
  i de strukturerte feltene øverst, eller bare inne i annonseteksten?

Fyll ut answers-malen. Merk parene uavhengig av andre.
"""


def pair_uid(candidate_id: str, nav_uuid: str) -> str:
    return hashlib.sha1(f"{candidate_id}|{nav_uuid}".encode()).hexdigest()[:12]


def render_nav_ad(ad: dict) -> str:
    """Reviewer-facing rendering: NAV's structured facts, then the real text."""
    lines = [f"Tittel: {ad.get('title')}",
             f"Arbeidsgiver: {ad.get('employer_name') or '(ikke oppgitt)'}"]
    locs = [f"{(l or {}).get('city')} ({(l or {}).get('county')})"
            for l in (ad.get("work_locations") or [])]
    lines.append("Arbeidssted: " + (", ".join(filter(None, locs)) or "(ikke oppgitt)"))
    lines.append(f"Omfang: {ad.get('extent') or '(ikke oppgitt)'}")
    lines.append(f"Ansettelsesform: {ad.get('engagementtype') or '(ikke oppgitt)'}")
    lines.append(f"Sektor: {ad.get('sector') or '(ikke oppgitt)'}")
    lines.append(f"Antall stillinger: {ad.get('positioncount') or '(ikke oppgitt)'}")
    lines.append(f"Søknadsfrist: {ad.get('application_due') or '(ikke oppgitt)'}")
    for oc in (ad.get("occupation_categories") or []):
        lines.append(f"Yrkeskategori: {oc.get('level1')} / {oc.get('level2')}")
    lines.append("")
    lines.append("--- ANNONSETEKST (ordrett fra NAV) ---")
    lines.append(plain_text(ad.get("description_html")))
    return "\n".join(lines)


def export(validation_dir: Path, snapshot_dir: Path, out_dir: Path,
           decoys_per_candidate: int = 8, seed: int = 23) -> dict:
    report = json.loads((validation_dir / "b2_validation.json").read_text(encoding="utf-8"))
    ads = {a["uuid"]: a for a in load_snapshot(snapshot_dir)}
    candidates = {c.id: c for c in load_candidates_v2()}
    rng = random.Random(f"navlabel:{seed}")

    pairs: list[tuple[str, str]] = []
    for cand_id, res in report["per_candidate"].items():
        recommended = [r["nav_uuid"] for r in res["recommendations"]]
        pairs += [(cand_id, u) for u in recommended]
        pool = [u for u in ads if u not in set(recommended)]
        pairs += [(cand_id, u) for u in rng.sample(pool, min(decoys_per_candidate, len(pool)))]

    rng.shuffle(pairs)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = {}
    with (out_dir / "pairs.jsonl").open("w", encoding="utf-8") as f:
        for cand_id, nav_uuid in pairs:
            uid = pair_uid(cand_id, nav_uuid)
            key[uid] = {"candidate_id": cand_id, "nav_uuid": nav_uuid}
            f.write(json.dumps({
                "pair_uid": uid,
                "candidate": render_candidate(candidates[cand_id]),
                "job": render_nav_ad(ads[nav_uuid]),
            }, ensure_ascii=False) + "\n")

    (out_dir / "pair_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    (out_dir / "instructions.md").write_text(RUBRIC, encoding="utf-8")
    with (out_dir / "answers_template.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pair_uid", "grade", "eligibility", "requirement_location",
                    "blocking_requirement", "rationale", "reviewer_id"])
        for cand_id, nav_uuid in pairs:
            w.writerow([pair_uid(cand_id, nav_uuid), "", "", "", "", "", ""])

    summary = {"batch": out_dir.name, "pairs": len(pairs),
               "candidates": sorted({c for c, _ in pairs}),
               "decoys_per_candidate": decoys_per_candidate,
               "blinding": "reviewer sees only candidate + real NAV ad; no rank, "
                           "score, system identity, or machine flags"}
    (out_dir / "batch_meta.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--validation", required=True)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--decoys-per-candidate", type=int, default=8)
    args = p.parse_args(argv)
    export(Path(args.validation), Path(args.snapshot), Path(args.out),
           args.decoys_per_candidate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
