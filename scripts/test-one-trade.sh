#!/usr/bin/env bash
# Place ONE controlled market order on Headway (demo recommended) and capture ticket_id.
#
# Dry-run (default — no Buy/Sell click):
#   ./scripts/test-one-trade.sh
#
# Live test (0.01 lots):
#   ALLOW_ORDERS=yes ./scripts/test-one-trade.sh --execute --lots 0.01
#
# Full strategy size (0.50 lots) — only after dry-run + small live test succeed:
#   ALLOW_ORDERS=yes ./scripts/test-one-trade.sh --execute --lots 0.50 --sl 1.05000
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HW="$ROOT/headway/headway-mt5-web-automation"
cd "$HW"

SIDE=buy
LOTS=0.01
SL=""
EXECUTE=""
CLOSE=""
SIGNAL_ID="test-trade-$(date +%s)"
SYMBOL=EURUSD

while [ $# -gt 0 ]; do
  case "$1" in
    --execute) EXECUTE="--execute"; shift ;;
    --close) CLOSE="--close"; shift ;;
    --side) SIDE="$2"; shift 2 ;;
    --lots) LOTS="$2"; shift 2 ;;
    --sl) SL="$2"; shift 2 ;;
    --signal-id) SIGNAL_ID="$2"; shift 2 ;;
    --symbol) SYMBOL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 2 ;;
  esac
done

if [ ! -f .env ] && [ -f .env.example ]; then
  echo "Copy .env.example to .env and set MT5_LOGIN / MT5_PASSWORD first."
  exit 1
fi

echo "=== test-one-trade ==="
echo "symbol=$SYMBOL side=$SIDE lots=$LOTS signal_id=$SIGNAL_ID execute=${EXECUTE:-no}"
echo "Guards: ALLOW_ORDERS=${ALLOW_ORDERS:-no} ACCOUNT_MODE=${ACCOUNT_MODE:-demo}"

ARGS=(--mode=strategy-order --symbol "$SYMBOL" --side "$SIDE" --lots "$LOTS" --signal-id "$SIGNAL_ID")
[ -n "$SL" ] && ARGS+=(--sl "$SL")
[ -n "$EXECUTE" ] && ARGS+=($EXECUTE)
[ -n "$CLOSE" ] && ARGS+=($CLOSE)

npx ts-node src/index.ts "${ARGS[@]}"
echo "--- result ---"
cat artifacts/strategy_order_result.json 2>/dev/null || true
if [ -f artifacts/managed_positions.json ]; then
  echo "--- managed_positions (for monitor) ---"
  cat artifacts/managed_positions.json
fi
