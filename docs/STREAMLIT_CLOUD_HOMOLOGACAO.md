# Streamlit Cloud — homologação

## Branch e origem

- Branch local: `homologacao/streamlit-cloud`
- Repositório recomendado: `doctortio/smart-money-ai-4.0`
- Entry point: `app.py`
- Supabase exclusivo: `https://otqloermcjoposokoprg.supabase.co`

Não fazer push ou deploy a partir deste documento. A criação da aplicação deve ser feita manualmente no Streamlit Cloud, como uma segunda aplicação, sem substituir `smart-money-ai-4`.

## Secrets necessários

Configurar no painel da nova aplicação, sem valores no repositório:

- `SMA_ENV=homologacao`
- `SUPABASE_URL=https://otqloermcjoposokoprg.supabase.co`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY` (somente backend/Secrets; nunca no navegador)
- `PUBLIC_SIGNUP_FUNCTION_URL=https://otqloermcjoposokoprg.supabase.co/functions/v1/sma-public-signup`
- `PUBLIC_SIGNUP_INTERNAL_HMAC_SECRET`
- `TURNSTILE_SITE_KEY`
- `APP_URL` (URL HTTPS gerada pelo Streamlit Cloud para esta aplicação)
- `SUPPORT_EMAIL` e `SUPPORT_WHATSAPP`, se aprovados

Para confirmação de e-mail na Edge Function, manter os secrets já configurados na homologação e adicionar `APP_LOGIN_URL` com a URL HTTPS real da nova aplicação. Não usar localhost, IP privado ou a URL de produção como fallback.

## Checkout

Não configurar `MERCADOPAGO_CHECKOUT_URL` até existir um endpoint sandbox separado. Com `SMA_ENV=homologacao`, o aplicativo bloqueia o fallback para o endpoint financeiro de produção.

## Criação manual no Streamlit Cloud

1. Create app → repositório `doctortio/smart-money-ai-4.0` → branch `homologacao/streamlit-cloud`.
2. Main file path: `app.py`.
3. Python/dependencies: usar `requirements.txt` versionado; não instalar pacotes adicionais.
4. Inserir os Secrets acima no painel, nunca em commit.
5. Deploy como nova aplicação, mantendo `smart-money-ai-4` intacta.
6. Conferir a URL HTTPS gerada e somente então configurar `APP_URL` e `APP_LOGIN_URL`.
7. Validar leitura do Supabase de homologação, login, cadastro/confirmacão e bloqueio de checkout sem endpoint sandbox.
8. Rollback: parar/reverter a nova aplicação; não alterar a aplicação de produção.
