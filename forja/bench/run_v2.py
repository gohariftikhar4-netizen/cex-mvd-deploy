"""Benchmark V2 phase 1: run the workflow arms. NO GOLD ACCESS.

    python -m forja.bench.run_v2 --mode offline --slice 1000 --workflows b0,b1,b2,b3 \
        --candidates all --data benchmark_data --out runs_v2

This module deliberately imports neither the corpus manifest nor gold labels
(enforced by tests): matching and scoring are separate phases. Scoring happens
afterwards via `python -m forja.bench.score_v2 <run_dir>`.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

from ..llm import AnthropicClient, OfflineDeterministicClient, live_capability
from ..runlog import RunLogger
from ..schemas import Job, load_candidates_v2
from ..workflows import WORKFLOWS
from .costs import aggregate_usage


def load_corpus(data_dir: Path, slice_name: str | None) -> list[Job]:
    jobs_raw = json.loads((data_dir / "jobs_v2.json").read_text(encoding="utf-8"))
    jobs = [Job.from_dict(d) for d in jobs_raw]
    if slice_name and slice_name != "full":
        slices = json.loads((data_dir / "slices.json").read_text(encoding="utf-8"))
        if slice_name not in slices:
            raise SystemExit(f"unknown slice {slice_name!r}; available: {sorted(slices)} or 'full'")
        keep = set(slices[slice_name])
        jobs = [j for j in jobs if j.id in keep]
    return jobs


def run(mode: str, workflows: list[str], candidate_ids: list[str] | None,
        data_dir: Path, slice_name: str | None, out_dir: Path) -> Path:
    candidates = load_candidates_v2()
    if candidate_ids:
        wanted = set(candidate_ids)
        unknown = wanted - {c.id for c in candidates}
        if unknown:
            raise SystemExit(f"unknown candidates: {sorted(unknown)}")
        candidates = [c for c in candidates if c.id in wanted]
    jobs = load_corpus(data_dir, slice_name)

    if mode == "auto":
        capable, why = live_capability()
        mode = "live" if capable else "offline"
        print(f"[auto] {why} -> mode={mode}")

    run_id = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ") + f"-v2-{mode}"
    run_dir = out_dir / run_id
    logger = RunLogger(run_dir)
    client = AnthropicClient(logger) if mode == "live" else OfflineDeterministicClient(logger)
    print(f"run: {run_id}  client: {client.name}  model: {client.model}  "
          f"jobs: {len(jobs)}  candidates: {len(candidates)}  arms: {workflows}")

    outputs: dict[str, dict[str, dict]] = {}
    for cand in candidates:
        outputs[cand.id] = {}
        for wf in workflows:
            print(f"  {cand.id} × {wf} ...", flush=True)
            outputs[cand.id][wf] = WORKFLOWS[wf](cand, jobs, logger, client)

    usage = aggregate_usage(logger.model_calls)
    meta = {
        "run_id": run_id,
        "phase": "run",
        "mode": mode,
        "model": client.model,
        "client": client.name,
        "date": _dt.date.today().isoformat(),
        "workflows": workflows,
        "candidates": [c.id for c in candidates],
        "n_jobs": len(jobs),
        "slice": slice_name or "full",
        "data_dir": str(data_dir),
    }
    (run_dir / "outputs_v2.json").write_text(
        json.dumps(outputs, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "usage.json").write_text(json.dumps(usage, indent=2), encoding="utf-8")
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"phase-1 artifacts: {run_dir}")
    print("next: python3 -m forja.bench.score_v2", run_dir)
    return run_dir


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["auto", "offline", "live"], default="auto")
    parser.add_argument("--workflows", default="b0,b1,b2,b3")
    parser.add_argument("--candidates", default="all",
                        help="'all' or comma-separated candidate ids")
    parser.add_argument("--data", default="benchmark_data")
    parser.add_argument("--slice", default="1000",
                        help="slice name from slices.json, or 'full'")
    parser.add_argument("--out", default="runs_v2")
    args = parser.parse_args(argv)
    cand_ids = None if args.candidates == "all" else [
        c.strip() for c in args.candidates.split(",") if c.strip()]
    run(args.mode, [w.strip() for w in args.workflows.split(",") if w.strip()],
        cand_ids, Path(args.data), args.slice, Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
