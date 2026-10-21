# GitHub Actions — Telegram worker

The workflow is currently manual (`workflow_dispatch`) and forces
`TELEGRAM_DRY_RUN=true`. It does not publish to Telegram or start a scheduler.

Local schedule in `America/Sao_Paulo`:

- 09:00
- 14:00
- 20:00

GitHub Actions cron uses UTC and changes with daylight-saving policy. Convert the
three times immediately before activation and keep the workflow disabled until
that review is complete.

Required GitHub Actions secrets for a future activation:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID` (must be `@smartmoneyaioficial`, never the private test chat)

The current worker's file state is not durable across GitHub-hosted runners.
Activation therefore requires an approved durable, atomic idempotency store
(for example a dedicated table/KV with a unique execution key). Do not use an
artifact or a committed state file as the lock.

The existing signal adapter imports the validated motor lazily. Linux execution
also requires the app's non-secret runtime dependencies and a network path to
the public market-data endpoint. No production Supabase credentials are needed
for Telegram publication.
