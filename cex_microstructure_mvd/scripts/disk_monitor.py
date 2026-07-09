#!/usr/bin/env python3
"""Disk-usage guard for the recorder host.

Logs data-root size and free space. If free space falls below a threshold it
exits non-zero (so the systemd timer marks a failure and journald records it),
and — only when --prune is given — deletes the oldest date partitions until
back above the low-water mark. Off by default: an MVD should never silently
drop data, so pruning is opt-in.

Usage:
  disk_monitor.py [--data-root DIR] [--min-free-gb 10] [--prune] [--keep-days 30]
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
import time


def dir_size_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=os.environ.get("DATA_ROOT", "/var/lib/cexrec/data"))
    ap.add_argument("--min-free-gb", type=float, default=10.0)
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--keep-days", type=int, default=30)
    args = ap.parse_args()

    st = shutil.disk_usage(args.data_root if os.path.exists(args.data_root) else "/")
    free_gb = st.free / 1e9
    data_gb = dir_size_bytes(args.data_root) / 1e9 if os.path.exists(args.data_root) else 0.0
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"{ts} disk: free={free_gb:.1f}GB used_by_data={data_gb:.2f}GB "
          f"total={st.total/1e9:.1f}GB min_free={args.min_free_gb}GB")

    if args.prune:
        cutoff = time.time() - args.keep_days * 86400
        for d in sorted(glob.glob(f"{args.data_root}/*/*/*/date=*")):
            name = os.path.basename(d).replace("date=", "")
            try:
                when = time.mktime(time.strptime(name, "%Y-%m-%d"))
            except ValueError:
                continue
            if when < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                print(f"{ts} pruned old partition {d}")

    if free_gb < args.min_free_gb:
        print(f"{ts} ALERT: free space {free_gb:.1f}GB below {args.min_free_gb}GB")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
