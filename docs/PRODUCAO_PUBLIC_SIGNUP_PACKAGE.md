# Pacote incremental de cadastro público — produção

## Escopo

Este pacote adiciona somente o cadastro público com confirmação de e-mail. Não inclui carteira, sinais, Mercado Pago, shadow ou migrações de auditoria.

## Ordem SQL

1. `20261015000000_public_signup_rate_limit.sql` — índice de e-mail normalizado, configuração e contador de rate limit, RPC legada compatível.
2. `20261019000000_upgrade_public_signup_email_confirmation.sql` — tabelas de verificação, RPC de cinco parâmetros, confirmação atômica e permissões.
3. `20261020163559_qualify_signup_digest.sql` — recompõe a RPC de cinco parâmetros qualificando `extensions.digest` sob `search_path` restrito.
4. `20261020190907_harden_public_signup_rls.sql` — habilita RLS e remove grants de cliente das tabelas auxiliares e RPCs.

As migrations `20260924161000` e `20260926183914` ficam fora deste pacote: tratam prioridade da carteira e `sma_signal_shadow`, respectivamente, e não são dependências do cadastro.

## Objetos esperados

- `public.sma_signup_rate_config`
- `public.sma_signup_attempts`
- `public.sma_signup_email_attempts`
- `public.sma_signup_email_verifications`
- `public.sma_public_signup(text,text,text,text)` (legada, preservada)
- `public.sma_public_signup(text,text,text,text,timestamptz)`
- `public.sma_confirm_signup_email(text,text)`
- índice único `clientes_email_lower_uidx`

As tabelas de controle devem permanecer com RLS habilitado e acesso operacional restrito a `service_role`; as RPCs não devem ser executáveis por `anon` ou `authenticated`.

## Funções

Publicar somente `sma-public-signup` e `sma-confirm-signup-email`. Ambas devem continuar com autenticação própria: Turnstile server-side, HMAC/timestamp, rate limit, token de uso único e confirmação antes da ativação. A confirmação deve responder HTML sem cache e aceitar `APP_LOGIN_URL` HTTPS opcional.

## Secrets e URLs

Configurar separadamente na produção, sem copiar valores automaticamente:

`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `PUBLIC_SIGNUP_INTERNAL_HMAC_SECRET`, `CAPTCHA_VERIFY_URL`, `CAPTCHA_SECRET_KEY`, `EMAIL_PROVIDER_URL`, `EMAIL_PROVIDER_API_KEY`, `EMAIL_VERIFY_BASE_URL`, `EMAIL_FROM`, `APP_LOGIN_URL=https://smart-money-ai-4.streamlit.app/`.

O hostname público precisa estar autorizado no Turnstile. Não usar localhost, IP privado ou URL de homologação em e-mails de produção.

## Pré-deploy

1. Exportar dump verificável de schema e objetos afetados.
2. Registrar hashes dos três SQL e do código das funções.
3. Confirmar que o projeto alvo é `tigccvvuemverlbczpoh` e que a branch/commit do aplicativo foi revisada.
4. Executar dry-run e verificar que apenas as três migrations acima estão pendentes.
5. Conferir secrets apenas por nome/presença.

## Pós-deploy

Verificar histórico SQL, assinaturas/permissões das RPCs, status das funções, headers HTML e logs de inicialização. Fazer apenas um smoke test controlado após autorização para envio de e-mail.

## Rollback

Se SQL falhar, parar e restaurar o backup dos objetos afetados; não executar `migration repair`. Para funções, redeployar a versão anterior registrada. Para o Streamlit, reverter o commit publicado. Não remover tabelas ou clientes automaticamente.

## Bloqueios conhecidos

O pacote está preparado, mas não aprovado para publicação enquanto não houver confirmação independente dos secrets de produção, backup recuperável, dry-run sem divergências e autorização explícita de deploy.

## Verificação pós-correção de segurança

Consultar `pg_class.relrowsecurity` para as quatro tabelas e `information_schema.role_table_grants`/`routine_privileges` para confirmar ausência de `anon`, `authenticated` e `PUBLIC`. Confirmar `EXECUTE` apenas para `service_role` nas RPCs operacionais.
