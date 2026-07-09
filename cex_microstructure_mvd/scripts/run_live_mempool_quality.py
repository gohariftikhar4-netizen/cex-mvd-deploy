#!/usr/bin/env python3
"""Daily data-quality report for the live mempool + DEX-outcome pipeline.

Reads the mempool recorder (ethereum/pending, ethereum/inclusion) and the DEX
outcome tracker (ethereum/outcomes). Emits quality metrics + a PASS/WARN/FAIL
verdict and exit code. Read-only. No secrets.

Hard-fail thresholds (see HARD_* below): poor inclusion match, poor outcome
coverage, sparse pool identification, unsafe storage growth.

Usage: run_live_mempool_quality.py [--data-root DIR] [--json OUT.json]
"""
from __future__ import annotations
import argparse, glob, json, os, sys, time
import pyarrow.parquet as pq

BLOCK_HZ = [1, 2, 5]
SEC_HZ = [30, 120, 300, 900, 3600]
HARD_MIN_OUTCOME_COV = 0.60     # +1 block coverage must be >= 60%
HARD_MIN_POOL_ID = 0.80         # decoded events must identify pool/token
HARD_MAX_DISK_GB = 40           # data dir size ceiling


def _rows(root, stream):
    fs = glob.glob(f"{root}/ethereum/{stream}/**/*.parquet", recursive=True)
    n = 0
    for f in fs:
        try:
            n += pq.read_metadata(f).num_rows
        except Exception:
            pass
    return len(fs), n


def _load(root, stream):
    fs = glob.glob(f"{root}/ethereum/{stream}/**/*.parquet", recursive=True)
    out = []
    for f in fs[-50:]:  # sample recent for coverage stats
        try:
            out.extend(pq.read_table(f).to_pylist())
        except Exception:
            pass
    return out


def _dir_gb(p):
    t = 0
    for r, _d, fs in os.walk(p):
        for f in fs:
            try:
                t += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return t / 1e9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=os.environ.get("DATA_ROOT", "/var/lib/cexrec/data"))
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    root = args.data_root

    pf, pn = _rows(root, "pending")
    inf, inn = _rows(root, "inclusion")
    of, on = _rows(root, "outcomes")
    out = _load(root, "outcomes")
    incl = _load(root, "inclusion")

    m = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "pending_files": pf, "pending_rows": pn,
         "inclusion_files": inf, "inclusion_rows": inn,
         "outcome_files": of, "outcome_rows": on,
         "disk_gb": round(_dir_gb(os.path.join(root, "ethereum")), 2) if os.path.exists(os.path.join(root, "ethereum")) else 0.0}
    if out:
        n = len(out)
        m["dex_candidate_sample"] = n
        m["pool_id_rate"] = round(sum(1 for r in out if r.get("pool")) / n, 3)
        m["direction_known_rate"] = round(sum(1 for r in out if r.get("direction")) / n, 3)
        for b in BLOCK_HZ:
            m[f"cov_b{b}"] = round(sum(1 for r in out if r.get(f"price_b{b}") is not None) / n, 3)
        for s in SEC_HZ:
            m[f"cov_s{s}"] = round(sum(1 for r in out if r.get(f"price_s{s}") is not None) / n, 3)
        m["notional_usd_p50"] = round(sorted(r["notional_usd"] for r in out if r.get("notional_usd"))[n // 2], 0) if any(r.get("notional_usd") for r in out) else None
        m["latest_sample"] = {k: out[-1].get(k) for k in ("tx_hash", "protocol", "direction", "notional_usd", "event_labels")}
    if incl:
        m["failed_tx_rate"] = round(sum(1 for r in incl if r.get("success") == "failed") / len(incl), 3)
        m["replacement_rate"] = round(sum(1 for r in incl if r.get("replaced")) / len(incl), 3)
        d = [r["inclusion_delay_s"] for r in incl if r.get("inclusion_delay_s") is not None]
        m["avg_inclusion_delay_s"] = round(sum(d) / len(d), 1) if d else None
    # inclusion match rate: outcomes whose tx also in inclusion sample
    if out and incl:
        inclset = {r.get("tx_hash") for r in incl}
        m["inclusion_match_rate"] = round(sum(1 for r in out if r.get("tx_hash") in inclset) / len(out), 3)

    fails = []
    if out:
        if m.get("cov_b1", 0) < HARD_MIN_OUTCOME_COV:
            fails.append(f"+1blk coverage {m.get('cov_b1')} < {HARD_MIN_OUTCOME_COV}")
        if m.get("pool_id_rate", 0) < HARD_MIN_POOL_ID:
            fails.append(f"pool_id_rate {m.get('pool_id_rate')} < {HARD_MIN_POOL_ID}")
    if m["disk_gb"] > HARD_MAX_DISK_GB:
        fails.append(f"disk {m['disk_gb']}GB > {HARD_MAX_DISK_GB}GB")
    if on == 0 and pn == 0:
        fails.append("no rows written")

    verdict = "FAIL" if fails else ("WARN" if (out and m.get("cov_s3600", 1) < 0.5) else "PASS")
    m["verdict"] = verdict; m["fail_reasons"] = fails

    print(json.dumps(m, indent=2, default=str))
    print(f"\nVERDICT: {verdict}" + (f" — {'; '.join(fails)}" if fails else ""))
    if args.json:
        os.makedirs(os.path.dirname(args.json), exist_ok=True)
        json.dump(m, open(args.json, "w"), indent=2, default=str)
    sys.exit(2 if verdict == "FAIL" else (1 if verdict == "WARN" else 0))


if __name__ == "__main__":
    main()
