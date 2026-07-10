#!/usr/bin/env python3
"""M2 — first live post-inclusion event study (PREPARED, gated — does not run early).

Tests whether abnormal INCLUDED DEX events predict post-inclusion moves, using the
event table from build_live_event_table.py. Groups A (pending-visible) and B
(inclusion-only) are tested SEPARATELY and never mixed. Enrichment/return vs
matched controls (protocol x liquidity-bucket x time-of-day). No holdout at M2.

GATE: refuses to run until >= MIN_DAYS days AND >= MIN_EVENTS events per tested
group. This is measurement only — no trading, no wallets, no frontrun/sandwich.

Usage: run_live_event_study.py --table live_event_table.csv [--min-days 7] [--min-events 300]
"""
from __future__ import annotations
import argparse, sys
import numpy as np, pandas as pd

BLOCK_RET = ["ret_b1_bps", "ret_b2_bps", "ret_b5_bps"]
TIME_RET = ["ret_s30_bps", "ret_s120_bps", "ret_s300_bps", "ret_s900_bps", "ret_s3600_bps"]


def study_group(df, gname):
    print(f"\n=== group {gname}: {len(df)} events ===")
    # direction: continuation of the swap direction (buy -> +1)
    sign = np.where(df["direction"] == "buy", 1.0, -1.0)
    strat = ["protocol", "liq_bucket", "tod_bucket"]
    rows = []
    for col in BLOCK_RET + TIME_RET:
        if col not in df:
            continue
        r = (sign * df[col]).dropna()
        if len(r) < 30:
            continue
        # matched control = same-strata mean of |ret| direction-neutral baseline (drift)
        base = df.groupby(strat, observed=True)[col].transform("mean")
        adj = (sign * (df[col] - base)).dropna()   # de-meaned by strata (removes protocol/liq/tod drift)
        rows.append({"group": gname, "horizon": col, "n": len(r),
                     "mean_bps": round(r.mean(), 1), "median_bps": round(r.median(), 1),
                     "ctrl_adj_bps": round(adj.mean(), 1), "winrate": round((r > 0).mean(), 3)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--min-days", type=int, default=7)
    ap.add_argument("--min-events", type=int, default=300)
    args = ap.parse_args()
    df = pd.read_csv(args.table)
    if "block_ts" in df:
        days = (pd.to_datetime(df["block_ts"], unit="ms", utc=True).dt.floor("D")).nunique()
    else:
        days = 0
    # GATE
    per_group = df["group"].value_counts().to_dict() if "group" in df else {}
    ready = days >= args.min_days and any(v >= args.min_events for v in per_group.values())
    print(f"days={days} events_by_group={per_group} min_days={args.min_days} min_events={args.min_events}")
    if not ready:
        print("GATE NOT MET — collect more data. M2 study not run (by design).")
        sys.exit(0)
    results = []
    for g, sub in df.groupby("group"):
        if len(sub) >= args.min_events:
            results.append(study_group(sub, g))
    if results:
        res = pd.concat(results, ignore_index=True)
        print("\n" + res.to_string(index=False))
        res.to_csv(args.table.replace(".csv", "_m2_results.csv"), index=False)
    print("\nNOTE: M2 has NO holdout. Interesting = ctrl-adjusted gross >= 30 bps, consistent across "
          "the week, group A and/or B, spread across pools. Proceed to M3 (P1/P2 + sealed holdout) only then.")


if __name__ == "__main__":
    main()
