# Telegram automation infrastructure

Homologação audit: no Telegram/worker idempotency table exists in the public
schema. The prepared migration adds a unique execution key, channel, assets,
timeframe, scheduled time, status, Telegram message ID, timestamps and owner.
The reservation function uses `INSERT ... ON CONFLICT DO NOTHING`, so only one
runner can reserve an execution. Public roles receive no table or function
privileges; the worker must use a restricted server-side credential.

The migration is local preparation only and has not been applied remotely.
`unknown` is a terminal state for an ambiguous Telegram response; the worker
must not retry it automatically.
