#!/usr/bin/env bash
#
# Add-on installer: start ONLY the Ethereum post-inclusion DEX outcome tracker.
#
# Does NOT touch cex-okx, cex-coinbase, or cex-mempool-eth. Reuses the existing
# cexrec user, venv, and DATA_ROOT. Measurement only — no trading, no same-block
# execution, no frontrun/sandwich, no private relay, no wallet keys.
#
# Usage (as root, from a fresh clone of the deploy repo):
#   sudo bash cex_microstructure_mvd/scripts/systemd/install_dex_outcome.sh

set -euo pipefail
APP_DIR="${APP_DIR:-/opt/cex_microstructure_mvd}"
RUN_USER="${RUN_USER:-cexrec}"
STATE_DIR="${STATE_DIR:-/var/lib/${RUN_USER}}"
DATA_ROOT="${DATA_ROOT:-${STATE_DIR}/data}"

if [[ $EUID -ne 0 ]]; then echo "run as root (sudo)"; exit 1; fi
here="$(cd "$(dirname "$0")" && pwd)"
pkg_root="$(cd "$here/../.." && pwd)"

echo "=== stage updated package into $APP_DIR (non-destructive copy) ==="
mkdir -p "$APP_DIR"
cp -a "$pkg_root/." "$APP_DIR/"          # overwrites files, adds new scripts; no deletes
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR" 2>/dev/null || true

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then echo "venv missing — run main install.sh first"; exit 2; fi
if ! id "$RUN_USER" >/dev/null 2>&1; then echo "user $RUN_USER missing — run main install.sh first"; exit 3; fi
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
mkdir -p "$DATA_ROOT"; chown -R "$RUN_USER:$RUN_USER" "$STATE_DIR"

echo "=== render + enable ONLY cex-dex-outcome-eth.service ==="
sed -e "s#@APP_DIR@#${APP_DIR}#g" -e "s#@RUN_USER@#${RUN_USER}#g" -e "s#@DATA_ROOT@#${DATA_ROOT}#g" \
    "$here/cex-dex-outcome-eth.service" > /etc/systemd/system/cex-dex-outcome-eth.service
systemctl daemon-reload
systemctl enable --now cex-dex-outcome-eth.service

echo "=== status (OKX/Coinbase/mempool NOT touched) ==="
systemctl --no-pager --lines=0 status cex-dex-outcome-eth.service || true
echo "--- all research services ---"
systemctl is-active cex-okx cex-coinbase cex-mempool-eth cex-dex-outcome-eth || true
cat <<EOF

Outcome tracker started. Other services untouched.
Watch:   journalctl -u cex-dex-outcome-eth -f
Files:   find $DATA_ROOT/ethereum/outcomes -name '*.parquet' | head
Quality: sudo -u $RUN_USER DATA_ROOT=$DATA_ROOT $APP_DIR/.venv/bin/python $APP_DIR/scripts/run_live_mempool_quality.py
EOF
