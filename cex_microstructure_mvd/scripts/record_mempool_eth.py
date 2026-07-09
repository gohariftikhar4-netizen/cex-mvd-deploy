#!/usr/bin/env python3
"""Ethereum mainnet mempool recorder — pending tx research dataset.

Free public WSS only (default: PublicNode). No key, no auth, no orders, no
private relay. DATA COLLECTION ONLY — not a MEV bot: it never builds, signs,
sends, front-runs, or sandwiches anything. It subscribes to pending tx hashes,
enriches them to full tx via eth_getTransactionByHash, decodes DEX-router
candidates, and tracks block inclusion + delay.

Streams (hourly zstd parquet, atomic writes):
  pending    one row per enriched pending tx (from/to/nonce/value/gas fields/
             selector/router/chain_id + receive & enrich timestamps)
  inclusion  one row per tracked tx once mined (block, block_ts, inclusion_delay,
             success from receipt, replaced flag)

FEASIBILITY NOTE: the free feed is SAMPLED/throttled (~10 tx/s observed vs the
full ~100+/s mempool), so this is a representative sample, not the complete
mempool. Documented in reports/mempool_gate0.md.

Env:
  DATA_ROOT     output dir                 (default ./data)
  ETH_WS_URL    ws endpoint                (default wss://ethereum-rpc.publicnode.com)
  ENRICH        1=enrich pending to full tx (default 1); 0=hash+timing only
  FETCH_RECEIPT 1=fetch receipt for status (default 1)
  MAX_TRACK     max in-flight tracked txs  (default 20000)
  RUN_SECONDS   stop after N s (0=forever; smoke tests)
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import time

import pyarrow as pa
import pyarrow.parquet as pq
import websockets

CHAIN = "ethereum"
PROVIDER = "publicnode"
DATA_ROOT = os.environ.get("DATA_ROOT", "./data")
WS_URL = os.environ.get("ETH_WS_URL", "wss://ethereum-rpc.publicnode.com")
ENRICH = os.environ.get("ENRICH", "1") == "1"
FETCH_RECEIPT = os.environ.get("FETCH_RECEIPT", "1") == "1"
MAX_TRACK = int(os.environ.get("MAX_TRACK", "20000"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "0"))
CA = os.environ.get("SSL_CERT_FILE")
_ssl = ssl.create_default_context(cafile=CA) if CA else ssl.create_default_context()

# Known DEX routers/aggregators (lowercased). Data labelling only.
ROUTERS = {
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d": "uniswap_v2_router",
    "0xe592427a0aece92de3edee1f18e0157c05861564": "uniswap_v3_router",
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45": "uniswap_v3_router2",
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad": "uniswap_universal_router",
    "0x66a9893cc07d91d95644aedd05d03f95e1dba8af": "uniswap_universal_router2",
    "0x1111111254eeb25477b68fb85ed929f73a960582": "1inch_v5",
    "0x111111125421ca6dc452d289314280a0f8842a65": "1inch_v6",
    "0xdef1c0ded9bec7f1a1670819833240f027b25eff": "0x_exchange_proxy",
    "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f": "sushi_router",
    "0x9008d19f58aabd9ed0d60971565aa8510560ab41": "cowswap_settlement",
}
# Common swap selectors (first 4 bytes of calldata) -> label.
SELECTORS = {
    "0x38ed1739": "swapExactTokensForTokens",
    "0x8803dbee": "swapTokensForExactTokens",
    "0x7ff36ab5": "swapExactETHForTokens",
    "0x18cbafe5": "swapExactTokensForETH",
    "0x414bf389": "exactInputSingle",
    "0xc04b8d59": "exactInput",
    "0xdb3e2198": "exactOutputSingle",
    "0x5ae401dc": "multicall(v3)",
    "0xac9650d8": "multicall",
    "0x3593564c": "universalRouter.execute",
    "0x12aa3caf": "1inch.swap",
    "0xe449022e": "1inch.uniswapV3Swap",
}

PENDING_SCHEMA = pa.schema([
    ("chain", pa.string()), ("provider", pa.string()),
    ("tx_hash", pa.string()), ("ts_local_receive", pa.int64()),
    ("ts_enrich", pa.int64()), ("from_addr", pa.string()), ("to_addr", pa.string()),
    ("nonce", pa.int64()), ("value_eth", pa.float64()),
    ("gas", pa.int64()), ("gas_price_gwei", pa.float64()),
    ("max_fee_gwei", pa.float64()), ("max_priority_gwei", pa.float64()),
    ("tx_type", pa.string()), ("chain_id", pa.int64()),
    ("selector", pa.string()), ("method", pa.string()),
    ("router", pa.string()), ("calldata_len", pa.int64()),
])
INCLUSION_SCHEMA = pa.schema([
    ("chain", pa.string()), ("tx_hash", pa.string()),
    ("ts_local_receive", pa.int64()), ("block_number", pa.int64()),
    ("block_ts", pa.int64()), ("inclusion_delay_s", pa.float64()),
    ("success", pa.string()), ("replaced", pa.bool_()),
])


def _log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


class Rotator:
    """Minimal hourly parquet rotator with atomic writes (self-contained)."""
    def __init__(self, stream, schema, max_rows=4000, max_s=60):
        self.stream, self.schema = stream, schema
        self.max_rows, self.max_s = max_rows, max_s
        self.buf, self.last, self.seq = [], time.time(), 0

    def add(self, row):
        self.buf.append(row)
        if len(self.buf) >= self.max_rows or time.time() - self.last >= self.max_s:
            self.flush()

    def flush(self):
        if not self.buf:
            self.last = time.time(); return
        rows, self.buf = self.buf, []
        self.last = time.time(); self.seq += 1
        tm = time.gmtime()
        d = os.path.join(DATA_ROOT, CHAIN, self.stream,
                         f"date={time.strftime('%Y-%m-%d', tm)}", f"hour={time.strftime('%H', tm)}")
        os.makedirs(d, exist_ok=True)
        final = os.path.join(d, f"part-{int(time.time()*1000)}-{self.seq:06d}.parquet")
        cols = {n: [r.get(n) for r in rows] for n in self.schema.names}
        pq.write_table(pa.table(cols, schema=self.schema), final + ".tmp", compression="zstd")
        os.replace(final + ".tmp", final)
        _log(f"{self.stream}: flushed {len(rows)} -> {final}")


rot_pending = Rotator("pending", PENDING_SCHEMA)
rot_incl = Rotator("inclusion", INCLUSION_SCHEMA)

# tracked: tx_hash -> {receive, from, nonce}; by_key: (from,nonce)->hash for replace detection
tracked: dict[str, dict] = {}
by_key: dict[tuple, str] = {}
_rid = [1000]


def _next_id():
    _rid[0] += 1
    return _rid[0]


def _wei_hex_to_eth(h):
    try:
        return int(h, 16) / 1e18
    except Exception:
        return None


def _gwei(h):
    try:
        return int(h, 16) / 1e9
    except Exception:
        return None


async def _rpc(ws, pending_rpc, method, params):
    rid = _next_id()
    fut = asyncio.get_event_loop().create_future()
    pending_rpc[rid] = fut
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    return await asyncio.wait_for(fut, timeout=12)


def _enrich_row(tx, receive_ts):
    inp = tx.get("input", "0x") or "0x"
    sel = inp[:10] if len(inp) >= 10 else ""
    to = (tx.get("to") or "").lower()
    return {
        "chain": CHAIN, "provider": PROVIDER, "tx_hash": tx.get("hash"),
        "ts_local_receive": receive_ts, "ts_enrich": int(time.time() * 1000),
        "from_addr": (tx.get("from") or "").lower(), "to_addr": to,
        "nonce": int(tx.get("nonce", "0x0"), 16),
        "value_eth": _wei_hex_to_eth(tx.get("value", "0x0")),
        "gas": int(tx.get("gas", "0x0"), 16),
        "gas_price_gwei": _gwei(tx.get("gasPrice", "0x0")),
        "max_fee_gwei": _gwei(tx.get("maxFeePerGas")) if tx.get("maxFeePerGas") else None,
        "max_priority_gwei": _gwei(tx.get("maxPriorityFeePerGas")) if tx.get("maxPriorityFeePerGas") else None,
        "tx_type": tx.get("type"), "chain_id": int(tx.get("chainId", "0x1"), 16) if tx.get("chainId") else 1,
        "selector": sel, "method": SELECTORS.get(sel, ""),
        "router": ROUTERS.get(to, ""), "calldata_len": (len(inp) - 2) // 2,
    }


async def main():
    deadline = time.time() + RUN_SECONDS if RUN_SECONDS else None
    while True:
        try:
            async with websockets.connect(WS_URL, ssl=_ssl, ping_interval=20,
                                          ping_timeout=10, max_size=None) as ws:
                pending_rpc: dict[int, asyncio.Future] = {}
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe",
                                          "params": ["newPendingTransactions"]}))
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "eth_subscribe",
                                          "params": ["newHeads"]}))
                sub_pending = sub_heads = None
                enrich_q: asyncio.Queue = asyncio.Queue(maxsize=50000)
                block_q: asyncio.Queue = asyncio.Queue(maxsize=1000)
                counters = {"pending": 0, "enriched": 0, "included": 0, "replaced": 0}
                _log(f"connected {WS_URL} enrich={ENRICH}")

                # --- workers: ALL RPC calls happen here, never in the read loop
                #     (the read loop is the only place that resolves the futures) ---
                async def enrich_worker():
                    while True:
                        h, rts = await enrich_q.get()
                        try:
                            tx = await _rpc(ws, pending_rpc, "eth_getTransactionByHash", [h])
                            if tx:
                                row = _enrich_row(tx, rts)
                                rot_pending.add(row); counters["enriched"] += 1
                                key = (row["from_addr"], row["nonce"])
                                if key in by_key and by_key[key] != h and by_key[key] in tracked:
                                    tracked[by_key[key]]["replaced"] = True
                                    counters["replaced"] += 1
                                by_key[key] = h
                        except Exception:
                            pass
                        finally:
                            enrich_q.task_done()

                async def block_worker():
                    while True:
                        bn, bts = await block_q.get()
                        try:
                            blk = await _rpc(ws, pending_rpc, "eth_getBlockByNumber", [bn, False])
                            if blk:
                                bnum = int(bn, 16)
                                for th in blk.get("transactions", []):
                                    if th in tracked:
                                        info = tracked.pop(th)
                                        success = ""
                                        if FETCH_RECEIPT:
                                            try:
                                                rc = await _rpc(ws, pending_rpc, "eth_getTransactionReceipt", [th])
                                                if rc:
                                                    success = "success" if rc.get("status") == "0x1" else "failed"
                                            except Exception:
                                                pass
                                        rot_incl.add({
                                            "chain": CHAIN, "tx_hash": th,
                                            "ts_local_receive": info["receive"], "block_number": bnum,
                                            "block_ts": bts * 1000,
                                            "inclusion_delay_s": max(0.0, bts - info["receive"] / 1000.0),
                                            "success": success, "replaced": info.get("replaced", False),
                                        })
                                        counters["included"] += 1
                                if len(tracked) > MAX_TRACK * 0.9:  # evict stale (dropped)
                                    cutoff = int(time.time() * 1000) - 180000
                                    for k in [k for k, v in tracked.items() if v["receive"] < cutoff][:5000]:
                                        tracked.pop(k, None)
                        except Exception:
                            pass
                        finally:
                            block_q.task_done()

                async def heartbeat():
                    while True:
                        await asyncio.sleep(15)
                        rot_pending.flush(); rot_incl.flush()
                        _log("alive " + " ".join(f"{k}={v}" for k, v in counters.items())
                             + f" tracked={len(tracked)} q={enrich_q.qsize()}")

                async def timer():
                    if deadline:
                        await asyncio.sleep(max(0, deadline - time.time()))
                        await ws.close()  # breaks the read loop cleanly

                tasks = [asyncio.create_task(enrich_worker()) for _ in range(4)]
                tasks += [asyncio.create_task(block_worker()), asyncio.create_task(heartbeat()),
                          asyncio.create_task(timer())]

                # --- read loop: parse + dispatch ONLY, no awaited RPCs ---
                async for raw in ws:
                    msg = json.loads(raw)
                    mid = msg.get("id")
                    if mid in pending_rpc:
                        fut = pending_rpc.pop(mid)
                        if not fut.done():
                            fut.set_result(msg.get("result"))
                        continue
                    if mid == 1:
                        sub_pending = msg.get("result"); continue
                    if mid == 2:
                        sub_heads = msg.get("result"); continue
                    p = msg.get("params", {})
                    sub = p.get("subscription")
                    if sub == sub_pending:
                        h = p.get("result")
                        if not h:
                            continue
                        rts = int(time.time() * 1000)
                        counters["pending"] += 1
                        if len(tracked) < MAX_TRACK:
                            tracked[h] = {"receive": rts, "replaced": False}
                        if ENRICH and not enrich_q.full():
                            enrich_q.put_nowait((h, rts))
                    elif sub == sub_heads:
                        if not block_q.full():
                            block_q.put_nowait((p["result"]["number"],
                                                int(p["result"].get("timestamp", "0x0"), 16)))

                for t in tasks:
                    t.cancel()
                rot_pending.flush(); rot_incl.flush()
                if deadline and time.time() >= deadline:
                    _log("RUN_SECONDS reached; exiting")
                    return
        except Exception as e:
            _log(f"ws error: {e}; reconnect in 5s")
            rot_pending.flush(); rot_incl.flush()
            if deadline and time.time() >= deadline:
                return
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        rot_pending.flush(); rot_incl.flush()
