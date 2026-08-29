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

from ..llm import (AnthropicClient, OfflineDeterministicClient,
                   OpenRouterClient, live_capability)
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
        data_dir: Path, slice_name: str | None, out_dir: Path,
        parallel: int = 1, provider: str = "anthropic",
        model: str | None = None, provider_route: str | None = None) -> Path:
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
    if mode != "live":
        client = OfflineDeterministicClient(logger)
    elif provider == "openrouter":
        if not model:
            raise SystemExit("--provider openrouter requires --model <slug>")
        client = OpenRouterClient(logger, model=model, provider_route=provider_route)
    elif provider == "anthropic":
        client = AnthropicClient(logger, **({"model": model} if model else {}))
    else:
        raise SystemExit(f"unknown provider {provider!r}")
    print(f"run: {run_id}  client: {client.name}  model: {client.model}  "
          f"route: {provider_route or 'default'}  jobs: {len(jobs)}  "
          f"candidates: {len(candidates)}  arms: {workflows}")

    outputs_path = run_dir / "outputs_v2.json"

    def save_outputs(outputs_now: dict) -> None:
        # Incremental, atomic: an aborted run must still yield scoreable work.
        tmp = outputs_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(outputs_now, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(outputs_path)
        usage_tmp = run_dir / "usage.tmp"
        usage_tmp.write_text(json.dumps(aggregate_usage(logger.model_calls), indent=2),
                             encoding="utf-8")
        usage_tmp.replace(run_dir / "usage.json")

    outputs: dict[str, dict[str, dict]] = {}

    def run_candidate(cand):
        result = {}
        for wf in workflows:
            print(f"  {cand.id} × {wf} ...", flush=True)
            last_error = None
            for attempt in (1, 2):  # one retry per arm; a flake must not kill the run
                try:
                    result[wf] = WORKFLOWS[wf](cand, jobs, logger, client)
                    last_error = None
                    break
                except Exception as e:  # noqa: BLE001 — recorded, never silent
                    if ("credit balance" in str(e).lower()
                            or "credits exhausted" in str(e).lower()):
                        # Billing exhaustion is not transient: persist what we
                        # have, then stop the whole run immediately.
                        outputs.setdefault(cand.id, {}).update(result)
                        save_outputs(outputs)
                        raise SystemExit(
                            f"ABORTED: API credit balance exhausted during "
                            f"{cand.id} × {wf}. Partial outputs saved to "
                            f"{outputs_path}. Top up credits and rerun the "
                            f"remainder.") from e
                    last_error = e
                    print(f"    ! {cand.id} × {wf} attempt {attempt} failed: {e}",
                          flush=True)
            if last_error is not None:
                result[wf] = {"workflow": wf, "candidate_id": cand.id,
                              "error": str(last_error), "recommendations": [],
                              "extended": [], "wall_time_s": None}
                logger.log_decision("run.arm_failed", candidate_id=cand.id,
                                    workflow=wf, error=str(last_error))
        return cand.id, result

    if parallel > 1 and len(candidates) > 1:
        # First candidate runs alone so the shared jobs-prefix prompt cache is
        # written once; the rest then read it concurrently.
        cid, res = run_candidate(candidates[0])
        outputs[cid] = res
        save_outputs(outputs)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            for cid, res in pool.map(run_candidate, candidates[1:]):
                outputs[cid] = res
                save_outputs(outputs)
        outputs = {c.id: outputs[c.id] for c in candidates if c.id in outputs}
    else:
        for cand in candidates:
            cid, res = run_candidate(cand)
            outputs[cid] = res
            save_outputs(outputs)

    save_outputs(outputs)
    meta = {
        "run_id": run_id,
        "phase": "run",
        "mode": mode,
        "provider": client.name,
        "provider_route": provider_route,
        "model": client.model,
        "client": client.name,
        "date": _dt.date.today().isoformat(),
        "workflows": workflows,
        "candidates": [c.id for c in candidates],
        "n_jobs": len(jobs),
        "slice": slice_name or "full",
        "data_dir": str(data_dir),
    }
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
    parser.add_argument("--parallel", type=int, default=1,
                        help="concurrent candidates (live runs; first candidate "
                             "always runs alone to warm the prompt cache)")
    parser.add_argument("--provider", choices=["anthropic", "openrouter"],
                        default="anthropic",
                        help="live-mode model provider (default: anthropic)")
    parser.add_argument("--model", default=None,
                        help="model id/slug (required for openrouter)")
    parser.add_argument("--provider-route", default=None,
                        help="openrouter: pin one upstream host (allow_fallbacks=false)")
    args = parser.parse_args(argv)
    cand_ids = None if args.candidates == "all" else [
        c.strip() for c in args.candidates.split(",") if c.strip()]
    run(args.mode, [w.strip() for w in args.workflows.split(",") if w.strip()],
        cand_ids, Path(args.data), args.slice, Path(args.out),
        parallel=args.parallel, provider=args.provider, model=args.model,
        provider_route=args.provider_route)
    return 0


if __name__ == "__main__":
    sys.exit(main())
