"""Parquet schemas shared by both recorders.

Every row carries `ts_exch` (exchange event time, ms) and `ts_local`
(recorder receive time, ms). The gap between them is the capture latency the
daily quality report checks. All prices/sizes are stored as float64.
"""

from __future__ import annotations

import pyarrow as pa

# Depth levels captured per L2 snapshot/update side.
L2_DEPTH = 10


def _l2_fields() -> list[pa.Field]:
    fields = [
        pa.field("ts_exch", pa.int64()),
        pa.field("ts_local", pa.int64()),
        pa.field("symbol", pa.string()),
        pa.field("update_type", pa.string()),  # "snapshot" | "update"
    ]
    for i in range(L2_DEPTH):
        fields += [
            pa.field(f"bid_px_{i}", pa.float64()),
            pa.field(f"bid_sz_{i}", pa.float64()),
            pa.field(f"ask_px_{i}", pa.float64()),
            pa.field(f"ask_sz_{i}", pa.float64()),
        ]
    return fields


TRADES = pa.schema([
    ("ts_exch", pa.int64()),
    ("ts_local", pa.int64()),
    ("symbol", pa.string()),
    ("trade_id", pa.string()),
    ("side", pa.string()),          # aggressor side: "buy" | "sell"
    ("px", pa.float64()),
    ("sz", pa.float64()),
])

BBO = pa.schema([
    ("ts_exch", pa.int64()),
    ("ts_local", pa.int64()),
    ("symbol", pa.string()),
    ("bid_px", pa.float64()),
    ("bid_sz", pa.float64()),
    ("ask_px", pa.float64()),
    ("ask_sz", pa.float64()),
])

L2 = pa.schema(_l2_fields())

FUNDING = pa.schema([
    ("ts_exch", pa.int64()),
    ("ts_local", pa.int64()),
    ("symbol", pa.string()),
    ("funding_rate", pa.float64()),
    ("next_funding_time", pa.int64()),
])

OPEN_INTEREST = pa.schema([
    ("ts_exch", pa.int64()),
    ("ts_local", pa.int64()),
    ("symbol", pa.string()),
    ("oi", pa.float64()),        # in contracts
    ("oi_ccy", pa.float64()),    # in base currency, if provided
])
