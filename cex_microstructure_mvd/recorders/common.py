"""Shared infrastructure for the CEX microstructure recorders.

No secrets, no API keys, no trading. Public market-data capture only.

A ParquetRotator buffers rows in memory and flushes them to hourly, partitioned
parquet files with an atomic write (temp file + os.replace) so a crash or a
mid-write kill never leaves a truncated part file that the quality report would
choke on.

Layout on disk:

    <DATA_ROOT>/<exchange>/<stream>/<symbol>/date=YYYY-MM-DD/hour=HH/
        part-<epoch_ms>-<seq>.parquet
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

import pyarrow as pa
import pyarrow.parquet as pq


def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False  # avoid duplicate lines via the root logger
        logging.Formatter.converter = time.gmtime
    return log


def utc_ms() -> int:
    return int(time.time() * 1000)


def _parts(ts_ms: int) -> tuple[str, str]:
    """(date=YYYY-MM-DD, hour=HH) in UTC for a millisecond timestamp."""
    tm = time.gmtime(ts_ms / 1000.0)
    return time.strftime("%Y-%m-%d", tm), time.strftime("%H", tm)


@dataclass
class ParquetRotator:
    """Buffers rows for one (exchange, stream, symbol) and flushes to parquet.

    Flush is triggered by whichever comes first: `max_rows` buffered, or
    `max_seconds` since the last flush. Flushing is also forced when the UTC
    hour partition rolls over so no part file straddles two hours.
    """

    data_root: str
    exchange: str
    stream: str
    symbol: str
    schema: pa.Schema
    max_rows: int = 5000
    max_seconds: int = 60

    _buf: List[dict] = field(default_factory=list, init=False)
    _last_flush: float = field(default_factory=time.time, init=False)
    _cur_hour: tuple[str, str] | None = field(default=None, init=False)
    _seq: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _log: logging.Logger = field(init=False)

    def __post_init__(self) -> None:
        self._log = get_logger(f"{self.exchange}.{self.stream}")

    def add(self, row: dict) -> None:
        with self._lock:
            hour = _parts(row.get("ts_local", utc_ms()))
            if self._cur_hour is None:
                self._cur_hour = hour
            if hour != self._cur_hour and self._buf:
                self._flush_locked()
                self._cur_hour = hour
            self._buf.append(row)
            if len(self._buf) >= self.max_rows or (time.time() - self._last_flush) >= self.max_seconds:
                self._flush_locked()

    def tick(self) -> None:
        """Time-based flush; call periodically so low-volume streams still land."""
        with self._lock:
            if self._buf and (time.time() - self._last_flush) >= self.max_seconds:
                self._flush_locked()

    def close(self) -> None:
        with self._lock:
            if self._buf:
                self._flush_locked()

    def _flush_locked(self) -> None:
        rows, self._buf = self._buf, []
        self._last_flush = time.time()
        if not rows:
            return
        date, hour = self._cur_hour or _parts(rows[0]["ts_local"])
        d = os.path.join(
            self.data_root, self.exchange, self.stream, self.symbol,
            f"date={date}", f"hour={hour}",
        )
        os.makedirs(d, exist_ok=True)
        self._seq += 1
        base = f"part-{utc_ms()}-{self._seq:06d}.parquet"
        final = os.path.join(d, base)
        tmp = final + ".tmp"
        cols = {name: [r.get(name) for r in rows] for name in self.schema.names}
        table = pa.table(cols, schema=self.schema)
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, final)
        self._log.info(f"flushed {len(rows)} rows -> {final}")


class Heartbeat:
    """Background thread that periodically ticks rotators and logs liveness."""

    def __init__(self, rotators: Sequence[ParquetRotator], period: int = 15):
        self._rotators = list(rotators)
        self._period = period
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._log = get_logger("heartbeat")
        self._counters: Dict[str, int] = {}

    def bump(self, key: str, n: int = 1) -> None:
        self._counters[key] = self._counters.get(key, 0) + n

    def start(self) -> None:
        self._t.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._period):
            for r in self._rotators:
                try:
                    r.tick()
                except Exception as e:  # never let a flush error kill the recorder
                    self._log.error(f"rotator tick error: {e}")
            if self._counters:
                summary = " ".join(f"{k}={v}" for k, v in sorted(self._counters.items()))
                self._log.info(f"alive {summary}")


def install_shutdown(callback) -> None:
    """Run `callback` once on SIGTERM/SIGINT (systemd stop / Ctrl-C)."""
    done = {"v": False}

    def handler(signum, _frame):
        if done["v"]:
            return
        done["v"] = True
        get_logger("shutdown").info(f"signal {signum}: flushing and exiting")
        try:
            callback()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
