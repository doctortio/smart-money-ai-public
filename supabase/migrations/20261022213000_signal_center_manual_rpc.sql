-- Prepared for isolated testing only. No remote migration or deployment.
-- Trusted backend MUST authenticate the session and bind p_admin_id/email itself.
-- The service_role-only RPCs below do not authenticate a browser user.
create or replace function public.sma_signal_manual_create_preview(
 p_preview_id uuid, p_admin_id bigint, p_admin_email text,
 p_assets text[], p_timeframe text, p_signals jsonb,
 p_signals_sha256 text, p_expires_at timestamptz
) returns uuid
language plpgsql security definer set search_path=public,pg_temp
as $$
declare v_stop boolean;
begin
 if not public.sma_signal_center_is_admin(p_admin_id,p_admin_email) then
   raise exception 'admin_required';
 end if;
 if p_assets is null or cardinality(p_assets)<>2 or p_assets[1]=p_assets[2]
    or p_assets[1] not in ('BTCUSDT','ETHUSDT')
    or p_assets[2] not in ('BTCUSDT','ETHUSDT')
    or p_timeframe <> '5m' or p_signals is null
    or jsonb_typeof(p_signals)<>'array' or jsonb_array_length(p_signals)<>2
    or p_signals_sha256 !~ '^[0-9a-f]{64}$'
    or p_expires_at<=now() or p_expires_at>now()+interval '15 minutes' then
   raise exception 'invalid_preview';
 end if;
 select emergency_stop into v_stop from public.sma_signal_center_config where id=true;
 if v_stop is distinct from false then raise exception 'emergency_stop'; end if;
 insert into public.sma_signal_manual_previews
  (preview_id,admin_id,admin_email,assets,timeframe,signals,signals_sha256,expires_at)
 values (p_preview_id,p_admin_id,p_admin_email,p_assets,p_timeframe,p_signals,p_signals_sha256,p_expires_at);
 return p_preview_id;
end; $$;

create or replace function public.sma_signal_manual_confirm_preview(
 p_preview_id uuid, p_admin_id bigint, p_admin_email text,
 p_request_id uuid, p_execution_key text
) returns uuid
language plpgsql security definer set search_path=public,pg_temp
as $$
declare v_preview public.sma_signal_manual_previews%rowtype;
        v_stop boolean;
begin
 if not public.sma_signal_center_is_admin(p_admin_id,p_admin_email) then
   raise exception 'admin_required';
 end if;
 -- Serialize with configuration changes and emergency stop.
 select emergency_stop into v_stop from public.sma_signal_center_config
 where id=true for update;
 if v_stop is distinct from false then raise exception 'emergency_stop'; end if;
 select * into v_preview from public.sma_signal_manual_previews
 where preview_id=p_preview_id for update;
 if not found then raise exception 'preview_not_found'; end if;
 if v_preview.admin_id<>p_admin_id
    or lower(v_preview.admin_email)<>lower(p_admin_email) then
   raise exception 'preview_owner_mismatch';
 end if;
 if v_preview.status<>'pending' then raise exception 'preview_already_processed'; end if;
 if v_preview.expires_at<=now() then raise exception 'preview_expired'; end if;
 if p_execution_key is null or length(p_execution_key)<8
    or length(p_execution_key)>200 then raise exception 'invalid_execution_key'; end if;
 update public.sma_signal_manual_previews
 set status='confirmed',confirmed_at=now() where preview_id=p_preview_id;
 insert into public.sma_signal_manual_outbox
  (request_id,preview_id,execution_key,admin_id)
 values (p_request_id,p_preview_id,p_execution_key,p_admin_id);
 return p_request_id;
end; $$;

create or replace function public.sma_signal_manual_claim_request(
 p_request_id uuid
) returns table(execution_key text,signals jsonb,signals_sha256 text,assets text[],timeframe text)
language plpgsql security definer set search_path=public,pg_temp
as $$
declare v_stop boolean;
begin
 select emergency_stop into v_stop from public.sma_signal_center_config
 where id=true for update;
 if v_stop is distinct from false then raise exception 'emergency_stop'; end if;
 return query
 with claimed as (
  update public.sma_signal_manual_outbox o set status='reserved',updated_at=now()
  where o.request_id=p_request_id and o.status='queued'
  returning o.preview_id,o.execution_key
 )
 select c.execution_key,p.signals,p.signals_sha256,p.assets,p.timeframe
 from claimed c join public.sma_signal_manual_previews p on p.preview_id=c.preview_id;
end; $$;

revoke all on function public.sma_signal_manual_create_preview(uuid,bigint,text,text[],text,jsonb,text,timestamptz),
 public.sma_signal_manual_confirm_preview(uuid,bigint,text,uuid,text),
 public.sma_signal_manual_claim_request(uuid)
 from public,anon,authenticated;
grant execute on function public.sma_signal_manual_create_preview(uuid,bigint,text,text[],text,jsonb,text,timestamptz),
 public.sma_signal_manual_confirm_preview(uuid,bigint,text,uuid,text),
 public.sma_signal_manual_claim_request(uuid) to service_role;

-- The claim is NOT authorization to publish. Worker must reserve the ledger,
-- check emergency stop again immediately before send, and mark the result.
-- In-flight sends are inherently ambiguous on network failure; never blindly retry.
