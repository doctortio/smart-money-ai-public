-- Prepared only; do not apply without explicit approval.
create table if not exists public.sma_telegram_publications (
  execution_key text primary key,
  channel text not null,
  assets text[] not null,
  timeframe text not null,
  scheduled_at timestamptz not null,
  status text not null check (status in ('pending', 'reserved', 'sending', 'sent', 'failed', 'unknown')),
  telegram_message_id bigint,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  owner_id text not null
);

alter table public.sma_telegram_publications enable row level security;
revoke all on table public.sma_telegram_publications from public, anon, authenticated;

create or replace function public.sma_telegram_reserve_publication(
  p_execution_key text,
  p_channel text,
  p_assets text[],
  p_timeframe text,
  p_scheduled_at timestamptz,
  p_owner_id text
) returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  insert into public.sma_telegram_publications
    (execution_key, channel, assets, timeframe, scheduled_at, status, owner_id)
  values
    (p_execution_key, p_channel, p_assets, p_timeframe, p_scheduled_at, 'reserved', p_owner_id)
  on conflict (execution_key) do nothing;
  return found;
end;
$$;

revoke all on function public.sma_telegram_reserve_publication(text, text, text[], text, timestamptz, text)
  from public, anon, authenticated;
grant execute on function public.sma_telegram_reserve_publication(text, text, text[], text, timestamptz, text)
  to service_role;

create or replace function public.sma_telegram_mark_publication(
  p_execution_key text,
  p_status text,
  p_message_id bigint default null
) returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if p_status not in ('sending', 'sent', 'failed', 'unknown') then
    raise exception 'invalid publication status';
  end if;
  update public.sma_telegram_publications
     set status = p_status,
         telegram_message_id = case when p_status = 'sent' then p_message_id else telegram_message_id end,
         updated_at = now()
   where execution_key = p_execution_key
     and ((status = 'reserved' and p_status = 'sending')
       or (status = 'sending' and p_status in ('sent', 'failed', 'unknown')));
  return found;
end;
$$;

revoke all on function public.sma_telegram_mark_publication(text, text, bigint)
  from public, anon, authenticated;
grant execute on function public.sma_telegram_mark_publication(text, text, bigint)
  to service_role;

comment on table public.sma_telegram_publications is
  'Persistent idempotency ledger for the Telegram worker; service_role only.';
