# TSE → Headway trading automation (integrated)

| Folder | What |
|---|---|
| `tse-reference/` | TSE signal engine (51 BEST strategies), unchanged; run with `KB_DESIGNS=A`, EURUSD |
| `bridge/` | Python: native M30 store, signal intake + signal_id dedup, real-order path, position monitoring, hash-chained audit, Supabase sync |
| `headway/headway-mt5-web-automation/` | Playwright: **fail-closed verified login**, chartprobe diagnostics, history attempt, quotes, probe, guarded orders, position monitor |
| `supabase/schema.sql` | Tables for signals, positions, orders, candles, events, heartbeat |
| `scripts/run_cycle.sh` | One scheduled cycle |

**Read in this order:** `SUPABASE.md` → `RUNBOOK.md` → `AUDIT.md` → `IMPLEMENTATION-NOTES.md`.

## Honest status (integrated)

- **Account identity** (post-login labelled account number + server, same frame, exact match) is implemented and fail-closed.
- **Chart selection** (EURUSD + M30 + visible title verification) is implemented and fail-closed inside `chartprobe`.
- **Native 500-candle extraction is NOT complete.**  
  `history.ts` is a best-effort evaluator against window/chart objects.  
  `chartprobe` deliberately reports `nativeCandles.validated=false` and overall audit **FAIL** until a proven decoder exists for Headway’s binary WebSocket history frames.  
  The cycle fails closed when insufficient native bars are returned — it does **not** invent candles from ticks or hover samples.
- Strategy scan and order path exist, but production readiness depends on a successful native history pull.

## Contract
- **Market data**: latest ~500 EURUSD M30 candles from the authenticated Headway MT5 terminal (source=`headway_native`). No manufactured 1s→M30 bars for the strategy.
- **Strategy**: 51 BEST (Design A) every 30 minutes. Original parameters and time-exit horizons preserved.
- **Execution**: valid signals → real market orders on the configured account (demo first). `signal_id` dedup. Position monitoring applies SL + time-exit.
- **Simulation** is no longer the primary execution path.

## Workflows
- `market-cycle.yml` — scheduled every 30 min across the FX week.
- `watchdog.yml` — heartbeat + daily audit-chain integrity.
- `headway-terminal.yml` — manual login / quotes / history / probe / order / monitor.
- `chart-probe.yml` — fail-closed diagnostic run (identity + chart + WS evidence). Upload artifacts even on failure.

## Secrets
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- `WEB_TERMINAL_URL`, `MT5_LOGIN`, `MT5_PASSWORD` (demo)
- Optional real: `ACCOUNT_MODE=real`, `EXPECTED_SERVER_LABEL`, `REAL_*`
- Optional `PROXY_SERVER` (residential recommended for CI)

There is **no Twelve Data dependency**. Every price used by the strategy is intended to come from the Headway terminal.


## Historical bootstrap and demo execution

The market-cycle workflow requires at least 500 validated, completed EURUSD M30 candles. It first ingests available Headway-native history, then runs `scripts/bootstrap_dukascopy.py` to fill missing timestamps from the pinned `dukascopy-node@1.51.1` public-data downloader. No Dukascopy account or API key is required. Bootstrap inserts are tagged `dukascopy_bootstrap` and are insert-only: they do not overwrite existing Headway rows. The scanner still requires fresh, valid market data and a successful Headway session before it can produce an executable order.

The scheduled market-cycle workflow is configured for demo-only automatic strategy execution (`ALLOW_ORDERS=yes`), capped at one new strategy order per cycle. It remains locked to the Headway demo account; this workflow does not enable live-account trading. Existing position monitoring and broker confirmation remain part of each cycle.
