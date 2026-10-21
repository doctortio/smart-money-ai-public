-- Prepared for homologation only; do not apply automatically.
create table if not exists public.sma_signal_center_config (
  id boolean primary key default true check (id), auto_assets text[] not null, manual_assets text[] not null,
  timeframe text not null, auto_enabled boolean not null default false, emergency_stop boolean not null default true,
  slots text[] not null default array['09:00','14:00','20:00'], timezone text not null default 'America/Sao_Paulo',
  updated_at timestamptz not null default now(), updated_by text not null
);
create table if not exists public.sma_signal_center_audit (
  id bigint generated always as identity primary key, origin text not null check(origin in ('manual','automatic')),
  execution_key text not null, assets text[] not null, timeframe text not null, status text not null,
  detail text, created_at timestamptz not null default now()
);
alter table public.sma_signal_center_config enable row level security;
alter table public.sma_signal_center_audit enable row level security;
revoke all on public.sma_signal_center_config, public.sma_signal_center_audit from public, anon, authenticated;
grant all on public.sma_signal_center_config, public.sma_signal_center_audit to service_role;
