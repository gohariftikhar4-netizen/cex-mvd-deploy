"""Blind human labeling of candidate–job pairs.

Export builds a reviewer packet with NO workflow attribution and NO gold:
reviewers see only the candidate record and the job ad, graded 0/1/2 under
the frozen rubric. Multiple reviewers label the same pairs independently;
import merges answers, reports inter-rater agreement, consolidates by
majority, and (optionally) validates generator-derived gold against the
human labels.

    python -m forja.bench.labeling export --run runs_v2/<run_id> \
        --per-candidate 30 --out labeling_batches/batch_001
    python -m forja.bench.labeling import --batch labeling_batches/batch_001 \
        --answers svar_anna.csv svar_bjarne.csv [--validate-gold]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from pathlib import Path

from ..baseline import render_candidate, render_job
from ..schemas import load_candidates_v2
from .run_v2 import load_corpus

RUBRIC = (
    "Gi hvert par en karakter:\n"
    "  2 = sterk match: stillingen bryter INGEN av kandidatens absolutte krav, "
    "og kjernekompetansen treffer så godt at du ville satt den øverst.\n"
    "  1 = verdt å søke: ingen brudd på absolutte krav, og en troverdig hel "
    "eller delvis/overførbar vei inn — du ville tatt den med, med forbehold.\n"
    "  0 = ikke verdt kandidatens tid: bryter minst ett absolutt krav "
    "(pendletid, skift, fysikk, førerkort, påkrevd autorisasjon, språknivå, "
    "statsborgerskap, stillingsprosent, oppgitt lønn under gulvet, "
    "overnattingsreiser, utløpt frist) ELLER er faglig/målmessig feil.\n"
    "Du vurderer KUN paret under. Du får ikke vite hvordan paret ble valgt ut, "
    "og skal ikke forsøke å gjette det.\n"
)


def pair_uid(candidate_id: str, job_id: str) -> str:
    return hashlib.sha1(f"{candidate_id}|{job_id}".encode()).hexdigest()[:12]


def export_batch(run_dir: Path, per_candidate: int, out_dir: Path,
                 seed: int = 11) -> dict:
    meta = json.loads((run_dir / "run_meta.json").read_text())
    outputs = json.loads((run_dir / "outputs_v2.json").read_text(encoding="utf-8"))
    candidates = {c.id: c for c in load_candidates_v2()}
    jobs = {j.id: j for j in load_corpus(Path(meta["data_dir"]), meta["slice"])}
    rng = random.Random(f"labeling:{seed}:{run_dir.name}")

    pairs: list[tuple[str, str]] = []
    for cand_id, arms in outputs.items():
        pool: set[str] = set()
        for output in arms.values():
            pool |= {r["job_id"] for r in output.get("recommendations", [])
                     if r.get("job_id") in jobs}
            pool |= set(output.get("extended", [])[:15])
        # decoys from the wider slice so reviewers cannot infer that every
        # shown pair was machine-selected
        decoys = rng.sample(sorted(set(jobs) - pool), min(8, len(jobs) - len(pool)))
        chosen = sorted(pool)
        rng.shuffle(chosen)
        for job_id in (chosen[:per_candidate] + decoys):
            pairs.append((cand_id, job_id))

    rng.shuffle(pairs)  # no candidate grouping order effects
    out_dir.mkdir(parents=True, exist_ok=True)
    key = {}
    with (out_dir / "pairs.jsonl").open("w", encoding="utf-8") as f:
        for cand_id, job_id in pairs:
            uid = pair_uid(cand_id, job_id)
            key[uid] = {"candidate_id": cand_id, "job_id": job_id}
            f.write(json.dumps({
                "pair_uid": uid,
                "candidate": render_candidate(candidates[cand_id]),
                "job": render_job(jobs[job_id]),
            }, ensure_ascii=False) + "\n")
    (out_dir / "pair_key.json").write_text(json.dumps(key, indent=1))
    (out_dir / "instructions.md").write_text(
        "# Blindmerking av kandidat–stilling-par\n\n" + RUBRIC +
        "\nFyll ut answers-malen: pair_uid, grade (0/1/2), rationale (kort), "
        "reviewer_id (ditt navn). Merk parene uavhengig av andre.\n",
        encoding="utf-8")
    with (out_dir / "answers_template.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pair_uid", "grade", "rationale", "reviewer_id"])
        for cand_id, job_id in pairs:
            w.writerow([pair_uid(cand_id, job_id), "", "", ""])
    summary = {"batch": out_dir.name, "pairs": len(pairs), "run": run_dir.name}
    print(json.dumps(summary, indent=2))
    return summary


def _weighted_kappa(a: list[int], b: list[int]) -> float | None:
    """Quadratic-weighted Cohen's kappa on grades 0..2."""
    n = len(a)
    if n == 0:
        return None
    cats = 3
    w = [[(i - j) ** 2 / (cats - 1) ** 2 for j in range(cats)] for i in range(cats)]
    obs = [[0.0] * cats for _ in range(cats)]
    for x, y in zip(a, b):
        obs[x][y] += 1 / n
    pa = [sum(1 for x in a if x == k) / n for k in range(cats)]
    pb = [sum(1 for y in b if y == k) / n for k in range(cats)]
    po = sum(w[i][j] * obs[i][j] for i in range(cats) for j in range(cats))
    pe = sum(w[i][j] * pa[i] * pb[j] for i in range(cats) for j in range(cats))
    if pe == 0:
        return None
    return round(1 - po / pe, 4) if pe != 0 else None


def import_answers(batch_dir: Path, answer_files: list[Path],
                   validate_gold: bool = False) -> dict:
    key = json.loads((batch_dir / "pair_key.json").read_text())
    by_reviewer: dict[str, dict[str, int]] = {}
    rationales: dict[str, dict[str, str]] = {}
    for path in answer_files:
        with Path(path).open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                uid, grade = row["pair_uid"].strip(), row["grade"].strip()
                reviewer = row.get("reviewer_id", "").strip() or Path(path).stem
                if uid not in key or grade not in ("0", "1", "2"):
                    continue
                by_reviewer.setdefault(reviewer, {})[uid] = int(grade)
                rationales.setdefault(uid, {})[reviewer] = row.get("rationale", "")

    reviewers = sorted(by_reviewer)
    agreement = {}
    for i, r1 in enumerate(reviewers):
        for r2 in reviewers[i + 1:]:
            shared = sorted(set(by_reviewer[r1]) & set(by_reviewer[r2]))
            if not shared:
                continue
            a = [by_reviewer[r1][u] for u in shared]
            b = [by_reviewer[r2][u] for u in shared]
            agreement[f"{r1}|{r2}"] = {
                "pairs": len(shared),
                "exact_agreement": round(sum(x == y for x, y in zip(a, b)) / len(shared), 4),
                "weighted_kappa": _weighted_kappa(a, b),
            }

    consolidated: dict[str, dict] = {}
    disputes: list[str] = []
    for uid in key:
        votes = [by_reviewer[r][uid] for r in reviewers if uid in by_reviewer[r]]
        if not votes:
            continue
        counts = {g: votes.count(g) for g in set(votes)}
        best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        grade, n_best = best[0]
        if len(best) > 1 and best[1][1] == n_best:
            disputes.append(uid)
            continue
        consolidated[uid] = {**key[uid], "grade": grade, "votes": votes,
                             "rationales": rationales.get(uid, {})}

    result = {
        "reviewers": reviewers,
        "labeled_pairs": sum(len(v) for v in by_reviewer.values()),
        "agreement": agreement,
        "consolidated": len(consolidated),
        "disputes_needing_adjudication": disputes,
    }

    if validate_gold:
        # Compare consolidated human labels against generator-derived gold.
        from .goldgen import GoldV2  # eval side only
        run_meta_candidates = load_candidates_v2()
        data_dir = None
        for parent in [batch_dir, *batch_dir.parents]:
            if (parent / "benchmark_data" / "manifest.json").exists():
                data_dir = parent / "benchmark_data"
                break
        if data_dir is None:
            data_dir = Path("benchmark_data")
        gold = GoldV2(data_dir / "manifest.json", run_meta_candidates)
        pairs = [(v["candidate_id"], v["job_id"], v["grade"])
                 for v in consolidated.values()]
        human = [g for _, _, g in pairs]
        generator = [gold.grade(c, j) for c, j, _ in pairs]
        result["gold_validation"] = {
            "pairs": len(pairs),
            "exact_agreement": (round(sum(h == g for h, g in zip(human, generator)) / len(pairs), 4)
                                if pairs else None),
            "weighted_kappa": _weighted_kappa(human, generator),
        }

    (batch_dir / "labels_human.json").write_text(
        json.dumps({"result": result, "labels": consolidated},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_exp = sub.add_parser("export")
    p_exp.add_argument("--run", required=True)
    p_exp.add_argument("--per-candidate", type=int, default=30)
    p_exp.add_argument("--out", required=True)
    p_imp = sub.add_parser("import")
    p_imp.add_argument("--batch", required=True)
    p_imp.add_argument("--answers", nargs="+", required=True)
    p_imp.add_argument("--validate-gold", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "export":
        export_batch(Path(args.run), args.per_candidate, Path(args.out))
    else:
        import_answers(Path(args.batch), [Path(a) for a in args.answers],
                       validate_gold=args.validate_gold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
