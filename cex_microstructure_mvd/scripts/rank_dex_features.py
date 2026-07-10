#!/usr/bin/env python3
"""Leakage-safe feature-promise ranking for the DEX discovery engine.

Ranks features SEPARATELY by availability class (from the .classes.json sidecar) so
decision-time information is never mixed:

  --class pre_inclusion  : PRE_INCLUSION features, GROUP A (pending-visible) only,
                           outcomes measured from inclusion price.
  --class post_block     : POST_BLOCK features, all events, outcomes REBASED to +1 block
                           (earliest non-frontrunning entry), horizons after +1 block only.
  --class proxy_research : CURRENT_STATE_PROXY features — LOOKAHEAD-UNSAFE, quarantined,
                           PROHIBITED from M2/M3 promotion until captured point-in-time.

Multiple-testing: reports the EXACT test count (features x horizons), raw p, and
Benjamini-Hochberg FDR q. Dependence: reports unique pools/tokens/wallets and
leave-one-pool-out / leave-one-token-out IC; a candidate is `robust` only if it
survives removing its largest contributing pool AND token.

*** Rankings are UNVALIDATED hypotheses. Nothing is an edge until M2 + M3. ***
No trading, no wallets. Usage: rank_dex_features.py --matrix M.parquet --class post_block
"""
from __future__ import annotations
import argparse, json, math, os, sys
import numpy as np, pandas as pd

ID_COLS = {"tx_hash", "block_number", "block_ts", "pool", "token_base", "from_addr",
           "price_incl", "mfe", "mae", "event_labels"}
BLOCK_HZ = ["price_b1", "price_b2", "price_b5"]
TIME_HZ = ["price_s30", "price_s120", "price_s300", "price_s900", "price_s3600"]


def _phi(x):  # standard normal CDF
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def spearman(x, y):
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(d) < 20 or d["x"].nunique() < 2:
        return None, 0
    ic = d["x"].rank().corr(d["y"].rank())
    return (ic, len(d)) if ic == ic else (None, len(d))


def ic_p(ic, n):
    if ic is None or n < 5 or abs(ic) >= 1:
        return None, None
    t = ic * math.sqrt((n - 2) / max(1e-9, 1 - ic**2))
    p = 2 * (1 - _phi(abs(t)))
    return round(t, 2), p


def bh_qvalues(pvals):
    m = len(pvals); order = np.argsort(pvals)
    q = np.empty(m); prev = 1.0
    for rank, idx in enumerate(reversed(order), start=1):
        i = m - rank + 1
        prev = min(prev, pvals[idx] * m / i)
        q[idx] = prev
    return q


def loo(df, feat, base, horizon, group_col):
    """leave-one-<group>-out IC: recompute IC removing the single largest group."""
    sign = np.where(df["direction"] == "buy", 1.0, -1.0)
    tgt = sign * (df[horizon] / df[base] - 1) * 1e4
    biggest = df[group_col].value_counts().idxmax() if df[group_col].notna().any() else None
    keep = df[group_col] != biggest
    ic, n = spearman(df.loc[keep, feat], tgt[keep])
    return (round(ic, 3) if ic is not None else None), biggest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--class", dest="cls", required=True,
                    choices=["pre_inclusion", "post_block", "proxy_research"])
    ap.add_argument("--min-events", type=int, default=200)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    fm = pd.read_parquet(args.matrix)
    classes = {}
    sc = args.matrix.replace(".parquet", ".classes.json")
    if os.path.exists(sc):
        classes = json.load(open(sc))
    cls_map = {"pre_inclusion": "PRE_INCLUSION", "post_block": "POST_BLOCK", "proxy_research": "CURRENT_STATE_PROXY"}
    want = cls_map[args.cls]

    # event scope + base price + horizons per class
    if args.cls == "pre_inclusion":
        if "pending_visible" in fm:
            fm = fm[fm["pending_visible"] == 1]
        base, horizons = "price_incl", [h for h in BLOCK_HZ + TIME_HZ if h in fm]
    elif args.cls == "post_block":
        base, horizons = "price_b1", [h for h in ["price_b2", "price_b5"] + TIME_HZ if h in fm]  # entry at +1 block
    else:
        base, horizons = "price_incl", [h for h in BLOCK_HZ + TIME_HZ if h in fm]

    feats = [c for c in fm.columns if c not in ID_COLS and not c.startswith("price_")
             and classes.get(c, {}).get("class", None) == want]
    if not feats:
        # fall back to embedded map if sidecar missing
        from importlib.util import spec_from_file_location, module_from_spec
        b = spec_from_file_location("b", os.path.join(os.path.dirname(__file__), "build_dex_feature_matrix.py"))
        mod = module_from_spec(b); b.loader.exec_module(mod)
        feats = [c for c in fm.columns if mod.FEATURE_CLASSES.get(c) == want]

    n_events = len(fm)
    uniq = {"pools": fm["pool"].nunique() if "pool" in fm else None,
            "tokens": fm["token_base"].nunique() if "token_base" in fm else None,
            "wallets": fm["from_addr"].nunique() if "from_addr" in fm else None}
    print(f"class={want} events={n_events} features={len(feats)} horizons={len(horizons)} "
          f"unique_pools/tokens/wallets={uniq['pools']}/{uniq['tokens']}/{uniq['wallets']}")
    n_tests = len(feats) * len(horizons)
    print(f"EXACT TESTS this leaderboard = features x horizons = {n_tests}")

    if base not in fm:
        print(f"base price {base} missing"); sys.exit(0)

    rows, pvals = [], []
    for f in feats:
        s = fm[f]
        if s.dropna().nunique() < 2:
            continue
        for hz in horizons:
            sign = np.where(fm["direction"] == "buy", 1.0, -1.0)
            tgt = sign * (fm[hz] / fm[base] - 1) * 1e4
            ic, n = spearman(s, tgt)
            if ic is None:
                continue
            t, p = ic_p(ic, n)
            rows.append({"feature": f, "class": want, "horizon": hz.replace("price_", "ret_"),
                         "n": n, "ic": round(ic, 3), "t": t, "raw_p": round(p, 4) if p is not None else None})
            pvals.append(p if p is not None else 1.0)
    if not rows:
        print("no rankable (feature,horizon) cells yet — keep collecting."); return
    lb = pd.DataFrame(rows)
    lb["bh_q"] = np.round(bh_qvalues(np.array(pvals)), 4)

    # headline per feature = strongest |ic| horizon; LOO clustering on the headline
    head = lb.loc[lb.groupby("feature")["ic"].apply(lambda s: s.abs().idxmax())].copy()
    ic_pool, ic_tok, robust = [], [], []
    for _, r in head.iterrows():
        hz = r["horizon"].replace("ret_", "price_")
        icp, _ = loo(fm, r["feature"], base, hz, "pool")
        ict, _ = loo(fm, r["feature"], base, hz, "token_base")
        ic_pool.append(icp); ic_tok.append(ict)
        keep = abs(r["ic"]) > 0
        rob = (icp is not None and ict is not None and np.sign(icp) == np.sign(r["ic"])
               and np.sign(ict) == np.sign(r["ic"]) and abs(icp) >= 0.5 * abs(r["ic"]) and abs(ict) >= 0.5 * abs(r["ic"]))
        robust.append(bool(rob))
    head["ic_loo_pool"] = ic_pool; head["ic_loo_token"] = ic_tok; head["robust"] = robust
    # time-half stability
    med = fm["block_ts"].median()
    stab = []
    for _, r in head.iterrows():
        hz = r["horizon"].replace("ret_", "price_"); sign = np.where(fm["direction"] == "buy", 1.0, -1.0)
        tgt = sign * (fm[hz] / fm[base] - 1) * 1e4
        i1, _ = spearman(fm.loc[fm.block_ts <= med, r["feature"]], tgt[fm.block_ts <= med])
        i2, _ = spearman(fm.loc[fm.block_ts > med, r["feature"]], tgt[fm.block_ts > med])
        stab.append(bool(i1 is not None and i2 is not None and np.sign(i1) == np.sign(i2)))
    head["stable"] = stab
    head["promotable"] = ((want != "CURRENT_STATE_PROXY") & (head["bh_q"] < 0.10)
                          & head["stable"] & head["robust"] & (head["n"] >= args.min_events))
    head = head.sort_values("ic", key=lambda s: s.abs(), ascending=False)

    dest = args.out or args.matrix.replace(".parquet", f"_leaderboard_{args.cls}.csv")
    head.to_csv(dest, index=False)
    lb.to_csv(dest.replace(".csv", "_fullgrid.csv"), index=False)

    banner = "LOOKAHEAD-UNSAFE — PROHIBITED FROM M2/M3" if want == "CURRENT_STATE_PROXY" else "UNVALIDATED — HYPOTHESIS ONLY"
    print("\n" + "=" * 80)
    print(f"{args.cls.upper()} LEADERBOARD  [{banner}]")
    print(f"entry base={base}; {n_tests} tests; BH-FDR q reported; promotion needs q<0.10 + stable + robust")
    print("=" * 80)
    cols = ["feature", "horizon", "n", "ic", "t", "raw_p", "bh_q", "ic_loo_pool", "ic_loo_token", "stable", "robust", "promotable"]
    print(head[cols].head(15).to_string(index=False))
    npromo = int(head["promotable"].sum()) if want != "CURRENT_STATE_PROXY" else 0
    print(f"\npromotable candidates (q<0.10, stable, robust, n>= {args.min_events}): {npromo}")
    if n_events < args.min_events:
        print("*** below sample gate — treat ALL rows as noise; revisit after more collection. ***")
    if want == "CURRENT_STATE_PROXY":
        print("*** PROXY features are lookahead-unsafe; leaderboard is research-only, never promoted. ***")


if __name__ == "__main__":
    main()
