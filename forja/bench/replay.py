"""Deterministic reconstruction of a phase-1 run from its logged model calls.

An aborted live run leaves a complete model_calls.jsonl (every prompt and
raw response, in full) but no outputs_v2.json. Because every deterministic
stage of every arm is seeded and stable-sorted, re-running the arms with a
client that SERVES the already-logged responses instead of calling the API
reproduces the exact outputs the live run would have written — with zero new
API calls and zero changes to prompts, scoring, or the arms themselves.

    python3 -m forja.bench.replay --source runs_v2/<aborted_run> \
        --out runs_v2/<reconstructed> [--workflows b2,b3] [--slice 1000]

The ReplayClient matches each call by (workflow, candidate, task) — every
required task fires exactly once per arm on a single-chunk slice — and
verifies the reconstructed prompt byte-matches the logged prompt, so any
divergence from the live run fails loudly rather than silently fabricating.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..llm import ModelResult
from ..runlog import RunLogger
from ..schemas import Job, load_candidates_v2
from ..workflows import WORKFLOWS
from .costs import aggregate_usage
from .run_v2 import load_corpus


class ReplayError(RuntimeError):
    pass


class ReplayClient:
    """Serves logged responses; makes no network calls. Interface-compatible
    with the live clients (same complete() signature)."""

    def __init__(self, calls: list[dict], logger: RunLogger, model: str):
        self.name = "replay"
        self.model = model
        self._logger = logger
        # (workflow, candidate, task) -> queue of logged call records (seq order)
        self._buckets: dict[tuple, list[dict]] = defaultdict(list)
        for c in sorted(calls, key=lambda r: r.get("seq", 0)):
            if not c.get("parsed_ok", False):
                continue  # a failed live call was retried; only serve the good one
            t = c.get("tags") or {}
            self._buckets[(t.get("workflow"), t.get("candidate_id"), c["task"])].append(c)
        self._cursor: dict[tuple, int] = defaultdict(int)
        self.served = 0

    def complete(self, *, task: str, system: str, user: str,
                 json_schema: dict | None = None, max_tokens: int = 4096,
                 tags: dict | None = None,
                 cached_prefix: str | None = None) -> ModelResult:
        tags = tags or {}
        key = (tags.get("workflow"), tags.get("candidate_id"), task)
        queue = self._buckets.get(key, [])
        idx = self._cursor[key]
        if idx >= len(queue):
            raise ReplayError(
                f"no logged response for {key} (call #{idx + 1}); the source run "
                f"did not complete this call — cannot reconstruct without a paid call")
        record = queue[idx]
        self._cursor[key] = idx + 1

        full_prompt = (cached_prefix + "\n\n" + user) if cached_prefix else user
        if record.get("prompt") != full_prompt:
            raise ReplayError(
                f"prompt divergence at {key} call #{idx + 1}: reconstruction does "
                f"not match the logged prompt (lengths {len(full_prompt)} vs "
                f"{len(record.get('prompt') or '')}). Refusing to fabricate.")

        text = record.get("response") or ""
        parsed: Any | None = None
        if json_schema is not None:
            parsed = json.loads(text)  # was parsed_ok at log time; must parse now
        self.served += 1
        # Re-log with the ORIGINAL usage/cost so the reconstructed run carries
        # faithful token/cost accounting for the source (Opus 5) run.
        self._logger.log_model_call(
            task=task, client="replay", model=record.get("model", self.model),
            system=system, prompt=full_prompt, response=text, parsed_ok=True,
            latency_s=record.get("latency_s"), input_tokens=record.get("input_tokens"),
            output_tokens=record.get("output_tokens"), error=None, tags=tags,
            cache_creation_input_tokens=record.get("cache_creation_input_tokens") or 0,
            cache_read_input_tokens=record.get("cache_read_input_tokens") or 0,
            cache_creation_1h_tokens=record.get("cache_creation_1h_tokens") or 0,
            reconstructed=True)
        return ModelResult(text=text, parsed_json=parsed,
                           latency_s=record.get("latency_s") or 0.0,
                           input_tokens=record.get("input_tokens"),
                           output_tokens=record.get("output_tokens"),
                           client_name="replay", model=record.get("model", self.model))


def reconstruct(source: Path, out_dir: Path, workflows: list[str],
                slice_name: str | None, data_dir: Path | None) -> Path:
    src_meta = json.loads((source / "run_meta.json").read_text()) if (source / "run_meta.json").exists() else {}
    calls = [json.loads(l) for l in (source / "model_calls.jsonl").read_text().splitlines() if l.strip()]
    src_model = next((c.get("model") for c in calls if c.get("model")), "unknown")
    data_dir = data_dir or Path(src_meta.get("data_dir", "benchmark_data"))
    slice_name = slice_name or src_meta.get("slice", "1000")

    candidates = {c.id: c for c in load_candidates_v2()}
    jobs = load_corpus(data_dir, slice_name)

    out_dir.mkdir(parents=True, exist_ok=True)
    logger = RunLogger(out_dir)
    client = ReplayClient(calls, logger, model=src_model)

    # Which (candidate, arm) pairs have a complete logged call set?
    have: dict[str, set] = defaultdict(set)
    for key, queue in client._buckets.items():
        wf, cand, _task = key
        if wf and cand and queue:
            have[cand].add(wf)
    need = {"b0": {"v2.merge"}, "b1": {"v2.merge", "v2.critique", "v2.verify"},
            "b2": {"v2.rerank"}, "b3": {"forja.profile_enrichment", "v2.soft_pref"}}

    outputs: dict[str, dict[str, dict]] = {}
    reconstructed, skipped = [], []
    for cand_id in sorted(candidates):
        for wf in workflows:
            tasks_present = {t for (w, c, t) in client._buckets if w == wf and c == cand_id}
            if not need[wf] <= tasks_present:
                skipped.append(f"{cand_id}:{wf}")
                continue
            try:
                out = WORKFLOWS[wf](candidates[cand_id], jobs, logger, client)
            except Exception as e:  # noqa: BLE001
                skipped.append(f"{cand_id}:{wf}({type(e).__name__})")
                raise
            outputs.setdefault(cand_id, {})[wf] = out
            reconstructed.append(f"{cand_id}:{wf}")

    meta = {
        "run_id": out_dir.name, "phase": "run", "mode": "live",
        "provider": "anthropic", "model": src_model, "client": "replay",
        "reconstructed_from": source.name,
        "reconstruction": "deterministic replay of logged model responses; no new API calls",
        "date": src_meta.get("date", "2026-08-29"),
        "workflows": workflows, "candidates": sorted(outputs),
        "n_jobs": len(jobs), "slice": slice_name, "data_dir": str(data_dir),
    }
    (out_dir / "outputs_v2.json").write_text(
        json.dumps(outputs, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "usage.json").write_text(
        json.dumps(aggregate_usage(logger.model_calls), indent=2), encoding="utf-8")
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"reconstructed {len(reconstructed)} (candidate,arm) pairs from {source.name}; "
          f"served {client.served} logged responses; skipped {len(skipped)} incomplete.")
    if skipped:
        print("  incomplete (no new calls made):", ", ".join(skipped))
    print(f"artifacts: {out_dir}\nnext: python3 -m forja.bench.score_v2 {out_dir}")
    return out_dir


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--workflows", default="b0,b1,b2,b3")
    p.add_argument("--slice", default=None)
    p.add_argument("--data", default=None)
    args = p.parse_args(argv)
    reconstruct(Path(args.source), Path(args.out),
                [w.strip() for w in args.workflows.split(",") if w.strip()],
                args.slice, Path(args.data) if args.data else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
