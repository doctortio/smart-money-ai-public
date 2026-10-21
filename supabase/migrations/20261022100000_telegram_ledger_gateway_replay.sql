-- Prepared only; do not apply without explicit authorization.
create table if not exists public.sma_telegram_hmac_nonces (
  nonce text primary key,
  execution_key text not null,
  operation text not null check (operation in ('reserve','mark')),
  channel text not null check (channel = '@smartmoneyaioficial'),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);
alter table public.sma_telegram_hmac_nonces enable row level security;
revoke all on public.sma_telegram_hmac_nonces from anon, authenticated, public;
grant all on public.sma_telegram_hmac_nonces to service_role;

create or replace function public.sma_telegram_claim_nonce(
  p_nonce text, p_execution_key text, p_operation text, p_channel text, p_expires_at timestamptz
) returns boolean language plpgsql security definer set search_path = public, pg_temp as $$
begin
  insert into public.sma_telegram_hmac_nonces(nonce, execution_key, operation, channel, expires_at)
  values (p_nonce, p_execution_key, p_operation, p_channel, p_expires_at)
  on conflict (nonce) do nothing;
  return found;
end; $$;
revoke all on function public.sma_telegram_claim_nonce(text,text,text,text,timestamptz) from public, anon, authenticated;
grant execute on function public.sma_telegram_claim_nonce(text,text,text,text,timestamptz) to service_role;

create or replace function public.sma_telegram_owner_matches(p_execution_key text, p_owner_id text)
returns boolean language sql security definer set search_path = public, pg_temp as $$
  select exists (select 1 from public.sma_telegram_publications where execution_key = p_execution_key and owner_id = p_owner_id);
$$;
revoke all on function public.sma_telegram_owner_matches(text,text) from public, anon, authenticated;
grant execute on function public.sma_telegram_owner_matches(text,text) to service_role;
