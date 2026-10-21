-- Local-only incremental migration for homologation.
-- Do not apply to production without isolated backup and explicit authorization.

create unique index if not exists clientes_email_lower_uidx
  on public.clientes (lower(email));

create table if not exists public.sma_signup_rate_config (
  key text primary key,
  window_seconds integer not null check (window_seconds between 60 and 86400),
  max_email integer not null check (max_email between 1 and 20),
  updated_at timestamptz not null default now()
);
do $config$
begin
  if exists (
    select 1 from information_schema.columns
     where table_schema = 'public'
       and table_name = 'sma_signup_rate_config'
       and column_name = 'max_ip'
  ) then
    execute $sql$
      insert into public.sma_signup_rate_config(key, window_seconds, max_email, max_ip)
      values ('public_signup', 3600, 5, 20)
      on conflict (key) do nothing
    $sql$;
  else
    insert into public.sma_signup_rate_config(key, window_seconds, max_email)
    values ('public_signup', 3600, 5)
    on conflict (key) do nothing;
  end if;
end;
$config$;

-- 20261015000000 may already own sma_signup_attempts with a bucket column.
-- Keep it untouched; this contract uses a dedicated email-only counter.
create table if not exists public.sma_signup_email_attempts (
  bucket_key text not null,
  window_start timestamptz not null,
  attempts integer not null default 0 check (attempts >= 0),
  updated_at timestamptz not null default now(),
  primary key (bucket_key, window_start)
);
create index if not exists sma_signup_email_attempts_expiry_idx
  on public.sma_signup_email_attempts(window_start);

create table if not exists public.sma_signup_email_verifications (
  email text primary key,
  token_hash text not null,
  nome text not null,
  senha_hash text not null,
  expires_at timestamptz not null,
  attempts integer not null default 0 check (attempts >= 0),
  confirmed_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists sma_signup_email_verifications_expiry_idx
  on public.sma_signup_email_verifications(expires_at);

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
  select * into v_cfg
    from public.sma_signup_rate_config
   where key = 'public_signup'
   for share;

  if v_cfg is null
     or length(trim(coalesce(p_nome, ''))) < 1
     or position('@' in v_email) < 2
     or p_token_hash is null
     or p_token_expires is null
     or p_token_expires <= now()
     or p_senha_hash !~ '^scrypt[$][0-9]+[$][0-9]+[$][0-9]+[$][0-9a-f]+[$][0-9a-f]+$'
  then
    return jsonb_build_object('ok', false, 'code', 'REQUEST_ACCEPTED');
  end if;

  v_window := to_timestamp(
    floor(extract(epoch from now()) / v_cfg.window_seconds) * v_cfg.window_seconds
  );
  insert into public.sma_signup_email_attempts(bucket_key, window_start, attempts)
  values (encode(digest(v_email, 'sha256'), 'hex'), v_window, 1)
  on conflict (bucket_key, window_start) do update
    set attempts = public.sma_signup_email_attempts.attempts + 1, updated_at = now();
  select attempts into v_attempts
    from public.sma_signup_email_attempts
   where bucket_key = encode(digest(v_email, 'sha256'), 'hex')
     and window_start = v_window;
  if v_attempts > v_cfg.max_email then
    return jsonb_build_object('ok', false, 'code', 'REQUEST_ACCEPTED');
  end if;

  if exists (select 1 from public.clientes where lower(email) = v_email) then
    return jsonb_build_object('ok', false, 'code', 'REQUEST_ACCEPTED');
  end if;

  insert into public.sma_signup_email_verifications(email, token_hash, nome, senha_hash, expires_at)
  values (v_email, p_token_hash, trim(p_nome), p_senha_hash, p_token_expires)
  on conflict (email) do update
    set token_hash = excluded.token_hash,
        nome = excluded.nome,
        senha_hash = excluded.senha_hash,
        expires_at = excluded.expires_at,
        attempts = 0,
        confirmed_at = null;
  return jsonb_build_object('ok', true);
exception when others then
  return jsonb_build_object('ok', false, 'code', 'REQUEST_ACCEPTED');
end;
$$;

create or replace function public.sma_confirm_signup_email(p_email text, p_token_hash text)
returns jsonb language plpgsql security definer
set search_path = public, pg_temp
as $$
declare
  v_email text := lower(trim(coalesce(p_email, '')));
  v_row public.sma_signup_email_verifications%rowtype;
begin
  select * into v_row
    from public.sma_signup_email_verifications
   where email = v_email
   for update;
  if not found or v_row.confirmed_at is not null
     or v_row.expires_at <= now() or v_row.attempts >= 5 then
    return jsonb_build_object('ok', false, 'code', 'REQUEST_ACCEPTED');
  end if;
  if v_row.token_hash <> coalesce(p_token_hash, '') then
    update public.sma_signup_email_verifications
       set attempts = attempts + 1
     where email = v_email;
    return jsonb_build_object('ok', false, 'code', 'REQUEST_ACCEPTED');
  end if;
  update public.sma_signup_email_verifications
     set confirmed_at = now()
   where email = v_email and confirmed_at is null;
  insert into public.clientes(email, nome, senha_hash, vencimento, ativo, plano, tipo_usuario, criado_em, atualizado_em)
  values (v_row.email, v_row.nome, v_row.senha_hash, current_date + 1, true, 'gratis', 'CLIENTE', now(), now())
  on conflict (email) do nothing;
  return jsonb_build_object('ok', true);
exception when others then
  return jsonb_build_object('ok', false, 'code', 'REQUEST_ACCEPTED');
end;
$$;

revoke all on table public.sma_signup_email_verifications from public, anon, authenticated;
do $migration$
begin
  if to_regprocedure('public.sma_public_signup(text,text,text,text)') is not null then
    execute 'revoke all on function public.sma_public_signup(text,text,text,text) from public, anon, authenticated, service_role';
  end if;
end;
$migration$;
revoke all on function public.sma_public_signup(text, text, text, text, timestamptz) from public, anon, authenticated;
revoke all on function public.sma_confirm_signup_email(text, text) from public, anon, authenticated;
grant execute on function public.sma_public_signup(text, text, text, text, timestamptz) to service_role;
grant execute on function public.sma_confirm_signup_email(text, text) to service_role;
