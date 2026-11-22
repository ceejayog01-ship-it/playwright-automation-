# Update notes — 2026-11-22

Base: user-provided `playwright-automation--main (1).zip` (archive commit marker `5e9c29c2acc12c72b96df95c576d0db071f696a8`).

## Changes

- Added `scripts/bootstrap_dukascopy.py`, using `dukascopy-node@1.51.1` via pinned `npx` (no Dukascopy account or API key).
- Added a 500-completed-M30-bar bootstrap gate, UTC timestamp/OHLC validation, forming-candle exclusion, freshness validation, source tagging, and an overlap-price diagnostic.
- Added insert-only conflict handling for Dukascopy so existing Headway-native rows are not overwritten.
- Integrated bootstrap into `scripts/run_cycle.sh` before the strategy scan; if data is insufficient/stale, the scan/order stage is blocked.
- Updated `market-cycle.yml` to accept the new Supabase `SUPABASE_SECRET_KEY` secret alias, hard-lock the workflow to Headway demo, and permanently enable strategy-generated demo orders with a one-new-order-per-cycle cap. The demo terminal URL remains optional because the existing automation has a demo default.
- Removed the separate one-time `demo-trade-test.yml` workflow; strategy execution is part of the scheduled market cycle.
- Updated the Supabase REST client: new `sb_secret_...` keys are sent in `apikey` only; legacy JWT service-role keys keep the existing Bearer header behavior.
- Fixed the M30 Supabase store read path and added full remote audit-chain verification before sync.
- Updated docs and replaced stale store tests that did not match the current repository implementation.
- Headway login source files were not modified. Strategy parameters/sizing were not retuned.

## Validation performed

- `bridge` pytest suite: **60 passed**.
- Python syntax compilation: passed.
- `scripts/run_cycle.sh` Bash syntax: passed.
- All GitHub Actions YAML files parsed successfully.
- No secrets or credentials were added to the package.

## Not yet verified

- Actual Dukascopy network download from GitHub Actions (requires a workflow run).
- User's Supabase project connectivity using their private secret.
- Headway live quote availability and actual broker demo order/close confirmation.
- TypeScript `npm test` was not run in this packaging environment because npm dependency installation timed out; the TypeScript source was not changed.

A successful workflow is not itself proof of a broker fill. Require the broker's position ticket and closure confirmation in artifacts/terminal history.
