# Runbook

## A. One-time setup

1. GitHub Actions secrets: `(no API key required)`, `MT5_LOGIN`, `MT5_PASSWORD` (Headway demo). Optional: `WEB_TERMINAL_URL`, `PROXY_SERVER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Never commit credentials.
2. Yahoo Finance intraday history is fetched through yfinance; no provider API key is required.
3. State is restored from the Actions cache. Because this repository was deleted/rebuilt, the first run may not have a prior audit ledger. Before the first manual dispatch, verify the Headway demo account has no unmanaged bot positions; then use the explicit fresh-state confirmation. Do not run the scheduled cycle until that migration has completed and the ledger is being cached.
4. Old cached candles (from earlier Headway/other-vendor paths) are purged automatically on the first run; the store is rebuilt from Yahoo Finance.

## B. Each cycle (every 30 minutes, FX week)

1. Restore audit ledger + candle CSV (hash chain verified).
2. `scripts/fetch_market_data.py`: fetch EURUSD M30 from Yahoo Finance, validate, require 500 completed candles and the latest completed candle (waits up to ~100 s for it). Fail-closed: no data, no scan, no order. Weekend closure is a clean skip (exit 20).
3. Headway login (auth evidence only).
4. 51-strategy scan (Design A).
5. Intake + dedupe; send at most ONE order (demo only); results ingested only with broker evidence; Telegram notice.
6. Advance ledger; monitor closes positions at their original time exit and reconciles; Telegram notice for confirmed closes.
7. Verify audit chain; save state.

## C. Safety and evidence

- Demo-locked. No trailing stop. No TP. Strategy parameters untouched.
- Stop-loss is broker-side (required on the ticket). The monitor does not read prices.
- Telegram wording: `ORDER CONFIRMED BY BROKER`, `ORDER NOT CONFIRMED - CHECK THE ACCOUNT MANUALLY`, `POSITION CLOSED (broker-confirmed)`, `POSITION NO LONGER VISIBLE` (e.g. broker stop-out; not claimed as a confirmed close).
- Exit code 5 = a click or close could not be confirmed: check the account manually.

## D. Artifacts

`work/market_data_report.json`, `audit/events.jsonl`, `audit/summary.json`, Headway `strategy_order_result.json`, `position_monitor.json`, logs (no screenshots).

## E. Limitations

GitHub scheduling is best-effort (late or skipped runs). The ledger's SL/TIME replay uses Yahoo Finance candles and is a record, not broker truth; broker truth comes from the terminal confirmations. Exit price and P&L are not read from the broker. Actions cache can be evicted (hard stop, reconcile).
