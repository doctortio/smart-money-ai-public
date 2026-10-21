\set ON_ERROR_STOP on
do $$ begin
  if not exists (select 1 from pg_class where relname = 'sma_telegram_hmac_nonces') then raise exception 'nonce table missing'; end if;
  if not (select relrowsecurity from pg_class where relname = 'sma_telegram_hmac_nonces') then raise exception 'RLS disabled'; end if;
  if has_table_privilege('anon', 'public.sma_telegram_hmac_nonces', 'select') then raise exception 'anon access granted'; end if;
  if has_table_privilege('authenticated', 'public.sma_telegram_hmac_nonces', 'select') then raise exception 'authenticated access granted'; end if;
end $$;
select public.sma_telegram_claim_nonce('nonce-test-1','exec-test-1','reserve','@smartmoneyaioficial',now()+interval '5 minutes');
do $$ begin
  if public.sma_telegram_claim_nonce('nonce-test-1','exec-test-1','reserve','@smartmoneyaioficial',now()+interval '5 minutes') then raise exception 'replay accepted'; end if;
end $$;
select public.sma_telegram_claim_nonce('nonce-test-2','exec-test-1','mark','@smartmoneyaioficial',now()+interval '5 minutes');
insert into public.clientes(id,email,tipo_usuario,plano) values (1,'admin@test.invalid','ADMIN','admin/base'),(2,'user@test.invalid','CLIENTE','mensal');
do $$ begin
  if not public.sma_signal_center_is_admin(1,'admin@test.invalid') then raise exception 'admin rejected'; end if;
  if public.sma_signal_center_is_admin(2,'admin@test.invalid') then raise exception 'id/email spoof accepted'; end if;
  if public.sma_signal_center_is_admin(2,'user@test.invalid') then raise exception 'user accepted'; end if;
end $$;
select public.sma_signal_center_get(1,'admin@test.invalid');
