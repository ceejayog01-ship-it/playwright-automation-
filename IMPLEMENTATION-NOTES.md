# Implementation notes — integrated package

## What this package contains

1. **TSE reference engine** (unchanged) — 51 BEST strategies, Design A.
2. **Python bridge** — store, cycle, signals, lifecycle, verify, Supabase sync, hash-chained audit.
3. **Headway Playwright automation** (merged):
   - Stronger **fail-closed login** (labelled account + server in the same post-login frame).
   - **chartprobe** mode: identity gate + EURUSD/M30 selection + hover/WS evidence capture. Deliberately reports native 500-candle gate as FAIL until a decoder is proven.
   - **history** mode: best-effort `fetchNativeM30()` against window/chart objects. Fails closed on insufficient bars.
   - **nativeRates** pure validator (exactly 500, ordered timestamps, OHLC consistency) — used by tests; not yet fed by a real decoder.
   - quotes, ticks/sample (diagnostic), probe, guarded dummy order, position monitor.
4. **Workflows** for market cycle, watchdog, terminal modes, and chart-probe audit.

## What was removed / cleaned
- Historical LOGIN-FIX-NOTES and intermediate AUDIT-STATUS / README-CHARTPROBE documents that described intermediate fixes.
- Duplicate nested `.github` inside the automation package.
- Conflicting claims that native 500-candle extraction was complete.

## Remaining work before treating native history as production-ready
1. Run `chart-probe.yml` (or `npm run chartprobe`) on a live demo session.
2. Examine saved `frames/*.bin` and `chart-probe-report.json`.
3. Implement a protocol decoder that produces exactly 500 unique, ordered, validated EURUSD M30 OHLC records.
4. Wire that decoder into `history.ts` / `fetchNativeM30` and re-run the native-rates validation gate.
5. Only then can the overall audit report PASS for the 500-candle requirement.

Until then the system is intentionally fail-closed: missing or unproven native history aborts the cycle rather than inventing prices.

## Strategy order + ticket capture (added)

### Headway
- `src/strategyOrder.ts` — places one market order for a signal, optional SL field, captures `ticket_id` from Trade tab.
- Mode: `npm run strategy-order` / `--mode=strategy-order`
- Guards: `ALLOW_ORDERS=yes`, lots in `[0.01, 0.50]`, real-mode confirm string, single click, dry-run default.
- Artifacts: `artifacts/strategy_order_result.json`, `artifacts/managed_positions.json` (when ticket known).

### Bridge
- Ingests `strategy_order_result.json` → `STRATEGY_ORDER` + `ORDER_FILLED` (attaches `ticket_id` to open ledger position).
- `write_reports` exports `work/managed_positions.json` for the monitor.

### Test one trade
```bash
cd headway/headway-mt5-web-automation
cp .env.example .env   # set demo login
# 1) dry-run (no order)
../../scripts/test-one-trade.sh
# 2) live tiny trade
ALLOW_ORDERS=yes ../../scripts/test-one-trade.sh --execute --lots 0.01
# 3) optional close in same run
ALLOW_ORDERS=yes ../../scripts/test-one-trade.sh --execute --lots 0.01 --close
```

### Still manual / UI-dependent
- Order ticket selectors may need one dry-run screenshot pass on your terminal build.
- If SL input is not found, order still places; set SL manually or refine selectors.
- Ticket parsing is best-effort from Trade tab text; outcome `executed_unconfirmed` means check the account.

## Full wiring pass (signal → order → ticket → monitor)

1. `run_cycle.sh` step **4b** reads `work/pending_orders.json` and runs Headway `--mode=strategy-order --execute` for up to `MAX_ORDERS_PER_CYCLE` (default 1).
2. Results ingested as `STRATEGY_ORDER` + `ORDER_FILLED` or `ORDER_FAILED`.
3. `REQUIRE_BROKER_SL=yes` (default) aborts live placement if SL cannot be set on the ticket.
4. Monitor close requires the position to disappear from the Trade tab.
5. TSE `KB_DESIGNS` defaults to **A** only.
6. `history.ts` verifies chart title and runs `validateNativeM30Rates` on retrieved bars.

### Environment knobs
- `ALLOW_ORDERS=yes` — enable live Buy/Sell/Close
- `MAX_ORDERS_PER_CYCLE=1` — cap autonomous placements per cycle
- `REQUIRE_BROKER_SL=yes` — fail if SL field missing/unapplied
- `ALLOW_ORDER_WITHOUT_SL=yes` — override (not recommended)
- `HISTORY_STRICT=1` — require exactly 500 validated bars for history ok
- `HISTORY_MIN_BARS=120` — soft minimum when not strict
