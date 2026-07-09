# CEX Microstructure MVD — Recorder Deployment

Persistent recorder for public CEX market-data microstructure. Captures live
order-flow from **OKX** and **Coinbase** into partitioned parquet, runs as
systemd services, and self-reports data quality daily.

**Scope guardrails (enforced by design):** public market data only — no API
keys, no authentication, no orders, no trading, no backtests. No secrets are
read or written by any script here.

## What gets captured

| Exchange | Symbol         | Streams                                   | Transport |
|----------|----------------|-------------------------------------------|-----------|
| OKX      | BTC-USDT-SWAP  | trades, BBO, L2 (10-lvl), funding, OI     | WS + REST poll |
| Coinbase | BTC-USD        | trades, BBO                               | WS |

**Coinbase L2 note:** Coinbase Exchange moved the `level2` depth channel behind
authentication. Because API keys are out of scope, Coinbase captures trades +
BBO only; full L2 depth comes from OKX (free, unauthenticated). This is a
deliberate constraint, not a gap in the recorder.

All rows carry `ts_exch` (exchange event time) and `ts_local` (recorder receive
time); their difference is the capture latency the quality report tracks.

## Deploy (fresh Debian/Ubuntu VPS)

**One command, from a clone on the box:**

```bash
sudo bash cex_microstructure_mvd/scripts/bootstrap.sh
```

That installs OS packages, stages the package to `/opt/cex_microstructure_mvd`,
then runs the systemd installer. Equivalent manual path:

```bash
sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip git rsync jq curl logrotate
sudo rsync -a cex_microstructure_mvd/ /opt/cex_microstructure_mvd/
cd /opt/cex_microstructure_mvd/scripts/systemd
sudo APP_DIR=/opt/cex_microstructure_mvd RUN_USER=cexrec ./install.sh
```

### Pre-deploy gates (hard stops in `install.sh`)

| Gate | Condition | Exit |
|------|-----------|------|
| Disk | free space `< 50 GB` | 10 — BLOCKED BY DISK |
| systemd | `systemctl` missing/broken | 11 |
| Endpoints | OKX **and** Coinbase both unreachable | 12 — BLOCKED BY ENDPOINT |
| Python | `python3` absent and not installable | 13 — BLOCKED BY INSTALL ERROR |

The installer creates the `cexrec` system user (no login shell), a venv, the
data dir at `/var/lib/cexrec/data`, and enables the services + timers.

## Verify after start (wait 5–10 min)

```bash
systemctl status cex-okx cex-coinbase          # both active (running)
systemctl list-timers 'cex-*'                  # quality + diskmon timers listed
journalctl -u cex-okx -u cex-coinbase -f       # live "alive ..." heartbeats
find /var/lib/cexrec/data -name '*.parquet' | head

# Full quality report across everything captured so far:
sudo -u cexrec DATA_ROOT=/var/lib/cexrec/data \
  /opt/cex_microstructure_mvd/.venv/bin/python \
  /opt/cex_microstructure_mvd/scripts/daily_quality_report.py --date all
```

A healthy report shows every stream `OK` and `VERDICT: PASS` (exit 0). Checks:
no crossed BBO/L2, no bad prices/sizes, no oversized gaps, capture latency
percentiles.

## Services & timers

| Unit | Role |
|------|------|
| `cex-okx.service`      | OKX recorder (Restart=always) |
| `cex-coinbase.service` | Coinbase recorder (Restart=always) |
| `cex-quality.timer`    | daily 00:10 UTC → quality report → `reports/quality-latest.json` + journal |
| `cex-diskmon.timer`    | hourly disk-usage check (report-only; pruning is opt-in) |

Units are hardened: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`,
writes confined to the data/reports dirs. `SIGTERM` triggers a buffer flush
before exit, so `systemctl stop` never loses in-memory rows.

## Data layout

```
/var/lib/cexrec/data/<exchange>/<stream>/<symbol>/date=YYYY-MM-DD/hour=HH/part-<ms>-<seq>.parquet
```

Hourly, zstd-compressed, written atomically (temp file + rename) so a crash
never leaves a torn part file.

## M1 — 24h milestone

Once the recorders have run **24h continuously**, grade M1:

```bash
sudo -u cexrec DATA_ROOT=/var/lib/cexrec/data \
  /opt/cex_microstructure_mvd/.venv/bin/python \
  /opt/cex_microstructure_mvd/scripts/daily_quality_report.py --date all --json /tmp/m1.json
```

M1 passes if: all streams `OK`, no gaps beyond bounds (recorder stayed up),
zero crossed books, and OKX funding/OI rows accumulate (~1 every 30s). Send the
report output for the written M1 verdict.

## Local verification status

Both recorders were smoke-tested against the **live** OKX and Coinbase feeds
before commit: real trades/BBO/L2/funding/OI captured, parquet validated (0
crossed books, monotonic L2 ladder, sane prices/latency), and the quality
report returns `PASS` on the combined output. The only step that cannot be run
from the build sandbox is the SSH connection to the VPS itself — the sandbox
firewalls all outbound port 22 (verified: `github.com:22` and the VPS:22 both
blocked while :443 is open). Run `bootstrap.sh` on the VPS to start the clock.
