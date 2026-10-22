\set ON_ERROR_STOP on
-- Disposable PostgreSQL only: no Telegram requests.
DO $$
DECLARE t text; f text;
BEGIN
 FOREACH t IN ARRAY ARRAY['sma_signal_manual_previews','sma_signal_manual_outbox','sma_signal_center_config'] LOOP
  IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid=('public.'||t)::regclass) THEN RAISE EXCEPTION 'RLS missing: %',t; END IF;
  IF has_table_privilege('anon','public.'||t,'SELECT') OR has_table_privilege('authenticated','public.'||t,'SELECT') THEN RAISE EXCEPTION 'public table access: %',t; END IF;
 END LOOP;
 FOREACH f IN ARRAY ARRAY[
  'sma_signal_manual_create_preview(uuid,bigint,text,text[],text,jsonb,text,timestamptz)',
  'sma_signal_manual_confirm_preview(uuid,bigint,text,uuid,text)',
  'sma_signal_manual_claim_request(uuid)',
  'sma_signal_center_worker_config()'
 ] LOOP
  IF has_function_privilege('anon','public.'||f,'EXECUTE') OR has_function_privilege('authenticated','public.'||f,'EXECUTE') THEN RAISE EXCEPTION 'public RPC access: %',f; END IF;
 END LOOP;
END $$;
-- Existing fixture has admin id 1 and an initialized config with emergency stop enabled.
DO $$
BEGIN
 IF (public.sma_signal_center_worker_config()->>'emergency_stop')::boolean IS DISTINCT FROM true THEN RAISE EXCEPTION 'emergency stop not enabled by default'; END IF;
END $$;
-- Test real atomic confirmation and duplicate claim under a disabled emergency stop.
UPDATE public.sma_signal_center_config SET emergency_stop=false WHERE id=true;
SELECT public.sma_signal_manual_create_preview(
 '00000000-0000-4000-8000-000000000001',1,'admin@test.invalid',
 ARRAY['BTCUSDT','ETHUSDT'],'5m',
 '[{"asset":"BTCUSDT"},{"asset":"ETHUSDT"}]'::jsonb,
 repeat('a',64),now()+interval '10 minutes'
);
DO $$
BEGIN
 PERFORM public.sma_signal_manual_confirm_preview(
  '00000000-0000-4000-8000-000000000001',2,'user@test.invalid',
  '00000000-0000-4000-8000-000000000002','manual:test:one'
 );
 RAISE EXCEPTION 'nonadmin confirmation succeeded';
EXCEPTION WHEN others THEN
 IF SQLERRM='nonadmin confirmation succeeded' THEN RAISE; END IF;
END $$;
SELECT public.sma_signal_manual_confirm_preview(
 '00000000-0000-4000-8000-000000000001',1,'admin@test.invalid',
 '00000000-0000-4000-8000-000000000002','manual:test:one'
);
DO $$
BEGIN
 IF (SELECT count(*) FROM public.sma_signal_manual_claim_request('00000000-0000-4000-8000-000000000002'))<>1 THEN RAISE EXCEPTION 'first claim failed'; END IF;
 IF (SELECT count(*) FROM public.sma_signal_manual_claim_request('00000000-0000-4000-8000-000000000002'))<>0 THEN RAISE EXCEPTION 'duplicate claim succeeded'; END IF;
END $$;
UPDATE public.sma_signal_center_config SET emergency_stop=true WHERE id=true;
DO $$
BEGIN
 IF (SELECT (public.sma_signal_center_worker_config()->>'emergency_stop')::boolean) IS DISTINCT FROM true THEN RAISE EXCEPTION 'stop not persisted'; END IF;
END $$;

-- Fail-closed regression checks on disposable PostgreSQL.
DO $$
BEGIN
  PERFORM public.sma_signal_manual_confirm_preview(
    '00000000-0000-4000-8000-000000000001',1,'admin@test.invalid',
    '00000000-0000-4000-8000-000000000003','manual:test:duplicate');
  RAISE EXCEPTION 'duplicate confirmation succeeded';
EXCEPTION WHEN others THEN
  IF SQLERRM='duplicate confirmation succeeded' THEN RAISE; END IF;
END $$;
DO $$
BEGIN
  PERFORM public.sma_signal_manual_create_preview(
    '00000000-0000-4000-8000-000000000004',1,'admin@test.invalid',
    ARRAY['BTCUSDT','ETHUSDT'],'5m',
    '[{"asset":"BTCUSDT"},{"asset":"ETHUSDT"}]'::jsonb,
    repeat('a',64),now()+interval '5 minutes');
  RAISE EXCEPTION 'preview bypassed emergency stop';
EXCEPTION WHEN others THEN
  IF SQLERRM='preview bypassed emergency stop' THEN RAISE; END IF;
END $$;
UPDATE public.sma_signal_center_config SET emergency_stop=false WHERE id=true;
SELECT public.sma_signal_manual_create_preview(
 '00000000-0000-4000-8000-000000000005',1,'admin@test.invalid',
 ARRAY['BTCUSDT','ETHUSDT'],'5m',
 '[{"asset":"BTCUSDT"},{"asset":"ETHUSDT"}]'::jsonb,
 repeat('b',64),now()+interval '10 minutes');
UPDATE public.sma_signal_center_config SET emergency_stop=true WHERE id=true;
DO $$
BEGIN
  PERFORM public.sma_signal_manual_confirm_preview(
    '00000000-0000-4000-8000-000000000005',1,'admin@test.invalid',
    '00000000-0000-4000-8000-000000000006','manual:test:stopped');
  RAISE EXCEPTION 'confirmation bypassed emergency stop';
EXCEPTION WHEN others THEN
  IF SQLERRM='confirmation bypassed emergency stop' THEN RAISE; END IF;
END $$;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM public.sma_signal_manual_outbox WHERE request_id='00000000-0000-4000-8000-000000000006') THEN
    RAISE EXCEPTION 'stopped confirmation created outbox';
  END IF;
END $$;
UPDATE public.sma_signal_center_config SET emergency_stop=false WHERE id=true;
SELECT public.sma_signal_manual_create_preview(
 '00000000-0000-4000-8000-000000000007',1,'admin@test.invalid',
 ARRAY['BTCUSDT','ETHUSDT'],'5m',
 '[{"asset":"BTCUSDT"},{"asset":"ETHUSDT"}]'::jsonb,
 repeat('c',64),now()+interval '10 minutes');
UPDATE public.sma_signal_manual_previews SET expires_at=now()-interval '1 minute'
 WHERE preview_id='00000000-0000-4000-8000-000000000007';
DO $$
BEGIN
  PERFORM public.sma_signal_manual_confirm_preview(
    '00000000-0000-4000-8000-000000000007',1,'admin@test.invalid',
    '00000000-0000-4000-8000-000000000008','manual:test:expired');
  RAISE EXCEPTION 'expired preview confirmed';
EXCEPTION WHEN others THEN
  IF SQLERRM='expired preview confirmed' THEN RAISE; END IF;
END $$;
UPDATE public.sma_signal_center_config SET emergency_stop=true WHERE id=true;
