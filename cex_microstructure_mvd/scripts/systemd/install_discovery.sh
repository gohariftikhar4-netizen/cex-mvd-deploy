#!/usr/bin/env bash
#
# Add-on installer: the DEX discovery-engine daily timer + a first run NOW.
# Installs ONLY cex-discovery.{service,timer}; NEVER touches cex-okx / cex-coinbase /
# cex-mempool-eth / cex-dex-outcome-eth. Measurement only.
#
# Usage (root, from a fresh clone of the deploy repo):
#   sudo bash cex_microstructure_mvd/scripts/systemd/install_discovery.sh

set -euo pipefail
APP_DIR="${APP_DIR:-/opt/cex_microstructure_mvd}"
RUN_USER="${RUN_USER:-cexrec}"
STATE_DIR="${STATE_DIR:-/var/lib/${RUN_USER}}"
DATA_ROOT="${DATA_ROOT:-${STATE_DIR}/data}"
REPORTS="${REPORTS:-${STATE_DIR}/reports}"

if [[ $EUID -ne 0 ]]; then echo "run as root (sudo)"; exit 1; fi
here="$(cd "$(dirname "$0")" && pwd)"; pkg_root="$(cd "$here/../.." && pwd)"

echo "=== stage updated package into $APP_DIR (non-destructive copy) ==="
mkdir -p "$APP_DIR" "$REPORTS"
cp -a "$pkg_root/." "$APP_DIR/"
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR" "$REPORTS" 2>/dev/null || true
[[ -x "$APP_DIR/.venv/bin/python" ]] || { echo "venv missing — run main install.sh first"; exit 2; }
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt" || true

echo "=== render + enable ONLY cex-discovery.{service,timer} ==="
for u in cex-discovery.service cex-discovery.timer; do
  sed -e "s#@APP_DIR@#${APP_DIR}#g" -e "s#@RUN_USER@#${RUN_USER}#g" \
      -e "s#@DATA_ROOT@#${DATA_ROOT}#g" -e "s#@REPORTS@#${REPORTS}#g" \
      "$here/$u" > "/etc/systemd/system/$u"
done
systemctl daemon-reload
systemctl enable --now cex-discovery.timer

echo "=== first run NOW (blocks until done; safe — recorders untouched) ==="
systemctl start cex-discovery.service || true

echo "=== recorder services must still be active ==="
systemctl is-active cex-okx cex-coinbase cex-mempool-eth cex-dex-outcome-eth || true
echo "=== discovery timer ==="; systemctl list-timers cex-discovery.timer --no-pager || true
echo
echo "Reports written to $REPORTS :"
ls -la "$REPORTS"/discovery_health.json "$REPORTS"/leaderboard_*.csv 2>/dev/null || echo "  (health json present; leaderboards appear once the sample gate is met)"
echo "--- discovery_health.json ---"; cat "$REPORTS/discovery_health.json" 2>/dev/null || true
