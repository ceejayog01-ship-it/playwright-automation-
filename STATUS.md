# Implementation status (honest)

Updated after the integration pass that wired signal→order, hardened SL/close, Design A defaults, and history validation gates.

## Fixed / implemented in code

| Item | Status |
|------|--------|
| EURUSD restriction | Done (bridge + scan env) |
| Design A enforcement | Done — bridge rejects B/TP; `KB_DESIGNS=A` default in TSE config + run_cycle |
| Account verification | Done (fail-closed labelled login+server) |
| Signal id + dedup | Done |
| Signal → execution integration | Done — `pending_orders.json` → Headway `strategy-order` when `ALLOW_ORDERS=yes` |
| Strategy market order module | Done (`strategyOrder.ts`) with ticket capture |
| Broker SL required | Done — refuses live order without SL unless `ALLOW_ORDER_WITHOUT_SL=yes`; SL apply failure aborts when `REQUIRE_BROKER_SL=yes` (default) |
| Close verification | Hardened — requires position gone from Trade tab, not only click |
| Time-exit deadlines | Done in ledger (`horizon_bars` → deadline); monitor uses deadline for TIME close |
| Crash recovery (ledger) | Done via event log; pending re-export each cycle |
| Order failure path | Done — `ORDER_FAILED` event when strategy order fails |
| Historical validation helper | Connected to history path when bars exist |
| Chart M30 independent check | Used inside `history.ts` (title must show EURUSD, M30) |
| Autonomous cycle wiring | Done in `scripts/run_cycle.sh` step 4b |

## Still incomplete (cannot be claimed done without live evidence)

| Item | Why |
|------|-----|
| Native historical M30 decoder | No proven binary WebSocket protocol decoder. `history.ts` is best-effort window/chart evaluation. Without live rates arrays or a real decoder, 500 native candles cannot be guaranteed. |
| Exact 500 candles every cycle | Enforced only when data is present and validates; often unavailable until decoder exists. Use `HISTORY_STRICT=1` to require 500. |
| Continuous native refresh | Depends on successful history pull each cycle. |
| Broker selectors 100% proven | Order ticket / Trade tab selectors need one successful live dry-run + execute on your terminal build. |
| Independent full E2E proof | Requires your demo credentials and a live run; cannot be finished in this offline package alone. |
| Broker reconciliation after crash | Ledger recovers; automatic mismatch repair vs broker is still best-effort via monitor NOT_FOUND. |

## How to run a real demo path

```bash
# demo credentials in headway/.../.env
export ALLOW_ORDERS=yes          # required for live clicks
export MAX_ORDERS_PER_CYCLE=1    # safety
export REQUIRE_BROKER_SL=yes
# optional: export HISTORY_STRICT=1

./scripts/run_cycle.sh
# or single trade:
./scripts/test-one-trade.sh --execute --lots 0.01 --sl <price>
```

## Acceptance rule

Do not treat the system as production-complete until:

1. `chartprobe` or `history` returns validated native M30 bars from a live session, and  
2. One full cycle places an order, captures `ticket_id`, and monitor/close is verified on the terminal.
