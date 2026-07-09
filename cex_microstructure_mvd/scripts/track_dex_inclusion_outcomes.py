#!/usr/bin/env python3
"""Live post-inclusion DEX outcome tracker (Ethereum).

Detects INCLUDED Uniswap V2/V3 swaps from block logs, identifies pool + tokens,
records the executed price at inclusion, then measures post-inclusion price at
+1/+2/+5 blocks and +30s/+2m/+5m/+15m/+60m — all via free public RPC at `latest`
(the tracker runs live-forward, so future horizons are just current state; no
archive, no key, no paid API).

MEASUREMENT ONLY. No trading, no same-block execution, no frontrun/sandwich,
no private relay, no wallet keys. Entries are never placed; this only records
what the price DID after public inclusion, for later event studies.

Env:
  DATA_ROOT          output dir (default ./data)
  ETH_WS_URL         newHeads feed (default wss://ethereum-rpc.publicnode.com)
  ETH_RPC_URL        request/response RPC (default https://ethereum-rpc.publicnode.com)
  MIN_NOTIONAL_USD   only track swaps >= this notional (default 5000) to bound RPC
  RUN_SECONDS        stop after N s (0 = forever)
"""
from __future__ import annotations
import asyncio, json, os, ssl, time, urllib.request
import pyarrow as pa, pyarrow.parquet as pq
import websockets

DATA_ROOT = os.environ.get("DATA_ROOT", "./data")
WS_URL = os.environ.get("ETH_WS_URL", "wss://ethereum-rpc.publicnode.com")
RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
MIN_NOTIONAL_USD = float(os.environ.get("MIN_NOTIONAL_USD", "5000"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "0"))
CA = os.environ.get("SSL_CERT_FILE")
_ssl = ssl.create_default_context(cafile=CA) if CA else ssl.create_default_context()

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
STABLES = {"0xdac17f958d2ee523a2206206994597c13d831ec7": 6,   # USDT
           "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,   # USDC
           "0x6b175474e89094c44da98b954eedeac495271d0f": 18}  # DAI
V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
V3_SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
USDC_WETH_V3 = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"  # for ETH/USD

BLOCK_HZ = [int(x) for x in os.environ.get("BLOCK_HZ", "1,2,5").split(",")]
SEC_HZ = [int(x) for x in os.environ.get("SEC_HZ", "30,120,300,900,3600").split(",")]

OUT_SCHEMA = pa.schema([
    ("tx_hash", pa.string()), ("chain", pa.string()), ("protocol", pa.string()),
    ("block_number", pa.int64()), ("block_ts", pa.int64()), ("log_index", pa.int64()),
    ("pool", pa.string()), ("token_base", pa.string()), ("token_quote", pa.string()),
    ("direction", pa.string()), ("amount_base", pa.float64()), ("amount_quote", pa.float64()),
    ("notional_usd", pa.float64()), ("price_incl", pa.float64()), ("eth_usd", pa.float64()),
    ("event_labels", pa.string()),
] + [(f"price_b{b}", pa.float64()) for b in BLOCK_HZ]
  + [(f"price_s{s}", pa.float64()) for s in SEC_HZ]
  + [("mfe", pa.float64()), ("mae", pa.float64()), ("eth_rel_ret_s300", pa.float64())])

_dec_cache: dict[str, int] = {}
_tok_cache: dict[str, tuple] = {}
_rid = [0]


def _log(m): print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {m}", flush=True)


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


def ecall(to, data, block="latest"):
    return rpc("eth_call", [{"to": to, "data": data}, block])


def decimals(tok):
    if tok not in _dec_cache:
        if tok in STABLES:
            _dec_cache[tok] = STABLES[tok]
        elif tok == WETH:
            _dec_cache[tok] = 18
        else:
            r = ecall(tok, "0x313ce567")
            _dec_cache[tok] = int(r[2:], 16) if r and r != "0x" else 18
    return _dec_cache[tok]


def pool_tokens(pool):
    if pool not in _tok_cache:
        t0 = ecall(pool, "0x0dfe1681"); t1 = ecall(pool, "0xd21220a7")
        t0 = ("0x" + t0[-40:]).lower() if t0 and len(t0) >= 42 else None
        t1 = ("0x" + t1[-40:]).lower() if t1 and len(t1) >= 42 else None
        _tok_cache[pool] = (t0, t1)
    return _tok_cache[pool]


def pool_price(pool, is_v3, d0, d1, block="latest"):
    """quote(token1) per base(token0), decimal-adjusted."""
    if is_v3:
        s = ecall(pool, "0x3850c7bd", block)
        if not s or len(s) < 66:
            return None
        sp = int(s[2:66], 16)
        return (sp / 2**96) ** 2 * (10**d0 / 10**d1)
    r = ecall(pool, "0x0902f1ac", block)
    if not r or len(r) < 130:
        return None
    r0 = int(r[2:66], 16); r1 = int(r[66:130], 16)
    return (r1 / 10**d1) / (r0 / 10**d0) if r0 else None


def eth_usd():
    d0 = decimals("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"); d1 = decimals(WETH)  # USDC/WETH pool token0=USDC,token1=WETH
    p = pool_price(USDC_WETH_V3, True, d0, d1)   # WETH per USDC
    return (1.0 / p) if p else None


class Rotator:
    def __init__(self, schema, stream="outcomes", max_rows=500, max_s=120):
        self.schema, self.stream = schema, stream
        self.max_rows, self.max_s = max_rows, max_s
        self.buf, self.last, self.seq = [], time.time(), 0

    def add(self, row):
        self.buf.append(row)
        if len(self.buf) >= self.max_rows or time.time() - self.last >= self.max_s:
            self.flush()

    def flush(self):
        if not self.buf:
            self.last = time.time(); return
        rows, self.buf = self.buf, []; self.last = time.time(); self.seq += 1
        tm = time.gmtime()
        d = os.path.join(DATA_ROOT, "ethereum", self.stream, f"date={time.strftime('%Y-%m-%d', tm)}", f"hour={time.strftime('%H', tm)}")
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, f"part-{int(time.time()*1000)}-{self.seq:06d}.parquet")
        cols = {n: [r.get(n) for r in rows] for n in self.schema.names}
        pq.write_table(pa.table(cols, schema=self.schema), f + ".tmp", compression="zstd")
        os.replace(f + ".tmp", f)
        _log(f"{self.stream}: flushed {len(rows)} -> {f}")


rot = Rotator(OUT_SCHEMA)
active: list[dict] = []          # events awaiting outcome fills
recent_by_pool: dict[str, list] = {}  # pool -> recent event times (cluster labels)


def decode_swap(log, is_v3):
    pool = log["address"].lower()
    t0, t1 = pool_tokens(pool)
    if not t0 or not t1:
        return None
    # base = the non-quote token; quote = WETH or stable
    if t1 == WETH or t1 in STABLES:
        base, quote, base_is0 = t0, t1, True
    elif t0 == WETH or t0 in STABLES:
        base, quote, base_is0 = t1, t0, False
    else:
        return None  # token/token pool, skip (no free USD anchor)
    d0, d1 = decimals(t0), decimals(t1)
    data = log["data"][2:]
    if is_v3:
        def i256(h):
            v = int(h, 16); return v - (1 << 256) if v >= (1 << 255) else v
        a0 = i256(data[0:64]) / 10**d0; a1 = i256(data[64:128]) / 10**d1
        amt0, amt1 = a0, a1
    else:
        a0in = int(data[0:64], 16)/10**d0; a1in = int(data[64:128], 16)/10**d1
        a0out = int(data[128:192], 16)/10**d0; a1out = int(data[192:256], 16)/10**d1
        amt0 = a0out - a0in; amt1 = a1out - a1in
    base_amt = amt0 if base_is0 else amt1
    quote_amt = amt1 if base_is0 else amt0
    direction = "buy" if base_amt > 0 else "sell"  # base flowing to trader = buy
    price = abs(quote_amt) / abs(base_amt) if base_amt else None  # quote per base
    quote_usd = 1.0 if quote in STABLES else (eth_usd() or 0)
    notional = abs(quote_amt) * quote_usd
    return {"pool": pool, "base": base, "quote": quote, "d0": d0, "d1": d1, "is_v3": is_v3,
            "base_is0": base_is0, "direction": direction, "price": price,
            "amount_base": abs(base_amt), "amount_quote": abs(quote_amt), "notional_usd": notional,
            "quote_is_stable": quote in STABLES}


def label(ev):
    labs = ["LARGE_SWAP"] if ev["notional_usd"] >= MIN_NOTIONAL_USD * 2 else []
    labs.append("BUY_CLUSTER" if ev["direction"] == "buy" else "SELL_CLUSTER") if _cluster(ev) else None
    labs.append(ev["direction"].upper())
    return ";".join([x for x in labs if x])


def _cluster(ev):
    now = time.time(); pool = ev["pool"]
    recent_by_pool.setdefault(pool, [])
    recent_by_pool[pool] = [(t, d) for (t, d) in recent_by_pool[pool] if now - t < 60]
    same = sum(1 for (t, d) in recent_by_pool[pool] if d == ev["direction"])
    recent_by_pool[pool].append((now, ev["direction"]))
    return same >= 2


def process_block(bn_hex, bts):
    logs = rpc("eth_getLogs", [{"fromBlock": bn_hex, "toBlock": bn_hex, "topics": [[V2_SWAP, V3_SWAP]]}]) or []
    bn = int(bn_hex, 16)
    added = 0
    for lg in logs:
        is_v3 = lg["topics"][0] == V3_SWAP
        try:
            d = decode_swap(lg, is_v3)
        except Exception:
            d = None
        if not d or not d["price"] or d["notional_usd"] < MIN_NOTIONAL_USD:
            continue
        ev = {"tx_hash": lg["transactionHash"], "chain": "ethereum",
              "protocol": "uniswap_v3" if is_v3 else "uniswap_v2",
              "block_number": bn, "block_ts": bts * 1000, "log_index": int(lg["logIndex"], 16),
              "pool": d["pool"], "token_base": d["base"], "token_quote": d["quote"],
              "direction": d["direction"], "amount_base": d["amount_base"], "amount_quote": d["amount_quote"],
              "notional_usd": round(d["notional_usd"], 2), "price_incl": d["price"], "eth_usd": eth_usd() or 0,
              "_meta": d, "_start_bn": bn, "_start_t": time.time(), "_prices": [d["price"]],
              **{f"price_b{b}": None for b in BLOCK_HZ}, **{f"price_s{s}": None for s in SEC_HZ}}
        ev["event_labels"] = label(d)
        active.append(ev); added += 1
    if added:
        _log(f"block {bn}: {len(logs)} swaps, +{added} tracked (active={len(active)})")
    # fill block-horizon outcomes
    for ev in active:
        for b in BLOCK_HZ:
            if ev[f"price_b{b}"] is None and bn >= ev["_start_bn"] + b:
                m = ev["_meta"]; ev[f"price_b{b}"] = pool_price(ev["pool"], m["is_v3"], m["d0"], m["d1"])
                if ev[f"price_b{b}"]:
                    ev["_prices"].append(ev[f"price_b{b}"])


def fill_tick():
    now = time.time(); done = []
    for ev in list(active):
        m = ev["_meta"]
        for s in SEC_HZ:
            if ev[f"price_s{s}"] is None and now - ev["_start_t"] >= s:
                ev[f"price_s{s}"] = pool_price(ev["pool"], m["is_v3"], m["d0"], m["d1"])
                if ev[f"price_s{s}"]:
                    ev["_prices"].append(ev[f"price_s{s}"])
        if now - ev["_start_t"] >= SEC_HZ[-1]:  # last horizon done -> finalize
            done.append(ev)
    for ev in done:
        _finalize(ev)
        if ev in active:
            active.remove(ev)


async def fill_time_outcomes():
    while True:
        await asyncio.sleep(10)
        await asyncio.to_thread(fill_tick)


def _finalize(ev):
    p0 = ev["price_incl"]; prices = [p for p in ev["_prices"] if p]
    if p0 and prices:
        rets = [p / p0 - 1 for p in prices]
        ev["mfe"] = max(rets); ev["mae"] = min(rets)
    p300 = ev.get("price_s300")
    if p0 and p300 and not ev["_meta"]["quote_is_stable"]:
        ev["eth_rel_ret_s300"] = p300 / p0 - 1   # already ETH-quoted -> ETH-relative
    elif p0 and p300:
        ev["eth_rel_ret_s300"] = None            # USD-quoted; ETH-relative needs ETH path (left null in MVP)
    row = {k: ev.get(k) for k in OUT_SCHEMA.names}  # optional outcome keys -> None
    rot.add(row)


async def main():
    deadline = time.time() + RUN_SECONDS if RUN_SECONDS else None
    filler = asyncio.create_task(fill_time_outcomes())
    while True:
        try:
            async with websockets.connect(WS_URL, ssl=_ssl, ping_interval=20, ping_timeout=10, max_size=None) as ws:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]}))
                sub = json.loads(await ws.recv()).get("result")
                _log(f"connected {WS_URL}; RPC {RPC}; min_notional=${MIN_NOTIONAL_USD:.0f}")
                async for raw in ws:
                    if deadline and time.time() > deadline:
                        for ev in list(active):
                            _finalize(ev)
                        rot.flush(); _log("RUN_SECONDS reached"); filler.cancel(); return
                    m = json.loads(raw)
                    if m.get("params", {}).get("subscription") == sub:
                        h = m["params"]["result"]
                        await asyncio.to_thread(process_block, h["number"], int(h.get("timestamp", "0x0"), 16))
                        rot.flush()
        except Exception as e:
            _log(f"ws error: {e}; reconnect in 5s"); rot.flush()
            if deadline and time.time() > deadline:
                return
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        for ev in list(active):
            _finalize(ev)
        rot.flush()
