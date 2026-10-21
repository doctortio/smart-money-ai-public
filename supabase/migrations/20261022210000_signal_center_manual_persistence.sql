-- Prepared only. Do not apply remotely until identity and gateway reviews pass.
-- Durable manual preview and outbox state; only trusted service_role may access.
create table if not exists public.sma_signal_manual_previews (
  preview_id uuid primary key,
  admin_id bigint not null,
  admin_email text not null,
  assets text[] not null,
  timeframe text not null check (timeframe = '5m'),
  signals jsonb not null,
  signals_sha256 text not null check (signals_sha256 ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  confirmed_at timestamptz,
  status text not null default 'pending'
    check (status in ('pending','confirmed','expired','cancelled')),
  constraint sma_preview_two_assets check (
    cardinality(assets) = 2 and assets[1] <> assets[2]
  ),
  constraint sma_preview_confirmation_consistent check (
    (status = 'confirmed') = (confirmed_at is not null)
  )
);
create table if not exists public.sma_signal_manual_outbox (
  request_id uuid primary key,
  preview_id uuid not null unique references public.sma_signal_manual_previews(preview_id),
  execution_key text not null unique,
  admin_id bigint not null,
  channel text not null default '@smartmoneyaioficial'
    check (channel = '@smartmoneyaioficial'),
  status text not null default 'queued'
    check (status in ('queued','reserved','sending','sent','unknown','failed','cancelled')),
  telegram_message_id bigint,
  error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.sma_signal_manual_previews enable row level security;
alter table public.sma_signal_manual_outbox enable row level security;
revoke all on public.sma_signal_manual_previews, public.sma_signal_manual_outbox
  from public, anon, authenticated;
grant select, insert, update, delete on public.sma_signal_manual_previews,
  public.sma_signal_manual_outbox to service_role;
-- Never expose signals through anon/authenticated Data API.
-- Confirmation and dispatch must be implemented as separate atomic RPCs,
-- checking a trusted authenticated principal, expiration, emergency stop,
-- and idempotency. This migration alone DOES NOT authorize sending.
