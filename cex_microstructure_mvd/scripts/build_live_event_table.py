#!/usr/bin/env python3
"""Assemble the flat live event table for the M2/M3 post-inclusion studies.

Joins the DEX outcome tracker (ethereum/outcomes) with the mempool recorder
(ethereum/pending + ethereum/inclusion) on tx_hash to attach pending-side fields
(priority fee, replacement, inclusion delay), and derives post-inclusion returns
at each horizon. Output is ready for enrichment / forward tests — no study here.

Usage: build_live_event_table.py [--data-root DIR] [--out FILE.csv]
Research/measurement only.
"""
from __future__ import annotations
import argparse, glob, os
import pandas as pd

BLOCK_HZ = [1, 2, 5]
SEC_HZ = [30, 120, 300, 900, 3600]


def _load(root, stream):
    fs = glob.glob(f"{root}/ethereum/{stream}/**/*.parquet", recursive=True)
    if not fs:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=os.environ.get("DATA_ROOT", "/var/lib/cexrec/data"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out = _load(args.data_root, "outcomes")
    if out.empty:
        print("no outcome rows yet"); return
    out["tx_hash"] = out["tx_hash"].str.lower()  # normalize for join (getLogs vs recorder)

    # attach pending-side metadata (pending_first_seen_ts + priority fee + router)
    pend = _load(args.data_root, "pending")
    if not pend.empty:
        pend["tx_hash"] = pend["tx_hash"].str.lower()
        pcols = {"ts_local_receive": "pending_first_seen_ts", "max_priority_gwei": "priority_fee_gwei",
                 "gas_price_gwei": "gas_price_gwei", "router": "router_pending", "method": "method_pending"}
        keep = ["tx_hash"] + [c for c in pcols if c in pend.columns]
        pj = pend[keep].drop_duplicates("tx_hash").rename(columns=pcols)
        out = out.merge(pj, on="tx_hash", how="left")
    incl = _load(args.data_root, "inclusion")
    if not incl.empty:
        incl["tx_hash"] = incl["tx_hash"].str.lower()
        ic = [x for x in ["tx_hash", "inclusion_delay_s", "success", "replaced"] if x in incl.columns]
        out = out.merge(incl[ic].drop_duplicates("tx_hash"), on="tx_hash", how="left")

    # A/B split: A = seen in pending BEFORE inclusion; B = observed at inclusion only.
    out["pending_visible"] = out.get("pending_first_seen_ts").notna() if "pending_first_seen_ts" in out else False
    out["group"] = out["pending_visible"].map({True: "A_pending_visible", False: "B_inclusion_only"})
    out["included_ts"] = out["block_ts"]
    out["pool_liquidity_usd"] = None   # not recorded by tracker yet (V3 needs liquidity()); TODO enhancement

    # matched-control buckets: protocol x liquidity/notional bucket x time-of-day
    out["tod_bucket"] = (pd.to_datetime(out["block_ts"], unit="ms", utc=True).dt.hour // 4).astype("Int64")
    liq = out["pool_liquidity_usd"].fillna(out["notional_usd"])  # proxy until liquidity is captured
    out["liq_bucket"] = pd.qcut(liq.rank(method="first"), 4, labels=False, duplicates="drop") if len(out) >= 8 else 0

    # derived returns per horizon (post-inclusion)
    p0 = out["price_incl"]
    for b in BLOCK_HZ:
        if f"price_b{b}" in out:
            out[f"ret_b{b}_bps"] = (out[f"price_b{b}"] / p0 - 1) * 1e4
    for s in SEC_HZ:
        if f"price_s{s}" in out:
            out[f"ret_s{s}_bps"] = (out[f"price_s{s}"] / p0 - 1) * 1e4

    dest = args.out or f"{args.data_root}/../reports/live_event_table.csv"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    out.to_csv(dest, index=False)
    print(f"event table: {len(out)} events, {out.shape[1]} cols -> {dest}")
    print("group A/B:", out["group"].value_counts().to_dict())
    print("labels:", out["event_labels"].value_counts().head(8).to_dict() if "event_labels" in out else {})
    for b in BLOCK_HZ:
        c = f"ret_b{b}_bps"
        if c in out:
            print(f"  +{b}blk coverage={out[c].notna().mean():.0%} median={out[c].median():.1f}bps")


if __name__ == "__main__":
    main()
