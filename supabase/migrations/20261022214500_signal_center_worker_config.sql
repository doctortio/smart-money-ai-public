-- Worker-only configuration read, protected by HMAC at Edge Function boundary.
-- This RPC must never be granted to anon/authenticated.
create or replace function public.sma_signal_center_worker_config()
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare c public.sma_signal_center_config%rowtype;
begin
 select * into c from public.sma_signal_center_config where id=true;
 if not found then raise exception 'signal_center_not_configured'; end if;
 return jsonb_build_object(
  'auto_assets',c.auto_assets,'manual_assets',c.manual_assets,
  'timeframe',c.timeframe,'auto_enabled',c.auto_enabled,
  'emergency_stop',c.emergency_stop,'slots',c.slots,
  'timezone',c.timezone,'updated_at',c.updated_at
 );
end; $$;
revoke all on function public.sma_signal_center_worker_config() from public,anon,authenticated;
grant execute on function public.sma_signal_center_worker_config() to service_role;
