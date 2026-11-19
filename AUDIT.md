# Audit guide — TSE → Headway bridge (v1.2.0)

## Locked contract (`bridge/tsebridge/config.py`, constants, recorded in every RUN_STARTED event)
EURUSD · BUY/SELL · **0.50 lots** · stop-loss from TSE signal · **no take-profit** · **Design A only** ·
exit = SL or the strategy's original time exit (12/24/48 M30 bars = 6/12/24 h).
Design B and any signal carrying a TP are rejected permanently.

## Component → code → evidence
| Required component | Code | Evidence in the repo |
|---|---|---|
| TSE strategy engine (51 BEST) | `tse-reference/…/scan_once.py` (unchanged), run with `KB_DESIGNS=A`, `KB_PAIRS=EURUSD` | `tests/test_signal_parity.py` (TSE side) |
| Signal bridge | `signals.py` (strict validation) | `bridge/tests/test_signals.py` |
| Headway authentication | `headway/…/src/login.ts` + `policy.ts` → `artifacts/auth_result.json` (schema 2) → `auth.py` gate | `test/policy.test.ts`, `test_cycle.py::test_*auth*`, `test_*real*` |
| Headway quotes | `src/quotes.ts` → `quotes.json` → `QUOTE_SNAPSHOT` (bid, ask, spread; plausibility-checked) | `test/parsers.test.ts`, `test_cycle.py::test_quote_*` |
| Connectivity-test (dummy) order | `src/order.ts` guards + `DUMMY_ORDER` event | `test/order-guard.test.ts`, `test_cycle.py::test_*dummy*` |
| Candle store & data policy | `store.py` (M1→M30, coverage), `headway/…/src/ticks.ts` sampler | `test_store.py`, `test/parsers.test.ts` |
| Supabase persistence | `supabase_sync.py`, `supabase/schema.sql` | `test_supabase.py` (real HTTP against a fake PostgREST) |
| Execution-state management | `eventlog.py` (`State` replay), `cycle.py` | `test_cycle.py` (idempotency, id-conflict, crash recovery) |
| Lifecycle simulation | `lifecycle.py` (pure function) | `test_lifecycle.py` |
| Audit & persistence | `audit/events.jsonl` (hash-chained), `trades.csv`, `summary.json`; `verify.py` | `test_eventlog.py`, `test_cycle.py::test_verify_*` |

## Invariants and how each is enforced
1. **No Design B / TP ever opens**: `normalize()` rejects; `verify` fails any position or exit that violates it (exit reasons limited to `SL`, `TIME`).
2. **A signal opens at most once**: id seen in replayed state → `SIGNAL_DUPLICATE`; same id with different content → `SIGNAL_REJECTED id_conflict_different_content`.
3. **No position without a verified login of the configured account type**: `BRIDGE_ACCOUNT_MODE` (default `demo`) must equal the login's `account_mode`, with server/URL/host consistent with it (a demo login can never satisfy real mode and vice versa; real mode needs `BRIDGE_EXPECTED_SERVER`), ≤ 20 min old. Failure defers the signal (`SIGNAL_DEFERRED`, exit code 3) without burning its id.
8. **Single price source**: every candle is sampled from the Headway web terminal (no third-party feed in the code path). Each bar stores `coverage` (observed minutes / 30). The scan only uses bars ≥ 75 % covered and only runs if the newest closed bar is solid and ≥ 120 bars exist (fail closed). Every simulated exit records `min_coverage`, the lowest coverage on the stop-checked path.
9. **No invented fills**: a price-path hole inside an open trade keeps it open (`DATA_ERROR`); after 6 h past its deadline it is `POSITION_VOIDED` and excluded from P&L.
10. **Remote integrity**: Supabase `bridge_events` is append-only (trigger); each sync compares head hashes and refuses on mismatch; a daily job recomputes the whole remote chain.
7. **Dummy orders cannot contaminate the strategy ledger**: `DUMMY_ORDER` events are capped (≤ 0.10 lots, EURUSD), never create positions, and `verify` fails any 'confirmed' claim without a margin increase.
4. **Crash safety**: event written + fsynced before state is trusted; torn last line is truncated and logged (`RECOVERY`); accepted-but-unopened signals are opened exactly once on the next run.
5. **Tamper evidence**: every event carries `prev`/`hash` (SHA-256). Editing, deleting or reordering breaks the chain.
6. **No silent data guesses**: a non-weekend hole in candles returns `DATA_GAP`; the position stays open and a single `DATA_ERROR` is logged.

## Reproduce / verify
```
cd bridge && python -m pytest -q                                   # 38 tests
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
- **Strategy lifecycle is simulation.** No strategy signal is sent to the broker. The only order path is the separate guarded DUMMY order (≤ 0.10 lots), dry-run by default.
- **Order-ticket / close selectors are unverified** against the live terminal (use the dry run). Confirmation relies on the account Margin text rising/falling, not a ticket number.
- **Real-account mode is untested** (no real login available); its gates are unit-tested but the real terminal host/label must be pinned from a first real `login` run.
- **Headway-native candle history is unavailable until the probe is run.**
- **GitHub scheduling is best-effort.** Runs can start late or be skipped; late starts lower coverage (bars < 75 % are excluded) and the watchdog alerts on silence.
- SL distance is taken from TSE (2 × ATR14 M30); the bridge only bounds it to 3–150 pips as a data sanity guard. The legacy hourly stop-tightening is not applied.
