#!/usr/bin/env bash
# One scheduled market cycle. All prices come from the Headway web terminal.
#   0  restore the audit chain from Supabase (CI runners start empty)
#   1  build strategy history (M30 from sampled Headway candles) -> may legitimately be "not ready" (warm-up)
#   2  start the Headway sampler FIRST (login + 1 Hz bid sampling until the next half-hour boundary)
#   3  scan the 51 strategies (Design A, EURUSD) on that history while the sampler logs in
#   4  once the login result exists, intake signals (auth-gated), then keep sampling
#   5  ingest the sampled candles, advance the lifecycle simulation (SL / time exits), verify
# Exit code is the worst problem found; "history not ready" is a warning, not a failure.
set -uo pipefail
ROOT="$(pwd)"
TSE="$ROOT/tse-reference/tse-mt5-integration"; HW="$ROOT/headway/headway-mt5-web-automation"; BR="$ROOT/bridge"; WORK="$ROOT/work"
mkdir -p "$WORK" "$HW/artifacts" "$ROOT/audit"
export BRIDGE_DATA_DIR="${BRIDGE_DATA_DIR:-$ROOT/data}"
RUN="${GITHUB_RUN_ID:-local}"
rc=0; worst() { [ "$1" -gt "$rc" ] && rc="$1"; return 0; }
py() { (cd "$BR" && python -m "$@"); }

# 0 ---------------------------------------------------------------------------------------------
py tsebridge.supabase_sync pull --audit-dir "$ROOT/audit" || { echo "::error::cannot restore audit chain (exit $?); aborting so the chain is never forked"; echo 1 > "$WORK/exit_code"; exit 1; }

# 1 ---------------------------------------------------------------------------------------------
py tsebridge.store export-m30 --out "$WORK/eurusd_m30.csv" --quality "$WORK/history_quality.json"; hist=$?
[ "$hist" -ne 0 ] && [ "$hist" -ne 10 ] && worst 4

# 2 ---------------------------------------------------------------------------------------------
now=$(date +%s); until=$(( (now / 1800 + 1) * 1800 - 30 ))            # stop 30 s before the next M30 boundary
[ $(( until - now )) -lt 150 ] && until=$(( until + 1800 ))           # too close to the boundary: skip to the next window
(cd "$HW" && npx ts-node src/index.ts --mode=sample --until "$until") > "$HW/artifacts/sample.log" 2>&1 &
SPID=$!

# 3 ---------------------------------------------------------------------------------------------
rm -f "$TSE/signals_out.json"
if [ "$hist" -eq 0 ]; then
  (cd "$TSE" && KB_PAIRS=EURUSD KB_EXEC_PAIRS=EURUSD KB_DESIGNS=A KB_M30_CSV="$WORK/eurusd_m30.csv" python scan_once.py) || { echo "::warning::TSE scan failed"; worst 1; }
else
  echo "::warning::scan skipped: $(python -c "import json;print(json.load(open('$WORK/history_quality.json'))['reason'])") (warm-up or a degraded/missing latest bar)"
fi
n=$(python -c "import json,os;p='$TSE/signals_out.json';print(len(json.load(open(p))) if os.path.exists(p) else 0)")
echo "signals this bar: $n"

# 4 ---------------------------------------------------------------------------------------------
if [ "$n" -gt 0 ]; then
  for _ in $(seq 1 100); do [ -f "$HW/artifacts/auth_result.json" ] && break; kill -0 "$SPID" 2>/dev/null || break; sleep 3; done
fi
py tsebridge.cycle --signals "$TSE/signals_out.json" --auth "$HW/artifacts/auth_result.json" --quotes "$HW/artifacts/quotes.json" \
   --audit-dir "$ROOT/audit" --run-id "$RUN-intake"; worst $?

# 5 ---------------------------------------------------------------------------------------------
wait "$SPID"; srate=$?; [ "$srate" -ne 0 ] && { echo "::warning::sampler exited $srate"; worst 1; }
cat "$HW/artifacts/sample_status.json" 2>/dev/null || true
if [ -f "$HW/artifacts/headway_bars_EURUSD_M1.csv" ]; then
  py tsebridge.store ingest-m1 --csv "$HW/artifacts/headway_bars_EURUSD_M1.csv" --run-id "$RUN"; worst $?
else
  echo "::warning::no sampled candles this run (login or quote failure)"; worst 1
fi
echo '[]' > "$WORK/none.json"
py tsebridge.cycle --signals "$WORK/none.json" --auth "$HW/artifacts/auth_result.json" --audit-dir "$ROOT/audit" --run-id "$RUN-advance"; worst $?
py tsebridge.verify --audit-dir "$ROOT/audit"; worst $?
echo "$rc" > "$WORK/exit_code"; echo "cycle exit code: $rc"; exit "$rc"
