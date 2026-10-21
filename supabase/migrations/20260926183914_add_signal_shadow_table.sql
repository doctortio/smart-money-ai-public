create table if not exists public.sma_signal_shadow (
  id bigserial primary key,
  official_signal_id text not null,
  user_id text null,
  created_at timestamptz not null default now(),
  decision_timestamp timestamptz not null,
  market_data_timestamp timestamptz null,
  market text not null,
  symbol text not null,
  timeframe text not null,
  v1_engine_version text not null default 'V1_OFFICIAL',
  v1_direction text null,
  v1_score numeric null,
  v1_risk text null,
  v2_engine_version text not null,
  v2_config_hash text not null,
  v2_direction text null,
  v2_long_score numeric null,
  v2_short_score numeric null,
  v2_risk text null,
  v2_no_trade boolean not null default false,
  v2_no_trade_reason text null,
  v2_regime text null,
  v1_latency_ms numeric null,
  v2_latency_ms numeric null,
  shadow_status text not null,
  shadow_error_code text null,
  result_direct text null,
  result_p1 text null,
  result_p2 text null,
  result_p5 text null,
  evaluated_at timestamptz null,
  metadata jsonb not null default '{}'::jsonb,
  constraint sma_signal_shadow_status_chk check (shadow_status in ('shadow_success','shadow_no_trade','shadow_error','shadow_unsupported')),
  constraint sma_signal_shadow_unique unique (official_signal_id, v2_engine_version, v2_config_hash)
);

alter table public.sma_signal_shadow enable row level security;

revoke all on table public.sma_signal_shadow from anon;
revoke all on table public.sma_signal_shadow from authenticated;
grant select, insert, update on table public.sma_signal_shadow to service_role;
grant usage, select on sequence public.sma_signal_shadow_id_seq to service_role;

create index if not exists idx_sma_signal_shadow_created_at on public.sma_signal_shadow(created_at desc);
create index if not exists idx_sma_signal_shadow_official_signal_id on public.sma_signal_shadow(official_signal_id);
create index if not exists idx_sma_signal_shadow_status on public.sma_signal_shadow(shadow_status);
