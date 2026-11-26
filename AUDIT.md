# Audit guide — TSE → Headway bridge (integrated)

> **Current honest status:** Market candles come only from Yahoo Finance. Headway supplies no candles or prices. Account-identity gates and the broker-confirmation rules are fail-closed; live broker behaviour is unverified until a demo run.

---

## Locked contract (`bridge/tsebridge/config.py`, constants, recorded in every RUN_STARTED event)
EURUSD · BUY/SELL · **0.50 lots** · stop-loss from TSE signal · **no take-profit** · **Design A only** ·
exit = SL or the strategy's original time exit (12/24/48 M30 bars = 6/12/24 h).
Design B and any signal carrying a TP are rejected permanently.

**Data source**: Yahoo Finance EURUSD M30 (source=`yfinance`) only; >= 500 completed candles and the latest completed candle are required before a scan.
**Execution**: the scheduled workflow is locked to the Headway demo account and enables the guarded signal-driven order path. Broker confirmation is required before a fill is recorded.


## Invariants and how each is enforced
1. **No Design B / TP ever opens**: `normalize()` rejects; `verify` fails any position or exit that violates it (exit reasons limited to `SL`, `TIME`).
2. **A signal opens at most once**: id seen in replayed state → `SIGNAL_DUPLICATE`; same id with different content → `SIGNAL_REJECTED id_conflict_different_content`.
3. **No position without a verified login of the configured account type**: `BRIDGE_ACCOUNT_MODE` (default `demo`) must equal the login's `account_mode`, with server/URL/host consistent with it (a demo login can never satisfy real mode and vice versa; real mode needs `BRIDGE_EXPECTED_SERVER`), ≤ 20 min old. Failure defers the signal (`SIGNAL_DEFERRED`, exit code 3) without burning its id.
8. **Single price source**: only Yahoo Finance candles are stored (other sources are purged). Every candle is OHLC-validated, UTC-aligned and closed. The scan requires 500 bars and a fresh latest candle.
9. **No invented fills**: a price-path hole inside an open trade keeps it open (`DATA_ERROR`); after 6 h past its deadline it is `POSITION_VOIDED` and excluded from P&L.
10. **Persistent state**: the local audit chain and candle CSV are restored/saved using GitHub Actions cache. The event log verifies sequence numbers, previous hashes and SHA-256 digests before state replay. GitHub cache is not a transactional database and can be evicted; a missing cache requires broker/audit reconciliation before order execution.
7. **Dummy orders cannot contaminate the strategy ledger**: `DUMMY_ORDER` events are capped (≤ 0.10 lots, EURUSD), never create positions, and `verify` fails any 'confirmed' claim without a margin increase.
4. **Crash safety**: event written + fsynced before state is trusted; torn last line is truncated and logged (`RECOVERY`); accepted-but-unopened signals are opened exactly once on the next run.
5. **Tamper evidence**: every event carries `prev`/`hash` (SHA-256). Editing, deleting or reordering breaks the chain.
6. **No silent data guesses**: a non-weekend hole in candles returns `DATA_GAP`; the position stays open and a single `DATA_ERROR` is logged.

## Reproduce / verify
```
cd bridge && python -m pytest -q                                   # 92 tests in this package
cd headway/headway-mt5-web-automation && npm test                  # policy / parsers / order-guard
python -m tsebridge.verify --audit-dir ../audit                     # chain + invariants
python -m tsebridge.verify --audit-dir ../audit --candles-csv X.csv # + independent re-simulation of every closed trade
```
The second form defeats a re-chained (forged) log: P&L is recomputed from raw candles.

## Fill model (lifecycle.py docstring is authoritative)
Opens at signal-bar close at the signal's entry; SL checked bar by bar (gap through SL fills at bar open, not at SL);
time exit at the open of the first bar at/after the deadline. Weekend gaps are tolerated; any other gap is `DATA_GAP`.

## Known limitations (state these to the auditor)
- **Provider data differs from the broker feed.** Yahoo Finance candles are not Headway's prices; the strategy calibration and the ledger's SL/TIME replay use them, while fills/stops happen at Headway. Slippage/spread are not modelled.
- **Ledger replay is a record, not broker truth.** Broker truth is the terminal confirmation. Exit price/P&L are not read from the broker.
- **Order-ticket / close selectors are unverified** against the live terminal; confirmation uses margin change and Trade-tab rows.
- **Real-account mode is untested**; automated execution is demo-only by guard.
- **GitHub scheduling is best-effort** and the Actions cache can be evicted (hard stop).
- SL distance comes from TSE (2 x ATR14 M30); the bridge only bounds it to 3-150 pips.
