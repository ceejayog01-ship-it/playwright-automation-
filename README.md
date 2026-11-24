# TSE → Headway trading automation (integrated)

| Folder | What |
|---|---|
| `tse-reference/` | TSE signal engine (51 BEST strategies); strategy files are unchanged |
| `bridge/` | Python signal intake, deduplication, risk checks, local candle store, position lifecycle and hash-chained audit |
| `headway/headway-mt5-web-automation/` | Playwright terminal automation; existing login implementation is unchanged |
| `data/candles_m30.csv` | Local M30 candle store, persisted between scheduled runs by GitHub Actions cache |
| `scripts/run_cycle.sh` | Scheduled market-cycle entry point |

Read `RUNBOOK.md`, then `AUDIT.md` and `IMPLEMENTATION-NOTES.md`.

## Market data

The cycle attempts Headway-native EURUSD M30 history first. If the local store has fewer than 500 valid completed M30 bars, `scripts/bootstrap_dukascopy.py` downloads historical EURUSD M30 bid candles from the public Dukascopy feed. Dukascopy rows are insert-only and do not overwrite existing Headway rows. Freshness and OHLC checks still apply; a failed history gate prevents scanning and order placement. The Headway native binary-history decoder remains unproven, so the Dukascopy fallback is the historical bootstrap path.

## Strategy and execution

The scheduled `.github/workflows/market-cycle.yml` runs the existing 51-strategy scanner using the configured Design A path. It is explicitly locked to the Headway demo account, sets `ALLOW_ORDERS=yes`, and caps new strategy orders at one per cycle. A signal is not considered executed merely because the workflow is green: broker confirmation and the resulting ticket/position evidence are required.

The existing strategy files, parameters, and Headway login verification implementation are not changed by this patch. The manual `demo-trade-test.yml` workflow remains separate and is not a substitute for the scheduled signal-driven execution path.

## State persistence without Supabase

Supabase REST calls, schema, sync code, and Supabase-only watchdog are removed. The market cycle stores candles in `data/candles_m30.csv` and restores/saves that file plus `audit/events.jsonl` through GitHub Actions cache. The event log remains hash-chained and verifies its chain before replay.

GitHub Actions cache is not a transactional database and can be evicted. The workflow therefore refuses to trade when `audit/events.jsonl` is missing. On the first migration only, manually dispatch `market-cycle.yml` and select the explicit fresh-state confirmation **only after** checking that the Headway demo account has no unmanaged bot positions and accepting that the old Supabase signal/audit history will not be imported. Do not use that confirmation to bypass a missing cache after normal operation; reconcile broker positions and state first.

## Workflows

- `market-cycle.yml` — scheduled demo signal scan, execution, position management and local audit.
- `headway-terminal.yml` — manual Headway terminal operations.
- `chart-probe.yml` — read-only chart and WebSocket diagnostics.
- `main.yml` — repository CI / automation checks.
- `demo-trade-test.yml` — optional manual demo connectivity test; not part of the scheduled strategy cycle.

## Verification status

Offline Python bridge suite: 50 tests passed in the packaging environment. Bash syntax and workflow YAML are checked during packaging. Live Dukascopy availability, Headway login, broker order placement, broker-side position management and closure still require a real GitHub Actions demo run; none is claimed complete by creating this ZIP.
