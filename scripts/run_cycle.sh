#!/usr/bin/env bash
# One scheduled market cycle.
# Prices: Yahoo Finance is the ONLY market-price provider. Headway supplies no candles and no prices;
#         it is used for login, account access, order execution and broker confirmations.
#   0  use the audit chain and candle store restored from GitHub Actions cache
#   1  fetch + validate the latest 500 completed EURUSD M30 candles from Yahoo Finance (fail-closed)
#   2  Headway login (auth proof only)
#   3  TSE 51 BEST scan, Design A only (unchanged strategy)
#   4  signal intake (dedup) + at most ONE new broker order per cycle, broker-confirmed
#   5  advance ledger + broker-confirmed position monitor (time exit) + Telegram + verify
set -uo pipefail
ROOT="$(pwd)"
TSE="$ROOT/tse-reference/tse-mt5-integration"
HW="$ROOT/headway/headway-mt5-web-automation"
BR="$ROOT/bridge"
WORK="$ROOT/work"
mkdir -p "$WORK" "$HW/artifacts" "$ROOT/audit"
# Remove stale result artifacts before this run. Never ingest a previous cycle's
# auth/order/monitor JSON as if it were a current broker response.
rm -f "$HW/artifacts/auth_result.json" \
      "$HW/artifacts/order_result.json" \
      "$HW/artifacts/strategy_order_result.json" \
      "$HW/artifacts/position_monitor.json" \
      "$HW/artifacts/managed_positions.json" \
      "$WORK/strategy_order_result.json" \
      "$WORK/position_monitor.json"

# The frozen 51-strategy definitions are required inputs, not generated defaults.
# Fail before login or market execution if the supplied recovery bundle omitted them.
if [ ! -s "$TSE/data/strategies.json" ] || [ ! -s "$TSE/data/edges.json" ]; then
  echo "::error::Frozen TSE strategy data missing: expected data/strategies.json and data/edges.json. Restore the original strategy files; do not substitute invented strategies."
  exit 6
fi
# Never create a fresh deduplication/position ledger silently. The workflow must
# restore it from cache or create the empty file only after explicit migration confirmation.
if [ ! -f "$ROOT/audit/events.jsonl" ]; then
  echo "::error::audit/events.jsonl is missing; refusing to run without restored trading state"
  exit 5
fi
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
# The workflow restores audit/events.jsonl and data/candles_m30.csv from its
# serialized GitHub Actions cache before this script starts. EventLog verifies
# the complete local hash chain; if it is corrupt, cycle.py fails closed.

# 1 ---------------------------------------------------------------------------------------------
echo "== market data: Yahoo Finance M30 (sole price provider) =="
python scripts/fetch_market_data.py --min-bars "${MARKET_MIN_M30_BARS:-500}" \
  --output "$WORK/eurusd_m30.csv" --report "$WORK/market_data_report.json"
mrc=$?
case "$mrc" in
  0)  DATA_OK=1 ;;
  20) DATA_OK=0; echo "::notice::market closed; scan skipped" ;;
  *)  DATA_OK=0; echo "::error::market data gate failed (exit $mrc); no scan, no new orders"; worst 4 ;;
esac

# 2 ---------------------------------------------------------------------------------------------
# Headway login only: produces artifacts/auth_result.json (the account-type proof the bridge requires).
echo "== Headway login (no market data is read) =="
(cd "$HW" && npx ts-node src/index.ts --mode=login) > "$HW/artifacts/login.log" 2>&1 || worst 1
cat "$HW/artifacts/login.log" || true

# 3 ---------------------------------------------------------------------------------------------
rm -f "$TSE/signals_out.json"
if [ "$DATA_OK" -eq 1 ]; then
  echo "== TSE scan (51 BEST, Design A only) on validated Yahoo Finance M30 history =="
  (cd "$TSE" && KB_PAIRS=EURUSD KB_EXEC_PAIRS=EURUSD KB_DESIGNS=A KB_M30_CSV="$WORK/eurusd_m30.csv" python scan_once.py) \
    || { echo "::warning::TSE scan failed"; worst 1; }
else
  echo "::warning::scan skipped: market data gate did not pass"
fi
n=$(python -c "import json,os;p='$TSE/signals_out.json';print(len(json.load(open(p))) if os.path.exists(p) else 0)" 2>/dev/null || echo 0)
echo "signals this bar: $n"

# 4 ---------------------------------------------------------------------------------------------
AUTH="$HW/artifacts/auth_result.json"
if [ ! -f "$AUTH" ]; then
  echo "::warning::auth_result missing after Headway login"
  worst 1
fi

py tsebridge.cycle --signals "$TSE/signals_out.json" --auth "$AUTH" \
   --audit-dir "$ROOT/audit" --run-id "$RUN-intake" --real-execution
worst $?

# 4b --------------------------------------------------------------------------------------------
# Autonomous strategy-order placement for pending signals (requires ALLOW_ORDERS=yes).
PENDING="$WORK/pending_orders.json"
if [ -f "$PENDING" ] && [ "${ALLOW_ORDERS:-no}" = "yes" ] && [ "${ACCOUNT_MODE:-demo}" = "demo" ]; then
  echo "== strategy-order placement for pending signals =="
  count=$(python -c "import json;print(len(json.load(open('$PENDING'))))" 2>/dev/null || echo 0)
  echo "pending orders: $count"
  # HARD CAP: exactly one new order per cycle. Not configurable by environment.
  MAX_N=1
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
    rm -f "$HW/artifacts/strategy_order_result.json"
    (cd "$HW" && npx ts-node src/index.ts --mode=strategy-order --execute \
        --signal-file "$WORK/one_signal.json" --signal-id "$sid" --side "$side" --lots "$lots" $SL_ARG) \
      > "$HW/artifacts/strategy-order-$sid.log" 2>&1 || worst 1
    cat "$HW/artifacts/strategy-order-$sid.log" || true
    if [ -s "$HW/artifacts/strategy_order_result.json" ] && python - "$HW/artifacts/strategy_order_result.json" "$sid" <<'PY'
import json, sys
try:
    r=json.load(open(sys.argv[1], encoding="utf-8"))
    valid=(isinstance(r,dict) and r.get("schema")==1 and r.get("kind")=="STRATEGY_ORDER"
           and r.get("signal_id")==sys.argv[2] and isinstance(r.get("outcome"),str)
           and isinstance(r.get("execute"),bool) and isinstance(r.get("opened_confirmed"),bool))
except Exception:
    valid=False
raise SystemExit(0 if valid else 1)
PY
    then
      cp "$HW/artifacts/strategy_order_result.json" "$WORK/strategy_order_result.json"
      py tsebridge.cycle --signals "$WORK/none.json" --auth "$AUTH" \
         --audit-dir "$ROOT/audit" --run-id "$RUN-fill-$sid" \
         --order-result "$WORK/strategy_order_result.json" --real-execution || worst $?
    else
      echo "::error::Fresh strategy-order result missing or invalid; it was not ingested."
      worst 1
    fi
  done
elif [ -f "$PENDING" ]; then
  echo "[orders] pending orders not placed (needs ALLOW_ORDERS=yes, demo account, and a passed market-data gate)"
else
  echo "[orders] no pending orders"
fi

# 5 ---------------------------------------------------------------------------------------------
echo '[]' > "$WORK/none.json"
py tsebridge.cycle --signals "$WORK/none.json" --auth "$AUTH" \
   --audit-dir "$ROOT/audit" --run-id "$RUN-advance" --real-execution
worst $?

# 5b Live position monitor
MANAGED="$WORK/managed_positions.json"
if [ -f "$MANAGED" ]; then
  echo "== broker-confirmed position monitor (time exit; stop-loss is broker-side) =="
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
