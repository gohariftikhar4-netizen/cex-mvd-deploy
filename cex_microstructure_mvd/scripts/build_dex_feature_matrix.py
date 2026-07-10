#!/usr/bin/env python3
"""DEX event feature-enrichment engine (fast, robust) — many explanatory vars per event.

Reads outcome-tracker events (ethereum/outcomes) and enriches each with a wide feature
matrix from FREE sources. Optimized after the v1 enricher hung (~10 sequential RPC +
2.2s GeckoTerminal sleep per event):
  * JSON-RPC BATCHING (one HTTP request per event round; block calls batched + cached)
  * bounded CONCURRENCY (thread pool over independent events)
  * per-call TIMEOUT, RETRIES w/ exponential backoff, 429 rate-limit handling
  * CHECKPOINT/RESUME (skip already-enriched tx_hash; append; atomic write)
  * overall TIME BUDGET (never hangs — writes what it has and exits)
  * GeckoTerminal OFF by default (its fields are CURRENT_STATE_PROXY = quarantined,
    never promotable, so not worth the rate-limit cost; enable with --with-gt)

Feature classes (PRE_INCLUSION / POST_BLOCK / CURRENT_STATE_PROXY) emitted to a
<matrix>.classes.json sidecar and enforced by rank_dex_features.py. No edge claims here.
No trading, no wallets. Usage: build_dex_feature_matrix.py [--max-events N] [--workers 8] [--with-gt]
"""
from __future__ import annotations
import argparse, glob, json, math, os, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

FEATURE_CLASSES = {
    "pending_visible": "PRE_INCLUSION", "priority_fee_gwei": "PRE_INCLUSION",
    "gas_price_gwei": "PRE_INCLUSION", "replaced": "PRE_INCLUSION",
    "protocol": "POST_BLOCK", "direction": "POST_BLOCK", "notional_usd": "POST_BLOCK",
    "log_notional": "POST_BLOCK", "tx_index_in_block": "POST_BLOCK", "is_first_in_block": "POST_BLOCK",
    "block_tx_count": "POST_BLOCK", "swaps_in_block": "POST_BLOCK", "same_pool_swaps": "POST_BLOCK",
    "sandwich_candidate": "POST_BLOCK", "gas_used": "POST_BLOCK", "effective_gas_price_gwei": "POST_BLOCK",
    "inclusion_delay_s": "POST_BLOCK", "from_is_contract": "POST_BLOCK", "to_is_contract": "POST_BLOCK",
    "token_base_has_code": "POST_BLOCK", "nonce_at_event": "POST_BLOCK",
    "wallet_eth_balance_proxy": "CURRENT_STATE_PROXY",
    "gt_fdv_usd": "CURRENT_STATE_PROXY", "gt_reserve_usd": "CURRENT_STATE_PROXY",
    "gt_vol_h24": "CURRENT_STATE_PROXY", "gt_market_cap": "CURRENT_STATE_PROXY", "swap_size_vs_liq": "CURRENT_STATE_PROXY",
}
OBSERVABLE_DELAY = {"PRE_INCLUSION": "before inclusion (pending)", "POST_BLOCK": "at inclusion block (~0)",
                    "CURRENT_STATE_PROXY": "unbounded (fetched post-event; may post-date the outcome)"}

RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
GT = "https://api.geckoterminal.com/api/v2"
V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
V3 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
TIMEOUT = float(os.environ.get("RPC_TIMEOUT", "12"))
_lock = threading.Lock(); _rid = [0]
_code_cache, _block_cache, _gt_cache = {}, {}, {}
_min_interval = float(os.environ.get("RPC_MIN_INTERVAL", "0.0")); _last = [0.0]


def _throttle():
    if _min_interval <= 0:
        return
    with _lock:
        dt = time.time() - _last[0]
        if dt < _min_interval:
            time.sleep(_min_interval - dt)
        _last[0] = time.time()


def rpc_batch(calls):
    """calls=[(method,params),...] -> results in order. One HTTP round; retried w/ backoff."""
    if not calls:
        return []
    with _lock:
        base = _rid[0]; _rid[0] += len(calls)
    payload = [{"jsonrpc": "2.0", "id": base + i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
    body = json.dumps(payload).encode()
    for attempt in range(4):
        _throttle()
        try:
            req = urllib.request.Request(RPC, data=body, headers={"content-type": "application/json", "User-Agent": "Mozilla/5.0"})
            resp = json.load(urllib.request.urlopen(req, timeout=TIMEOUT))
            by = {r.get("id"): r.get("result") for r in resp} if isinstance(resp, list) else {}
            return [by.get(base + i) for i in range(len(calls))]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
            elif attempt == 3:
                return [None] * len(calls)
            else:
                time.sleep(1 + attempt)
        except Exception:
            if attempt == 3:
                return [None] * len(calls)
            time.sleep(1 + attempt)
    return [None] * len(calls)


def rpc(method, params):
    return rpc_batch([(method, params)])[0]


def _is_code(c):
    return int(c not in (None, "0x", "0x0")) if c is not None else None


def block_data(bn):
    h = hex(int(bn))
    if h not in _block_cache:
        blk, logs = rpc_batch([("eth_getBlockByNumber", [h, False]),
                               ("eth_getLogs", [{"fromBlock": h, "toBlock": h, "topics": [[V2, V3]]}])])
        _block_cache[h] = (len((blk or {}).get("transactions", [])), logs or [])
    return _block_cache[h]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def gt_token(addr, on):
    if not on or not addr:
        return {}
    a = addr.lower()
    if a not in _gt_cache:
        try:
            req = urllib.request.Request(f"{GT}/networks/eth/tokens/{a}", headers={"User-Agent": "x", "Accept": "application/json"})
            d = json.load(urllib.request.urlopen(req, timeout=TIMEOUT))["data"]["attributes"]
            _gt_cache[a] = {"gt_fdv_usd": _f(d.get("fdv_usd")), "gt_reserve_usd": _f(d.get("total_reserve_in_usd")),
                            "gt_vol_h24": _f((d.get("volume_usd") or {}).get("h24")), "gt_market_cap": _f(d.get("market_cap_usd"))}
        except Exception:
            _gt_cache[a] = {}
        time.sleep(2.2)
    return _gt_cache.get(a, {})


def enrich(ev, pidx, iidx, with_gt):
    txh = ev["tx_hash"].lower()
    f = {"tx_hash": txh, "block_number": ev["block_number"], "block_ts": ev["block_ts"],
         "pool": ev.get("pool"), "token_base": ev.get("token_base"),
         "protocol": ev["protocol"], "direction": ev["direction"], "notional_usd": ev["notional_usd"],
         "log_notional": math.log10(ev["notional_usd"]) if ev.get("notional_usd") else None,
         "event_labels": ev.get("event_labels"), "price_incl": ev.get("price_incl")}
    p = pidx.get(txh); i = iidx.get(txh)
    f["pending_visible"] = int(p is not None)
    if p:
        f["priority_fee_gwei"] = p.get("max_priority_gwei"); f["gas_price_gwei"] = p.get("gas_price_gwei")
    if i:
        f["inclusion_delay_s"] = i.get("inclusion_delay_s"); f["replaced"] = int(bool(i.get("replaced")))
    tx, rc = rpc_batch([("eth_getTransactionByHash", [txh]), ("eth_getTransactionReceipt", [txh])])
    tx = tx or {}; rc = rc or {}
    frm = tx.get("from"); to = tx.get("to")
    f["from_addr"] = frm.lower() if frm else None
    f["tx_index_in_block"] = int(tx["transactionIndex"], 16) if tx.get("transactionIndex") else None
    f["is_first_in_block"] = int(f["tx_index_in_block"] == 0) if f["tx_index_in_block"] is not None else None
    f["nonce_at_event"] = int(tx["nonce"], 16) if tx.get("nonce") else None   # POINT-IN-TIME nonce (free, from the tx)
    f["gas_used"] = int(rc["gasUsed"], 16) if rc.get("gasUsed") else None
    f["effective_gas_price_gwei"] = int(rc["effectiveGasPrice"], 16) / 1e9 if rc.get("effectiveGasPrice") else None
    bal = None
    if frm:
        codes = rpc_batch([("eth_getCode", [frm.lower(), "latest"]), ("eth_getCode", [(to or "").lower(), "latest"]),
                           ("eth_getCode", [(ev.get("token_base") or "").lower(), "latest"]),
                           ("eth_getBalance", [frm.lower(), "latest"])])
        f["from_is_contract"] = _is_code(codes[0])
        f["to_is_contract"] = _is_code(codes[1]) if to else None
        f["token_base_has_code"] = _is_code(codes[2]) if ev.get("token_base") else None
        bal = codes[3]
    f["wallet_eth_balance_proxy"] = int(bal, 16) / 1e18 if bal else None
    ntx, logs = block_data(ev["block_number"])
    same = [l for l in logs if l["address"].lower() == (ev.get("pool") or "").lower()]
    li = int(ev.get("log_index", 0))
    before = sum(1 for l in same if int(l["logIndex"], 16) < li); after = sum(1 for l in same if int(l["logIndex"], 16) > li)
    f["block_tx_count"] = ntx; f["swaps_in_block"] = len(logs); f["same_pool_swaps"] = len(same)
    f["sandwich_candidate"] = int(before >= 1 and after >= 1)
    g = gt_token(ev.get("token_base"), with_gt); f.update(g)
    if g.get("gt_reserve_usd"):
        f["swap_size_vs_liq"] = ev["notional_usd"] / g["gt_reserve_usd"]
    for k in [c for c in ev.keys() if c.startswith("price_b") or c.startswith("price_s")] + ["mfe", "mae"]:
        f[k] = ev.get(k)
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=os.environ.get("DATA_ROOT", "/var/lib/cexrec/data"))
    ap.add_argument("--max-events", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=int(os.environ.get("ENRICH_WORKERS", "8")))
    ap.add_argument("--time-budget", type=int, default=int(os.environ.get("ENRICH_TIME_BUDGET", "900")))
    ap.add_argument("--with-gt", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    root = args.data_root
    dest = args.out or f"{root}/../reports/dex_feature_matrix.parquet"
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    def load(stream):
        fs = glob.glob(f"{root}/ethereum/{stream}/**/*.parquet", recursive=True)
        return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()

    ev = load("outcomes")
    if ev.empty:
        print("no outcome events yet"); return
    pend = load("pending"); incl = load("inclusion")
    pidx = {r["tx_hash"].lower(): r for r in pend.to_dict("records")} if not pend.empty else {}
    iidx = {r["tx_hash"].lower(): r for r in incl.to_dict("records")} if not incl.empty else {}

    done = set()
    if os.path.exists(dest):
        try:
            done = set(pd.read_parquet(dest, columns=["tx_hash"])["tx_hash"].str.lower())
        except Exception:
            done = set()
    ev = ev.sort_values("block_ts").tail(args.max_events)
    todo = [e for e in ev.to_dict("records") if e["tx_hash"].lower() not in done]
    print(f"events={len(ev)} already_enriched={len(done)} to_enrich={len(todo)} workers={args.workers} gt={args.with_gt}")
    if not todo:
        print("nothing new to enrich"); return

    t0 = time.time(); rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(enrich, e, pidx, iidx, args.with_gt) for e in todo]
        for k, fut in enumerate(futs):
            if time.time() - t0 > args.time_budget:
                print(f"time budget {args.time_budget}s reached — stopping at {k}, writing partial"); break
            try:
                rows.append(fut.result(timeout=args.time_budget))
            except Exception:
                pass
            if (k + 1) % 50 == 0:
                print(f"  enriched {k+1}/{len(todo)}  ({(k+1)/(time.time()-t0):.1f}/s)")
    if not rows:
        print("no rows enriched"); return
    new = pd.DataFrame(rows)
    if done and os.path.exists(dest):
        new = pd.concat([pd.read_parquet(dest), new], ignore_index=True).drop_duplicates("tx_hash")
    tmp = dest + ".tmp"; new.to_parquet(tmp, index=False); os.replace(tmp, dest)
    meta = {c: {"class": FEATURE_CLASSES[c], "observable_delay": OBSERVABLE_DELAY[FEATURE_CLASSES[c]]}
            for c in new.columns if c in FEATURE_CLASSES}
    json.dump(meta, open(dest.replace(".parquet", ".classes.json"), "w"), indent=2)
    dt = time.time() - t0
    print(f"feature matrix: {len(new)} events x {new.shape[1]} cols -> {dest}  ({len(rows)} new in {dt:.0f}s, {len(rows)/max(dt,1):.1f}/s)")


if __name__ == "__main__":
    main()
