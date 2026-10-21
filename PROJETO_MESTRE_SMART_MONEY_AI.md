# Projeto Mestre Smart Money AI — checklist de lançamento

## Estado

- Aplicativo: `app.py` / Streamlit.
- Site institucional: `C:\Users\Dell\Documents\Codex\smart-money-ai-institutional-site`.
- Homologação: `otqloermcjoposokoprg`.
- Produção: `tigccvvuemverlbczpoh`.
- Pacote incremental de cadastro: `docs/PRODUCAO_PUBLIC_SIGNUP_PACKAGE.md`.
- RLS de cadastro: migration local `20261020190907_harden_public_signup_rls.sql` corresponde à versão remota `20261020191551_harden_public_signup_rls` (mesmo conteúdo lógico; o timestamp remoto foi gerado na aplicação).

## Funcionalidades preservadas

Login, sessão, Turnstile, confirmação de e-mail, franquia gratuita, carteira, planos, Mercado Pago e motor de sinais permanecem fora do escopo de alterações SQL deste pacote.

## Publicação pendente

Aplicar somente as migrations 20261015000000, 20261019000000, 20261020163559 e 20261020190907, nessa ordem, após backup, dry-run e conferência de secrets. Publicar somente as duas funções de cadastro/confirmação. Não aplicar as migrations de carteira ou shadow como parte deste lançamento.

## Decisões de segurança

`APP_LOGIN_URL` de produção deve ser configurado explicitamente como `https://smart-money-ai-4.streamlit.app/`. Não existe fallback para produção na homologação. Nenhum secret deve ser commitado ou copiado entre ambientes.

## Critério de aprovação

Só considerar pronto após: backup recuperável; dry-run limpo; permissões/RLS conferidas; funções ativas; logs sem boot error; confirmação HTML com e sem URL; e smoke test autorizado de cadastro/login.

## Homologação

Fluxo integrado pós-RLS aprovado pelo proprietário: CAPTCHA, cadastro, confirmação de e-mail, login, sinal gratuito e bloqueio do segundo sinal. Pendência visual remanescente: a versão remota da confirmação deve receber o patch HTML local.
