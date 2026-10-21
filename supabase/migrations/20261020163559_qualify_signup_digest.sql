-- Qualify pgcrypto calls while preserving the restricted SECURITY DEFINER search_path.
create or replace function public.sma_public_signup(
  p_nome text,
  p_email text,
  p_senha_hash text,
  p_token_hash text,
  p_token_expires timestamptz
) returns jsonb
language plpgsql security definer
set search_path = public, pg_temp
as $$
declare
  v_email text := lower(trim(coalesce(p_email, '')));
  v_cfg record;
  v_window timestamptz;
  v_attempts integer;
begin
  select * into v_cfg from public.sma_signup_rate_config where key = 'public_signup' for share;
  if v_cfg is null or length(trim(coalesce(p_nome, ''))) < 1 or position('@' in v_email) < 2
     or p_token_hash is null or p_token_expires is null or p_token_expires <= now()
     or p_senha_hash !~ '^scrypt[$][0-9]+[$][0-9]+[$][0-9]+[$][0-9a-f]+[$][0-9a-f]+$' then
    return jsonb_build_object('ok', false, 'code', 'REQUEST_ACCEPTED');
  end if;
  v_window := to_timestamp(floor(extract(epoch from now()) / v_cfg.window_seconds) * v_cfg.window_seconds);
  insert into public.sma_signup_email_attempts(bucket_key, window_start, attempts)
  values (encode(extensions.digest(v_email, 'sha256'), 'hex'), v_window, 1)
  on conflict (bucket_key, window_start) do update
    set attempts = public.sma_signup_email_attempts.attempts + 1, updated_at = now();
  select attempts into v_attempts from public.sma_signup_email_attempts
   where bucket_key = encode(extensions.digest(v_email, 'sha256'), 'hex') and window_start = v_window;
  if v_attempts > v_cfg.max_email then return jsonb_build_object('ok', false, 'code', 'REQUEST_ACCEPTED'); end if;
  if exists (select 1 from public.clientes where lower(email) = v_email) then
    return jsonb_build_object('ok', false, 'code', 'REQUEST_ACCEPTED');
  end if;
  insert into public.sma_signup_email_verifications(email, token_hash, nome, senha_hash, expires_at)
  values (v_email, p_token_hash, trim(p_nome), p_senha_hash, p_token_expires)
  on conflict (email) do update set token_hash = excluded.token_hash, nome = excluded.nome,
    senha_hash = excluded.senha_hash, expires_at = excluded.expires_at, attempts = 0, confirmed_at = null;
  return jsonb_build_object('ok', true);
exception when others then return jsonb_build_object('ok', false, 'code', 'REQUEST_ACCEPTED');
end;
$$;

revoke all on function public.sma_public_signup(text, text, text, text, timestamptz) from public, anon, authenticated;
grant execute on function public.sma_public_signup(text, text, text, text, timestamptz) to service_role;

