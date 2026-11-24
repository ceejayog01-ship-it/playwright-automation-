# Audit guide — TSE → Headway bridge (integrated)

> **Current honest status:** Account-identity and chart-selection gates are implemented and fail-closed.
> The Headway native binary-history decoder is **not** proven. `history.ts` is best-effort only. The scheduled cycle can use the pinned Dukascopy historical bootstrap to supply at least 500 completed M30 bars when the local store is short; freshness and OHLC gates still apply.

---

## Locked contract (`bridge/tsebridge/config.py`, constants, recorded in every RUN_STARTED event)
EURUSD · BUY/SELL · **0.50 lots** · stop-loss from TSE signal · **no take-profit** · **Design A only** ·
exit = SL or the strategy's original time exit (12/24/48 M30 bars = 6/12/24 h).
Design B and any signal carrying a TP are rejected permanently.

**Data source**: Headway-native M30 history is preferred; Dukascopy M30 bid candles are an insert-only historical fallback (source=`dukascopy_bootstrap`). No 1-second samples are used to manufacture strategy candles.
**Execution**: the scheduled workflow is locked to the Headway demo account and enables the guarded signal-driven order path. Broker confirmation is required before a fill is recorded.


## Invariants and how each is enforced
1. **No Design B / TP ever opens**: `normalize()` rejects; `verify` fails any position or exit that violates it (exit reasons limited to `SL`, `TIME`).
2. **A signal opens at most once**: id seen in replayed state → `SIGNAL_DUPLICATE`; same id with different content → `SIGNAL_REJECTED id_conflict_different_content`.
3. **No position without a verified login of the configured account type**: `BRIDGE_ACCOUNT_MODE` (default `demo`) must equal the login's `account_mode`, with server/URL/host consistent with it (a demo login can never satisfy real mode and vice versa; real mode needs `BRIDGE_EXPECTED_SERVER`), ≤ 20 min old. Failure defers the signal (`SIGNAL_DEFERRED`, exit code 3) without burning its id.
8. **Controlled price sources**: Headway-native candles have priority; Dukascopy is used only to fill missing historical timestamps and never overwrites an existing Headway row. Every candle is OHLC-validated, UTC-aligned and closed. The scan requires at least 500 bars and a fresh latest completed candle.
9. **No invented fills**: a price-path hole inside an open trade keeps it open (`DATA_ERROR`); after 6 h past its deadline it is `POSITION_VOIDED` and excluded from P&L.
10. **Persistent state**: the local audit chain and candle CSV are restored/saved using GitHub Actions cache. The event log verifies sequence numbers, previous hashes and SHA-256 digests before state replay. GitHub cache is not a transactional database and can be evicted; a missing cache requires broker/audit reconciliation before order execution.
7. **Dummy orders cannot contaminate the strategy ledger**: `DUMMY_ORDER` events are capped (≤ 0.10 lots, EURUSD), never create positions, and `verify` fails any 'confirmed' claim without a margin increase.
4. **Crash safety**: event written + fsynced before state is trusted; torn last line is truncated and logged (`RECOVERY`); accepted-but-unopened signals are opened exactly once on the next run.
5. **Tamper evidence**: every event carries `prev`/`hash` (SHA-256). Editing, deleting or reordering breaks the chain.
6. **No silent data guesses**: a non-weekend hole in candles returns `DATA_GAP`; the position stays open and a single `DATA_ERROR` is logged.

## Reproduce / verify
```
cd bridge && python -m pytest -q                                   # 50 tests in this package
cd headway/headway-mt5-web-automation && npm test                  # policy / parsers / order-guard
python -m tsebridge.verify --audit-dir ../audit                     # chain + invariants
python -m tsebridge.verify --audit-dir ../audit --candles-csv X.csv # + independent re-simulation of every closed trade
```
The second form defeats a re-chained (forged) log: P&L is recomputed from raw candles.

## Fill model (lifecycle.py docstring is authoritative)
Opens at signal-bar close at the signal's entry; SL checked bar by bar (gap through SL fills at bar open, not at SL);
time exit at the open of the first bar at/after the deadline. Weekend gaps are tolerated; any other gap is `DATA_GAP`.

## Known limitations (state these to the auditor)
- **Sampled, not tick-complete, prices.** A 1 Hz poll of the bid can miss intra-second spikes, and the CI schedule leaves ~10 % of each half-hour unobserved (login, teardown). Stop touches in unobserved minutes are unknowable; `coverage` / `min_coverage` quantify it per bar and per exit. A persistent runner (VPS) would raise coverage to ~100 %.
- **Bid-only candles.** Bars are built from the bid; BUY stops are right, SELL stops (triggered on ask) are approximated. Spread/slippage/commission are not modelled.
- **Calibration drift.** The strategy thresholds were calibrated on another feed's candles; Headway-derived bars (broker bid, different session edges) can shift signals. Imported history is tagged with its `source`.
- **Broker execution remains unverified until a live demo run.** The scheduled workflow enables signal-driven demo orders, but a successful GitHub run alone does not prove a broker fill; inspect the broker-confirmed ticket and position.
- **Order-ticket / close selectors are unverified** against the live terminal (use the dry run). Confirmation relies on the account Margin text rising/falling, not a ticket number.
- **Real-account mode is untested** (no real login available); its gates are unit-tested but the real terminal host/label must be pinned from a first real `login` run.
- **Headway-native binary candle history remains unproven.** Dukascopy provides the historical fallback; live Headway quotes and authentication remain required for execution.
- **GitHub scheduling is best-effort.** Runs can start late or be skipped. The Supabase-only external watchdog has been removed.
- SL distance is taken from TSE (2 × ATR14 M30); the bridge only bounds it to 3–150 pips as a data sanity guard. The legacy hourly stop-tightening is not applied.
