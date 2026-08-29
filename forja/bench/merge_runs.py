"""Merge multiple phase-1 run directories into one scoreable run.

    python3 -m forja.bench.merge_runs --out runs_v2/merged_<name> <run_dir> [<run_dir> ...]

Use case: budget-constrained campaigns run different arms (or candidate
subsets) in separate phase-1 runs against the SAME corpus slice and model;
scoring wants one directory. The merge is purely mechanical: outputs are
unioned per (candidate, arm) — a later run dir wins on conflict, loudly —
and the audit logs are concatenated with their source recorded. run_meta
records full provenance. Refuses to merge runs with mismatched slice, data
dir, model, or provider (that would not be one experiment)."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path


def merge(run_dirs: list[Path], out_dir: Path) -> Path:
    metas = [json.loads((d / "run_meta.json").read_text()) for d in run_dirs]
    for key in ("slice", "data_dir", "model", "provider", "mode"):
        values = {str(m.get(key)) for m in metas}
        if len(values) > 1:
            raise SystemExit(f"refusing to merge: {key} differs across runs: {values}")

    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, dict] = {}
    usage: dict[str, dict] = {}
    conflicts = []
    for d in run_dirs:
        run_outputs = json.loads((d / "outputs_v2.json").read_text(encoding="utf-8"))
        for cand_id, arms in run_outputs.items():
            for wf, out in arms.items():
                if wf in outputs.get(cand_id, {}):
                    conflicts.append(f"{cand_id}×{wf} (kept {d.name})")
                outputs.setdefault(cand_id, {})[wf] = out
        if (d / "usage.json").exists():
            for key, val in json.loads((d / "usage.json").read_text()).items():
                if key in usage:
                    for field in ("model_calls", "input_tokens", "output_tokens",
                                  "cache_write_tokens", "cache_read_tokens"):
                        usage[key][field] = usage[key].get(field, 0) + val.get(field, 0)
                    usage[key]["latency_s"] = round(
                        usage[key].get("latency_s", 0) + val.get("latency_s", 0), 2)
                    usage[key]["cost_usd"] = round(
                        usage[key].get("cost_usd", 0) + val.get("cost_usd", 0), 6)
                else:
                    usage[key] = val
        for log_name in ("model_calls.jsonl", "decisions.jsonl"):
            src = d / log_name
            if src.exists():
                with (out_dir / log_name).open("a", encoding="utf-8") as f:
                    f.write(src.read_text(encoding="utf-8"))
    if conflicts:
        print("WARNING: overlapping cases overwritten:", conflicts)

    workflows = sorted({wf for arms in outputs.values() for wf in arms})
    meta = dict(metas[0])
    meta.update({
        "run_id": out_dir.name,
        "merged_from": [d.name for d in run_dirs],
        "workflows": workflows,
        "candidates": sorted(outputs),
    })
    (out_dir / "outputs_v2.json").write_text(
        json.dumps(outputs, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "usage.json").write_text(json.dumps(usage, indent=2), encoding="utf-8")
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"merged {len(run_dirs)} runs -> {out_dir} "
          f"({len(outputs)} candidates, arms {workflows})")
    return out_dir


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    merge([Path(d) for d in args.run_dirs], Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
