# Supabase setup (about 5 minutes)

1. **Create / update the tables:** Supabase dashboard → *SQL Editor* → paste `supabase/schema.sql` → *Run*. (Safe to re-run; adds `orders` table and extends `positions` for real tickets.)
2. **Add two GitHub secrets** (repo → Settings → Secrets and variables → Actions):
   - `SUPABASE_URL` — Project Settings → API → *Project URL*
   - `SUPABASE_SERVICE_ROLE_KEY` — Project Settings → API → *service_role* key
   The service-role key bypasses RLS: keep it a CI secret only; never paste it into a browser/mobile app or commit it.
3. Run **Actions → Watchdog → Run workflow** once. It prints `WATCHDOG {...}` if the connection works.

## What lands where
| Table | Content |
|---|---|
| `bridge_events` | Every decision, hash-chained and append-only (audit source of truth) |
| `signals` | Every signal seen: accepted or rejected, with the reason |
| `positions` | Real trades: entry, SL, deadline (6/12/24 h), ticket_id, exit reason `SL`/`TIME`/`MANUAL`/`VOID`/`FAILED`, exit time/price, pips, P&L |
| `orders` | Strategy order attempts (signal_id linked) |
| `candles_m30` | Native Headway M30 bars (`source=headway_native`) |
| `candles_m1` | Optional diagnostic samples only |
| `quotes`, `dummy_orders`, `runs` | Snapshots, connectivity-test orders, per-run heartbeat |
| views `v_trade_ledger`, `v_daily_pnl` | Convenience ledgers |

Your existing Supabase project is the store; just apply the updated schema.
