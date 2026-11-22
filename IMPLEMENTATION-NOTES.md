# Implementation notes — autonomous demo market cycle

## Preserved components

- Existing Headway Playwright login implementation: unchanged.
- Existing Headway quote/history extraction: retained as the preferred broker-side source.
- Existing TSE reference engine: 51 BEST strategies, Design A, unchanged.
- Existing Python bridge, signal intake, risk checks, event ledger, position monitor and audit chain: retained.
- Existing order UI implementation and ticket confirmation logic: retained.

## Dukascopy historical bootstrap

`scripts/bootstrap_dukascopy.py` uses the pinned open-source `dukascopy-node@1.51.1` CLI. It downloads EURUSD M30 bid candles, parses UTC epoch-millisecond timestamps, rejects incomplete/misaligned/invalid OHLC rows, and requires at least 500 completed bars.

No Dukascopy login or API key is required. The downloader uses Dukascopy's publicly available historical-data feed. It may still be subject to provider availability, rate limits or WAF challenges.

The existing `candles_m30` table already has a `source` column. Dukascopy rows are tagged `dukascopy_bootstrap` and inserted with conflict-ignore semantics. Existing Headway rows are never overwritten by the bootstrap. Headway-native ingestion continues to use normal upsert semantics and therefore retains priority for timestamps it can supply.

The report `work/dukascopy_bootstrap.json` includes before/after counts, the latest candle, quality/freshness status and diagnostic price differences on overlapping Headway timestamps.

## Permanent demo strategy execution

The scheduled `.github/workflows/market-cycle.yml` explicitly sets:

- `ACCOUNT_MODE=demo`
- `BRIDGE_ACCOUNT_MODE=demo`
- `BRIDGE_EXPECTED_SERVER=Headway-Demo`
- `ALLOW_ORDERS=yes`
- `BRIDGE_REAL_EXECUTION=true`
- `MAX_ORDERS_PER_CYCLE=1`

This is the normal strategy execution path on every scheduled cycle, not a one-time connectivity test. The separate one-time demo-trade workflow was removed. Existing order code must still receive a valid authenticated Headway session, current quotes, an accepted signal and a broker-confirmed result before the system records a fill.

This workflow is intentionally locked to demo. It does not enable real-account execution.

## Supabase API key compatibility

The Python REST client accepts either:

- `SUPABASE_SERVICE_ROLE_KEY` for the legacy JWT-format service-role key; or
- `SUPABASE_SECRET_KEY` for the new `sb_secret_...` key.

New `sb_secret_...` keys are sent in the `apikey` header only. Legacy JWT-format service-role keys continue to be sent in both `apikey` and `Authorization: Bearer` headers. Never use the Publishable key for this backend store.

## Verification performed for this package

- Python bridge tests: 60 passed.
- Python syntax compilation: passed.
- `scripts/run_cycle.sh` Bash syntax: passed.
- All GitHub Actions YAML files parsed successfully.
- Live Dukascopy download, Supabase credentials, Headway login, broker order placement and broker-side closure have not been tested from this offline packaging environment.
