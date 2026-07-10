#!/usr/bin/env python3
"""DEX event feature-enrichment engine — compute MANY explanatory variables per event.

Reads the outcome tracker's events (ethereum/outcomes) and enriches each with a wide
feature matrix from FREE sources (public RPC + GeckoTerminal + mempool-recorder join).
This is the "discovery" substrate: rank_dex_features.py scores these vs outcomes.

Feature groups (see reports/feature_catalog.md):
  event      protocol, direction, notional_usd, log_notional, labels
  mempool    pending_visible, inclusion_delay_s, priority_fee_gwei, gas_price_gwei, replaced
  wallet     wallet_nonce*, wallet_eth_balance*, from_is_contract   (* current-state proxy; live capture is exact)
  contract   to_is_contract, token_base_has_code
  ordering   tx_index_in_block, is_first_in_block, block_tx_count, swaps_in_block, same_pool_swaps
  mev_proxy  sandwich_candidate (same-pool swaps bracketing), gas_used, effective_gas_price
  liquidity  pool_liquidity_usd*, swap_size_vs_liq*                 (* current-state proxy)
  token_hist gt_fdv_usd, gt_reserve_usd, gt_vol_h24, gt_market_cap  (GeckoTerminal snapshot)

Point-in-time note: block/log/receipt features are EXACT for recent past blocks (free);
wallet-state & pool-liquidity at the exact block need archive -> we use 'latest' as a
proxy and flag it. The live tracker can capture these exactly at inclusion (enhancement).

No edge claims here — enrichment only. No trading, no wallets, no frontrun.
Usage: build_dex_feature_matrix.py [--data-root DIR] [--max-events N] [--out FILE]
"""
from __future__ import annotations
import argparse, glob, json, os, time, urllib.request
import pandas as pd

# Availability class per feature — the anti-leakage contract (see reports/leakage_audit.md).
#   PRE_INCLUSION      known from the pending tx, before inclusion
#   POST_BLOCK         known once the inclusion block is finalized (exact for recent blocks)
#   CURRENT_STATE_PROXY fetched at 'latest' AFTER the event -> lookahead-UNSAFE, quarantined
FEATURE_CLASSES = {
    "pending_visible": "PRE_INCLUSION", "priority_fee_gwei": "PRE_INCLUSION",
    "gas_price_gwei": "PRE_INCLUSION", "replaced": "PRE_INCLUSION",
    "protocol": "POST_BLOCK", "direction": "POST_BLOCK", "notional_usd": "POST_BLOCK",
    "log_notional": "POST_BLOCK", "tx_index_in_block": "POST_BLOCK", "is_first_in_block": "POST_BLOCK",
    "block_tx_count": "POST_BLOCK", "swaps_in_block": "POST_BLOCK", "same_pool_swaps": "POST_BLOCK",
    "sandwich_candidate": "POST_BLOCK", "gas_used": "POST_BLOCK", "effective_gas_price_gwei": "POST_BLOCK",
    "inclusion_delay_s": "POST_BLOCK", "from_is_contract": "POST_BLOCK", "to_is_contract": "POST_BLOCK",
    "token_base_has_code": "POST_BLOCK",
    "wallet_nonce_proxy": "CURRENT_STATE_PROXY", "wallet_eth_balance_proxy": "CURRENT_STATE_PROXY",
    "gt_fdv_usd": "CURRENT_STATE_PROXY", "gt_reserve_usd": "CURRENT_STATE_PROXY",
    "gt_vol_h24": "CURRENT_STATE_PROXY", "gt_market_cap": "CURRENT_STATE_PROXY",
    "swap_size_vs_liq": "CURRENT_STATE_PROXY",
}
OBSERVABLE_DELAY = {"PRE_INCLUSION": "before inclusion (pending)",
                    "POST_BLOCK": "at inclusion block (~0)",
                    "CURRENT_STATE_PROXY": "unbounded (fetched post-event; may post-date the outcome)"}

RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
GT = "https://api.geckoterminal.com/api/v2"
V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
V3 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
_rid = [0]
_code_cache, _gt_cache, _block_cache = {}, {}, {}


def rpc(method, params):
    _rid[0] += 1
    body = json.dumps({"jsonrpc": "2.0", "id": _rid[0], "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={"content-type": "application/json", "User-Agent": "Mozilla/5.0"})
    for _ in range(3):
        try:
            return json.load(urllib.request.urlopen(req, timeout=20)).get("result")
        except Exception:
            time.sleep(1)
    return None


def has_code(addr):
    if not addr:
        return None
    a = addr.lower()
    if a not in _code_cache:
        c = rpc("eth_getCode", [a, "latest"])
        _code_cache[a] = (c not in (None, "0x", "0x0"))
    return _code_cache[a]


def gt_token(addr):
    a = (addr or "").lower()
    if a and a not in _gt_cache:
        try:
            req = urllib.request.Request(f"{GT}/networks/eth/tokens/{a}", headers={"User-Agent": "x", "Accept": "application/json"})
            d = json.load(urllib.request.urlopen(req, timeout=20))["data"]["attributes"]
            _gt_cache[a] = {"gt_fdv_usd": _f(d.get("fdv_usd")), "gt_reserve_usd": _f(d.get("total_reserve_in_usd")),
                            "gt_vol_h24": _f((d.get("volume_usd") or {}).get("h24")), "gt_market_cap": _f(d.get("market_cap_usd"))}
        except Exception:
            _gt_cache[a] = {}
        time.sleep(2.2)  # GT rate limit
    return _gt_cache.get(a, {})


def block_features(bn_hex, pool, log_index):
    if bn_hex not in _block_cache:
        blk = rpc("eth_getBlockByNumber", [bn_hex, False]) or {}
        logs = rpc("eth_getLogs", [{"fromBlock": bn_hex, "toBlock": bn_hex, "topics": [[V2, V3]]}]) or []
        _block_cache[bn_hex] = (len(blk.get("transactions", [])), logs)
    ntx, logs = _block_cache[bn_hex]
    same = [l for l in logs if l["address"].lower() == pool.lower()]
    li = [int(l["logIndex"], 16) for l in same]
    before = sum(1 for x in li if x < log_index); after = sum(1 for x in li if x > log_index)
    return {"block_tx_count": ntx, "swaps_in_block": len(logs), "same_pool_swaps": len(same),
            "sandwich_candidate": int(before >= 1 and after >= 1)}  # bracketed same-pool = sandwich-shaped


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def enrich(ev, pend_idx, incl_idx):
    import math
    txh = ev["tx_hash"].lower()
    f = {"tx_hash": txh, "block_number": ev["block_number"], "block_ts": ev["block_ts"],
         "pool": ev.get("pool"), "token_base": ev.get("token_base"),  # cluster IDs (not features)
         "protocol": ev["protocol"], "direction": ev["direction"], "notional_usd": ev["notional_usd"],
         "log_notional": math.log10(ev["notional_usd"]) if ev.get("notional_usd") else None,
         "event_labels": ev.get("event_labels"), "price_incl": ev.get("price_incl")}
    # mempool-recorder join (pending visibility)
    p = pend_idx.get(txh); i = incl_idx.get(txh)
    f["pending_visible"] = int(p is not None)
    if p:
        f["priority_fee_gwei"] = p.get("max_priority_gwei"); f["gas_price_gwei"] = p.get("gas_price_gwei")
    if i:
        f["inclusion_delay_s"] = i.get("inclusion_delay_s"); f["replaced"] = int(bool(i.get("replaced")))
    # tx + wallet
    tx = rpc("eth_getTransactionByHash", [txh]) or {}
    frm = tx.get("from"); to = tx.get("to")
    f["from_addr"] = frm.lower() if frm else None   # cluster ID (not a feature)
    f["tx_index_in_block"] = int(tx["transactionIndex"], 16) if tx.get("transactionIndex") else None
    f["is_first_in_block"] = int(f["tx_index_in_block"] == 0) if f["tx_index_in_block"] is not None else None
    f["from_is_contract"] = int(has_code(frm)) if frm else None
    f["to_is_contract"] = int(has_code(to)) if to else None
    if frm:
        n = rpc("eth_getTransactionCount", [frm, "latest"]); f["wallet_nonce_proxy"] = int(n, 16) if n else None
        b = rpc("eth_getBalance", [frm, "latest"]); f["wallet_eth_balance_proxy"] = int(b, 16) / 1e18 if b else None
    # receipt (gas / effective price)
    rc = rpc("eth_getTransactionReceipt", [txh]) or {}
    f["gas_used"] = int(rc["gasUsed"], 16) if rc.get("gasUsed") else None
    f["effective_gas_price_gwei"] = int(rc["effectiveGasPrice"], 16) / 1e9 if rc.get("effectiveGasPrice") else None
    # block/ordering/MEV proxies (exact for recent past blocks)
    f.update(block_features(hex(int(ev["block_number"])), ev["pool"], ev.get("log_index", 0)))
    # contract + token history
    f["token_base_has_code"] = int(has_code(ev.get("token_base"))) if ev.get("token_base") else None
    f.update(gt_token(ev.get("token_base")))
    # liquidity proxy (GT reserve) + swap/liq
    if f.get("gt_reserve_usd"):
        f["swap_size_vs_liq"] = ev["notional_usd"] / f["gt_reserve_usd"] if f["gt_reserve_usd"] else None
    # carry outcomes forward for ranking
    for k in [c for c in ev.keys() if c.startswith("price_b") or c.startswith("price_s")] + ["mfe", "mae"]:
        f[k] = ev.get(k)
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=os.environ.get("DATA_ROOT", "/var/lib/cexrec/data"))
    ap.add_argument("--max-events", type=int, default=400)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    root = args.data_root

    def load(stream):
        fs = glob.glob(f"{root}/ethereum/{stream}/**/*.parquet", recursive=True)
        return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()

    ev = load("outcomes")
    if ev.empty:
        print("no outcome events yet"); return
    pend = load("pending"); incl = load("inclusion")
    pidx = {r["tx_hash"].lower(): r for r in pend.to_dict("records")} if not pend.empty else {}
    iidx = {r["tx_hash"].lower(): r for r in incl.to_dict("records")} if not incl.empty else {}
    ev = ev.sort_values("block_ts").tail(args.max_events)
    rows = []
    for j, e in enumerate(ev.to_dict("records")):
        rows.append(enrich(e, pidx, iidx))
        if (j + 1) % 25 == 0:
            print(f"  enriched {j+1}/{len(ev)}")
    fm = pd.DataFrame(rows)
    dest = args.out or f"{root}/../reports/dex_feature_matrix.parquet"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fm.to_parquet(dest, index=False)
    # availability-class metadata sidecar (the anti-leakage contract)
    meta = {c: {"class": FEATURE_CLASSES.get(c, "UNCLASSIFIED"),
                "observable_delay": OBSERVABLE_DELAY.get(FEATURE_CLASSES.get(c, ""), "unknown")}
            for c in fm.columns if c in FEATURE_CLASSES}
    with open(dest.replace(".parquet", ".classes.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"feature matrix: {len(fm)} events x {fm.shape[1]} cols -> {dest}")
    print(f"class sidecar -> {dest.replace('.parquet', '.classes.json')}")
    feats = [c for c in fm.columns if not (c.startswith("price_") or c in ("tx_hash", "mfe", "mae"))]
    print(f"features computed: {len(feats)}")
    print("coverage:", {c: round(fm[c].notna().mean(), 2) for c in feats[:12]})


if __name__ == "__main__":
    main()
