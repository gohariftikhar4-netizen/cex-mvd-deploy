"""Blinded advisor review console — measures REAL human active work.

Start a session (arms are shuffled and hidden behind neutral case ids):

    python -m forja.bench.review start --run runs_v2/<run_id> --reviewer anna \
        --out review_sessions

Console commands while reviewing a case:
    open N        show recommendation N in full (logs recommendation_opened)
    reverify N    open N flagged as a manual re-verification of its claims
    reject N ...  reject recommendation N with a reason
    modify N ...  note a correction to recommendation N
    research      toggle external research (logs started/finished)
    approve       approve the case and move on (logs case_approved)
    quit          stop the session (resume later with the same command)

Afterwards:

    python -m forja.bench.review report --session review_sessions/<session_id>

computes human active minutes per completed case (HYPOTHESIS_V2.md
definition), correction counts, per-arm aggregates (unblinded only at report
time), and the paired bootstrap HAM ratio of B3 versus each baseline arm.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import random
import sys
import time
from pathlib import Path

from ..baseline import render_candidate, render_job
from ..schemas import load_candidates_v2
from .events import bootstrap_ratio, case_metrics, load_events
from .run_v2 import load_corpus


def _append_event(path: Path, event: str, case_id: str, **payload) -> None:
    record = {"ts": time.time(), "event": event, "case_id": case_id, **payload}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def start_session(run_dir: Path, reviewer: str, out_root: Path) -> None:
    meta = json.loads((run_dir / "run_meta.json").read_text())
    outputs = json.loads((run_dir / "outputs_v2.json").read_text(encoding="utf-8"))
    candidates = {c.id: c for c in load_candidates_v2()}
    jobs = {j.id: j for j in load_corpus(Path(meta["data_dir"]), meta["slice"])}

    session_id = f"{reviewer}-{_dt.datetime.now(_dt.UTC).strftime('%Y%m%dT%H%M%SZ')}"
    session_dir = out_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    events_path = session_dir / "events.jsonl"

    cases = [(cand_id, wf) for cand_id in outputs for wf in outputs[cand_id]]
    random.Random(f"blind:{session_id}").shuffle(cases)
    blinding = {f"case_{i + 1:03d}": {"candidate_id": c, "workflow": w, "run_dir": str(run_dir)}
                for i, (c, w) in enumerate(cases)}
    (session_dir / "blinding_key.json").write_text(json.dumps(blinding, indent=2))
    done: set[str] = {m["case_id"] for m in _completed(events_path)} if events_path.exists() else set()

    print(f"Session {session_id}: {len(blinding)} cases ({len(done)} already done).")
    print("Du får IKKE vite hvilket system som laget hver liste. Ikke åpne blinding_key.json.")
    for case_id, key in blinding.items():
        if case_id in done:
            continue
        output = outputs[key["candidate_id"]][key["workflow"]]
        if not _review_case(events_path, case_id, candidates[key["candidate_id"]],
                            output, jobs):
            print("Avslutter. Fortsett senere med samme kommando.")
            return
    print("Alle saker ferdig. Kjør:  python3 -m forja.bench.review report --session", session_dir)


def _completed(events_path: Path) -> list[dict]:
    if not events_path.exists():
        return []
    return [e for e in load_events(events_path) if e["event"] == "case_approved"]


def _review_case(events_path: Path, case_id: str, candidate, output: dict,
                 jobs: dict) -> bool:
    recs = output.get("recommendations", [])
    detail = {d["job_id"]: d for d in output.get("forja_detail", [])}
    print("\n" + "=" * 72)
    print(f"SAK {case_id} — kandidat under, deretter anbefalingsliste.")
    print("=" * 72)
    print(render_candidate(candidate))
    print("-" * 72)
    for i, r in enumerate(recs, start=1):
        job = jobs.get(r["job_id"])
        title = f"{job.title} – {job.employer} ({job.location_city})" if job else r["job_id"]
        print(f"{i:2d}. {title}")
    _append_event(events_path, "review_started", case_id, n_recommendations=len(recs))

    researching = False
    while True:
        try:
            cmd = input(f"[{case_id}] open/reverify/reject/modify/research/approve/quit > ").strip()
        except EOFError:
            return False
        parts = cmd.split(maxsplit=2)
        verb = parts[0].lower() if parts else ""
        if verb in ("open", "reverify") and len(parts) >= 2 and parts[1].isdigit():
            n = int(parts[1])
            if not (1 <= n <= len(recs)):
                print("ukjent nummer")
                continue
            r = recs[n - 1]
            job = jobs.get(r["job_id"])
            print("-" * 60)
            print(render_job(job) if job else f"(ukjent stilling {r['job_id']})")
            for c in r.get("claims", []):
                print(f"  BELEGG [{c.get('source')}]: {c.get('claim')}  «{c.get('quote')}»")
            extra = detail.get(r["job_id"])
            if extra:
                for g in extra.get("gaps", []):
                    print(f"  GAP: {g['detail']}  → {g['next_action']}")
            _append_event(events_path, "recommendation_opened", case_id,
                          n=n, job_id=r["job_id"], reverify=(verb == "reverify"))
        elif verb == "reject" and len(parts) >= 2 and parts[1].isdigit():
            n = int(parts[1])
            reason = parts[2] if len(parts) > 2 else ""
            _append_event(events_path, "recommendation_rejected", case_id,
                          n=n, job_id=recs[n - 1]["job_id"] if 1 <= n <= len(recs) else None,
                          reason=reason)
            print("notert: avvist")
        elif verb == "modify" and len(parts) >= 2 and parts[1].isdigit():
            n = int(parts[1])
            note = parts[2] if len(parts) > 2 else ""
            _append_event(events_path, "recommendation_modified", case_id, n=n, note=note)
            print("notert: endret")
        elif verb == "research":
            researching = not researching
            _append_event(events_path,
                          "external_research_started" if researching
                          else "external_research_finished", case_id)
            print("research:", "startet" if researching else "avsluttet")
        elif verb == "approve":
            if researching:
                _append_event(events_path, "external_research_finished", case_id)
            _append_event(events_path, "case_approved", case_id)
            return True
        elif verb == "quit":
            return False
        else:
            print("kommandoer: open N | reverify N | reject N grunn | modify N notat | research | approve | quit")


def report(session_dir: Path) -> dict:
    blinding = json.loads((session_dir / "blinding_key.json").read_text())
    events = load_events(session_dir / "events.jsonl")

    per_case: dict[str, dict] = {}
    for case_id, key in blinding.items():
        m = case_metrics(events, case_id)
        if m:
            per_case[case_id] = {**m, **key}

    arms = sorted({v["workflow"] for v in per_case.values()})
    by_arm: dict[str, dict] = {}
    for arm in arms:
        cases = [v for v in per_case.values() if v["workflow"] == arm]
        n = len(cases)
        by_arm[arm] = {
            "completed_cases": n,
            "mean_active_minutes": round(sum(c["active_minutes"] for c in cases) / n, 3) if n else None,
            "mean_research_minutes": round(sum(c["research_minutes"] for c in cases) / n, 3) if n else None,
            "total_rejected": sum(c["recommendations_rejected"] for c in cases),
            "total_modified": sum(c["recommendations_modified"] for c in cases),
            "total_reverifications": sum(c["reverifications"] for c in cases),
            "total_external_searches": sum(c["external_searches"] for c in cases),
        }

    ratios = {}
    b3 = {v["candidate_id"]: v["active_minutes"] for v in per_case.values()
          if v["workflow"] == "b3"}
    for arm in arms:
        if arm == "b3":
            continue
        base = {v["candidate_id"]: v["active_minutes"] for v in per_case.values()
                if v["workflow"] == arm}
        ratios[f"b3_vs_{arm}"] = bootstrap_ratio(b3, base)

    out = {"session": session_dir.name, "per_case": per_case,
           "by_arm": by_arm, "ham_ratio_bootstrap": ratios}
    (session_dir / "review_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"by_arm": by_arm, "ham_ratio_bootstrap": ratios}, indent=2))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_start = sub.add_parser("start")
    p_start.add_argument("--run", required=True)
    p_start.add_argument("--reviewer", required=True)
    p_start.add_argument("--out", default="review_sessions")
    p_report = sub.add_parser("report")
    p_report.add_argument("--session", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "start":
        start_session(Path(args.run), args.reviewer, Path(args.out))
    else:
        report(Path(args.session))
    return 0


if __name__ == "__main__":
    sys.exit(main())
