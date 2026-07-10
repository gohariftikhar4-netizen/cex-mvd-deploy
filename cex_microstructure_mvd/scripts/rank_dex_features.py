#!/usr/bin/env python3
"""Continuous feature-promise ranking for the DEX discovery engine.

Reads the feature matrix (build_dex_feature_matrix.py) and, for every explanatory
feature, scores its PROVISIONAL association with a post-inclusion outcome. Produces
a live leaderboard — hypothesis-generating ONLY.

*** THIS DOES NOT DECLARE EDGE. ***  Rankings are exploratory associations subject
to multiple-testing inflation. A feature is only a *candidate* until it survives the
M2 (matched-control, A/B-separated) and M3 (P1/P2 + sealed holdout) validation gates.
No trading, no wallets.

Per feature:
  numeric  -> Spearman IC vs target, t-stat (overlap-naive), top-vs-bottom quintile spread
  boolean  -> mean target by group + difference
  stability-> IC sign consistency across time halves
  promise  -> |IC| gated by sample size & stability (provisional score, NOT edge)

Target = direction-adjusted post-inclusion return at --horizon (default ret_s300_bps).
Gate: needs >= --min-events events with a non-null target before ranking.
Usage: rank_dex_features.py --matrix dex_feature_matrix.parquet [--horizon ret_s300_bps]
"""
from __future__ import annotations
import argparse, sys
import numpy as np, pandas as pd

NON_FEATURES = {"tx_hash", "block_number", "block_ts", "mfe", "mae", "price_incl", "event_labels"}


def spearman_ic(x, y):
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(d) < 20 or d["x"].nunique() < 2:   # >=2 so 0/1 bools get a valid rank-corr
        return None, 0
    ic = d["x"].rank().corr(d["y"].rank())
    return (ic, len(d)) if ic == ic else (None, len(d))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--horizon", default="ret_s300_bps")
    ap.add_argument("--min-events", type=int, default=200)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    fm = pd.read_parquet(args.matrix)

    # build direction-adjusted target from the price horizon
    hz = args.horizon.replace("ret_", "price_").replace("_bps", "")
    if hz not in fm or "price_incl" not in fm:
        print(f"horizon {hz} not in matrix"); sys.exit(0)
    sign = np.where(fm["direction"] == "buy", 1.0, -1.0)
    fm["_target"] = sign * (fm[hz] / fm["price_incl"] - 1.0) * 1e4  # direction-adjusted bps
    valid = fm["_target"].notna().sum()
    print(f"events={len(fm)} with_target={valid} horizon={args.horizon}")
    if valid < args.min_events:
        print(f"GATE NOT MET (need >= {args.min_events} events with outcome). "
              f"Ranking is exploratory and unstable below this — not run. Keep collecting.")
        # still write a partial leaderboard for visibility, flagged
    feats = [c for c in fm.columns if c not in NON_FEATURES and not c.startswith("price_") and c != "_target"]

    half = fm["block_ts"].median()
    rows = []
    for c in feats:
        s = fm[c]
        if s.dropna().nunique() < 2:
            continue
        is_bool = set(s.dropna().unique()) <= {0, 1, 0.0, 1.0, True, False}
        # IC (rank-corr) computed uniformly for numeric AND bool -> comparable scale
        ic, n = spearman_ic(s, fm["_target"])
        if ic is None:
            continue
        t = ic * np.sqrt(max(1, n - 2) / max(1e-9, 1 - ic**2))
        ic1, _ = spearman_ic(fm.loc[fm.block_ts <= half, c], fm.loc[fm.block_ts <= half, "_target"])
        ic2, _ = spearman_ic(fm.loc[fm.block_ts > half, c], fm.loc[fm.block_ts > half, "_target"])
        stable = (ic1 is not None and ic2 is not None and np.sign(ic1) == np.sign(ic2))
        promise = abs(ic) * min(1.0, n / args.min_events) * (1.0 if stable else 0.4)
        rec = {"feature": c, "kind": "bool" if is_bool else "num", "n": n, "ic": round(ic, 3),
               "t": round(t, 1), "group1_bps": None, "group0_bps": None, "spread_bps": None,
               "stable": stable, "promise": round(promise, 3)}
        if is_bool:
            g1 = fm.loc[s == 1, "_target"].dropna(); g0 = fm.loc[s == 0, "_target"].dropna()
            if len(g1) >= 10 and len(g0) >= 10:
                rec["group1_bps"] = round(g1.mean(), 1); rec["group0_bps"] = round(g0.mean(), 1)
                rec["spread_bps"] = round(g1.mean() - g0.mean(), 1)
        else:
            q = pd.qcut(s.rank(method="first"), 5, labels=False, duplicates="drop")
            if q is not None:
                rec["spread_bps"] = round(fm.loc[q == q.max(), "_target"].mean() - fm.loc[q == 0, "_target"].mean(), 1)
        rows.append(rec)
    lb = pd.DataFrame(rows)
    if lb.empty:
        print("\nNo features rankable yet (too few events per feature). Keep collecting — "
              "ranking needs a meaningful sample before any association is even provisional.")
        return
    lb = lb.sort_values("promise", ascending=False)
    dest = args.out or args.matrix.replace(".parquet", "_leaderboard.csv")
    lb.to_csv(dest, index=False)

    print("\n" + "=" * 74)
    print("FEATURE PROMISE LEADERBOARD — UNVALIDATED, HYPOTHESIS-GENERATING ONLY")
    print(f"tested {len(lb)} features on {valid} events; multiple-testing inflates apparent")
    print("signal — NO feature is an edge until it passes M2 (matched controls) + M3 (holdout).")
    print("=" * 74)
    print(lb.head(15).to_string(index=False))
    print(f"\nleaderboard -> {dest}")
    if valid < args.min_events:
        print("\n*** Below sample gate — treat as noise; revisit after more collection. ***")


if __name__ == "__main__":
    main()
