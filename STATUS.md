# Implementation status — Dukascopy bootstrap + autonomous demo cycle

This status reflects the code in this ZIP. It distinguishes implemented code from behavior that still requires a successful GitHub/Headway demo run.

## Implemented in this update

| Item | Status |
|---|---|
| Dukascopy historical downloader | Added `scripts/bootstrap_dukascopy.py`, pinned to `dukascopy-node@1.51.1`; no Dukascopy account/API key required |
| Initial history requirement | Market cycle requires at least 500 valid, completed EURUSD M30 bars before scanning |
| Headway data priority | Existing Headway rows are retained; Dukascopy rows are insert-only for missing timestamps |
| Provenance | Every M30 row retains its `source`, including `headway_native` and `dukascopy_bootstrap` |
| Feed comparison | Overlapping Headway/Dukascopy close differences are recorded in the bootstrap report; no equivalence is assumed |
| Freshness gate | The latest completed M30 candle must pass the existing freshness rule before scan proceeds |
| Supabase new key format | `SUPABASE_SECRET_KEY` / `sb_secret_...` supported; sent in `apikey` only, not as a Bearer JWT |
| Legacy Supabase key | `SUPABASE_SERVICE_ROLE_KEY` remains supported |
| Permanent strategy execution | Scheduled `market-cycle.yml` sets `ALLOW_ORDERS=yes` and `BRIDGE_REAL_EXECUTION=true` |
| Demo-only guard | The scheduled market-cycle is hard-locked to `ACCOUNT_MODE=demo` and `Headway-Demo` |
| Per-cycle order cap | Maximum one new strategy order per scheduled cycle |
| Existing position management | Existing monitor/reconciliation stages remain in the cycle |
| Audit integrity | Remote Supabase audit chain is verified in full before sync |
| Login implementation | Existing Headway login code is unchanged in this update |

## Still requires live verification

| Item | Why it is not claimed complete |
|---|---|
| Dukascopy download from GitHub runner | Network/WAF availability can only be confirmed by running the workflow |
| Supabase connection with the user's actual project key | Credentials are not included in this ZIP and must remain GitHub secrets |
| Headway login and live quote | Requires the user's demo credentials and terminal access |
| Native Headway 500-bar decoder | Still not proven; Dukascopy supplies the historical bootstrap fallback |
| Headway order selectors and broker confirmation | Must be verified against the actual demo terminal; code cannot prove a broker fill offline |
| Automatic position close | Must be verified by a live demo run and broker-side evidence |
| End-to-end autonomous run | Requires a successful GitHub Actions run with the user's secrets |

## Operational behavior

1. Headway history is attempted first and its rows are ingested when present.
2. Dukascopy downloads historical EURUSD M30 only when fewer than 500 bars are stored.
3. Dukascopy inserts only missing timestamps and never overwrites an existing Headway row.
4. The system validates candle timestamps, OHLC values, completion status, minimum count and freshness.
5. If the data gate fails, the strategy scan and order stage are skipped.
6. If Headway authentication or current quotes fail, the bridge's existing authorization/quote gates prevent order execution.
7. If an eligible strategy signal survives all gates, the market-cycle attempts a demo order and records broker-confirmation evidence.

The workflow is configured for demo execution, but this ZIP does not claim that a trade has already been placed. That requires a live run and broker-confirmed ticket evidence.
