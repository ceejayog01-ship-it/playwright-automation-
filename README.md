# TSE → Headway trading automation (recovery bundle)

> **Rebuilt from the original repository ZIP supplied by the user plus the Yahoo Finance / Headway update files.** Original TSE scanner and Headway browser/login source were restored from that archive. Review `AUDIT.md`, `RUNBOOK.md`, and `IMPLEMENTATION-NOTES.md` before enabling the scheduled demo cycle.


| Folder | What |
|---|---|
| `tse-reference/` | TSE signal engine (51 BEST strategies); strategy files are unchanged |
| `bridge/` | Python signal intake, deduplication, risk checks, Yahoo Finance provider, local candle store, lifecycle ledger, Telegram notices, hash-chained audit |
| `headway/headway-mt5-web-automation/` | Playwright terminal automation: login, order execution, broker confirmations. Login implementation unchanged |
| `scripts/fetch_market_data.py` | Yahoo Finance fetch + validation gate (500 completed EURUSD M30 candles) |
| `scripts/run_cycle.sh` | Scheduled market-cycle entry point |

Read `RUNBOOK.md`, then `AUDIT.md` and `IMPLEMENTATION-NOTES.md`.

## Architecture (single responsibility per provider)

- **Yahoo Finance** is the only market-price provider. `yfinance.download` fetches EURUSD M30 candles (UTC); no API key is required. Nothing else supplies candles or prices; Dukascopy is removed; `yfinance` is the sole market-price provider.
- **Headway** is used for login, account access, order execution and broker confirmations only. It collects no candles and reads no market prices.
- **GitHub Actions** runs the cycle on a schedule (no VPS). State (audit ledger + candle CSV) persists in the Actions cache.

## Strategy and execution

`.github/workflows/market-cycle.yml` runs the unchanged 51-strategy scanner (Design A: SL, no TP, original 6/12/24 h time exit, no trailing stop) every 30 minutes. It is locked to the Headway **demo** account (`ACCOUNT_MODE=demo`, `Headway-Demo`) and places **at most one new order per cycle**. The stop-loss must be attached to the broker ticket or the order is refused. A trade is reported as executed only after the terminal confirms it; a click that cannot be confirmed is reported as UNCONFIRMED (Telegram) and exits with code 5.

Signals beyond the one-per-cycle cap are expired (never traded late at a stale entry).

## Secrets

`(no API key required)`, `MT5_LOGIN`, `MT5_PASSWORD`, optional `WEB_TERMINAL_URL`, `PROXY_SERVER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Workflows

- `market-cycle.yml` — scheduled demo cycle.
- `headway-terminal.yml` — manual: `login`, guarded dummy `order`, `monitor`.
- `main.yml` — repository installer (unchanged).

## Verification status

Offline rebuild checks: 90 Python tests passed, Bash syntax and Python compilation passed, and workflow YAML parsed. The TypeScript/npm suite was not run locally; GitHub Actions runs it. Not yet verified live: Yahoo Finance data retrieval on a GitHub runner, Headway order/close selectors, and a broker-confirmed fill/close. A green workflow is not proof of a fill; require the ticket and the Telegram confirmation.
