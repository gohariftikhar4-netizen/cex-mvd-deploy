#!/usr/bin/env bash
#
# One-command, from-scratch deploy on a fresh Debian/Ubuntu VPS.
# Run as root on the VPS:
#
#   curl -fsSL <raw-url>/scripts/bootstrap.sh | sudo bash
# or, from a clone:
#   sudo bash cex_microstructure_mvd/scripts/bootstrap.sh
#
# It installs OS packages, copies the package to /opt, then hands off to the
# systemd installer (which runs the pre-deploy gates and starts the recorders).
#
# Env overrides: REPO_URL, BRANCH, APP_DIR, RUN_USER.
# Public market data only. No API keys, no secrets, no trading.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/gohariftikhar4-netizen/work-v2.git}"
BRANCH="${BRANCH:-claude/sann-start-5jp7p8}"
APP_DIR="${APP_DIR:-/opt/cex_microstructure_mvd}"
RUN_USER="${RUN_USER:-cexrec}"

if [[ $EUID -ne 0 ]]; then echo "run as root (sudo bash bootstrap.sh)"; exit 1; fi

echo "=== apt packages ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip git rsync jq curl logrotate ca-certificates >/dev/null
echo "packages installed"

echo "=== fetch package -> $APP_DIR ==="
SRC_PKG="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || true)"
if [[ -n "$SRC_PKG" && -f "$SRC_PKG/requirements.txt" ]]; then
  # Running from an existing clone: copy this package into place.
  mkdir -p "$APP_DIR"
  rsync -a --delete "$SRC_PKG"/ "$APP_DIR"/
else
  # No local copy: clone the repo and copy the package subdir.
  tmp="$(mktemp -d)"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$tmp"
  mkdir -p "$APP_DIR"
  rsync -a --delete "$tmp/cex_microstructure_mvd"/ "$APP_DIR"/
  rm -rf "$tmp"
fi
echo "package staged at $APP_DIR"

echo "=== systemd install ==="
cd "$APP_DIR/scripts/systemd"
chmod +x install.sh
APP_DIR="$APP_DIR" RUN_USER="$RUN_USER" ./install.sh
