#!/usr/bin/env python3
"""OKX public market-data recorder.

Captures, for a single perpetual-swap instrument (default BTC-USDT-SWAP):

  * trades        (WS channel: trades)
  * BBO           (WS channel: bbo-tbt, tick-by-tick best bid/offer)
  * L2 book       (WS channel: books, 400-level; top L2_DEPTH stored)
  * funding rate  (REST poll: /api/v5/public/funding-rate)
  * open interest (REST poll: /api/v5/public/open-interest)

Public endpoints only — no API key, no authentication, no orders.

Env:
  DATA_ROOT     output directory              (default ./data)
  OKX_SYMBOL    instrument id                 (default BTC-USDT-SWAP)
  OKX_WS_URL    override ws url                (default wss://ws.okx.com:8443/ws/v5/public)
  OKX_REST_URL  override rest base            (default https://www.okx.com)
  POLL_SECONDS  funding/OI poll interval      (default 30)
  RUN_SECONDS   stop after N seconds (0 = run forever; used for smoke tests)
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import time

import aiohttp
import websockets

from common import Heartbeat, ParquetRotator, get_logger, install_shutdown, utc_ms
import schemas

log = get_logger("okx")

DATA_ROOT = os.environ.get("DATA_ROOT", "./data")
SYMBOL = os.environ.get("OKX_SYMBOL", "BTC-USDT-SWAP")
WS_URL = os.environ.get("OKX_WS_URL", "wss://ws.okx.com:8443/ws/v5/public")
REST_URL = os.environ.get("OKX_REST_URL", "https://www.okx.com")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "0"))
CA_BUNDLE = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")

_ssl = ssl.create_default_context(cafile=CA_BUNDLE) if CA_BUNDLE else ssl.create_default_context()

rot_trades = ParquetRotator(DATA_ROOT, "okx", "trades", SYMBOL, schemas.TRADES)
rot_bbo = ParquetRotator(DATA_ROOT, "okx", "bbo", SYMBOL, schemas.BBO)
rot_l2 = ParquetRotator(DATA_ROOT, "okx", "l2", SYMBOL, schemas.L2)
rot_funding = ParquetRotator(DATA_ROOT, "okx", "funding", SYMBOL, schemas.FUNDING, max_rows=10, max_seconds=30)
rot_oi = ParquetRotator(DATA_ROOT, "okx", "open_interest", SYMBOL, schemas.OPEN_INTEREST, max_rows=10, max_seconds=30)
ALL_ROTATORS = [rot_trades, rot_bbo, rot_l2, rot_funding, rot_oi]

hb = Heartbeat(ALL_ROTATORS)

# In-memory L2 book reconstructed from the `books` snapshot + incremental
# deltas. OKX sends a full snapshot on (re)subscribe, then updates that carry
# only changed price levels (size "0" = remove). We apply them and write the
# reconstructed top-L2_DEPTH so every stored row is a real, uncrossed book.
_bids: dict[float, float] = {}
_asks: dict[float, float] = {}


def _apply_side(book: dict[float, float], levels) -> None:
    for lvl in levels:
        px = float(lvl[0]); sz = float(lvl[1])
        if sz == 0:
            book.pop(px, None)
        else:
            book[px] = sz


def _flush_all() -> None:
    for r in ALL_ROTATORS:
        try:
            r.close()
        except Exception as e:
            log.error(f"close error: {e}")


def _l2_row(ts_exch: int, update_type: str) -> dict:
    """Reconstructed top-L2_DEPTH from the maintained in-memory book."""
    now = utc_ms()
    row = {"ts_exch": ts_exch, "ts_local": now, "symbol": SYMBOL, "update_type": update_type}
    top_bids = sorted(_bids.items(), key=lambda x: -x[0])[:schemas.L2_DEPTH]
    top_asks = sorted(_asks.items(), key=lambda x: x[0])[:schemas.L2_DEPTH]
    for i in range(schemas.L2_DEPTH):
        if i < len(top_bids):
            row[f"bid_px_{i}"], row[f"bid_sz_{i}"] = top_bids[i]
        if i < len(top_asks):
            row[f"ask_px_{i}"], row[f"ask_sz_{i}"] = top_asks[i]
    return row


async def _handle(msg: dict) -> None:
    arg = msg.get("arg", {})
    channel = arg.get("channel")
    data = msg.get("data")
    if not data:
        return
    now = utc_ms()
    if channel == "trades":
        for d in data:
            rot_trades.add({
                "ts_exch": int(d["ts"]), "ts_local": now, "symbol": SYMBOL,
                "trade_id": str(d.get("tradeId", "")), "side": d.get("side", ""),
                "px": float(d["px"]), "sz": float(d["sz"]),
            })
            hb.bump("trades")
    elif channel == "bbo-tbt":
        for d in data:
            bids, asks = d.get("bids", []), d.get("asks", [])
            if not bids or not asks:
                continue
            rot_bbo.add({
                "ts_exch": int(d["ts"]), "ts_local": now, "symbol": SYMBOL,
                "bid_px": float(bids[0][0]), "bid_sz": float(bids[0][1]),
                "ask_px": float(asks[0][0]), "ask_sz": float(asks[0][1]),
            })
            hb.bump("bbo")
    elif channel == "books":
        action = msg.get("action", "update")
        for d in data:
            if action == "snapshot":
                _bids.clear(); _asks.clear()
            _apply_side(_bids, d.get("bids", []))
            _apply_side(_asks, d.get("asks", []))
            rot_l2.add(_l2_row(int(d.get("ts", now)), "snapshot" if action == "snapshot" else "update"))
            hb.bump("l2")


async def _ws_loop() -> None:
    sub = {"op": "subscribe", "args": [
        {"channel": "trades", "instId": SYMBOL},
        {"channel": "bbo-tbt", "instId": SYMBOL},
        {"channel": "books", "instId": SYMBOL},
    ]}
    backoff = 1
    while True:
        try:
            async with websockets.connect(WS_URL, ssl=_ssl, ping_interval=20, ping_timeout=10, max_size=None) as ws:
                await ws.send(json.dumps(sub))
                log.info(f"OKX WS connected {WS_URL} sub={SYMBOL}")
                backoff = 1
                async for raw in ws:
                    if raw == "pong":
                        continue
                    msg = json.loads(raw)
                    if msg.get("event"):
                        log.info(f"OKX WS event: {msg}")
                        continue
                    await _handle(msg)
        except Exception as e:
            log.error(f"OKX WS error: {e}; reconnect in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def _poll_loop() -> None:
    async with aiohttp.ClientSession() as sess:
        while True:
            now = utc_ms()
            try:
                async with sess.get(f"{REST_URL}/api/v5/public/funding-rate",
                                    params={"instId": SYMBOL}, ssl=_ssl, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    j = await r.json()
                    for d in j.get("data", []):
                        rot_funding.add({
                            "ts_exch": int(d.get("ts", now)), "ts_local": now, "symbol": SYMBOL,
                            "funding_rate": float(d["fundingRate"]),
                            "next_funding_time": int(d.get("nextFundingTime", 0) or 0),
                        })
                        hb.bump("funding")
                async with sess.get(f"{REST_URL}/api/v5/public/open-interest",
                                    params={"instType": "SWAP", "instId": SYMBOL}, ssl=_ssl,
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                    j = await r.json()
                    for d in j.get("data", []):
                        rot_oi.add({
                            "ts_exch": int(d.get("ts", now)), "ts_local": now, "symbol": SYMBOL,
                            "oi": float(d.get("oi", 0) or 0), "oi_ccy": float(d.get("oiCcy", 0) or 0),
                        })
                        hb.bump("oi")
            except Exception as e:
                log.error(f"OKX poll error: {e}")
            await asyncio.sleep(POLL_SECONDS)


async def main() -> None:
    install_shutdown(_flush_all)
    hb.start()
    tasks = [asyncio.create_task(_ws_loop()), asyncio.create_task(_poll_loop())]
    if RUN_SECONDS > 0:
        await asyncio.sleep(RUN_SECONDS)
        for t in tasks:
            t.cancel()
        _flush_all()
        log.info("RUN_SECONDS reached; exiting")
        return
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _flush_all()
