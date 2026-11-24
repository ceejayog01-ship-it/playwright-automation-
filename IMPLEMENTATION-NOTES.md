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

The local `data/candles_m30.csv` store includes a `source` column. Dukascopy rows are tagged `dukascopy_bootstrap` and inserted with ignore-existing semantics. Existing Headway rows are never overwritten by the bootstrap. Headway-native ingestion continues to use normal upsert semantics and therefore retains priority for timestamps it can supply.

The report `work/dukascopy_bootstrap.json` includes before/after counts, the latest candle, quality/freshness status and diagnostic price differences on overlapping Headway timestamps.

## Permanent demo strategy execution

The scheduled `.github/workflows/market-cycle.yml` explicitly sets:

- `ACCOUNT_MODE=demo`
- `BRIDGE_ACCOUNT_MODE=demo`
- `BRIDGE_EXPECTED_SERVER=Headway-Demo`
- `ALLOW_ORDERS=yes`
- `BRIDGE_REAL_EXECUTION=true`
- `MAX_ORDERS_PER_CYCLE=1`

This is the normal strategy execution path on every scheduled cycle, not a one-time connectivity test. The separate manual `demo-trade-test.yml` remains available as an optional diagnostic, but it is not used as a substitute for signal-driven execution. Existing order code must still receive a valid authenticated Headway session, current quotes, an accepted signal and a broker-confirmed result before the system records a fill.

This workflow is intentionally locked to demo. It does not enable real-account execution.

## State persistence without Supabase

The market cycle is now local-only. `data/candles_m30.csv` and `audit/events.jsonl` are restored before the cycle and saved after it using GitHub Actions cache. The local event ledger remains hash-chained and is verified when loaded. The workflow's concurrency group serializes scheduled runs to avoid overlapping cache writes. The workflow fails closed when the audit ledger is missing.

This removes Supabase credentials, REST requests, remote audit restore and remote sync from the runtime path. GitHub Actions cache is not a transactional database and can be evicted. The first migration requires explicit manual confirmation after checking the broker account for unmanaged positions; a later missing cache must not be treated as a fresh start.

## Verification performed for this package

- Python bridge tests: 50 passed in the packaging environment.
- Python syntax compilation: passed.
- `scripts/run_cycle.sh` Bash syntax: passed.
- All GitHub Actions YAML files parsed successfully.
- Live Dukascopy download, Headway login, broker order placement and broker-side closure have not been tested from this offline packaging environment.
