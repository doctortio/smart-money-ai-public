-- Local pre-launch proposal. Apply only in isolated homologation after review.
-- Precondition: public.clientes.email has no duplicate values.
create unique index if not exists clientes_email_lower_uidx on public.clientes (lower(email));
create table if not exists public.sma_signup_rate_config (key text primary key, window_seconds integer not null check (window_seconds between 60 and 86400), max_email integer not null check (max_email between 1 and 20), max_ip integer not null check (max_ip between 1 and 100), updated_at timestamptz not null default now());
insert into public.sma_signup_rate_config(key,window_seconds,max_email,max_ip) values ('public_signup',3600,5,20) on conflict (key) do nothing;
create table if not exists public.sma_signup_attempts (bucket text not null check (bucket in ('email','ip')), bucket_key text not null, window_start timestamptz not null, attempts integer not null default 0 check (attempts >= 0), updated_at timestamptz not null default now(), primary key (bucket,bucket_key,window_start));
create index if not exists sma_signup_attempts_expiry_idx on public.sma_signup_attempts(window_start);

create or replace function public.sma_cleanup_signup_attempts(p_before timestamptz default now() - interval '2 days') returns integer language plpgsql security definer set search_path = public, pg_temp as $$ declare v_count integer; begin delete from public.sma_signup_attempts where window_start < p_before; get diagnostics v_count = row_count; return v_count; end; $$;

create or replace function public.sma_public_signup(p_nome text, p_email text, p_senha_hash text, p_ip_hash text default '') returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare v_email text := lower(trim(coalesce(p_email,''))); v_ip text := lower(trim(coalesce(p_ip_hash,''))); v_cfg record; v_window timestamptz; v_email_attempts integer; v_ip_attempts integer;
begin
  select * into v_cfg from public.sma_signup_rate_config where key='public_signup' for share;
  if length(trim(coalesce(p_nome,''))) < 1 or position('@' in v_email) < 2 or p_senha_hash !~ '^scrypt[$][0-9]+[$][0-9]+[$][0-9]+[$][0-9a-f]+[$][0-9a-f]+$' then return jsonb_build_object('ok',false,'code','REQUEST_ACCEPTED'); end if;
  v_window := to_timestamp(floor(extract(epoch from now()) / v_cfg.window_seconds) * v_cfg.window_seconds);
  insert into public.sma_signup_attempts(bucket,bucket_key,window_start,attempts) values ('email',encode(digest(v_email,'sha256'),'hex'),v_window,1) on conflict (bucket,bucket_key,window_start) do update set attempts=sma_signup_attempts.attempts+1,updated_at=now();
  insert into public.sma_signup_attempts(bucket,bucket_key,window_start,attempts) values ('ip',v_ip,v_window,1) on conflict (bucket,bucket_key,window_start) do update set attempts=sma_signup_attempts.attempts+1,updated_at=now();
  select attempts into v_email_attempts from public.sma_signup_attempts where bucket='email' and bucket_key=encode(digest(v_email,'sha256'),'hex') and window_start=v_window;
  select attempts into v_ip_attempts from public.sma_signup_attempts where bucket='ip' and bucket_key=v_ip and window_start=v_window;
  if v_email_attempts > v_cfg.max_email or v_ip_attempts > v_cfg.max_ip then return jsonb_build_object('ok',false,'code','REQUEST_ACCEPTED'); end if;
  insert into public.clientes(email,nome,senha_hash,vencimento,ativo,plano,tipo_usuario,criado_em,atualizado_em)
    values (v_email,trim(p_nome),p_senha_hash,current_date + 1,true,'gratis','CLIENTE',now(),now())
    on conflict do nothing;
  return jsonb_build_object('ok',true);
exception when others then return jsonb_build_object('ok',false,'code','REQUEST_ACCEPTED');
end; $$;

revoke all on table public.sma_signup_attempts from public,anon,authenticated;
revoke all on table public.sma_signup_rate_config from public,anon,authenticated;
revoke all on function public.sma_public_signup(text,text,text,text) from public,anon,authenticated;
revoke all on function public.sma_cleanup_signup_attempts(timestamptz) from public,anon,authenticated;
grant execute on function public.sma_public_signup(text,text,text,text) to service_role;
grant execute on function public.sma_cleanup_signup_attempts(timestamptz) to service_role;
