#!/usr/bin/env bash
#
# Add-on installer: start ONLY the Ethereum mempool recorder.
#
# Deliberately does NOT touch the running OKX/Coinbase services or their timers,
# so the live M1 clock is never disrupted. Reuses the existing cexrec user,
# virtualenv, and DATA_ROOT created by the main install.sh.
#
# Usage (as root, from a fresh clone):
#   sudo bash cex_microstructure_mvd/scripts/systemd/install_mempool.sh
#
# Public pending-tx data only. No keys, no orders, no private relay.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/cex_microstructure_mvd}"
RUN_USER="${RUN_USER:-cexrec}"
STATE_DIR="${STATE_DIR:-/var/lib/${RUN_USER}}"
DATA_ROOT="${DATA_ROOT:-${STATE_DIR}/data}"

if [[ $EUID -ne 0 ]]; then echo "run as root (sudo)"; exit 1; fi

here="$(cd "$(dirname "$0")" && pwd)"
pkg_root="$(cd "$here/../.." && pwd)"   # .../cex_microstructure_mvd in the clone

echo "=== stage updated package into $APP_DIR (non-destructive copy) ==="
mkdir -p "$APP_DIR"
cp -a "$pkg_root/." "$APP_DIR/"          # overwrites files, adds record_mempool_eth.py; no deletes
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR" 2>/dev/null || true

echo "=== ensure venv + deps (reuse existing; create only if missing) ==="
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  echo "venv missing — run the main install.sh first"; exit 2
fi
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

if ! id "$RUN_USER" >/dev/null 2>&1; then
  echo "user $RUN_USER missing — run the main install.sh first"; exit 3
fi
mkdir -p "$DATA_ROOT"; chown -R "$RUN_USER:$RUN_USER" "$STATE_DIR"

echo "=== render + enable ONLY cex-mempool-eth.service ==="
sed -e "s#@APP_DIR@#${APP_DIR}#g" -e "s#@RUN_USER@#${RUN_USER}#g" -e "s#@DATA_ROOT@#${DATA_ROOT}#g" \
    "$here/cex-mempool-eth.service" > /etc/systemd/system/cex-mempool-eth.service
systemctl daemon-reload
systemctl enable --now cex-mempool-eth.service

echo "=== status (OKX/Coinbase left untouched) ==="
systemctl --no-pager --lines=0 status cex-mempool-eth.service || true
cat <<EOF

Mempool recorder started. OKX/Coinbase services were NOT touched.
Watch:  journalctl -u cex-mempool-eth -f
Files:  find $DATA_ROOT/ethereum -name '*.parquet' | head
EOF
