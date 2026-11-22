# Runbook

## A. One-time setup

1. Apply `supabase/schema.sql` to the existing Supabase project (do not create a duplicate project).
2. Add GitHub Actions secrets:
   - `SUPABASE_URL` — Supabase Project URL.
   - `SUPABASE_SECRET_KEY` — the new `sb_secret_...` Secret key; alternatively use the legacy `SUPABASE_SERVICE_ROLE_KEY`.
   - `MT5_LOGIN` and `MT5_PASSWORD` — Headway demo credentials.
   - `WEB_TERMINAL_URL` — optional override; demo mode has a built-in default URL.
   - `PROXY_SERVER` only if your terminal setup requires it.
3. Never add the Supabase Publishable key as the backend service credential. Never commit secrets to GitHub.
4. Enable GitHub Actions and use the default branch.

## B. Autonomous demo operation

The scheduled `market-cycle.yml` runs every 30 minutes during the configured FX-week window. Each run:

1. Restores and verifies the Supabase audit chain.
2. Logs into the Headway demo terminal using the existing login implementation (unchanged).
3. Attempts to retrieve Headway-native EURUSD M30 history and current quotes.
4. Ensures at least 500 valid, completed M30 candles exist. If the store has fewer than 500, it downloads Dukascopy history and inserts only missing timestamps.
5. Records candle source provenance and an overlap-price diagnostic.
6. Requires the latest completed candle to pass freshness validation.
7. Runs the 51-strategy scanner when the data gate passes.
8. Submits eligible strategy-generated orders in the Headway demo account (`ALLOW_ORDERS=yes`), with a maximum of one new order per cycle.
9. Reconciles and manages existing positions, then synchronizes audit and ledger state to Supabase.

Dukascopy does not require an account or API key. The GitHub runner does need network access to npm and the Dukascopy public data service. A provider/WAF failure is fail-closed: insufficient or stale data means no scan/order.

## C. Safety and evidence

- The scheduled workflow is hard-locked to `ACCOUNT_MODE=demo` and `Headway-Demo`.
- Do not change it to a real account as part of this deployment.
- A green GitHub workflow is not proof of a broker fill. A successful execution requires the broker-confirmed position ticket and subsequent broker-confirmed closure evidence.
- Strategy sizing/risk configuration from the existing repository is preserved; this update does not retune strategy parameters.
- The maximum of one new strategy order per cycle is a concurrency/risk throttle, not a guarantee that an order will be generated.

## D. Expected artifacts

- `work/dukascopy_bootstrap.json` — historical count, source, quality and overlap diagnostic.
- `work/history_quality.json` — native history quality, when produced.
- `audit/events.jsonl` and `audit/summary.json` — local hash-chained audit.
- `headway/headway-mt5-web-automation/artifacts/strategy-order-*.log` — order attempt logs.
- `headway/headway-mt5-web-automation/artifacts/strategy_order_result.json` — broker order result when produced.
- `headway/headway-mt5-web-automation/artifacts/position_monitor.json` — position monitoring result when produced.

## E. Important limitations

GitHub scheduled workflows are best-effort and may start late or be skipped. The system is autonomous within successful workflow runs, but GitHub Actions is not a continuously running VPS. The native Headway binary candle decoder remains unproven; Dukascopy is the historical bootstrap fallback. A live demo run is still required to prove the user's credentials, data access, selectors, order fill and closure end to end.
