# Supabase setup (about 5 minutes)

1. **Update the existing project tables:** Supabase dashboard → *SQL Editor* → paste `supabase/schema.sql` → *Run*. Do not create a second project. The schema is safe to re-run.
2. **Add GitHub Actions secrets** (repo → Settings → Secrets and variables → Actions):
   - `SUPABASE_URL` — Dashboard → *Connect* → *Project URL*.
   - `SUPABASE_SECRET_KEY` — the new `sb_secret_...` key from Project Settings → API Keys. The workflow and REST client support this key format.
   - Alternatively, use legacy `SUPABASE_SERVICE_ROLE_KEY` if that key is still available.
   Secret keys bypass RLS and must stay in GitHub Actions secrets only. Never use the Publishable key for this backend store or commit a secret.
3. Run **Actions → Watchdog → Run workflow** once. It prints `WATCHDOG {...}` if the connection works.

## What lands where
| Table | Content |
|---|---|
| `bridge_events` | Every decision, hash-chained and append-only (audit source of truth) |
| `signals` | Every signal seen: accepted or rejected, with the reason |
| `positions` | Real trades: entry, SL, deadline (6/12/24 h), ticket_id, exit reason `SL`/`TIME`/`MANUAL`/`VOID`/`FAILED`, exit time/price, pips, P&L |
| `orders` | Strategy order attempts (signal_id linked) |
| `candles_m30` | Headway-native bars plus insert-only Dukascopy bootstrap bars (`source=dukascopy_bootstrap`) |
| `candles_m1` | Optional diagnostic samples only |
| `quotes`, `dummy_orders`, `runs` | Snapshots, connectivity-test orders, per-run heartbeat |
| views `v_trade_ledger`, `v_daily_pnl` | Convenience ledgers |

Your existing Supabase project is the store; just apply the updated schema.


## GitHub Actions credentials (current Supabase dashboard)

Set repository Actions secrets:

- `SUPABASE_URL`: Project URL from Supabase Dashboard → Connect → Project URL.
- `SUPABASE_SECRET_KEY`: the new Supabase **Secret key** (`sb_secret_...`). The workflow accepts this name.
- Alternatively, legacy `SUPABASE_SERVICE_ROLE_KEY` remains supported. Never use the Publishable key.

The Secret key is server-side only and must never be committed to the repository or placed in browser code.
