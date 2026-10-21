-- R04A preparation only. Do not execute as part of R04A.
-- Extends the existing service-role-only Shadow table; no commercial tables touched.

alter table public.sma_signal_shadow
  add column if not exists event_id uuid null,
  add column if not exists request_id uuid null,
  add column if not exists buy_primary_direction text null,
  add column if not exists buy_primary_probability double precision null,
  add column if not exists buy_challenger_direction text null,
  add column if not exists buy_challenger_probability double precision null,
  add column if not exists sell_research_direction text null,
  add column if not exists sell_research_probability double precision null,
  add column if not exists confidence_raw_shadow double precision null,
  add column if not exists confidence_calibrated_shadow double precision null,
  add column if not exists feature_hash text null,
  add column if not exists entrada_iso timestamptz null,
  add column if not exists p1_iso timestamptz null,
  add column if not exists p2_iso timestamptz null,
  add column if not exists p3_iso timestamptz null,
  add column if not exists resultado_official text null,
  add column if not exists resultado_shadow text null,
  add column if not exists error_message text null;

create unique index if not exists sma_signal_shadow_official_model_unique
  on public.sma_signal_shadow (official_signal_id, v2_engine_version);

create index if not exists idx_sma_signal_shadow_event_id
  on public.sma_signal_shadow (event_id)
  where event_id is not null;

create index if not exists idx_sma_signal_shadow_request_id
  on public.sma_signal_shadow (request_id)
  where request_id is not null;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'sma_signal_shadow_quant_directions_chk') then
    alter table public.sma_signal_shadow add constraint sma_signal_shadow_quant_directions_chk check (
      (buy_primary_direction is null or buy_primary_direction in ('COMPRA','VENDA')) and
      (buy_challenger_direction is null or buy_challenger_direction in ('COMPRA','VENDA')) and
      (sell_research_direction is null or sell_research_direction in ('COMPRA','VENDA')) and
      (v2_direction is null or v2_direction in ('COMPRA','VENDA'))
    ) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conname = 'sma_signal_shadow_quant_risk_chk') then
    alter table public.sma_signal_shadow add constraint sma_signal_shadow_quant_risk_chk
      check (v2_risk is null or v2_risk in ('BAIXO','MEDIO','ALTO')) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conname = 'sma_signal_shadow_quant_results_chk') then
    alter table public.sma_signal_shadow add constraint sma_signal_shadow_quant_results_chk check (
      (resultado_official is null or resultado_official in ('P1','P2','P3','LOSS','PENDENTE','SEM DADOS')) and
      (resultado_shadow is null or resultado_shadow in ('P1','P2','P3','LOSS','PENDENTE','SEM DADOS'))
    ) not valid;
  end if;
end $$;

alter table public.sma_signal_shadow enable row level security;
revoke all on table public.sma_signal_shadow from anon, authenticated;
