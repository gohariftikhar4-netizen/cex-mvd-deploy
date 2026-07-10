#!/usr/bin/env bash
#
# Daily discovery-engine job: build feature matrix -> rank per class (when the sample
# gate is met) -> write reports + a health/alert JSON to $REPORTS. Verifies the 4
# recorder services WITHOUT touching them. Measurement only; no trading, no wallets.
#
# Env: APP_DIR, DATA_ROOT, REPORTS, GATE (min events), MAX_EVENTS
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/cex_microstructure_mvd}"
DATA_ROOT="${DATA_ROOT:-/var/lib/cexrec/data}"
REPORTS="${REPORTS:-/var/lib/cexrec/reports}"
GATE="${GATE:-200}"
MAX_EVENTS="${MAX_EVENTS:-3000}"
PY="$APP_DIR/.venv/bin/python"
SC="$APP_DIR/scripts"
MATRIX="$REPORTS/dex_feature_matrix.parquet"
LOG="$REPORTS/discovery_run.log"
HEALTH="$REPORTS/discovery_health.json"
mkdir -p "$REPORTS"
export DATA_ROOT

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(ts) $*" | tee -a "$LOG"; }

log "=== discovery run start ==="

# 1) build feature matrix (fast/robust; GT off by default)
log "building feature matrix (max=$MAX_EVENTS)"
"$PY" "$SC/build_dex_feature_matrix.py" --data-root "$DATA_ROOT" --max-events "$MAX_EVENTS" \
      --out "$MATRIX" >>"$LOG" 2>&1
ENRICH_RC=$?

# 2) event counts + A/B groups from the matrix
read -r EVENTS GA GB <<<"$("$PY" - "$MATRIX" <<'PY' 2>/dev/null
import sys, pandas as pd, os
try:
    fm=pd.read_parquet(sys.argv[1])
    a=int((fm.get("pending_visible",0)==1).sum()); print(len(fm), a, len(fm)-a)
except Exception: print(0,0,0)
PY
)"
EVENTS=${EVENTS:-0}; GA=${GA:-0}; GB=${GB:-0}
log "matrix events=$EVENTS groupA(pending_visible)=$GA groupB=$GB gate=$GATE"

# 3) ranking (only if gate met) — 3 class leaderboards; horizons incl ret_b5/ret_s300/ret_s900
RANKED=0
if [ "$EVENTS" -ge "$GATE" ]; then
  for CLS in pre_inclusion post_block proxy_research; do
    "$PY" "$SC/rank_dex_features.py" --matrix "$MATRIX" --class "$CLS" --min-events "$GATE" \
        --out "$REPORTS/leaderboard_${CLS}.csv" >>"$LOG" 2>&1
    [ -f "$REPORTS/leaderboard_${CLS}.csv" ] && RANKED=$((RANKED+1))
  done
  log "ranking done: $RANKED/3 class leaderboards written"
else
  log "SAMPLE GATE NOT MET ($EVENTS < $GATE) — ranking skipped (by design)"
fi

# 4) health check — services (untouched), disk, RPC
SVC_OK=1; declare -A SVC
for s in cex-okx cex-coinbase cex-mempool-eth cex-dex-outcome-eth; do
  st=$(systemctl is-active "$s" 2>/dev/null || echo unknown)
  SVC[$s]=$st; [ "$st" = active ] || SVC_OK=0
done
DISK_GB=$(du -sb "$DATA_ROOT" 2>/dev/null | awk '{printf "%.2f",$1/1e9}')
DISK_FREE_GB=$(df -B1 --output=avail "$DATA_ROOT" 2>/dev/null | tail -1 | awk '{printf "%.1f",$1/1e9}')
RPC_OK=0; curl -fsS --max-time 10 -X POST "${ETH_RPC_URL:-https://ethereum-rpc.publicnode.com}" \
  -H 'content-type: application/json' -H 'User-Agent: Mozilla/5.0' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' >/dev/null 2>&1 && RPC_OK=1

# 5) verdict
if [ "$SVC_OK" -ne 1 ]; then VERDICT="DISCOVERY ENGINE WARNING — FIX NEEDED"
elif [ "$ENRICH_RC" -ne 0 ] || [ "$EVENTS" -eq 0 ]; then VERDICT="DISCOVERY ENGINE WARNING — FIX NEEDED"
elif [ "$EVENTS" -lt "$GATE" ]; then VERDICT="DISCOVERY ENGINE RUNNING — SAMPLE GATE NOT MET"
elif [ "$RANKED" -ge 1 ]; then VERDICT="DISCOVERY ENGINE RUNNING — PROVISIONAL RANKING READY"
else VERDICT="DISCOVERY ENGINE WARNING — FIX NEEDED"; fi
log "VERDICT: $VERDICT"

# 6) write health JSON (alert = verdict + any service down)
{
  echo "{"
  echo "  \"ts\": \"$(ts)\","
  echo "  \"verdict\": \"$VERDICT\","
  echo "  \"services\": {$(for s in "${!SVC[@]}"; do printf '"%s":"%s",' "$s" "${SVC[$s]}"; done | sed 's/,$//')},"
  echo "  \"all_services_active\": $([ $SVC_OK -eq 1 ] && echo true || echo false),"
  echo "  \"matrix_events\": $EVENTS, \"group_A_pending_visible\": $GA, \"group_B_inclusion_only\": $GB,"
  echo "  \"sample_gate\": $GATE, \"gate_met\": $([ "$EVENTS" -ge "$GATE" ] && echo true || echo false),"
  echo "  \"leaderboards_written\": $RANKED,"
  echo "  \"enrich_exit_code\": $ENRICH_RC,"
  echo "  \"disk_used_gb\": ${DISK_GB:-0}, \"disk_free_gb\": ${DISK_FREE_GB:-0},"
  echo "  \"rpc_ok\": $([ $RPC_OK -eq 1 ] && echo true || echo false),"
  echo "  \"alert\": $([ \"$VERDICT\" != \"DISCOVERY ENGINE RUNNING — SAMPLE GATE NOT MET\" ] && [ \"$SVC_OK\" -ne 1 ] && echo true || echo false)"
  echo "}"
} > "$HEALTH"
log "health -> $HEALTH ; reports in $REPORTS"
log "=== discovery run end ==="
# exit non-zero only on a real problem (service down / enrich fail) so the timer flags it
[ "$SVC_OK" -eq 1 ] && [ "$ENRICH_RC" -eq 0 ] && [ "$EVENTS" -gt 0 ]
