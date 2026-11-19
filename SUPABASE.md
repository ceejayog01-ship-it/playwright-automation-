# Supabase setup (about 5 minutes)

1. **Create the tables:** Supabase dashboard → *SQL Editor* → paste `supabase/schema.sql` → *Run*. (Safe to re-run.)
2. **Add two GitHub secrets** (repo → Settings → Secrets and variables → Actions):
   - `SUPABASE_URL` — Project Settings → API → *Project URL*
   - `SUPABASE_SERVICE_ROLE_KEY` — Project Settings → API → *service_role* key
   The service-role key bypasses RLS: keep it a CI secret only; never paste it into a browser/mobile app or commit it.
3. Run **Actions → Watchdog → Run workflow** once. It prints `WATCHDOG {...}` if the connection works.

## What lands where
| Table | Content |
|---|---|
| `bridge_events` | Every decision, hash-chained and append-only (the audit source of truth; CI restores it from here at the start of each run) |
| `signals` | Every signal seen: accepted or rejected, with the reason |
| `positions` | Each simulated trade: entry, SL, **deadline (6/12/24 h)**, exit reason `SL`/`TIME`/`VOID`, exit time/price, pips, P&L, observed coverage |
| `candles_m1`, `candles_m30` | Prices sampled from the Headway terminal, with `coverage` per bar |
| `quotes`, `dummy_orders`, `runs` | Bid/ask snapshots, connectivity-test orders, per-run heartbeat |
| views `v_trade_ledger`, `v_daily_pnl` | Ready-made history and daily P&L |

## Queries
```sql
select * from v_trade_ledger limit 50;
select * from v_daily_pnl;
select exit_reason, count(*), sum(pnl_usd) from positions where status='CLOSED' group by 1;
select * from signals where status='rejected' order by event_seq desc limit 20;   -- why signals were refused
```
`positions` and `signals` are derived from `bridge_events`; if they are ever damaged, truncate them and the next sync rebuilds them.
The log itself cannot be updated or deleted (trigger), and a sync refuses to continue if the remote chain differs from the local one.

## Optional: skip the warm-up with history you already own
The strategy needs 120 M30 bars (~2.5 market days of sampling) before it may signal. To start sooner, load M30 you already have
(`timestamp,open,high,low,close`), tagged so audits can see the mix; Headway bars always win over imported ones:
```
cd bridge && SUPABASE_URL=… SUPABASE_SERVICE_ROLE_KEY=… python -m tsebridge.store import-m30 --csv my_eurusd_m30.csv --source import:my-dataset
```
Caveat: bars from another feed differ slightly from the Headway terminal's, so signals on that mixed window are not strictly comparable to pure-Headway ones.
