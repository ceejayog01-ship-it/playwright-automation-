# Runbook

## A. One-time setup
1. `SUPABASE.md` (schema + 2 secrets). 2. Add Headway secrets (demo first). 3. Run **Actions → Headway terminal tools** in this order on **demo**:

| # | Mode | Proves | Pass looks like |
|---|---|---|---|
| 1 | `login` | The verified login survived the account-mode change | `[login] VERIFIED …` |
| 2 | `quotes` | Market Watch is readable as text | `quotes.json` status `ok` |
| 3 | `sample` (seconds=300) | The 1 Hz sampler works | `sample_status.json` with ~290 samples, `headway_bars_EURUSD_M1.csv` |
| 4 | `probe` | Whether the terminal exposes native candle history | `probe/probe-report.json` (send it to me) |
| 5 | `order`, execute **off** | Ticket opens, volume 0.01, right symbol; nothing sent | `dry_run_ok` + `order-ticket-ready-*.png` |
| 6 | `order`, execute **on**, demo | One 0.01 EURUSD trade opens and closes | `executed_confirmed`, `closed_confirmed: true` |

If step 5 fails, fix the `order` selectors in `config/selectors.ts` from the screenshot (they are unverified).

## B. Going autonomous
Enable Actions and merge to the default branch. `market-cycle.yml` then runs itself at :02 and :32 from Sun 21:00 UTC to Fri 22:00 UTC. Each run: samples the terminal until the next half-hour boundary → scans the 51 strategies on Headway-derived M30 → simulates SL / 6-12-24 h time exits → writes everything to Supabase. `watchdog.yml` emails you (failed run) if no heartbeat lands for 50 min while the market is open.

**Warm-up:** the strategy needs 120 M30 bars (≈2.5 market days of sampling) before it may signal. Until then each run logs `scan skipped: insufficient_history` (a warning, not a failure). To start sooner, import M30 history you own (see `SUPABASE.md`).

## C. Real account (only after A passes on demo)
1. Secrets `REAL_WEB_TERMINAL_URL`, `REAL_MT5_LOGIN`, `REAL_MT5_PASSWORD`; variable `EXPECTED_SERVER_LABEL` (exact server name on the real login form).
2. GitHub Environment **`real-account`** with *Required reviewers* (approval gate for manual real runs).
3. Run `login` with `account_mode=real`. The login aborts if URL path, visible server label and declared mode disagree.
4. A real dummy order also needs `execute` + `real_order_confirm = REAL-<last 4 digits of the login>`. Cap 0.10 lots, EURUSD only.
5. Scheduled cycle on real: set variable `ACCOUNT_MODE=real`. Strategy positions remain **simulated**.

## D. When something is off
| Symptom | Meaning / action |
|---|---|
| `scan skipped: latest_bar_missing_or_degraded` | The last half-hour was observed < 75 % (late/failed run). No signal on bad data. Normal occasionally |
| Exit code 3 | Signals were blocked by the login gate (auth failed / wrong account type) |
| Exit code 4 / `DATA_ERROR` | Candle store unreachable, or a price-path hole inside an open trade |
| `POSITION_VOIDED` | A trade's price path stayed unobservable 6 h past its deadline; excluded from P&L, never guessed |
| Exit code 5 / dummy order `executed_unconfirmed` or `…close_failed` | **Open the terminal and check/close the position by hand** |
| Exit 6 (sync) | Remote chain differs from local; stop and investigate, nothing was overwritten |
| Sync step failed | That run's events are in the `cycle-<id>` artifact; re-run `supabase_sync sync` with them |
