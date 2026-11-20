-- Supabase schema for the TSE -> Headway bridge. Paste into: Supabase dashboard -> SQL Editor -> Run. Idempotent.
-- Access model: RLS is ON with NO policies, so only the service_role key (used by CI) can read/write.
-- anon / authenticated keys get nothing. Never put the service_role key in browser or mobile code.

-- ---------------------------------------------------------------- price data (all from the Headway web terminal)
create table if not exists public.candles_m1 (
  symbol     text        not null,
  start_ts   timestamptz not null,
  open       numeric     not null, high numeric not null, low numeric not null, close numeric not null,
  ticks      integer     not null,            -- samples observed inside the minute
  source     text        not null default 'headway',
  run_id     text,
  primary key (symbol, start_ts)
);
create table if not exists public.candles_m30 (
  symbol          text        not null,
  start_ts        timestamptz not null,
  open            numeric     not null, high numeric not null, low numeric not null, close numeric not null,
  coverage        numeric     not null check (coverage >= 0 and coverage <= 1),   -- observed minutes / 30
  minutes_present integer     not null,
  samples         integer     not null,
  source          text        not null,       -- 'headway' | 'import:<name>' (history you imported yourself)
  primary key (symbol, start_ts)
);

-- ---------------------------------------------------------------- audit log (append-only, hash-chained)
create table if not exists public.bridge_events (
  seq        bigint      primary key,
  ts         timestamptz not null,
  type       text        not null,
  data       jsonb       not null,
  prev_hash  text        not null,
  hash       text        not null unique
);
create or replace function public.bridge_events_immutable() returns trigger language plpgsql as $$
begin raise exception 'bridge_events is append-only'; end $$;
drop trigger if exists bridge_events_no_change on public.bridge_events;
create trigger bridge_events_no_change before update or delete on public.bridge_events
  for each row execute function public.bridge_events_immutable();

-- ---------------------------------------------------------------- trading history (derived from the log; safe to rebuild)
create table if not exists public.signals (
  signal_id text primary key, status text not null, reject_reason text,
  pair text, side text, design text, entry numeric, sl numeric, horizon_bars integer, bar_time timestamptz,
  strategy_id text, strategy_rank integer, fingerprint text, raw_sha256 text, event_seq bigint
);
create table if not exists public.positions (
  signal_id text primary key, pair text not null, side text not null, lots numeric not null,
  entry numeric not null, sl numeric not null, horizon_bars integer not null, horizon_hours numeric not null,
  opened_at timestamptz not null, deadline timestamptz not null, strategy_id text, strategy_rank integer,
  status text not null check (status in ('OPEN','CLOSED','VOID')),
  exit_reason text check (exit_reason in ('SL','TIME','VOID')),      -- there is no take-profit exit
  exit_price numeric, exit_time timestamptz, gap_fill boolean, delay_min integer, pips numeric, pnl_usd numeric,
  min_coverage numeric,                                              -- lowest observed-coverage on the SL-checked path
  void_note text, updated_at timestamptz not null
);
create table if not exists public.quotes (
  event_seq bigint primary key, captured_at timestamptz not null, symbol text not null,
  bid numeric not null, ask numeric not null, spread_pips numeric, account_mode text
);
create table if not exists public.dummy_orders (
  id text primary key, event_seq bigint, account_mode text not null, symbol text not null, side text not null, lots numeric not null,
  executed boolean not null, outcome text not null, margin_before numeric, margin_after_open numeric, margin_after_close numeric, report jsonb
);
create table if not exists public.runs (        -- heartbeat: the watchdog alerts if this goes quiet while the FX market is open
  run_id text primary key, finished_at timestamptz not null, exit_code integer, account_mode text, git_sha text, events integer
);

-- ---------------------------------------------------------------- lock it down
alter table public.candles_m1    enable row level security;
alter table public.candles_m30   enable row level security;
alter table public.bridge_events enable row level security;
alter table public.signals       enable row level security;
alter table public.positions     enable row level security;
alter table public.quotes        enable row level security;
alter table public.dummy_orders  enable row level security;
alter table public.runs          enable row level security;
revoke all on all tables in schema public from anon, authenticated;

-- ---------------------------------------------------------------- convenience views (service_role / SQL editor only)
create or replace view public.v_trade_ledger with (security_invoker = true) as
  select p.signal_id, p.side, p.lots, p.entry, p.sl, p.opened_at, p.deadline, p.horizon_hours,
         p.status, p.exit_reason, p.exit_time, p.exit_price, p.pips, p.pnl_usd, p.min_coverage,
         s.strategy_id, s.strategy_rank
    from public.positions p left join public.signals s using (signal_id)
   order by p.opened_at desc;
create or replace view public.v_daily_pnl with (security_invoker = true) as
  select date_trunc('day', exit_time) as day, count(*) as trades, sum(pnl_usd) as pnl_usd,
         count(*) filter (where exit_reason = 'SL') as sl_exits, count(*) filter (where exit_reason = 'TIME') as time_exits
    from public.positions where status = 'CLOSED' group by 1 order by 1 desc;
revoke all on public.v_trade_ledger, public.v_daily_pnl from anon, authenticated;
