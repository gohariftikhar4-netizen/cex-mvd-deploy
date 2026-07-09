#!/usr/bin/env python3
"""Coinbase Exchange public market-data recorder.

Captures, for a single product (default BTC-USD):

  * trades   (WS channel: matches)
  * BBO      (WS channel: ticker -> best_bid / best_ask)

Public feed only — no API key, no authentication, no orders.

NOTE: Coinbase Exchange gated the `level2`/`level2_batch` depth channels behind
authentication, so full L2 is unavailable without an API key. Since keys are
out of scope, this recorder captures trades + BBO only; full L2 depth is
captured on the OKX side instead.

Env:
  DATA_ROOT     output directory        (default ./data)
  CB_PRODUCT    product id              (default BTC-USD)
  CB_WS_URL     override ws url         (default wss://ws-feed.exchange.coinbase.com)
  RUN_SECONDS   stop after N seconds    (0 = run forever; used for smoke tests)
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl

import websockets

from common import Heartbeat, ParquetRotator, get_logger, install_shutdown, utc_ms
import schemas

log = get_logger("coinbase")

DATA_ROOT = os.environ.get("DATA_ROOT", "./data")
PRODUCT = os.environ.get("CB_PRODUCT", "BTC-USD")
WS_URL = os.environ.get("CB_WS_URL", "wss://ws-feed.exchange.coinbase.com")
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "0"))
CA_BUNDLE = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")

_ssl = ssl.create_default_context(cafile=CA_BUNDLE) if CA_BUNDLE else ssl.create_default_context()

rot_trades = ParquetRotator(DATA_ROOT, "coinbase", "trades", PRODUCT, schemas.TRADES)
rot_bbo = ParquetRotator(DATA_ROOT, "coinbase", "bbo", PRODUCT, schemas.BBO)
ALL_ROTATORS = [rot_trades, rot_bbo]

hb = Heartbeat(ALL_ROTATORS)


def _iso_ms(ts: str) -> int:
    # Coinbase timestamps look like 2024-01-01T00:00:00.123456Z
    import calendar
    if not ts:
        return utc_ms()
    ts = ts.rstrip("Z")
    date, _, frac = ts.partition(".")
    tm = calendar.timegm(__import__("time").strptime(date, "%Y-%m-%dT%H:%M:%S"))
    ms = int((frac + "000000")[:6]) // 1000 if frac else 0
    return tm * 1000 + ms


def _flush_all() -> None:
    for r in ALL_ROTATORS:
        try:
            r.close()
        except Exception as e:
            log.error(f"close error: {e}")


async def _handle(msg: dict) -> None:
    t = msg.get("type")
    now = utc_ms()
    if t == "match" or t == "last_match":
        rot_trades.add({
            "ts_exch": _iso_ms(msg.get("time", "")), "ts_local": now, "symbol": PRODUCT,
            "trade_id": str(msg.get("trade_id", "")),
            # Coinbase "side" is the maker side; aggressor is the opposite.
            "side": "sell" if msg.get("side") == "buy" else "buy",
            "px": float(msg["price"]), "sz": float(msg["size"]),
        })
        hb.bump("trades")
    elif t == "ticker":
        if msg.get("best_bid") and msg.get("best_ask"):
            rot_bbo.add({
                "ts_exch": _iso_ms(msg.get("time", "")), "ts_local": now, "symbol": PRODUCT,
                "bid_px": float(msg["best_bid"]), "bid_sz": float(msg.get("best_bid_size", 0) or 0),
                "ask_px": float(msg["best_ask"]), "ask_sz": float(msg.get("best_ask_size", 0) or 0),
            })
            hb.bump("bbo")


async def _ws_loop() -> None:
    sub = {"type": "subscribe", "product_ids": [PRODUCT],
           "channels": ["matches", "ticker"]}
    backoff = 1
    while True:
        try:
            async with websockets.connect(WS_URL, ssl=_ssl, ping_interval=20, ping_timeout=10, max_size=None) as ws:
                await ws.send(json.dumps(sub))
                log.info(f"Coinbase WS connected {WS_URL} sub={PRODUCT}")
                backoff = 1
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") in ("subscriptions", "heartbeat"):
                        continue
                    if msg.get("type") == "error":
                        log.error(f"Coinbase WS error msg: {msg}")
                        continue
                    await _handle(msg)
        except Exception as e:
            log.error(f"Coinbase WS error: {e}; reconnect in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def main() -> None:
    install_shutdown(_flush_all)
    hb.start()
    task = asyncio.create_task(_ws_loop())
    if RUN_SECONDS > 0:
        await asyncio.sleep(RUN_SECONDS)
        task.cancel()
        _flush_all()
        log.info("RUN_SECONDS reached; exiting")
        return
    await task


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _flush_all()
