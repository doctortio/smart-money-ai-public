create or replace function public.sma_carteira_resumo(p_email text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_email text := lower(trim(coalesce(p_email,'')));
  v_row public.sma_carteira_sinais%rowtype;
  v_data date := ((now() at time zone 'America/Sao_Paulo')::date);
  v_gratis_usado boolean;
begin
  if v_email = '' then raise exception 'email obrigatorio'; end if;
  insert into public.sma_carteira_sinais(usuario_email) values (v_email)
  on conflict (usuario_email) do nothing;
  select * into v_row from public.sma_carteira_sinais where usuario_email = v_email;
  select exists (
    select 1 from public.sma_carteira_consumos
    where usuario_email = v_email
      and data_sp = v_data
      and tipo_consumo = 'GRATIS'
      and status in ('RESERVADO','CONFIRMADO')
  ) into v_gratis_usado;
  return jsonb_build_object(
    'email', v_row.usuario_email,
    'gratuito_disponivel', case when v_gratis_usado then 0 else 1 end,
    'creditos_comprados', greatest(v_row.creditos_comprados, 0),
    'ilimitado_ativo', coalesce(v_row.ilimitado_ativo,false) and (v_row.ilimitado_fim is null or v_row.ilimitado_fim > now()),
    'ilimitado_fim', v_row.ilimitado_fim,
    'total_consumido', greatest(v_row.total_consumido, 0)
  );
end;
$$;

create or replace function public.sma_carteira_reservar_sinal(p_email text, p_request_id uuid, p_origem text default 'streamlit')
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_email text := lower(trim(coalesce(p_email,'')));
  v_tipo text;
  v_data date := ((now() at time zone 'America/Sao_Paulo')::date);
  v_row public.sma_carteira_sinais%rowtype;
  v_gratis_usado boolean;
begin
  if v_email = '' or p_request_id is null then raise exception 'parametros obrigatorios'; end if;
  insert into public.sma_carteira_sinais(usuario_email) values (v_email)
  on conflict (usuario_email) do nothing;
  select * into v_row from public.sma_carteira_sinais where usuario_email = v_email for update;
  if exists (select 1 from public.sma_carteira_consumos where request_id = p_request_id) then
    return jsonb_build_object('ok', true, 'idempotente', true, 'request_id', p_request_id);
  end if;
  select exists (
    select 1 from public.sma_carteira_consumos
    where usuario_email = v_email
      and data_sp = v_data
      and tipo_consumo = 'GRATIS'
      and status in ('RESERVADO','CONFIRMADO')
  ) into v_gratis_usado;
  if coalesce(v_row.ilimitado_ativo,false) and (v_row.ilimitado_fim is null or v_row.ilimitado_fim > now()) then
    v_tipo := 'ILIMITADO';
  elsif not v_gratis_usado then
    v_tipo := 'GRATIS';
  elsif v_row.creditos_comprados > 0 then
    v_tipo := 'COMPRADO';
    update public.sma_carteira_sinais set creditos_comprados = creditos_comprados - 1, atualizado_em = now() where usuario_email = v_email;
  else
    return jsonb_build_object('ok', false, 'erro', 'SEM_CREDITOS');
  end if;
  insert into public.sma_carteira_consumos(request_id, usuario_email, tipo_consumo, status, data_sp, origem)
  values (p_request_id, v_email, v_tipo, 'RESERVADO', v_data, coalesce(nullif(trim(p_origem),''),'streamlit'));
  return jsonb_build_object('ok', true, 'request_id', p_request_id, 'tipo_consumo', v_tipo);
end;
$$;
