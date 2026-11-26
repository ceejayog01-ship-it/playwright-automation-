# Implementation notes

## Provider split
Yahoo Finance: prices. Headway: login, account access, execution, confirmations. `tests/test_data_policy.py` and `test/no-market-data.test.ts` enforce this structurally (no other vendor strings; no quote/candle modules or modes in Headway code).

## Yahoo Finance
`yfinance.download("EURUSD=X", period="60d", interval="30m")` is used; no API key or Twelve Data REST endpoint is involved. Strict parsing rejects the whole payload on non-UTC timezone, off-grid timestamps, inconsistent/non-finite/implausible OHLC. Forming candle dropped. Rows tagged `source=yfinance`; any other-source rows in a restored cache are purged. Gate: 500 completed bars and the latest completed candle.

## Execution safeguards
- Strategy order refuses unless SL is applied; symbol must be listed; no price is read. An exception after the Buy/Sell click is `executed_unconfirmed` (previously could be recorded as a clean failure).
- Monitor: time exit only; broker-side SL.
- New ledger events: `BROKER_CLOSE_CONFIRMED`, `BROKER_POSITION_ABSENT`, `ORDER_EXPIRED`, `MONITOR_REPORT`.
- Positions with a broker ticket stay in `managed_positions.json` until the terminal confirms closure/absence, even if the candle replay already closed them in the ledger.

## Defects fixed in this update
1. `m30_bars` was called with `(start, now)` but treated `now` as a count: `TypeError` on every cycle with an open position and the wrong time window. Fixed.
2. Monitor and strategy result files were ingested by the wrong parser, logging spurious DATA_ERRORs; monitor results were never ingested. Fixed.
3. Candle-replay close removed positions from monitoring, which could leave a real position open. Fixed.
