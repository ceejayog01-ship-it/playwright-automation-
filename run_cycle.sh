#!/usr/bin/env bash
# One scheduled market cycle — real-execution edition.
# Prices: Headway-native M30 preferred; Dukascopy fills only missing historical timestamps.
# Execution: accepted Design-A signals are permanently enabled for the demo account by market-cycle.yml.
#   0  restore audit chain from Supabase
#   1  login + fetch native M30 bars (fail-closed if invalid)
#   2  upsert bars + quality gate
#   3  TSE 51 BEST scan Design A only
#   4  signal intake (dedup) + export pending_orders.json
#   4b place strategy orders on Headway for each pending signal (when ALLOW_ORDERS=yes)
#   5  advance lifecycle + live monitor (SL/TIME) + verify
set -uo pipefail
ROOT="$(pwd)"
TSE="$ROOT/tse-reference/tse-mt5-integration"
HW="$ROOT/headway/headway-mt5-web-automation"
BR="$ROOT/bridge"
WORK="$ROOT/work"
mkdir -p "$WORK" "$HW/artifacts" "$ROOT/audit"
export BRIDGE_DATA_DIR="${BRIDGE_DATA_DIR:-$ROOT/data}"
export BRIDGE_WORK_DIR="$WORK"
RUN="${GITHUB_RUN_ID:-local}"
rc=0; worst() { [ "$1" -gt "$rc" ] && rc="$1"; return 0; }
py() { (cd "$BR" && python -m "$@"); }

# Force Design A only for production path
export KB_DESIGNS=A
export KB_PAIRS=EURUSD
export KB_EXEC_PAIRS=EURUSD

# 0 ---------------------------------------------------------------------------------------------
py tsebridge.supabase_sync pull --audit-dir "$ROOT/audit" || {
  echo "::error::cannot restore audit chain; aborting so the chain is never forked"
  echo 1 > "$WORK/exit_code"; exit 1
}

# 1 ---------------------------------------------------------------------------------------------
echo "== native M30 history pull =="
(cd "$HW" && npx ts-node src/index.ts --mode=history) > "$HW/artifacts/history.log" 2>&1
hrc=$?
cat "$HW/artifacts/history.log" || true
if [ "$hrc" -ne 0 ]; then
  echo "::warning::Headway native history pull failed (exit $hrc); attempting validated Dukascopy historical bootstrap."
fi

# 2 ---------------------------------------------------------------------------------------------
HIST_JSON="$HW/artifacts/history_EURUSD_M30.json"
HIST_CSV="$HW/artifacts/history_EURUSD_M30.csv"
if [ -f "$HIST_JSON" ]; then
  py tsebridge.store ingest-native --json "$HIST_JSON" --out "$WORK/eurusd_m30.csv" --quality "$WORK/history_quality.json"
  hist=$?
elif [ -f "$HIST_CSV" ]; then
  py tsebridge.store ingest-native --csv "$HIST_CSV" --out "$WORK/eurusd_m30.csv" --quality "$WORK/history_quality.json"
  hist=$?
else
  echo "::warning::no native history file produced; trying export of previously stored bars"
  py tsebridge.store export-m30 --out "$WORK/eurusd_m30.csv" --quality "$WORK/history_quality.json"
  hist=$?
fi
[ "$hist" -ne 0 ] && [ "$hist" -ne 10 ] && echo "::warning::native history ingest/export did not pass; Dukascopy bootstrap will attempt recovery"
cat "$WORK/history_quality.json" 2>/dev/null || true

# 2b --------------------------------------------------------------------------------------------
# Ensure at least 500 validated, completed M30 candles exist. Dukascopy inserts only missing timestamps;
# it cannot overwrite Headway-native records. If the provider fails or returns too few bars, no scan/orders.
echo "== historical bootstrap (Dukascopy fallback; minimum 500 M30 bars) =="
python scripts/bootstrap_dukascopy.py \
  --min-bars "${DUKASCOPY_MIN_M30_BARS:-500}" \
  --lookback-days "${DUKASCOPY_LOOKBACK_DAYS:-60}" \
  --output "$WORK/eurusd_m30.csv" \
  --report "$WORK/dukascopy_bootstrap.json"
hist=$?
if [ "$hist" -ne 0 ]; then
  echo "::error::historical bootstrap did not produce the required validated M30 history"
  worst 4
fi
cat "$WORK/dukascopy_bootstrap.json" 2>/dev/null || true

# 3 ---------------------------------------------------------------------------------------------
rm -f "$TSE/signals_out.json"
if [ "$hist" -eq 0 ]; then
  echo "== TSE scan (51 BEST, Design A only) on validated Headway + Dukascopy M30 history =="
  (cd "$TSE" && KB_PAIRS=EURUSD KB_EXEC_PAIRS=EURUSD KB_DESIGNS=A KB_M30_CSV="$WORK/eurusd_m30.csv" python scan_once.py) \
    || { echo "::warning::TSE scan failed"; worst 1; }
else
  echo "::warning::scan skipped: history quality gate failed"
fi
n=$(python -c "import json,os;p='$TSE/signals_out.json';print(len(json.load(open(p))) if os.path.exists(p) else 0)" 2>/dev/null || echo 0)
echo "signals this bar: $n"

# 4 ---------------------------------------------------------------------------------------------
AUTH="$HW/artifacts/auth_result.json"
QUOTES="$HW/artifacts/quotes.json"
if [ ! -f "$AUTH" ]; then
  echo "::warning::auth_result missing after history pull"
  worst 1
fi

py tsebridge.cycle --signals "$TSE/signals_out.json" --auth "$AUTH" --quotes "$QUOTES" \
   --audit-dir "$ROOT/audit" --run-id "$RUN-intake" --real-execution
worst $?

# 4b --------------------------------------------------------------------------------------------
# Autonomous strategy-order placement for pending signals (requires ALLOW_ORDERS=yes).
PENDING="$WORK/pending_orders.json"
if [ -f "$PENDING" ] && [ "${ALLOW_ORDERS:-no}" = "yes" ]; then
  echo "== strategy-order placement for pending signals =="
  count=$(python -c "import json;print(len(json.load(open('$PENDING'))))" 2>/dev/null || echo 0)
  echo "pending orders: $count"
  # Place at most one order per cycle by default (safe). Set MAX_ORDERS_PER_CYCLE higher if needed.
  MAX_N="${MAX_ORDERS_PER_CYCLE:-1}"
  python -c "
import json, pathlib
p=pathlib.Path('$PENDING')
orders=json.loads(p.read_text())[:int('$MAX_N')]
pathlib.Path('$WORK/pending_orders_this_cycle.json').write_text(json.dumps(orders, indent=2)+'\n')
print(len(orders))
" >/dev/null
  for i in $(seq 0 $((MAX_N - 1))); do
    sig=$(python -c "
import json
orders=json.load(open('$WORK/pending_orders_this_cycle.json'))
print(json.dumps(orders[$i]) if $i < len(orders) else '')
" 2>/dev/null || true)
    [ -z "$sig" ] && break
    echo "$sig" > "$WORK/one_signal.json"
    sid=$(python -c "import json;print(json.load(open('$WORK/one_signal.json'))['signal_id'])")
    side=$(python -c "import json;print(json.load(open('$WORK/one_signal.json'))['side'])")
    lots=$(python -c "import json;print(json.load(open('$WORK/one_signal.json')).get('lots',0.5))")
    sl=$(python -c "import json;print(json.load(open('$WORK/one_signal.json')).get('sl') or '')")
    echo "placing signal_id=$sid side=$side lots=$lots sl=$sl"
    SL_ARG=""
    [ -n "$sl" ] && [ "$sl" != "None" ] && SL_ARG="--sl $sl"
    (cd "$HW" && npx ts-node src/index.ts --mode=strategy-order --execute \
        --signal-file "$WORK/one_signal.json" --signal-id "$sid" --side "$side" --lots "$lots" $SL_ARG) \
      > "$HW/artifacts/strategy-order-$sid.log" 2>&1 || worst 1
    cat "$HW/artifacts/strategy-order-$sid.log" || true
    if [ -f "$HW/artifacts/strategy_order_result.json" ]; then
      cp "$HW/artifacts/strategy_order_result.json" "$WORK/strategy_order_result.json"
      py tsebridge.cycle --signals "$WORK/none.json" --auth "$AUTH" \
         --audit-dir "$ROOT/audit" --run-id "$RUN-fill-$sid" \
         --order-result "$WORK/strategy_order_result.json" --real-execution || true
    fi
  done
elif [ -f "$PENDING" ]; then
  echo "[orders] pending_orders.json exists but ALLOW_ORDERS!=yes — ledger only (no broker orders)"
else
  echo "[orders] no pending orders"
fi

# 5 ---------------------------------------------------------------------------------------------
echo '[]' > "$WORK/none.json"
py tsebridge.cycle --signals "$WORK/none.json" --auth "$AUTH" --quotes "$QUOTES" \
   --audit-dir "$ROOT/audit" --run-id "$RUN-advance" --real-execution
worst $?

# 5b Live position monitor
MANAGED="$WORK/managed_positions.json"
if [ -f "$MANAGED" ]; then
  echo "== live position monitor (TIME / SL close) =="
  EXEC_FLAG=""
  if [ "${ACCOUNT_MODE:-demo}" = "demo" ] && [ "${BRIDGE_REAL_EXECUTION:-true}" = "true" ] && [ "${ALLOW_ORDERS:-no}" = "yes" ]; then
    EXEC_FLAG="--execute"
  fi
  (cd "$HW" && npx ts-node src/index.ts --mode=monitor --managed "$MANAGED" $EXEC_FLAG) \
    > "$HW/artifacts/monitor.log" 2>&1 || worst 1
  cat "$HW/artifacts/monitor.log" || true
  if [ -f "$HW/artifacts/position_monitor.json" ]; then
    py tsebridge.cycle --signals "$WORK/none.json" --auth "$AUTH" \
       --audit-dir "$ROOT/audit" --run-id "$RUN-monitor" \
       --order-result "$HW/artifacts/position_monitor.json" --real-execution || true
  fi
else
  echo "[monitor] no managed_positions.json this cycle"
fi

# 5c Optional broker reconcile: compare ledger tickets to terminal (read-only unless ALLOW_ORDERS)
if [ "${RECONCILE_BROKER:-yes}" = "yes" ]; then
  echo "== broker reconcile snapshot =="
  (cd "$HW" && npx ts-node src/index.ts --mode=monitor --managed "$MANAGED" 2>/dev/null) || true
fi

py tsebridge.verify --audit-dir "$ROOT/audit"; worst $?
echo "$rc" > "$WORK/exit_code"
echo "cycle exit code: $rc"
exit "$rc"
