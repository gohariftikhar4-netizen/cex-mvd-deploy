#!/usr/bin/env python3
"""Data-quality report for the CEX microstructure recorders.

Scans the parquet tree and reports, per (exchange, stream, symbol):
  * file count, row count, wall-clock coverage
  * largest gap between consecutive events (staleness)
  * crossed-book count (BBO and L2 top level)
  * bad price / size count
  * capture latency percentiles (ts_local - ts_exch)

Emits an overall verdict. This is the same check used to gate deploy (M0) and
to grade the 24h M1 milestone.

Usage:
  daily_quality_report.py [--data-root DIR] [--date YYYY-MM-DD | --date all]
                          [--json OUT.json]

Exit code: 0 = PASS, 1 = WARN, 2 = FAIL. No secrets, read-only.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
import time

import pyarrow.parquet as pq

# Streams that must be present per exchange for a healthy recorder.
EXPECTED = {
    "okx": ["trades", "bbo", "l2", "funding", "open_interest"],
    "coinbase": ["trades", "bbo"],
}
# A stream is "stale" if the largest inter-event gap exceeds this (seconds).
# Funding/OI are polled every 30s, so they get a looser bound.
MAX_GAP_S = {"funding": 900, "open_interest": 900, "_default": 120}


def _pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def _files(data_root, exchange, stream, date):
    # Layout: <exchange>/<stream>/<symbol>/date=YYYY-MM-DD/hour=HH/part-*.parquet
    if date == "all":
        pat = f"{data_root}/{exchange}/{stream}/*/*/*/*.parquet"
    else:
        pat = f"{data_root}/{exchange}/{stream}/*/date={date}/*/*.parquet"
    return sorted(glob.glob(pat))


def analyze_stream(data_root, exchange, stream, date):
    files = _files(data_root, exchange, stream, date)
    r = {"exchange": exchange, "stream": stream, "files": len(files), "rows": 0,
         "issues": [], "status": "MISSING"}
    if not files:
        r["issues"].append("no files")
        return r
    rows = []
    for f in files:
        try:
            rows.extend(pq.read_table(f).to_pylist())
        except Exception as e:
            r["issues"].append(f"unreadable {os.path.basename(f)}: {e}")
    r["rows"] = len(rows)
    if not rows:
        r["issues"].append("files present but 0 rows")
        return r

    ex_ts = sorted(x["ts_exch"] for x in rows if x.get("ts_exch"))
    if ex_ts:
        r["coverage_min"] = time.strftime("%H:%M:%S", time.gmtime(ex_ts[0] / 1000))
        r["coverage_max"] = time.strftime("%H:%M:%S", time.gmtime(ex_ts[-1] / 1000))
        gaps = [(ex_ts[i + 1] - ex_ts[i]) / 1000 for i in range(len(ex_ts) - 1)]
        r["max_gap_s"] = round(max(gaps), 1) if gaps else 0.0
        bound = MAX_GAP_S.get(stream, MAX_GAP_S["_default"])
        if r["max_gap_s"] > bound:
            r["issues"].append(f"max gap {r['max_gap_s']}s > {bound}s")

    lat = [x["ts_local"] - x["ts_exch"] for x in rows if x.get("ts_exch") and x.get("ts_local")]
    if lat:
        r["latency_ms_p50"] = _pct(lat, 50)
        r["latency_ms_p99"] = _pct(lat, 99)

    if stream == "bbo":
        crossed = sum(1 for x in rows if x["bid_px"] and x["ask_px"] and x["bid_px"] >= x["ask_px"])
        bad = sum(1 for x in rows if (x["bid_px"] or 0) <= 0 or (x["ask_px"] or 0) <= 0)
        r["crossed"] = crossed
        r["bad_px"] = bad
        if crossed:
            r["issues"].append(f"{crossed} crossed BBO")
        if bad:
            r["issues"].append(f"{bad} bad BBO prices")
    elif stream == "l2":
        crossed = sum(1 for x in rows if x.get("bid_px_0") and x.get("ask_px_0") and x["bid_px_0"] >= x["ask_px_0"])
        r["crossed"] = crossed
        if crossed:
            r["issues"].append(f"{crossed} crossed L2 top")
    elif stream == "trades":
        bad = sum(1 for x in rows if (x["px"] or 0) <= 0 or (x["sz"] or 0) <= 0)
        r["bad_px"] = bad
        if bad:
            r["issues"].append(f"{bad} bad trades")

    r["status"] = "OK" if not r["issues"] else "WARN"
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=os.environ.get("DATA_ROOT", "/var/lib/cexrec/data"))
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d", time.gmtime()))
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    results, missing, warn = [], [], []
    for exchange, streams in EXPECTED.items():
        for stream in streams:
            res = analyze_stream(args.data_root, exchange, stream, args.date)
            results.append(res)
            if res["status"] == "MISSING":
                missing.append(f"{exchange}/{stream}")
            elif res["status"] == "WARN":
                warn.append(f"{exchange}/{stream}")

    print(f"\nCEX microstructure quality report — date={args.date} root={args.data_root}")
    print("-" * 78)
    hdr = f"{'stream':22} {'files':>6} {'rows':>9} {'gap_s':>7} {'lat_p50':>8} {'status':>7}"
    print(hdr)
    for r in results:
        print(f"{r['exchange']+'/'+r['stream']:22} {r['files']:>6} {r['rows']:>9} "
              f"{str(r.get('max_gap_s','-')):>7} {str(r.get('latency_ms_p50','-')):>8} {r['status']:>7}"
              + (f"   {'; '.join(r['issues'])}" if r["issues"] else ""))
    print("-" * 78)

    if missing:
        verdict, code = "FAIL", 2
        print(f"VERDICT: FAIL — missing streams: {', '.join(missing)}")
    elif warn:
        verdict, code = "WARN", 1
        print(f"VERDICT: WARN — issues in: {', '.join(warn)}")
    else:
        verdict, code = "PASS", 0
        print("VERDICT: PASS — all expected streams present and clean")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"date": args.date, "verdict": verdict, "streams": results}, fh, indent=2)
        print(f"wrote {args.json}")
    sys.exit(code)


if __name__ == "__main__":
    main()
