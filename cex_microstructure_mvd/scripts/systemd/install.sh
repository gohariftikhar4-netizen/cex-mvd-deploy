#!/usr/bin/env bash
#
# Install and start the CEX microstructure recorders as systemd services.
#
# Usage (as root):
#   sudo APP_DIR=/opt/cex_microstructure_mvd RUN_USER=cexrec ./install.sh
#
# Runs pre-deploy gates first and HARD STOPS on:
#   * free disk < 50 GB          -> exit 10  (BLOCKED BY DISK)
#   * systemd unavailable        -> exit 11
#   * OKX and Coinbase both down -> exit 12  (BLOCKED BY ENDPOINT)
#   * python3 unavailable        -> exit 13  (BLOCKED BY INSTALL ERROR)
#
# Public market data only. No API keys, no secrets, no trading.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/cex_microstructure_mvd}"
RUN_USER="${RUN_USER:-cexrec}"
STATE_DIR="${STATE_DIR:-/var/lib/${RUN_USER}}"
DATA_ROOT="${DATA_ROOT:-${STATE_DIR}/data}"
REPORTS="${REPORTS:-${STATE_DIR}/reports}"
MIN_FREE_GB="${MIN_FREE_GB:-50}"

say() { printf '\n=== %s ===\n' "$*"; }

if [[ $EUID -ne 0 ]]; then echo "must run as root (use sudo)"; exit 1; fi

say "PRE-DEPLOY CHECKS"

# --- disk ---
avail_kb=$(df --output=avail -k "$(dirname "$STATE_DIR")" | tail -1 | tr -d ' ')
avail_gb=$(( avail_kb / 1024 / 1024 ))
echo "free disk at $(dirname "$STATE_DIR"): ${avail_gb} GB (require >= ${MIN_FREE_GB} GB)"
if (( avail_gb < MIN_FREE_GB )); then
  echo "HARD STOP: insufficient disk"; exit 10
fi

# --- systemd ---
if ! command -v systemctl >/dev/null 2>&1 || ! systemctl --version >/dev/null 2>&1; then
  echo "HARD STOP: systemd unavailable"; exit 11
fi
echo "systemd: $(systemctl --version | head -1)"

# --- exchange reachability ---
okx_ok=1; cb_ok=1
curl -fsS --max-time 15 "https://www.okx.com/api/v5/public/time" >/dev/null 2>&1 || okx_ok=0
curl -fsS --max-time 15 "https://api.exchange.coinbase.com/time" >/dev/null 2>&1 || cb_ok=0
echo "OKX reachable: ${okx_ok}   Coinbase reachable: ${cb_ok}"
if (( okx_ok == 0 && cb_ok == 0 )); then
  echo "HARD STOP: neither exchange reachable"; exit 12
fi

# --- python ---
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 missing; attempting install..."
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip >/dev/null || { echo "HARD STOP: python install failed"; exit 13; }
  else
    echo "HARD STOP: no apt-get and no python3"; exit 13
  fi
fi
echo "python: $(python3 --version)"
# Ensure venv module is present (Debian splits it out).
if ! python3 -c 'import venv' 2>/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv >/dev/null 2>&1 || true
fi

say "USER + DIRECTORIES"
if ! id "$RUN_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$RUN_USER"
  echo "created system user $RUN_USER"
else
  echo "user $RUN_USER exists"
fi
mkdir -p "$DATA_ROOT" "$REPORTS"
chown -R "$RUN_USER:$RUN_USER" "$STATE_DIR"
echo "state dir: $STATE_DIR (data=$DATA_ROOT reports=$REPORTS)"

say "PYTHON VENV + DEPENDENCIES"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
echo "installed: $("$APP_DIR/.venv/bin/pip" freeze | tr '\n' ' ')"

say "RENDER + INSTALL SYSTEMD UNITS"
here="$(cd "$(dirname "$0")" && pwd)"
render() {
  sed -e "s#@APP_DIR@#${APP_DIR}#g" \
      -e "s#@RUN_USER@#${RUN_USER}#g" \
      -e "s#@DATA_ROOT@#${DATA_ROOT}#g" \
      -e "s#@REPORTS@#${REPORTS}#g" \
      "$here/$1" > "/etc/systemd/system/$1"
  echo "installed /etc/systemd/system/$1"
}
for u in cex-okx.service cex-coinbase.service \
         cex-quality.service cex-quality.timer \
         cex-diskmon.service cex-diskmon.timer; do
  render "$u"
done

systemctl daemon-reload
systemctl enable --now cex-okx.service cex-coinbase.service
systemctl enable --now cex-quality.timer cex-diskmon.timer

say "STATUS"
systemctl --no-pager --lines=0 status cex-okx.service cex-coinbase.service || true
systemctl list-timers --no-pager 'cex-*' || true

cat <<EOF

Done. Recorders are running as $RUN_USER, writing to $DATA_ROOT.

Watch live:   journalctl -u cex-okx -u cex-coinbase -f
Quality now:  sudo -u $RUN_USER DATA_ROOT=$DATA_ROOT $APP_DIR/.venv/bin/python $APP_DIR/scripts/daily_quality_report.py --date all
Files:        find $DATA_ROOT -name '*.parquet' | head
EOF
