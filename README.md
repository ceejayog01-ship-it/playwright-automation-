> **Status after the requirements correction.** Active now: the **chart probe** (`npm run chartprobe` in `headway/…`) to find how
> to read native M30 candles from the web terminal. The scheduled workflows are **switched off** because they implement the
> 1-second-sampling / simulated-execution design you rejected; that code (sampler, M1 store, `lifecycle.py`) is still in the
> package only until the replacement is built. See `RUNBOOK.md` section 0.

# TSE → Headway trading automation (Headway-only data, Supabase store)

| Folder | What |
|---|---|
| `tse-reference/` | TSE signal engine (51 BEST strategies), unchanged; run with `KB_DESIGNS=A`, EURUSD |
| `bridge/` | Python: candle store (M1→M30 with coverage), signal intake, execution state, lifecycle simulation, hash-chained audit, Supabase sync |
| `headway/headway-mt5-web-automation/` | Your verified Playwright login + account policy, **sampler**, quotes, probe, tick→candle export, guarded dummy order |
| `supabase/schema.sql` | Tables for signals, positions (incl. time exits), candles, events, heartbeat |
| `scripts/run_cycle.sh` | One scheduled cycle |

**Read in this order:** `SUPABASE.md` → `RUNBOOK.md` → `AUDIT.md`.

Workflows: `market-cycle.yml` (**scheduled**, every 30 min across the FX week) · `watchdog.yml` (**scheduled**, alerts if the pipeline goes quiet) · `headway-terminal.yml` (manual tools).
Secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `WEB_TERMINAL_URL`, `MT5_LOGIN`, `MT5_PASSWORD`, optional `PROXY_SERVER`. Real account: `REAL_*` secrets + variables `ACCOUNT_MODE=real`, `EXPECTED_SERVER_LABEL`.
There is **no Twelve Data dependency**: every price comes from the Headway web terminal.
