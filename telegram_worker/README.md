# Telegram Worker V1

Worker independente para preparar publicações do Smart Money AI no canal oficial.

## Segurança operacional

- `TELEGRAM_DRY_RUN=true` é o padrão e não envia mensagens.
- O scheduler não é iniciado por importação; `main()` encerra solicitando um adaptador explícito.
- O token do bot e o chat ID devem existir apenas no ambiente de execução como `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.
- Nenhum crédito ou franquia de cliente é consultado ou consumido.
- O estado idempotente deve ficar fora do Git (`TELEGRAM_STATE_PATH`).

## Configuração proposta

```text
TELEGRAM_DRY_RUN=true
TELEGRAM_BOT_TOKEN=<secret>
TELEGRAM_CHAT_ID=<secret>
TELEGRAM_STATE_PATH=./runtime/telegram_state.json
```

Horários: 09:00, 14:00 e 20:00 em `America/Sao_Paulo`; ativos: `BTCUSDT` e `ETHUSDT`; timeframe: `5m`.

Antes de qualquer ativação, confirmar no BotFather e no canal que o bot pode publicar. Executar primeiro em um chat/canal de teste e manter `DRY_RUN` até aprovação explícita.
