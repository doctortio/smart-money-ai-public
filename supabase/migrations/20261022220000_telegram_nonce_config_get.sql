-- Permit signed, replay-protected configuration reads through the existing HMAC nonce ledger.
ALTER TABLE public.sma_telegram_hmac_nonces DROP CONSTRAINT IF EXISTS sma_telegram_hmac_nonces_operation_check;
ALTER TABLE public.sma_telegram_hmac_nonces ADD CONSTRAINT sma_telegram_hmac_nonces_operation_check CHECK (operation = ANY (ARRAY['reserve'::text,'mark'::text,'config_get'::text]));
