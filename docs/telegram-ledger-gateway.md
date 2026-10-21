# Telegram ledger gateway (homologação)

The prepared function `sma-telegram-ledger` accepts only POST operations `reserve` and `mark` for `@smartmoneyaioficial`. The worker signs the canonical payload with `TELEGRAM_LEDGER_HMAC_SECRET`; the Edge Function keeps `SUPABASE_SERVICE_ROLE_KEY` inside Supabase and calls only the fixed RPCs. Replay protection and owner binding require the isolated migration `20261022100000_telegram_ledger_gateway_replay.sql`.

Required secrets: `TELEGRAM_LEDGER_GATEWAY_URL` and `TELEGRAM_LEDGER_HMAC_SECRET`. Do not configure or deploy them yet. Deploy order: migration, function secret, function, worker configuration. Rollback: stop workflow/public mode, revert worker gateway, then remove only the added function/migration objects after review.
