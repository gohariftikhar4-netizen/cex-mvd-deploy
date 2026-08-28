"""Benchmark runner CLI.

    python -m forja.run_benchmark [--mode auto|offline|live] [--out runs]

Runs the baseline workflow and the Forja workflow over every candidate,
evaluates both against the gold labels, and writes:

    <out>/<run_id>/results.json        full metrics
    <out>/<run_id>/summary.md          comparison tables
    <out>/<run_id>/outputs.json        raw workflow outputs
    <out>/<run_id>/model_calls.jsonl   every model call, in full
    <out>/<run_id>/decisions.jsonl     every intermediate pipeline decision

Modes:
  offline  deterministic lexical stand-in for both workflows (harness
           validation only — NOT evidence about the edge).
  live     real model calls through the Anthropic SDK (default model
           claude-opus-5; override with FORJA_MODEL).
  auto     live if credentials are detected, else offline (default).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

from . import baseline as baseline_mod
from .evaluation import evaluator
from .evaluation.gold import load_labels
from .llm import AnthropicClient, OfflineDeterministicClient, live_capability
from .pipeline import run_forja
from .runlog import RunLogger
from .schemas import load_candidates, load_jobs


def run(mode: str, out_dir: Path) -> dict:
    candidates = load_candidates()
    jobs = load_jobs()
    gold = load_labels()
    jobs_by_id = {j.id: j for j in jobs}

    if mode == "auto":
        capable, why = live_capability()
        mode = "live" if capable else "offline"
        print(f"[auto] {why} -> mode={mode}")

    run_id = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{mode}"
    run_dir = out_dir / run_id
    logger = RunLogger(run_dir)

    if mode == "live":
        client = AnthropicClient(logger)
    else:
        client = OfflineDeterministicClient(logger)
    print(f"run: {run_id}  client: {client.name}  model: {client.model}")

    outputs: dict[str, dict[str, dict]] = {}
    per_candidate: dict[str, dict[str, dict]] = {}
    for cand in candidates:
        print(f"  {cand.id}: baseline ...", end="", flush=True)
        b_out = baseline_mod.run_baseline(cand, jobs, logger, client)
        print(" forja ...", flush=True)
        f_out = run_forja(cand, jobs, logger, model_client=client)
        outputs[cand.id] = {"baseline": b_out, "forja": f_out}
        per_candidate[cand.id] = {
            "baseline": evaluator.evaluate_output(b_out, cand, jobs_by_id, gold),
            "forja": evaluator.evaluate_output(f_out, cand, jobs_by_id, gold),
        }

    results = {
        "run_meta": {
            "run_id": run_id,
            "mode": mode,
            "model": client.model,
            "client": client.name,
            "date": _dt.date.today().isoformat(),
            "n_candidates": len(candidates),
            "n_jobs": len(jobs),
        },
        "per_candidate": per_candidate,
        "aggregate": {
            wf: evaluator.aggregate([per_candidate[c][wf] for c in per_candidate])
            for wf in ("baseline", "forja")
        },
    }

    (run_dir / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "outputs.json").write_text(
        json.dumps(outputs, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = evaluator.format_summary(results)
    (run_dir / "summary.md").write_text(summary, encoding="utf-8")

    print()
    print(summary)
    print(f"\nartifacts: {run_dir}")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["auto", "offline", "live"], default="auto")
    parser.add_argument("--out", default="runs", help="output directory (default: runs/)")
    args = parser.parse_args(argv)
    run(args.mode, Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
