# Update notes — 2026-11-25

Base: playwright-automation--main (login.ts untouched; SHA-256 655a33c13aab419df595d2d2db41ad02aaa9a0d6ff94d0b6a459a6e361b0e423 in the supplied archive).

- Yahoo Finance through `yfinance` is the sole market-price provider; Twelve Data and Dukascopy are removed.
- Headway remains the broker for login, account access, order execution, position management and broker confirmations.
- No Yahoo API key or Twelve Data secret is required; the workflow installs `yfinance` from requirements.
- Headway no longer collects candles or prices; modes `quotes/history/sample/ticks/probe/chartprobe` removed.
- Hard cap of one new order per cycle; broker-confirmed reporting; Telegram execution notices.
- Removed workflows: `apply-supabase-removal-patch.yml` (its allow-list would re-add removed files), `chart-probe.yml`, `demo-trade-test.yml` (both captured terminal candle traffic).

Validation: 92 Python tests, TypeScript compile + 6 test files, YAML parse, bash -n. Not run live.
