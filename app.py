import streamlit as st
from signal_audit import calculate_audit_counts, evaluate_sequential_audit, normalize_audit_result, p3_from_entry
import pandas as pd
import google.generativeai as genai
import requests
import time
import math
from datetime import datetime, timedelta
import pytz
from pathlib import Path
import hmac
import hashlib
import uuid
import sqlite3
import smtplib
from email.message import EmailMessage
import secrets as py_secrets
import string
import urllib.parse
import base64
import zipfile
import logging
import json
import os
import threading
from mobile_ui import apply_mobile_styles
from browser_session import browser_session_command, bounded_expiry, parse_browser_record
from turnstile_component import render_turnstile


AUDITORIA_FILE = Path("auditoria_sinais.csv")
HISTORICO_FILE = Path("historico_sinais.csv")
USO_SINAIS_FILE = Path("uso_sinais_clientes.csv")
AUDITORIA_SUPABASE_TABLE = "auditoria_sinais_51"
_HTTP_SESSION = requests.Session()


def _http_request(method: str, url: str, **kwargs):
    """Reutiliza conexoes HTTP sem cachear decisoes de autenticacao."""
    return _HTTP_SESSION.request(method, url, **kwargs)



def gerar_event_id() -> str:
    """ID aleatorio e sanitizado para correlacao de suporte/logs."""
    return str(uuid.uuid4())


def event_id_valido(event_id: str) -> bool:
    try:
        return str(uuid.UUID(str(event_id))) == str(event_id)
    except Exception:
        return False


def obter_event_id_fluxo(nome_fluxo: str = "operacao") -> str:
    chave = "event_id_" + "".join(ch for ch in str(nome_fluxo or "operacao").lower() if ch.isalnum() or ch == "_")[:40]
    try:
        atual = str(st.session_state.get(chave, "") or "")
        if event_id_valido(atual):
            return atual
        novo = gerar_event_id()
        st.session_state[chave] = novo
        return novo
    except Exception:
        return gerar_event_id()


def sanitizar_erro_observabilidade(exc: Exception | str) -> str:
    texto = type(exc).__name__ if not isinstance(exc, str) else exc
    proibidos = ("SUPABASE_SERVICE_ROLE_KEY", "MERCADOPAGO_ACCESS_TOKEN", "MERCADOPAGO_WEBHOOK_SECRET", "Authorization", "Bearer", "service_role", "token", "password", "senha", "secret")
    if any(p.lower() in texto.lower() for p in proibidos):
        return "ERRO_SANITIZADO"
    return texto[:80]


def log_evento_operacional(event_id: str, operacao: str, status: str, erro: Exception | str | None = None) -> None:
    try:
        eid = str(event_id) if event_id_valido(str(event_id)) else gerar_event_id()
        op = "".join(ch for ch in str(operacao or "operacao") if ch.isalnum() or ch in "_-.")[:80] or "operacao"
        stt = "".join(ch for ch in str(status or "status") if ch.isalnum() or ch in "_-.")[:40] or "status"
        extra = f" erro={sanitizar_erro_observabilidade(erro)}" if erro is not None else ""
        logging.getLogger(__name__).info("[event_id=%s] %s status=%s%s", eid, op, stt, extra)
    except Exception:
        pass


def mensagem_usuario_com_event_id(mensagem: str, event_id: str) -> str:
    if not event_id_valido(str(event_id)):
        return str(mensagem or "Não foi possível concluir a operação.")
    return f"{str(mensagem or 'Não foi possível concluir a operação.')} Código de atendimento: {event_id}"

def carregar_lista_csv(caminho: Path) -> list:
    try:
        if caminho.exists() and caminho.stat().st_size > 0:
            df = pd.read_csv(caminho)
            df = df.where(pd.notnull(df), None)
            return df.to_dict("records")
    except Exception:
        pass
    return []


def salvar_lista_csv(caminho: Path, lista: list) -> None:
    try:
        pd.DataFrame(lista).to_csv(caminho, index=False, encoding="utf-8-sig")
    except Exception:
        pass


# =============================
# ENVIO DE E-MAIL AUTOMÁTICO
# =============================
def email_configurado() -> bool:
    try:
        return bool(st.secrets.get("SMTP_HOST", "") and st.secrets.get("SMTP_USER", "") and st.secrets.get("SMTP_PASS", ""))
    except Exception:
        return False


def enviar_email(destinatario: str, assunto: str, corpo: str) -> tuple[bool, str]:
    """Envia e-mail via SMTP configurado no Streamlit Secrets.
    Se não estiver configurado, o app continua funcionando normalmente.
    """
    destinatario = (destinatario or "").strip().lower()
    if not destinatario:
        return False, "Destinatário vazio."
    if not email_configurado():
        return False, "SMTP não configurado. Configure SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS e SMTP_FROM no Secrets."
    try:
        host = str(st.secrets.get("SMTP_HOST", "")).strip()
        port = int(st.secrets.get("SMTP_PORT", 587))
        user = str(st.secrets.get("SMTP_USER", "")).strip()
        # Senhas de app do Gmail aparecem com espaços. Removemos os espaços automaticamente.
        password = str(st.secrets.get("SMTP_PASS", "")).replace(" ", "").strip()
        remetente = str(st.secrets.get("SMTP_FROM", user)).strip() or user
        usar_ssl = bool(st.secrets.get("SMTP_SSL", False))

        msg = EmailMessage()
        msg["Subject"] = assunto
        msg["From"] = remetente
        msg["To"] = destinatario
        msg.set_content(corpo)

        if usar_ssl or port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(msg)
        return True, "E-mail enviado com sucesso."
    except smtplib.SMTPAuthenticationError as e:
        return False, f"Erro de autenticação SMTP. Confira SMTP_USER e SMTP_PASS/Senha de App do Gmail. Detalhes: {e}"
    except smtplib.SMTPException as e:
        return False, f"Erro SMTP: {e}"
    except Exception as e:
        return False, f"Falha ao enviar e-mail ({type(e).__name__}): {e}"


def enviar_email_com_anexo(destinatario: str, assunto: str, corpo: str, anexo_bytes: bytes | None = None, anexo_nome: str = "", anexo_mime: str = "application/octet-stream") -> tuple[bool, str]:
    """Envia e-mail via SMTP com anexo opcional.
    Usado para comprovantes de renovação, evitando links quebrados ou bucket privado.
    """
    destinatario = (destinatario or "").strip().lower()
    if not destinatario:
        return False, "Destinatário vazio."
    if not email_configurado():
        return False, "SMTP não configurado. Configure SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS e SMTP_FROM no Secrets."
    try:
        host = str(st.secrets.get("SMTP_HOST", "")).strip()
        port = int(st.secrets.get("SMTP_PORT", 587))
        user = str(st.secrets.get("SMTP_USER", "")).strip()
        password = str(st.secrets.get("SMTP_PASS", "")).replace(" ", "").strip()
        remetente = str(st.secrets.get("SMTP_FROM", user)).strip() or user
        usar_ssl = bool(st.secrets.get("SMTP_SSL", False))

        msg = EmailMessage()
        msg["Subject"] = assunto
        msg["From"] = remetente
        msg["To"] = destinatario
        msg.set_content(corpo)

        if anexo_bytes and anexo_nome:
            mime = (anexo_mime or "application/octet-stream").split(";")[0].strip()
            if "/" in mime:
                maintype, subtype = mime.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"
            msg.add_attachment(anexo_bytes, maintype=maintype, subtype=subtype, filename=anexo_nome)

        if usar_ssl or port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(msg)
        return True, "E-mail enviado com sucesso."
    except smtplib.SMTPAuthenticationError as e:
        return False, f"Erro de autenticação SMTP. Confira SMTP_USER e SMTP_PASS/Senha de App do Gmail. Detalhes: {e}"
    except smtplib.SMTPException as e:
        return False, f"Erro SMTP: {e}"
    except Exception as e:
        return False, f"Falha ao enviar e-mail com anexo ({type(e).__name__}): {e}"


def url_do_app() -> str:
    """Retorna a URL correta do app atual.
    Prioriza a URL real acessada no navegador para evitar enviar link antigo
    em recuperação de senha quando o DEV/produção muda de endereço.
    Se não conseguir detectar, usa APP_URL dos Secrets como fallback.
    """
    try:
        headers = dict(getattr(st, "context", {}).headers) if hasattr(st, "context") else {}
        host = headers.get("host") or headers.get("x-forwarded-host")
        proto = headers.get("x-forwarded-proto", "https")
        if host and "localhost" not in str(host):
            return f"{proto}://{host}"
    except Exception:
        pass
    try:
        return str(st.secrets.get("APP_URL", "https://seu-app.streamlit.app")).strip()
    except Exception:
        return "https://seu-app.streamlit.app"


def suporte_email() -> str:
    """E-mail de suporte exibido no login e usado nos avisos."""
    try:
        return str(st.secrets.get("SUPPORT_EMAIL", st.secrets.get("SMTP_FROM", st.secrets.get("SMTP_USER", "contato.ultrasinais@gmail.com")))).strip()
    except Exception:
        return "contato.ultrasinais@gmail.com"


def suporte_whatsapp_link() -> str:
    """Link de suporte via WhatsApp. Configure SUPPORT_WHATSAPP no Secrets com DDI+DDD+número."""
    try:
        numero = str(st.secrets.get("SUPPORT_WHATSAPP", "5524993142178")).strip()
        url = str(st.secrets.get("SUPPORT_URL", "")).strip()
        if url:
            return url
        if numero:
            numero_limpo = "".join(ch for ch in numero if ch.isdigit())
            if numero_limpo:
                msg = urllib.parse.quote("Olá, preciso de suporte no Smart Money AI.")
                return f"https://wa.me/{numero_limpo}?text={msg}"
    except Exception:
        pass
    return ""


def suporte_email_link() -> str:
    """Monta link mailto com assunto e mensagem padrão."""
    email = suporte_email()
    assunto = urllib.parse.quote("Suporte Smart Money AI")
    corpo = urllib.parse.quote("Olá, preciso de suporte no Smart Money AI. Meu e-mail de acesso é: ")
    return f"mailto:{email}?subject={assunto}&body={corpo}"


def gerar_senha_temporaria(tamanho: int = 10) -> str:
    alfabeto = string.ascii_letters + string.digits
    return "".join(py_secrets.choice(alfabeto) for _ in range(tamanho))


def validar_senha_nova(senha: str) -> tuple[bool, str]:
    if not isinstance(senha, str) or not senha.strip() or len(senha) < 8:
        return False, "A senha precisa ter pelo menos 8 caracteres."
    if len(senha) > 1024:
        return False, "A senha excede o tamanho permitido."
    if senha in ("123456", "12345678", "123456789", "password", "senha123"):
        return False, "Escolha uma senha diferente das senhas padrão."
    return True, ""


def _versao_credencial(cliente: dict) -> str:
    return hashlib.sha256(str(cliente.get("senha") or "").encode("utf-8")).hexdigest()


def _seguranca_operar(acao: str, dados: dict | None = None) -> dict:
    """Backend explícito: RPC remota ou SQLite local, nunca fallback por falha."""
    dados = dict(dados or {})
    url = str(st.secrets.get("SUPABASE_URL", "")).strip().rstrip("/")
    role = str(st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "")).strip()
    anon = str(st.secrets.get("SUPABASE_ANON_KEY", "")).strip()
    if url or role or anon:
        corr = uuid.uuid4().hex[:12]
        if not url or not role:
            logging.getLogger(__name__).warning("AUTH_CONFIG_ERROR stage=config error_type=RuntimeError correlation_id=%s", corr)
            raise RuntimeError("Segurança de acesso requer configuração Supabase completa no servidor.")
        try:
            response = requests.post(
                f"{url}/rest/v1/rpc/sma_auth_operar",
                headers={"apikey": role, "Authorization": f"Bearer {role}", "Content-Type": "application/json"},
                json={"p_acao": acao, "p_dados": dados}, timeout=15,
            )
        except requests.exceptions.Timeout:
            logging.getLogger(__name__).warning("AUTH_RPC_ERROR stage=request error_type=Timeout correlation_id=%s", corr)
            raise RuntimeError("Mecanismo de acesso indisponível.")
        except requests.exceptions.RequestException as exc:
            logging.getLogger(__name__).warning("AUTH_RPC_ERROR stage=request error_type=%s correlation_id=%s", type(exc).__name__, corr)
            raise RuntimeError("Mecanismo de acesso indisponível.")
        if response.status_code != 200:
            logging.getLogger(__name__).warning("AUTH_RPC_ERROR stage=response status_http=%s correlation_id=%s", response.status_code, corr)
            raise RuntimeError(f"Mecanismo de acesso indisponível (HTTP {response.status_code}).")
        result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            logging.getLogger(__name__).warning("AUTH_RPC_ERROR stage=decode error_type=InvalidResponse correlation_id=%s", corr)
            raise RuntimeError("Resposta inválida do mecanismo de acesso.")
        logging.getLogger(__name__).info("AUTH_RPC_OK stage=response correlation_id=%s", corr)
        return result
    return _seguranca_local(acao, dados)


def _seguranca_local(acao: str, dados: dict) -> dict:
    """Ledger transacional exclusivo do modo SQLite; tokens sempre em hash."""
    agora = datetime.now(fuso_br).timestamp()
    from contextlib import closing
    with closing(sqlite3.connect(SEGURANCA_DB, timeout=10)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE IF NOT EXISTS auth_tokens (
            token_hash TEXT PRIMARY KEY, usuario TEXT NOT NULL, tipo TEXT NOT NULL,
            sessao_id TEXT NOT NULL, vinculo_hash TEXT NOT NULL, credencial_versao TEXT NOT NULL,
            fingerprint TEXT NOT NULL, emitido_em REAL NOT NULL, expira_em REAL NOT NULL,
            visto_em REAL NOT NULL, status TEXT NOT NULL)""")
        conn.execute("BEGIN IMMEDIATE")
        if acao == "saude":
            return {"ok": True}
        if acao in ("emitir_reset", "emitir_sessao"):
            tipo = "reset" if acao == "emitir_reset" else "sessao"
            usuario = dados["usuario"]
            if tipo == "reset":
                recente = conn.execute("SELECT 1 FROM auth_tokens WHERE usuario=? AND tipo='reset' AND emitido_em>?", (usuario, agora - 60)).fetchone()
                if recente:
                    return {"ok": False, "motivo": "aguarde"}
            else:
                ttl = max(60, min(86400, int(dados.get("ttl", 180))))
                # Mudança de hash invalida também sessões de outras instâncias.
                conn.execute("UPDATE auth_tokens SET status='revogado' WHERE usuario=? AND tipo='sessao' AND credencial_versao<>?", (usuario, dados["credencial_versao"]))
                ativa = conn.execute("SELECT 1 FROM auth_tokens WHERE usuario=? AND tipo='sessao' AND status='ativo' AND expira_em>? AND visto_em>?", (usuario, agora, agora - ttl)).fetchone()
                if dados.get("unica", True) and ativa:
                    return {"ok": False, "motivo": "sessao_ativa"}
                if dados.get("unica", True):
                    conn.execute("UPDATE auth_tokens SET status='revogado' WHERE usuario=? AND tipo='sessao' AND status='ativo'", (usuario,))
            expira = agora + (1200 if tipo == "reset" else 43200)
            conn.execute("INSERT INTO auth_tokens VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                dados["token_hash"], usuario, tipo, dados["sessao_id"], dados.get("vinculo_hash", ""),
                dados["credencial_versao"], dados.get("fingerprint", ""), agora, expira, agora, "ativo"))
            return {"ok": True, "sessao_id": dados["sessao_id"], "expira_em": expira}
        if acao in ("validar_reset", "consumir_reset", "validar_sessao"):
            row = conn.execute("SELECT * FROM auth_tokens WHERE token_hash=?", (dados.get("token_hash", ""),)).fetchone()
            tipo = "sessao" if acao == "validar_sessao" else "reset"
            if (not row or row["tipo"] != tipo or row["status"] != "ativo" or row["expira_em"] <= agora
                    or row["usuario"] != dados.get("usuario") or row["credencial_versao"] != dados.get("credencial_versao")):
                return {"ok": False, "motivo": "invalido"}
            if tipo == "sessao" and not hmac.compare_digest(row["vinculo_hash"], dados.get("vinculo_hash", "")):
                return {"ok": False, "motivo": "vinculo_incompativel"}
            if acao == "consumir_reset":
                conn.execute("UPDATE auth_tokens SET status='usado' WHERE token_hash=?", (row["token_hash"],))
            elif tipo == "sessao":
                conn.execute("UPDATE auth_tokens SET visto_em=?,fingerprint=? WHERE token_hash=?", (agora, dados.get("fingerprint", ""), row["token_hash"]))
            return {"ok": True, "sessao_id": row["sessao_id"], "expira_em": row["expira_em"], "fingerprint_alterado": tipo == "sessao" and row["fingerprint"] != dados.get("fingerprint", "")}
        if acao == "revogar_token":
            conn.execute("UPDATE auth_tokens SET status='revogado' WHERE token_hash=?", (dados.get("token_hash", ""),))
        elif acao == "revogar_usuario":
            conn.execute("UPDATE auth_tokens SET status='revogado' WHERE usuario=?", (dados["usuario"],))
        elif acao == "revogar_sessao":
            conn.execute("UPDATE auth_tokens SET status='revogado' WHERE sessao_id=? AND tipo='sessao'", (dados["sessao_id"],))
        elif acao == "limpar_sessoes":
            conn.execute("UPDATE auth_tokens SET status='revogado' WHERE tipo='sessao'")
        elif acao == "expirar":
            conn.execute("UPDATE auth_tokens SET status='expirado' WHERE expira_em<=? AND status='ativo'", (agora,))
        elif acao == "listar_sessoes":
            rows = conn.execute("SELECT usuario,sessao_id,visto_em,fingerprint FROM auth_tokens WHERE tipo='sessao' AND status='ativo' AND expira_em>? ORDER BY visto_em DESC", (agora,)).fetchall()
            return {"ok": True, "registros": [dict(row) for row in rows]}
        else:
            raise ValueError("Operação de segurança desconhecida.")
        return {"ok": True}


def _hash_token_acesso(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _gravar_hash_confirmado(email: str, novo_hash: str, hash_anterior: str) -> bool:
    """Compare-and-swap: evita sobrescrever outra troca de senha concorrente."""
    if supabase_configurado():
        rows = _sb_patch("clientes", {"senha_hash": novo_hash, "atualizado_em": datetime.now(fuso_br).isoformat()},
                         params={"email": f"eq.{email}", "senha_hash": f"eq.{hash_anterior}"}, prefer="return=representation")
        return len(rows) == 1 and rows[0].get("email") == email and rows[0].get("senha_hash") == novo_hash
    from contextlib import closing
    with closing(sqlite3.connect(CLIENTES_DB)) as conn, conn:
        cursor = conn.execute("UPDATE clientes SET senha_hash=?, atualizado_em=? WHERE email=? AND senha_hash=?", (novo_hash, datetime.now(fuso_br).isoformat(), email, hash_anterior))
        return cursor.rowcount == 1


def _revogar_acessos_usuario(usuario: str) -> None:
    if not _seguranca_operar("revogar_usuario", {"usuario": usuario})["ok"]:
        raise RuntimeError("Não foi possível confirmar revogação.")


def _limpar_autenticacao_local() -> None:
    if st.session_state.get("auth_token") or st.session_state.get("_browser_record_present"):
        _agendar_sessao_browser("clear")
    st.session_state["_browser_record_present"] = False
    for chave in ("auth_token", "auth_binding", "session_token", "usuario_logado"):
        st.session_state.pop(chave, None)
    st.session_state.authenticated = False
    _set_query_param("sm_session", "")


def autenticar_credenciais(usuario: str, senha: str, encerrar_anteriores: bool = False) -> tuple[bool, str]:
    usuario = (usuario or "").strip().lower()
    _limpar_autenticacao_local()
    try:
        cliente = cliente_autorizado(usuario)
        if not cliente or not _data_valida(usuario, cliente)[0] or not _senha_confere(senha, str(cliente.get("senha") or "")):
            return False, "Não foi possível autorizar o acesso com essas credenciais."
        antigo = str(cliente["senha"])
        verificado = antigo
        if not antigo.startswith("scrypt$"):
            verificado = _hash_senha(senha)
            if not _gravar_hash_confirmado(usuario, verificado, antigo):
                return False, "Não foi possível confirmar a atualização segura do acesso. Tente novamente."
            cliente["senha"] = verificado
            cliente["senha_hash"] = verificado
        if encerrar_anteriores:
            _revogar_acessos_usuario(usuario)
        criar_login_persistente(usuario, verificado, cliente)
        st.session_state.authenticated = True
        st.session_state.usuario_logado = usuario
        st.session_state["_cliente_login_confirmado"] = {
            "email": cliente.get("email"),
            "plano": cliente.get("plano"),
            "tipo_usuario": cliente.get("tipo_usuario"),
            "vencimento": cliente.get("vencimento"),
            "ativo": cliente.get("ativo"),
        }
        return True, "Acesso autorizado."
    except Exception as exc:
        logging.getLogger(__name__).warning("Acesso negado: %s", type(exc).__name__)
        _limpar_autenticacao_local()
        return False, "Acesso temporariamente indisponível ou sessão já em uso. Tente novamente; para encerrar a anterior, confirme a opção no formulário."


def validar_token_recuperacao(email: str, token: str, consumir: bool = False) -> bool:
    try:
        email = (email or "").strip().lower()
        cliente = cliente_autorizado(email)
        if not token or not cliente or not cliente.get("ativo"):
            return False
        resultado = _seguranca_operar("consumir_reset" if consumir else "validar_reset", {
            "usuario": email, "token_hash": _hash_token_acesso(token), "credencial_versao": _versao_credencial(cliente),
        })
        return resultado["ok"]
    except Exception as exc:
        logging.getLogger(__name__).warning("Recuperação indisponível: %s", type(exc).__name__)
        return False


def concluir_recuperacao_senha(email: str, token: str, nova_senha: str, confirmar_senha: str) -> tuple[bool, str]:
    valido, mensagem = validar_senha_nova(nova_senha)
    if not valido or nova_senha != confirmar_senha:
        return False, mensagem or "A confirmação da senha não confere."
    email = (email or "").strip().lower()
    try:
        cliente = cliente_autorizado(email)
        if not cliente or not cliente.get("ativo"):
            return False, "Código inválido, expirado ou já utilizado."
        novo_hash = _hash_senha(nova_senha)
        # Consumir antes do CAS: falha na escrita queima o token, nunca permite reuso.
        # O CAS usa a mesma versão vinculada ao token, mesmo com troca concorrente.
        resultado = _seguranca_operar("consumir_reset", {
            "usuario": email, "token_hash": _hash_token_acesso(token), "credencial_versao": _versao_credencial(cliente),
        })
        if not resultado["ok"]:
            return False, "Código inválido, expirado ou já utilizado."
        if not _gravar_hash_confirmado(email, novo_hash, str(cliente["senha"])):
            return False, "Alteração não confirmada. Solicite outro código de recuperação."
        if st.session_state.get("usuario_logado") == email:
            _limpar_autenticacao_local()
        _revogar_acessos_usuario(email)
        return True, "Senha atualizada. As sessões antigas foram invalidadas. Faça login novamente."
    except Exception as exc:
        logging.getLogger(__name__).warning("Recuperação não confirmada: %s", type(exc).__name__)
        return False, "Não foi possível confirmar todas as etapas. Tente entrar com a nova senha ou solicite outro código."


def redefinir_senha_cliente_por_email(email: str) -> tuple[bool, str]:
    """Solicita código de uso único por e-mail; não altera a senha."""
    resposta = "Se for possível recuperar esse acesso, você receberá as instruções por e-mail. Aguarde antes de solicitar novamente."
    email = (email or "").strip().lower()
    token_hash = ""
    try:
        cliente = cliente_autorizado(email)
        if not cliente or not cliente.get("ativo"):
            return True, resposta
        token = py_secrets.token_urlsafe(32)
        token_hash = _hash_token_acesso(token)
        criado = _seguranca_operar("emitir_reset", {
            "usuario": email, "token_hash": token_hash, "sessao_id": str(uuid.uuid4()),
            "credencial_versao": _versao_credencial(cliente),
        })
        if not criado["ok"]:
            return True, resposta
        corpo = ("Foi solicitada a recuperação do seu acesso Smart Money AI.\n\n"
                 "Abra a plataforma pelo endereço que você já utiliza. Na tela de login, abra ESQUECI MINHA SENHA "
                 "e preencha Confirmar recuperação com seu e-mail, o código abaixo e uma nova senha.\n\n"
                 f"Código: {token}\n\nVálido por 20 minutos e para uma única utilização. "
                 "Se você não solicitou, ignore esta mensagem: sua senha não foi alterada.")
        ok, _ = enviar_email(email, "Recuperação de acesso - Smart Money AI", corpo)
        if not ok:
            _seguranca_operar("revogar_token", {"token_hash": token_hash})
            logging.getLogger(__name__).warning("Envio de recuperação não confirmado; senha preservada.")
    except Exception as exc:
        logging.getLogger(__name__).warning("Solicitação de recuperação não confirmada: %s", type(exc).__name__)
        if token_hash:
            try:
                _seguranca_operar("revogar_token", {"token_hash": token_hash})
            except Exception:
                pass  # token ainda expira em 20 min; senha nunca foi alterada
    return True, resposta


def alterar_senha_cliente_logado(email: str, senha_atual: str, nova_senha: str, confirmar_senha: str) -> tuple[bool, str]:
    email = (email or "").strip().lower()
    if email != st.session_state.get("usuario_logado") or not restaurar_login_persistente():
        return False, "Faça login novamente para alterar a senha."
    valido, mensagem = validar_senha_nova(nova_senha)
    if not valido or nova_senha != confirmar_senha:
        return False, mensagem or "A confirmação da senha não confere."
    try:
        cliente = cliente_autorizado(email)
        if not cliente or not _senha_confere(senha_atual, str(cliente.get("senha") or "")):
            return False, "Senha atual incorreta."
        ok, msg = atualizar_senha_cliente_db(email, nova_senha, hash_anterior=str(cliente["senha"]))
        _limpar_autenticacao_local()
        return ok, msg
    except Exception as exc:
        logging.getLogger(__name__).warning("Troca de senha não confirmada: %s", type(exc).__name__)
        return False, "Não foi possível confirmar a troca de senha."

def resumo_config_email() -> dict:
    """Mostra uma visão segura da configuração SMTP para diagnóstico no painel Admin."""
    try:
        smtp_pass = str(st.secrets.get("SMTP_PASS", ""))
        return {
            "APP_URL": st.secrets.get("APP_URL", "não configurado"),
            "SMTP_HOST": st.secrets.get("SMTP_HOST", "não configurado"),
            "SMTP_PORT": st.secrets.get("SMTP_PORT", "não configurado"),
            "SMTP_USER": st.secrets.get("SMTP_USER", "não configurado"),
            "SMTP_FROM": st.secrets.get("SMTP_FROM", "não configurado"),
            "SMTP_SSL": st.secrets.get("SMTP_SSL", False),
            "SMTP_PASS_configurada": "SIM" if bool(smtp_pass) else "NÃO",
            "SMTP_PASS_tamanho_sem_espacos": len(smtp_pass.replace(" ", "")),
        }
    except Exception as e:
        return {"erro": str(e)}


def mensagem_boas_vindas(nome: str, email: str, senha: str, plano: str, vencimento: str) -> str:
    nome = nome or email
    senha_txt = senha if senha else "a senha cadastrada pelo suporte"
    return f"""Olá, {nome}!

Seu acesso ao Smart Money AI foi liberado.

Acesse pelo link:
{url_do_app()}

Login: {email}
Senha: {senha_txt}
Plano: {plano}
Vencimento: {vencimento}

O sistema funciona tanto em computador quanto em celular.

Importante: seu acesso é individual. O compartilhamento de login pode bloquear a sessão.

Em caso de dúvidas, entre em contato com o suporte.
"""


def mensagem_cobranca(nome: str, email: str, plano: str, valor: float, vencimento_cobranca: str, observacao: str = "") -> str:
    nome = nome or email
    valor_txt = f"R$ {float(valor or 0):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    obs = f"\nObservação: {observacao}\n" if observacao else ""
    return f"""Olá, {nome}!

Foi gerada uma cobrança referente ao seu acesso ao Smart Money AI.

Plano: {plano}
Valor: {valor_txt}
Vencimento da cobrança: {vencimento_cobranca}
{obs}
Acesse o sistema pelo link:
{url_do_app()}

Após o pagamento, seu acesso será confirmado/renovado pelo suporte.

Em caso de dúvidas, entre em contato.
"""

st.set_page_config(
    page_title="Smart Money AI 4.3 FASE 5.1+ AUDITORIA SUPABASE COM COLUNAS",
    layout="wide",
    initial_sidebar_state="auto"
)
apply_mobile_styles(st)

APP_VERSION_LABEL = "Smart Money AI 4.3 FASE 5.1+ AUDITORIA SUPABASE COM COLUNAS"
APP_ENV_LABEL = "Ambiente de Desenvolvimento - não usar para clientes finais"


# =============================
# RODAPÉ LEGAL / VERSÃO COMERCIAL
# =============================
def render_rodape_legal() -> None:
    """Rodapé comercial exibido nas áreas internas."""
    st.markdown(
        """
        <div style="margin-top:36px;border-top:1px solid rgba(255,255,255,.12);padding:18px 0;text-align:center;color:#8b98aa;font-size:13px;line-height:1.8;">
            <div style="font-weight:800;color:#00ff88;letter-spacing:.4px;">SMART MONEY AI 4.7 CALIBRADO TESTE</div>
            <a href="?legal=privacidade" target="_self" style="color:#00ff88;text-decoration:none;font-weight:700;">Política de Privacidade</a>
            &nbsp;&nbsp;|&nbsp;&nbsp;
            <a href="?legal=termos" target="_self" style="color:#00ff88;text-decoration:none;font-weight:700;">Termos de Uso</a>
            &nbsp;&nbsp;|&nbsp;&nbsp;
            <a href="mailto:contato.ultrasinais@gmail.com" style="color:#00ff88;text-decoration:none;font-weight:700;">Contato de suporte</a>
            <br>
            <span>© 2026 Smart Money AI - Todos os direitos reservados. Uso individual e intransferível.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =============================
# PÁGINAS LEGAIS PÚBLICAS (sem voltar para login)
# =============================
def render_pagina_legal_publica() -> None:
    """Renderiza Política de Privacidade e Termos de Uso dentro do próprio app.
    Isso evita que o Streamlit navegue para outra página e volte para o login.
    """
    try:
        pagina = str(st.query_params.get("legal", "")).strip().lower()
    except Exception:
        try:
            pagina = str(st.experimental_get_query_params().get("legal", [""])[0]).strip().lower()
        except Exception:
            pagina = ""

    if pagina not in ["privacidade", "termos"]:
        return

    st.markdown(
        """
        <div style="background:#000;color:#00ff88;padding:14px;text-align:center;font-size:20px;font-weight:900;border-radius:10px;margin-bottom:20px;border:1px solid rgba(0,255,136,.35);">
        🚀 SMART MONEY AI 4.7 CALIBRADO TESTE
        </div>
        """,
        unsafe_allow_html=True,
    )

    if pagina == "privacidade":
        st.title("Política de Privacidade")
        st.caption("Smart Money AI • Versão 4.0 TESTE")
        st.markdown(
            """
            Esta Política de Privacidade explica como o Smart Money AI utiliza informações dos usuários para operar a plataforma.

            ### 1. Dados utilizados
            Podemos utilizar dados como nome, e-mail, plano contratado, vencimento, registros de acesso, solicitações de renovação, comprovantes de pagamento e informações técnicas necessárias para segurança da conta.

            ### 2. Finalidade
            Os dados são usados para controle de acesso, suporte, recuperação de senha, cobrança, renovação de plano, segurança contra compartilhamento indevido e melhoria da plataforma.

            ### 3. Segurança
            As credenciais e configurações sensíveis devem permanecer no Streamlit Secrets e no Supabase, nunca em repositórios públicos.

            ### 4. Pagamentos
            Comprovantes enviados pelo cliente podem ser armazenados para validação administrativa e histórico financeiro.

            ### 5. Suporte
            Para dúvidas sobre dados, acesso ou suporte, entre em contato pelo e-mail de suporte informado na plataforma.

            ### 6. Aviso de risco
            O Smart Money AI oferece sinais e análises técnicas. Nenhum resultado financeiro é garantido. O usuário é responsável pela própria gestão de risco.
            """
        )
    else:
        st.title("Termos de Uso")
        st.caption("Smart Money AI • Versão 4.0 TESTE")
        st.markdown(
            """
            Ao utilizar o Smart Money AI, o usuário concorda com estes termos.

            ### 1. Uso individual
            O acesso é pessoal e intransferível. O compartilhamento de login pode gerar bloqueio automático da sessão ou da conta.

            ### 2. Planos e vencimentos
            O acesso depende do plano contratado e da data de vencimento registrada no sistema. Planos vitalícios da versão 3.5 dão acesso permanente à versão 3.5, mas não garantem acesso automático a versões principais futuras.

            ### 3. Pagamentos e renovações
            Renovações podem ser solicitadas por PIX ou cartão, conforme opções disponíveis. A liberação pode depender da confirmação administrativa do pagamento.

            ### 4. Sinais e riscos
            Os sinais apresentados são ferramentas de apoio à decisão. O mercado financeiro possui risco. O Smart Money AI não garante lucro, resultado ou ausência de perdas.

            ### 5. Suporte
            O suporte é prestado pelos canais informados na tela de login e no rodapé da plataforma.

            ### 6. Suspensão de acesso
            O acesso pode ser bloqueado por vencimento, uso indevido, compartilhamento de conta, tentativa de fraude ou violação destes termos.

            ### 7. Alterações
            Estes termos podem ser atualizados conforme a evolução da plataforma.
            """
        )

    st.markdown("---")
    st.info("LGPD: seus dados são utilizados apenas para controle de acesso, suporte, cobranças e segurança da conta.")
    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("⬅️ Voltar ao login", key="btn_voltar_login_legal"):
            try:
                del st.query_params["legal"]
            except Exception:
                try:
                    st.experimental_set_query_params()
                except Exception:
                    pass
            st.rerun()
    with col2:
        st.markdown(f"📧 Suporte: [{suporte_email()}]({suporte_email_link()})")

    st.markdown("© 2026 Smart Money AI - Todos os direitos reservados.")
    st.stop()


# =============================
# CONTROLE DE ACESSO / LOGIN
# =============================
ACESSOS_FILE = Path("acessos_clientes.csv")
SESSOES_FILE = Path("sessoes_ativas.csv")
LOGIN_TOKENS_FILE = Path("login_tokens.csv")  # legado: nunca usado para autenticar
SEGURANCA_DB = Path("seguranca_acesso.db")
fuso_br = pytz.timezone("America/Sao_Paulo")


def formatar_data_br(valor) -> str:
    """Formata datas ISO/DateTime para o padrão brasileiro DD/MM/AAAA.
    Mantém valores vazios ou inválidos sem quebrar o app.
    """
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except Exception:
        pass
    texto = str(valor).strip()
    if not texto or texto.lower() in ["nat", "none", "nan"]:
        return ""
    try:
        # Remove horário ISO quando existir, mantendo apenas a data.
        if "T" in texto:
            texto = texto.split("T")[0]
        if " " in texto and "-" in texto:
            texto = texto.split(" ")[0]
        dt = pd.to_datetime(texto, errors="coerce")
        if pd.isna(dt):
            return str(valor)
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return str(valor)


def formatar_colunas_datas(df: pd.DataFrame) -> pd.DataFrame:
    """Cria uma cópia do DataFrame com datas visíveis em DD/MM/AAAA.
    É usado somente para exibição no painel, preservando o banco em formato técnico.
    """
    if df is None or df.empty:
        return df
    saida = df.copy()
    for col in ["vencimento", "vencimento_cobranca", "data_pagamento", "criado_em", "atualizado_em"]:
        if col in saida.columns:
            saida[col] = saida[col].apply(formatar_data_br)
    return saida



def formatar_data_hora_br(valor) -> str:
    """Formata data/hora para o fuso do Brasil: DD/MM/AAAA HH:MM:SS.
    Corrige a exibição do Supabase, que pode retornar timestamps em UTC/ISO.
    """
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except Exception:
        pass
    texto = str(valor).strip()
    if not texto or texto.lower() in ["nat", "none", "nan"]:
        return ""
    try:
        # Registros legados nesse formato já representam o horário de Brasília.
        if "/" in texto:
            dt_local = pd.to_datetime(texto, errors="coerce", dayfirst=True)
            if not pd.isna(dt_local):
                return dt_local.strftime("%d/%m/%Y %H:%M:%S")
        dt = pd.to_datetime(texto, errors="coerce")
        if pd.isna(dt):
            return texto
        if dt.tzinfo is None:
            # Valores sem offset foram gerados localmente e já estão em Brasília.
            return dt.strftime("%d/%m/%Y %H:%M:%S")
        return dt.tz_convert("America/Sao_Paulo").strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return texto


def formatar_colunas_datahora_br(df: pd.DataFrame) -> pd.DataFrame:
    """Formata colunas de data/hora para exibição no painel Admin."""
    if df is None or df.empty:
        return df
    saida = df.copy()
    for col in ["data_hora", "data_hora_iso", "last_seen", "last_seen_iso", "criado_em", "atualizado_em"]:
        if col in saida.columns:
            saida[col] = saida[col].apply(formatar_data_hora_br)
    return saida


def texto_dias_restantes(vencimento: str) -> str:
    try:
        hoje = datetime.now(fuso_br).date()
        data_venc = datetime.strptime(str(vencimento), "%Y-%m-%d").date()
        dias = (data_venc - hoje).days
        if dias < 0:
            return "🔴 Assinatura vencida"
        if dias <= 3:
            return f"⚠️ Restam {dias} dia(s)"
        return f"✅ Restam {dias} dia(s)"
    except Exception:
        return ""


def intervalo_minimo_sinal_segundos() -> int:
    """Intervalo mínimo entre sinais por cliente para proteger o app de excesso de cliques."""
    try:
        return int(st.secrets.get("SIGNAL_COOLDOWN_SECONDS", 45))
    except Exception:
        return 45


def carregar_uso_sinais() -> list:
    return carregar_lista_csv(USO_SINAIS_FILE)


def salvar_uso_sinais(lista: list) -> None:
    salvar_lista_csv(USO_SINAIS_FILE, lista[:10000])


def registrar_uso_sinal(usuario: str, ativo: str, timeframe: str, status: str, detalhe: str = "") -> None:
    registros = carregar_uso_sinais()
    agora = datetime.now(fuso_br)
    registros.insert(0, {
        "data_hora": agora.strftime("%d/%m/%Y %H:%M:%S"),
        "data_hora_iso": agora.isoformat(),
        "usuario": (usuario or "cliente").strip().lower(),
        "ativo": ativo,
        "timeframe": timeframe,
        "status": status,
        "detalhe": detalhe,
    })
    salvar_uso_sinais(registros)


def pode_gerar_sinal(usuario: str) -> tuple[bool, int, str]:
    """Retorna se o cliente pode gerar sinal agora, segundos restantes e mensagem.
    Regra comercial: todo usuário, inclusive admin em teste, respeita 1 sinal por minuto.
    Isso evita que o botão fique liberado quando o administrador testa como se fosse cliente.
    """
    usuario = (usuario or "cliente").strip().lower()
    intervalo = intervalo_minimo_sinal_segundos()
    agora = datetime.now(fuso_br)
    for reg in carregar_uso_sinais():
        if str(reg.get("usuario", "")).strip().lower() != usuario:
            continue
        if str(reg.get("status", "")).upper() != "GERADO":
            continue
        try:
            dt = datetime.fromisoformat(str(reg.get("data_hora_iso")))
            diff = (agora - dt).total_seconds()
            if diff < intervalo:
                falta = int(intervalo - diff) + 1
                return False, falta, f"Aguarde {falta}s para gerar outro sinal."
            break
        except Exception:
            continue
    return True, 0, "Liberado."


def resumo_uso_usuario(usuario: str) -> dict:
    usuario = (usuario or "cliente").strip().lower()
    hoje = datetime.now(fuso_br).date()
    registros = carregar_uso_sinais()
    gerados_hoje = 0
    bloqueados_hoje = 0
    total_gerados = 0
    for reg in registros:
        if str(reg.get("usuario", "")).strip().lower() != usuario:
            continue
        status = str(reg.get("status", "")).upper()
        if status == "GERADO":
            total_gerados += 1
        try:
            dt = datetime.fromisoformat(str(reg.get("data_hora_iso"))).date()
        except Exception:
            continue
        if dt == hoje:
            if status == "GERADO":
                gerados_hoje += 1
            elif status.startswith("BLOQUEADO"):
                bloqueados_hoje += 1
    return {"gerados_hoje": gerados_hoje, "bloqueados_hoje": bloqueados_hoje, "total_gerados": total_gerados}


def dataframe_uso_sinais() -> pd.DataFrame:
    df = pd.DataFrame(carregar_uso_sinais())
    if df.empty:
        return df
    cols = ["data_hora", "usuario", "ativo", "timeframe", "status", "detalhe"]
    return df[[c for c in cols if c in df.columns]]


CLIENTES_DB = Path("clientes_acesso.db")


def _hash_senha(senha: str) -> str:
    """scrypt com salt aleatório; formato versionado compatível com migração gradual."""
    if not isinstance(senha, str) or not senha or len(senha) > 1024:
        raise ValueError("Senha inválida.")
    salt = py_secrets.token_bytes(16)
    digest = hashlib.scrypt(senha.encode("utf-8"), salt=salt, n=131072, r=8, p=1, dklen=32, maxmem=268435456)
    return f"scrypt$131072$8$1${salt.hex()}${digest.hex()}"


def init_clientes_db() -> None:
    conn = sqlite3.connect(CLIENTES_DB)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS clientes (
            email TEXT PRIMARY KEY,
            nome TEXT,
            senha_hash TEXT NOT NULL,
            vencimento TEXT NOT NULL,
            ativo INTEGER DEFAULT 1,
            plano TEXT DEFAULT 'mensal',
            criado_em TEXT,
            atualizado_em TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def upsert_cliente(email: str, nome: str, senha: str | None, vencimento: str, plano: str = "mensal", ativo: bool = True) -> None:
    email = (email or "").strip().lower()
    if not email:
        return
    # Proteção da conta master/admin: nunca salva como bloqueada ou vencida.
    try:
        if usuario_master_protegido(email):
            ativo = True
            try:
                venc_data = datetime.strptime(str(vencimento), "%Y-%m-%d").date()
                if venc_data < datetime.now(fuso_br).date():
                    vencimento = "2099-12-31"
            except Exception:
                vencimento = "2099-12-31"
    except Exception:
        pass
    agora_iso = datetime.now(fuso_br).isoformat()
    if usuario_master_protegido(email):
        ativo = True
        vencimento = "2099-12-31"
        plano = plano or "admin/base"
    conn = sqlite3.connect(CLIENTES_DB)
    cur = conn.cursor()
    cur.execute("SELECT email, senha_hash, criado_em FROM clientes WHERE email = ?", (email,))
    row = cur.fetchone()
    if not row or senha is not None:
        valido, mensagem = validar_senha_nova(senha or "")
        if not valido:
            conn.close()
            raise ValueError(mensagem)
    if row:
        senha_hash = row[1] if senha is None else _hash_senha(senha)
        criado_em = row[2] or agora_iso
        cur.execute(
            """
            UPDATE clientes
            SET nome=?, senha_hash=?, vencimento=?, ativo=?, plano=?, criado_em=?, atualizado_em=?
            WHERE email=?
            """,
            (nome or email, senha_hash, vencimento, 1 if ativo else 0, plano, criado_em, agora_iso, email),
        )
    else:
        senha_hash = _hash_senha(senha)
        cur.execute(
            """
            INSERT INTO clientes (email, nome, senha_hash, vencimento, ativo, plano, criado_em, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (email, nome or email, senha_hash, vencimento, 1 if ativo else 0, plano, agora_iso, agora_iso),
        )
    conn.commit()
    conn.close()


def listar_clientes_db() -> pd.DataFrame:
    init_clientes_db()
    conn = sqlite3.connect(CLIENTES_DB)
    df = pd.read_sql_query(
        "SELECT email, nome, vencimento, ativo, plano, criado_em, atualizado_em FROM clientes ORDER BY atualizado_em DESC",
        conn,
    )
    conn.close()
    return df



def _precos_planos_padrao() -> dict:
    """Valores estimados para o painel comercial.
    Você pode sobrescrever pelo Streamlit Secrets usando [PLAN_PRICES].
    """
    precos = {
        "mensal": 97.0,
        "trimestral": 247.0,
        "semestral": 397.0,
        "anual": 697.0,
        "teste": 0.0,
        "vitalicio": 700.0,
        "vitalicio_v35": 700.0,
        "admin/base": 0.0,
        "secrets": 0.0,
    }
    try:
        custom = st.secrets.get("PLAN_PRICES", {})
        for plano, valor in dict(custom).items():
            try:
                precos[str(plano).strip().lower()] = float(valor)
            except Exception:
                pass
    except Exception:
        pass
    return precos


def enriquecer_clientes_comercial(clientes_df: pd.DataFrame) -> pd.DataFrame:
    if clientes_df is None or clientes_df.empty:
        return pd.DataFrame()
    df = clientes_df.copy()
    hoje = datetime.now(fuso_br).date()
    precos = _precos_planos_padrao()
    df["plano_norm"] = df["plano"].astype(str).str.strip().str.lower()
    if "tipo_usuario" not in df.columns:
        df["tipo_usuario"] = df["plano_norm"].apply(lambda p: "DEMO" if p in ["demo", "teste", "demonstração", "demonstracao"] else "CLIENTE")
    df["tipo_usuario"] = df["tipo_usuario"].apply(normalizar_tipo_usuario)
    df["valor_estimado"] = df.apply(lambda r: valor_cliente_para_receita(r.get("plano_norm", ""), r.get("tipo_usuario", "CLIENTE")), axis=1)
    df["vencimento_data"] = pd.to_datetime(df["vencimento"], errors="coerce").dt.date
    df["dias_restantes"] = df["vencimento_data"].apply(lambda d: (d - hoje).days if pd.notnull(d) else None)
    df["status_comercial"] = df.apply(
        lambda r: "BLOQUEADO" if not bool(r.get("ativo", 0)) else (
            "VENCIDO" if (r.get("dias_restantes") is not None and r.get("dias_restantes") < 0) else (
                "VENCE EM 7 DIAS" if (r.get("dias_restantes") is not None and r.get("dias_restantes") <= 7) else "ATIVO"
            )
        ),
        axis=1,
    )
    return df


def calcular_metricas_comerciais() -> dict:
    clientes = enriquecer_clientes_comercial(listar_clientes_db())
    if clientes.empty:
        return {
            "total_clientes": 0,
            "ativos": 0,
            "vencidos": 0,
            "bloqueados": 0,
            "vencem_7d": 0,
            "receita_ativa_estim": 0.0,
            "receita_total_estim": 0.0,
            "clientes_df": clientes,
        }
    ativos_df = clientes[(clientes["ativo"].astype(bool)) & (clientes["status_comercial"] != "VENCIDO")]
    return {
        "total_clientes": int(len(clientes)),
        "ativos": int(len(ativos_df)),
        "vencidos": int((clientes["status_comercial"] == "VENCIDO").sum()),
        "bloqueados": int((clientes["status_comercial"] == "BLOQUEADO").sum()),
        "vencem_7d": int((clientes["status_comercial"] == "VENCE EM 7 DIAS").sum()),
        "receita_ativa_estim": float(ativos_df["valor_estimado"].sum()),
        "receita_total_estim": float(clientes["valor_estimado"].sum()),
        "clientes_df": clientes,
    }



# =============================
# COBRANÇAS / RENOVAÇÕES
# =============================
COBRANCAS_DB = CLIENTES_DB


def init_cobrancas_db() -> None:
    init_clientes_db()
    conn = sqlite3.connect(COBRANCAS_DB)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cobrancas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            nome TEXT,
            plano TEXT,
            valor REAL DEFAULT 0,
            vencimento_cobranca TEXT,
            data_pagamento TEXT,
            metodo TEXT,
            status TEXT DEFAULT 'PENDENTE',
            observacao TEXT,
            criado_em TEXT,
            atualizado_em TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def criar_cobranca(email: str, nome: str, plano: str, valor: float, vencimento_cobranca: str, observacao: str = "") -> int:
    init_cobrancas_db()
    agora_iso = datetime.now(fuso_br).isoformat()
    conn = sqlite3.connect(COBRANCAS_DB)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO cobrancas (email, nome, plano, valor, vencimento_cobranca, status, observacao, criado_em, atualizado_em)
        VALUES (?, ?, ?, ?, ?, 'PENDENTE', ?, ?, ?)
        """,
        ((email or '').strip().lower(), nome or email, plano, float(valor or 0), vencimento_cobranca, observacao or '', agora_iso, agora_iso),
    )
    cobranca_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(cobranca_id)


def listar_cobrancas() -> pd.DataFrame:
    init_cobrancas_db()
    conn = sqlite3.connect(COBRANCAS_DB)
    df = pd.read_sql_query("SELECT * FROM cobrancas ORDER BY atualizado_em DESC, id DESC", conn)
    conn.close()
    return df


def atualizar_status_cobranca(cobranca_id: int, status: str, metodo: str = "", data_pagamento: str = "", observacao: str = "") -> None:
    init_cobrancas_db()
    agora_iso = datetime.now(fuso_br).isoformat()
    conn = sqlite3.connect(COBRANCAS_DB)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE cobrancas
        SET status=?, metodo=?, data_pagamento=?, observacao=?, atualizado_em=?
        WHERE id=?
        """,
        (status, metodo or '', data_pagamento or '', observacao or '', agora_iso, int(cobranca_id)),
    )
    conn.commit()
    conn.close()


def remover_cobranca(cobranca_id: int) -> None:
    init_cobrancas_db()
    conn = sqlite3.connect(COBRANCAS_DB)
    cur = conn.cursor()
    cur.execute("DELETE FROM cobrancas WHERE id=?", (int(cobranca_id),))
    conn.commit()
    conn.close()


def calcular_metricas_cobrancas() -> dict:
    df = listar_cobrancas()
    if df.empty:
        return {"pendentes": 0, "pagas": 0, "vencidas": 0, "receber": 0.0, "recebido": 0.0, "df": df}
    hoje = datetime.now(fuso_br).date()
    df = df.copy()
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce").fillna(0.0)
    df["venc_data"] = pd.to_datetime(df["vencimento_cobranca"], errors="coerce").dt.date
    df["status_real"] = df.apply(
        lambda r: "VENCIDA" if str(r.get("status", "")).upper() == "PENDENTE" and pd.notnull(r.get("venc_data")) and r.get("venc_data") < hoje else str(r.get("status", "PENDENTE")).upper(),
        axis=1,
    )
    pend = df[df["status_real"].isin(["PENDENTE", "VENCIDA"])]
    pagas = df[df["status_real"] == "PAGA"]
    return {
        "pendentes": int((df["status_real"] == "PENDENTE").sum()),
        "pagas": int((df["status_real"] == "PAGA").sum()),
        "vencidas": int((df["status_real"] == "VENCIDA").sum()),
        "receber": float(pend["valor"].sum()),
        "recebido": float(pagas["valor"].sum()),
        "df": df,
    }



# =============================
# AUTOMAÇÃO DE COBRANÇA / BLOQUEIO
# =============================

def init_automacoes_db() -> None:
    init_cobrancas_db()
    conn = sqlite3.connect(COBRANCAS_DB)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS automacoes_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            email TEXT NOT NULL,
            nome TEXT,
            plano TEXT,
            vencimento TEXT,
            dias_restantes INTEGER,
            acao TEXT,
            status TEXT,
            mensagem TEXT,
            data_ref TEXT,
            criado_em TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def registrar_automacao(tipo: str, email: str, nome: str = "", plano: str = "", vencimento: str = "", dias_restantes: int | None = None, acao: str = "", status: str = "", mensagem: str = "") -> None:
    init_automacoes_db()
    agora = datetime.now(fuso_br)
    data_ref = agora.date().isoformat()
    conn = sqlite3.connect(COBRANCAS_DB)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO automacoes_log (tipo, email, nome, plano, vencimento, dias_restantes, acao, status, mensagem, data_ref, criado_em)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (str(tipo), str(email or '').strip().lower(), nome or '', plano or '', vencimento or '', dias_restantes, acao or '', status or '', mensagem or '', data_ref, agora.isoformat()),
    )
    conn.commit()
    conn.close()


def automacao_ja_executada_hoje(tipo: str, email: str) -> bool:
    init_automacoes_db()
    hoje = datetime.now(fuso_br).date().isoformat()
    conn = sqlite3.connect(COBRANCAS_DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM automacoes_log WHERE tipo=? AND email=? AND data_ref=?",
        (str(tipo), str(email or '').strip().lower(), hoje),
    )
    total = cur.fetchone()[0]
    conn.close()
    return int(total or 0) > 0


def listar_automacoes_log(limite: int = 200) -> pd.DataFrame:
    init_automacoes_db()
    conn = sqlite3.connect(COBRANCAS_DB)
    df = pd.read_sql_query(
        "SELECT * FROM automacoes_log ORDER BY criado_em DESC, id DESC LIMIT ?",
        conn,
        params=(int(limite),),
    )
    conn.close()
    return df


def mensagem_aviso_vencimento(nome: str, email: str, plano: str, vencimento: str, dias_restantes: int, valor: float = 0.0) -> str:
    nome = nome or email
    venc_br = formatar_data_br(vencimento)
    if dias_restantes > 0:
        abertura = f"Seu acesso ao Smart Money AI vence em {dias_restantes} dia(s)."
    elif dias_restantes == 0:
        abertura = "Seu acesso ao Smart Money AI vence hoje."
    else:
        abertura = "Seu acesso ao Smart Money AI está vencido."
    valor_txt = f"R$ {float(valor or 0):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    return f"""Olá, {nome}!

{abertura}

Plano: {plano}
Vencimento: {venc_br}
Valor de referência do plano: {valor_txt}

Para evitar bloqueio ou restabelecer seu acesso, entre em contato com o suporte e confirme a renovação.

Acesse o sistema:
{url_do_app()}

Smart Money AI
"""


def _exigir_admin_comercial() -> None:
    if not restaurar_login_persistente() or not usuario_e_admin(st.session_state.get("usuario_logado", "")):
        raise PermissionError("Operação exclusiva da administração autenticada.")


def _rpc_comercial(acao: str, dados: dict) -> dict:
    """Uma chamada equivale a uma transação PostgreSQL; nunca encadeia PATCHs."""
    _exigir_admin_comercial()
    url = str(st.secrets.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = str(st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "")).strip()
    if not url or not key:
        raise RuntimeError("Infraestrutura comercial transacional indisponível.")
    response = requests.post(url + "/rest/v1/rpc/sma_comercial_operar", headers={"apikey": key, "Authorization": "Bearer " + key},
                             json={"p_acao": acao, "p_dados": dados}, timeout=30)
    if response.status_code != 200:
        raise RuntimeError("Transação não confirmada. Consulte a solicitação antes de tentar novamente.")
    result = response.json()
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise RuntimeError("Contrato comercial inválido.")
    return result


def identidade_obrigacao(email: str, plano: str, vencimento: str) -> str:
    dados = [(email or "").strip().lower(), (plano or "").strip().lower(), str(vencimento)]
    return hashlib.sha256(json.dumps(dados, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _aviso_operar(acao: str, dados: dict) -> dict:
    _exigir_admin_comercial()
    if supabase_configurado() or any(st.secrets.get(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY")):
        return _rpc_comercial(acao, dados)
    from contextlib import closing
    init_cobrancas_db()
    with closing(sqlite3.connect(COBRANCAS_DB, timeout=15)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS obrigacoes (chave TEXT PRIMARY KEY, cobranca_id INTEGER NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS avisos_cobranca (chave TEXT PRIMARY KEY, obrigacao TEXT NOT NULL, estado TEXT NOT NULL, tentativa TEXT NOT NULL, atualizado_em TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS avisos_eventos (id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT NOT NULL, tentativa TEXT NOT NULL, estado TEXT NOT NULL, criado_em TEXT NOT NULL)")
        conn.execute("BEGIN IMMEDIATE")
        agora = datetime.now(fuso_br).isoformat()
        if acao == "garantir_obrigacao":
            row = conn.execute("SELECT cobranca_id FROM obrigacoes WHERE chave=?", (dados['chave'],)).fetchone()
            if row:
                return {"ok": True, "criada": False, "cobranca_id": row[0]}
            legadas = conn.execute("SELECT id,status,valor FROM cobrancas WHERE email=? AND plano=? AND vencimento_cobranca=?", (dados['email'],dados['plano'],dados['vencimento'])).fetchall()
            if legadas:
                if len(legadas)!=1 or legadas[0][1]!='PENDENTE' or float(legadas[0][2])!=float(dados['valor']):
                    raise RuntimeError("Cobranças legadas precisam de conciliação; nenhuma cobrança nova foi criada.")
                conn.execute("INSERT INTO obrigacoes VALUES (?,?)",(dados['chave'],legadas[0][0]))
                return {"ok":True,"criada":False,"cobranca_id":legadas[0][0]}
            cur = conn.execute("INSERT INTO cobrancas(email,nome,plano,valor,vencimento_cobranca,status,observacao,criado_em,atualizado_em) VALUES(?,?,?,?,?,'PENDENTE',?,?,?)",
                               (dados['email'],dados['nome'],dados['plano'],dados['valor'],dados['vencimento'],"Obrigação de vencimento",agora,agora))
            conn.execute("INSERT INTO obrigacoes VALUES (?,?)", (dados['chave'],cur.lastrowid))
            return {"ok": True, "criada": True, "cobranca_id": cur.lastrowid}
        if acao == "reservar_aviso":
            row = conn.execute("SELECT estado FROM avisos_cobranca WHERE chave=?", (dados['chave'],)).fetchone()
            if row and row[0] != 'FALHOU':
                return {"ok": True, "reservado": False, "estado": row[0]}
            conn.execute("INSERT INTO avisos_cobranca VALUES(?,?,'TENTADO',?,?) ON CONFLICT(chave) DO UPDATE SET estado='TENTADO',tentativa=excluded.tentativa,atualizado_em=excluded.atualizado_em", (dados['chave'],dados['obrigacao'],dados['tentativa'],agora))
            estado = 'TENTADO'
        elif acao == "finalizar_aviso":
            estado = dados['estado']
            if estado not in ('ENVIADO','FALHOU','INCERTO'):
                raise ValueError("Estado de aviso inválido.")
            cur = conn.execute("UPDATE avisos_cobranca SET estado=?,atualizado_em=? WHERE chave=? AND tentativa=? AND estado='TENTADO'", (estado,agora,dados['chave'],dados['tentativa']))
            if cur.rowcount != 1:
                return {"ok": False}
        else:
            raise ValueError("Operação inválida.")
        conn.execute("INSERT INTO avisos_eventos(chave,tentativa,estado,criado_em) VALUES(?,?,?,?)", (dados['chave'],dados['tentativa'],estado,agora))
        return {"ok": True, "reservado": acao == 'reservar_aviso', "estado": estado}


def processar_aviso_cobranca(cli: dict, dias: int, enviar: bool, criar: bool) -> dict:
    """Independente da UI; reserva durável precede SMTP. Incerteza exige conciliação."""
    chave = identidade_obrigacao(cli['email'],cli['plano'],cli['vencimento'])
    resultado = {"avisos":0,"cobrancas":0,"falhas":0}
    if criar:
        cob = _aviso_operar('garantir_obrigacao', {'chave':chave,'email':cli['email'],'nome':cli.get('nome') or cli['email'],
                           'plano':cli['plano'],'valor':float(cli.get('valor_estimado') or 0),'vencimento':cli['vencimento']})
        if not cob.get('ok'):
            raise RuntimeError("Obrigação não confirmada.")
        resultado['cobrancas'] = int(cob.get('criada',False))
    if not enviar:
        return resultado  # simulação não consome aviso nem alega envio
    dados = {'chave':chave+':'+str(dias),'obrigacao':chave,'tentativa':str(uuid.uuid4())}
    reserva = _aviso_operar('reservar_aviso',dados)
    if not reserva.get('ok'):
        raise RuntimeError("Reserva de aviso não confirmada.")
    if not reserva.get('reservado'):
        resultado['falhas'] = int(reserva.get('estado') in ('TENTADO','INCERTO'))
        return resultado
    try:
        ok, msg = enviar_email(cli['email'], 'Aviso de vencimento - Smart Money AI' if dias > 0 else 'Seu acesso vence hoje - Smart Money AI',
                               mensagem_aviso_vencimento(cli.get('nome'),cli['email'],cli['plano'],cli['vencimento'],dias,float(cli.get('valor_estimado') or 0)))
        # O helper SMTP existente captura exceções; retorno negativo pode ser entrega ambígua.
        falha_definitiva = str(msg).startswith(('Destinatário vazio.', 'SMTP não configurado.', 'Erro de autenticação SMTP.'))
        estado = 'ENVIADO' if ok else 'FALHOU' if falha_definitiva else 'INCERTO'
    except Exception:
        estado = 'INCERTO'
    final = _aviso_operar('finalizar_aviso',dict(dados,estado=estado))
    if not final.get('ok'):
        raise RuntimeError("Resultado do envio não confirmado; não reenviar automaticamente.")
    resultado['avisos'] = int(estado == 'ENVIADO')
    resultado['falhas'] = int(estado != 'ENVIADO')
    return resultado


def validar_comprovante(conteudo: bytes, nome: str, mime: str) -> tuple[str, str]:
    nome = str(nome or '')
    if not nome or '/' in nome or '\\' in nome or '..' in nome or any(ord(c)<32 for c in nome):
        raise ValueError("Nome de comprovante inválido.")
    limite = min(20.0, max(0.1,float(st.secrets.get('COMPROVANTE_MAX_MB',8)))) * 1024 * 1024
    if not conteudo or len(conteudo)>limite:
        raise ValueError("Comprovante vazio ou acima do limite permitido.")
    ext = Path(nome).suffix.lower()
    tipos = {'.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}
    detectado = 'application/pdf' if conteudo.startswith(b'%PDF-') else 'image/png' if conteudo.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if conteudo.startswith(b'\xff\xd8\xff') else ''
    if ext not in tipos or tipos[ext] != detectado or str(mime).lower() != detectado:
        raise ValueError("Tipo, extensão e conteúdo do comprovante não conferem.")
    return ext, detectado


def _storage_privado(bucket: str) -> tuple[str, dict]:
    base = str(st.secrets.get('SUPABASE_URL','')).strip().rstrip('/')
    key = str(st.secrets.get('SUPABASE_SERVICE_ROLE_KEY','')).strip()
    if not base or not key or not bucket or '/' in bucket or '..' in bucket:
        raise RuntimeError("Configuração de Storage privado indisponível.")
    headers = {'apikey':key,'Authorization':'Bearer '+key}
    r = requests.get(base+'/storage/v1/bucket/'+urllib.parse.quote(bucket,safe=''),headers=headers,timeout=15)
    if r.status_code != 200 or r.json().get('public') is not False:
        raise RuntimeError("Comprovantes exigem bucket privado confirmado.")
    return base, headers


def obter_link_comprovante(solicitacao_id: int) -> tuple[str, int]:
    _exigir_admin_comercial()
    rows = _sb_get('solicitacoes_renovacao',params={'select':'*','id':f'eq.{int(solicitacao_id)}','limit':'1'})
    if len(rows)!=1:
        raise ValueError("Comprovante não encontrado.")
    sol = rows[0]
    path = str(sol.get('comprovante_path') or '')
    bucket = str(sol.get('comprovante_bucket') or bucket_comprovantes())
    if not path or path.startswith('/') or '..' in path.split('/') or '\\' in path:
        raise ValueError("Comprovante legado requer migração autenticada.")
    base, headers = _storage_privado(bucket)
    ttl = 120
    response = requests.post(base+'/storage/v1/object/sign/'+urllib.parse.quote(bucket,safe='')+'/'+urllib.parse.quote(path,safe='/'),headers=headers,json={'expiresIn':ttl},timeout=15)
    if response.status_code != 200:
        raise RuntimeError("Não foi possível autorizar o comprovante.")
    signed = response.json().get('signedURL') or response.json().get('signedUrl')
    if not isinstance(signed,str) or not signed.startswith('/object/sign/'):
        raise RuntimeError("Resposta de assinatura inválida.")
    return base+'/storage/v1'+signed, ttl


def _exportar_tabela_paginada(tabela: str, ordem: str, lote: int = 500) -> list[dict]:
    registros = []
    offset = 0
    anterior = None
    while True:
        pagina = _sb_get(tabela, params={'select':'*','order':ordem,'limit':str(lote),'offset':str(offset)})
        if not isinstance(pagina,list):
            raise RuntimeError("Página inválida.")
        if not pagina:
            return registros
        digest = hashlib.sha256(json.dumps(pagina,sort_keys=True,default=str).encode()).hexdigest()
        if digest == anterior:
            raise RuntimeError("Servidor repetiu página; exportação interrompida.")
        anterior = digest
        registros.extend(pagina)
        offset += len(pagina)  # não presume que limite solicitado seja o limite real


def verificar_manifest_exportacao(conteudo: bytes) -> bool:
    import io
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        manifest = json.loads(z.read('manifest.json'))
        if manifest.get('versao') != 1:
            return False
        for nome, meta in manifest['arquivos'].items():
            if hashlib.sha256(z.read(nome)).hexdigest()!=meta['sha256']:
                return False
        return set(z.namelist()) == set(manifest['arquivos']) | {'manifest.json'}


def executar_automacao_cobranca_bloqueio(enviar_emails: bool = True, bloquear_vencidos: bool = True, criar_cobranca_auto: bool = True) -> dict:
    """Executa a rotina comercial.
    - Envia avisos uma vez por dia para clientes com vencimento em 7, 3 ou 0 dias.
    - Bloqueia clientes vencidos, preservando a conta master/admin.
    - Registra tudo em automacoes_log.
    """
    _exigir_admin_comercial()
    init_automacoes_db()
    clientes = enriquecer_clientes_comercial(listar_clientes_db())
    if clientes.empty:
        return {"avisos": 0, "bloqueios": 0, "cobrancas": 0, "falhas": 0, "processados": 0}

    avisos = bloqueios = cobrancas = falhas = processados = 0
    hoje = datetime.now(fuso_br).date()

    for _, cli in clientes.iterrows():
        email = str(cli.get("email", "")).strip().lower()
        if not email:
            continue
        if usuario_master_protegido(email):
            continue
        processados += 1
        nome = str(cli.get("nome", email))
        plano = str(cli.get("plano", "mensal"))
        vencimento = str(cli.get("vencimento", ""))
        valor = float(cli.get("valor_estimado", 0.0) or 0.0)
        ativo = bool(cli.get("ativo", 0))
        try:
            venc_data = datetime.strptime(vencimento, "%Y-%m-%d").date()
            dias = int((venc_data - hoje).days)
        except Exception:
            registrar_automacao("erro_data", email, nome, plano, vencimento, None, "validar_vencimento", "ERRO", "Data de vencimento inválida.")
            falhas += 1
            continue

        if ativo and dias in [7, 3, 0]:
            try:
                resultado = processar_aviso_cobranca(dict(cli), dias, enviar_emails, criar_cobranca_auto)
                avisos += resultado['avisos']
                cobrancas += resultado['cobrancas']
                falhas += resultado['falhas']
            except Exception as exc:
                logging.getLogger(__name__).warning("Aviso comercial não confirmado: %s", type(exc).__name__)
                falhas += 1

        # Bloqueio automático após vencimento.
        if ativo and dias < 0 and bloquear_vencidos:
            tipo = "bloqueio_vencido"
            if not automacao_ja_executada_hoje(tipo, email):
                ok_block, msg_block = bloquear_cliente_db(email, False)
                if ok_block:
                    bloqueios += 1
                    registrar_automacao(tipo, email, nome, plano, vencimento, dias, "bloquear_cliente", "OK", msg_block)
                    if enviar_emails:
                        assunto = "Acesso bloqueado por vencimento - Smart Money AI"
                        corpo = mensagem_aviso_vencimento(nome, email, plano, vencimento, dias, valor)
                        ok_mail, msg_mail = enviar_email(email, assunto, corpo)
                        registrar_automacao("email_bloqueio", email, nome, plano, vencimento, dias, "enviar_email", "OK" if ok_mail else "ERRO", msg_mail)
                        if not ok_mail:
                            falhas += 1
                else:
                    registrar_automacao(tipo, email, nome, plano, vencimento, dias, "bloquear_cliente", "IGNORADO", msg_block)

    return {"avisos": avisos, "bloqueios": bloqueios, "cobrancas": cobrancas, "falhas": falhas, "processados": processados}


def obter_cliente_db(email: str) -> dict | None:
    init_clientes_db()
    email = (email or "").strip().lower()
    conn = sqlite3.connect(CLIENTES_DB)
    cur = conn.cursor()
    cur.execute("SELECT email, nome, senha_hash, vencimento, ativo, plano FROM clientes WHERE email = ?", (email,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"email": row[0], "nome": row[1], "senha": row[2], "vencimento": row[3], "ativo": bool(row[4]), "plano": row[5]}


def remover_cliente_db(email: str) -> None:
    init_clientes_db()
    conn = sqlite3.connect(CLIENTES_DB)
    cur = conn.cursor()
    cur.execute("DELETE FROM clientes WHERE email = ?", ((email or "").strip().lower(),))
    conn.commit()
    conn.close()


def bloquear_cliente_db(email: str, ativo: bool) -> tuple[bool, str]:
    init_clientes_db()
    email = (email or "").strip().lower()
    # Conta master/admin nunca pode ser bloqueada por teste, vencimento ou automação.
    if not ativo and usuario_master_protegido(email):
        return False, "Conta master/admin protegida: não pode ser bloqueada."
    conn = sqlite3.connect(CLIENTES_DB)
    cur = conn.cursor()
    cur.execute("UPDATE clientes SET ativo=?, atualizado_em=? WHERE email=?", (1 if ativo else 0, datetime.now(fuso_br).isoformat(), email))
    conn.commit()
    conn.close()
    return True, "Cliente ativado." if ativo else "Cliente bloqueado."


def renovar_cliente_dias(email: str, dias: int = 30) -> tuple[bool, str]:
    """Renova o cliente adicionando dias ao vencimento atual.
    Se o cliente estiver vencido, conta a renovação a partir de hoje.
    """
    init_clientes_db()
    email = (email or "").strip().lower()
    if not email:
        return False, "E-mail vazio."
    cliente = obter_cliente_db(email)
    if not cliente:
        return False, "Cliente não encontrado."
    hoje = datetime.now(fuso_br).date()
    try:
        venc_atual = datetime.strptime(str(cliente.get("vencimento", "")), "%Y-%m-%d").date()
    except Exception:
        venc_atual = hoje
    base = venc_atual if venc_atual >= hoje else hoje
    novo_venc = base + timedelta(days=int(dias or 30))
    conn = sqlite3.connect(CLIENTES_DB)
    cur = conn.cursor()
    cur.execute(
        "UPDATE clientes SET vencimento=?, ativo=1, atualizado_em=? WHERE email=?",
        (str(novo_venc), datetime.now(fuso_br).isoformat(), email),
    )
    conn.commit()
    conn.close()
    return True, str(novo_venc)



def seed_clientes_from_secrets() -> None:
    """Copia usuários do Secrets para o banco local na primeira execução.
    Depois você pode administrar clientes na aba Admin sem editar Secrets.
    """
    try:
        usuarios = _secret_dict("USERS")
        vencimentos = _secret_dict("ACCESS_EXPIRES")
        df = listar_clientes_db()
        existentes = set(df["email"].str.lower()) if not df.empty else set()
        for email, senha in usuarios.items():
            email = str(email).strip().lower()
            if email not in existentes:
                upsert_cliente(email=email, nome=email, senha=str(senha), vencimento=str(vencimentos.get(email, "2099-12-31")), plano="admin/base", ativo=True)
    except Exception:
        pass


def cliente_autorizado(email: str) -> dict | None:
    """Banco é autoritativo; não ressuscita usuários removidos via USERS."""
    email = (email or "").strip().lower()
    return obter_cliente_db(email) if email else None


def usuario_e_admin(email: str) -> bool:
    """Somente ADMINS explícito concede privilégio administrativo."""
    try:
        admins = st.secrets.get("ADMINS", [])
        if not isinstance(admins, (list, tuple)):
            return False
        return bool(email) and email.strip().lower() in {str(a).strip().lower() for a in admins}
    except Exception:
        return False


def usuario_master_protegido(email: str) -> bool:
    """Define a conta master/admin que nunca pode ser bloqueada por vencimento.
    Regra: qualquer e-mail em ADMINS ou o primeiro usuário de [USERS].
    """
    return usuario_e_admin(email)


def usuario_logado_e_admin() -> bool:
    """Retorna True somente quando o usuário logado é admin/master.
    Usado para travar visualmente e funcionalmente áreas comerciais/admin.
    """
    return usuario_e_admin(st.session_state.get("usuario_logado", ""))


def admin_signal_access_unlimited(email: str) -> bool:
    """Libera geração oficial sem consumo somente para admin autenticado.

    A fonte autoritativa continua sendo ADMINS; session_state isolado não concede
    privilégio sem restauração/validação da sessão persistente.
    """
    email = str(email or "").strip().lower()
    if not email:
        return False
    try:
        return bool(restaurar_login_persistente()) and usuario_e_admin(email)
    except Exception:
        return False


def bloquear_area_admin_para_cliente() -> None:
    """Barreira extra para qualquer bloco administrativo.
    Mesmo se algum fluxo interno tentar renderizar a área, cliente comum é bloqueado.
    """
    if not usuario_logado_e_admin():
        st.error("Acesso não autorizado. Esta área é exclusiva da administração.")
        st.stop()


def _secret_dict(nome: str) -> dict:
    try:
        valor = st.secrets.get(nome, {})
        return dict(valor) if valor else {}
    except Exception:
        return {}



# =============================
# SUPABASE / BANCO PERSISTENTE
# =============================
# Mantém o SQLite como fallback de emergência, mas usa Supabase quando
# SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (ou SUPABASE_ANON_KEY) estiverem no Secrets.

def supabase_configurado() -> bool:
    try:
        return bool(str(st.secrets.get("SUPABASE_URL", "")).strip() and (
            str(st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "")).strip()
            or str(st.secrets.get("SUPABASE_ANON_KEY", "")).strip()
        ))
    except Exception:
        return False


def _supabase_base_url() -> str:
    url = str(st.secrets.get("SUPABASE_URL", "")).strip().rstrip("/")
    return f"{url}/rest/v1"


def _supabase_key() -> str:
    return str(st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "") or st.secrets.get("SUPABASE_ANON_KEY", "")).strip()


def _supabase_service_role_key() -> str:
    """Backend-only credential required by service-role-only tables."""
    return str(st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "")).strip()


def _supabase_headers(prefer: str | None = None) -> dict:
    key = _supabase_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _sb_get(table: str, params: dict | None = None) -> list[dict]:
    if not supabase_configurado():
        raise RuntimeError("Supabase não configurado")
    url = f"{_supabase_base_url()}/{table}"
    r = _http_request("GET", url, headers=_supabase_headers(), params=params or {}, timeout=25)
    if r.status_code >= 400:
        raise RuntimeError(f"Supabase GET {table}: {r.status_code} {r.text[:300]}")
    return r.json() if r.text else []


def _sb_post(table: str, payload: dict, params: dict | None = None, prefer: str = "return=representation") -> list[dict]:
    if not supabase_configurado():
        raise RuntimeError("Supabase não configurado")
    url = f"{_supabase_base_url()}/{table}"
    r = _http_request("POST", url, headers=_supabase_headers(prefer), params=params or {}, json=payload, timeout=25)
    if r.status_code >= 400:
        raise RuntimeError(f"Supabase POST {table}: {r.status_code} {r.text[:300]}")
    return r.json() if r.text else []


def _sb_patch(table: str, payload: dict, params: dict, prefer: str = "return=representation") -> list[dict]:
    if not supabase_configurado():
        raise RuntimeError("Supabase não configurado")
    url = f"{_supabase_base_url()}/{table}"
    r = _http_request("PATCH", url, headers=_supabase_headers(prefer), params=params, json=payload, timeout=25)
    if r.status_code >= 400:
        raise RuntimeError(f"Supabase PATCH {table}: {r.status_code} {r.text[:300]}")
    return r.json() if r.text else []


def _sb_delete(table: str, params: dict) -> None:
    if not supabase_configurado():
        raise RuntimeError("Supabase não configurado")
    url = f"{_supabase_base_url()}/{table}"
    r = _http_request("DELETE", url, headers=_supabase_headers(), params=params, timeout=25)
    if r.status_code >= 400:
        raise RuntimeError(f"Supabase DELETE {table}: {r.status_code} {r.text[:300]}")


def _sb_rpc(funcao: str, payload: dict | None = None) -> dict:
    if not supabase_configurado():
        raise RuntimeError("Supabase não configurado")
    url = f"{_supabase_base_url()}/rpc/{funcao}"
    r = _http_request("POST", url, headers=_supabase_headers(), json=payload or {}, timeout=25)
    if r.status_code >= 400:
        raise RuntimeError(f"Supabase RPC {funcao}: {r.status_code}")
    dados = r.json() if r.text else {}
    if isinstance(dados, list):
        return dados[0] if dados else {}
    return dados if isinstance(dados, dict) else {"resultado": dados}


CARTEIRA_FREEMIUM_PLANOS = {
    "pacote_5": {"nome": "5 sinais", "preco": 14.90, "creditos": 5, "tipo": "CREDITOS"},
    "pacote_10": {"nome": "10 sinais", "preco": 24.90, "creditos": 10, "tipo": "CREDITOS"},
    "pacote_20": {"nome": "20 sinais", "preco": 42.90, "creditos": 20, "tipo": "CREDITOS"},
    "pacote_40": {"nome": "40 sinais", "preco": 74.90, "creditos": 40, "tipo": "CREDITOS"},
    "ilimitado_mensal": {"nome": "Ilimitado mensal", "preco": 99.90, "creditos": 0, "tipo": "ILIMITADO_MENSAL"},
    "ilimitado_anual": {"nome": "Ilimitado anual", "preco": 1119.90, "creditos": 0, "tipo": "ILIMITADO_ANUAL"},
}


def carteira_tipo_plano(plano: str) -> str:
    return str(CARTEIRA_FREEMIUM_PLANOS.get(str(plano or "").strip().lower(), {}).get("tipo", "GRATIS"))


def carteira_preco_plano(plano: str) -> float:
    return float(CARTEIRA_FREEMIUM_PLANOS.get(str(plano or "").strip().lower(), {}).get("preco", 0.0) or 0.0)


def carteira_creditos_plano(plano: str) -> int:
    return int(CARTEIRA_FREEMIUM_PLANOS.get(str(plano or "").strip().lower(), {}).get("creditos", 0) or 0)


def carteira_resumo_usuario(email: str) -> dict:
    email = str(email or "").strip().lower()
    padrao = {"plano_acesso": "GRÁTIS", "gratuito_hoje": "Indisponível", "saldo_creditos": 0, "ilimitado_ativo": False, "ilimitado_ate": "", "total_consumido": 0}
    if not email:
        return padrao
    try:
        dados = _sb_rpc("sma_carteira_resumo", {"p_email": email}) or {}
        resumo = {**padrao, **dados}
        creditos_comprados = int(dados.get("creditos_comprados", dados.get("saldo_creditos", 0)) or 0)
        gratuito_disponivel = int(dados.get("gratuito_disponivel", 0) or 0)
        resumo["saldo_creditos"] = creditos_comprados
        resumo["gratuito_hoje"] = "Disponível" if gratuito_disponivel > 0 else "Indisponível"
        resumo["plano_acesso"] = "ILIMITADO" if bool(dados.get("ilimitado_ativo")) else "GRÁTIS"
        resumo["ilimitado_ate"] = dados.get("ilimitado_fim", dados.get("ilimitado_ate", "")) if bool(dados.get("ilimitado_ativo")) else ""
        resumo["total_consumido"] = int(dados.get("total_consumido", 0) or 0)
        return resumo
    except Exception:
        return {**padrao, "erro": "RESUMO_INDISPONIVEL"}


def carteira_reservar_sinal(email: str, request_id: str) -> dict:
    event_id = obter_event_id_fluxo("carteira")
    log_evento_operacional(event_id, "carteira.reservar", "start")
    email = str(email or "").strip().lower()
    request_id = str(request_id or "").strip()
    if not email or not request_id:
        log_evento_operacional(event_id, "carteira.reservar", "fail")
        return {"ok": False, "status": "BLOQUEADO", "mensagem": "Carteira indisponível para reservar sinal."}
    if admin_signal_access_unlimited(email):
        log_evento_operacional(event_id, "carteira.reservar", "ok")
        return {
            "ok": True,
            "status": "RESERVADO",
            "origem": "ADMIN_NO_CHARGE",
            "request_id": request_id,
            "delta_credito": 0,
            "admin_signal_access": "UNLIMITED",
        }
    try:
        resultado = _sb_rpc("sma_carteira_reservar_sinal", {"p_email": email, "p_request_id": request_id, "p_origem": "streamlit"})
        log_evento_operacional(event_id, "carteira.reservar", "ok" if resultado.get("ok") else "fail")
        return resultado
    except Exception as exc:
        log_evento_operacional(event_id, "carteira.reservar", "error", exc)
        return {"ok": False, "status": "FAIL_CLOSED", "mensagem": "Não foi possível confirmar saldo no servidor. Sinal não gerado."}


def carteira_confirmar_sinal(request_id: str, id_sinal: str = "") -> dict:
    event_id = obter_event_id_fluxo("carteira")
    log_evento_operacional(event_id, "carteira.confirmar", "start")
    try:
        resultado = _sb_rpc("sma_carteira_confirmar_sinal", {"p_request_id": str(request_id or ""), "p_id_sinal": str(id_sinal or "")})
        log_evento_operacional(event_id, "carteira.confirmar", "ok" if resultado.get("ok") else "fail")
        return resultado
    except Exception as exc:
        log_evento_operacional(event_id, "carteira.confirmar", "error", exc)
        return {"ok": False, "status": "CONFIRMACAO_FALHOU", "mensagem": "Reserva não confirmada."}


def carteira_estornar_sinal(request_id: str, motivo: str = "") -> dict:
    event_id = obter_event_id_fluxo("carteira")
    log_evento_operacional(event_id, "carteira.estornar", "start")
    try:
        resultado = _sb_rpc("sma_carteira_estornar_sinal", {"p_request_id": str(request_id or ""), "p_motivo": str(motivo or "")[:180]})
        log_evento_operacional(event_id, "carteira.estornar", "ok" if resultado.get("ok") else "fail")
        return resultado
    except Exception as exc:
        log_evento_operacional(event_id, "carteira.estornar", "error", exc)
        return {"ok": False, "status": "ESTORNO_FALHOU", "mensagem": "Estorno não confirmado."}


def signal_v2_shadow_enabled() -> bool:
    try:
        valor = st.secrets.get("SIGNAL_V2_SHADOW_ENABLED", "false")
    except Exception:
        valor = "false"
    return str(valor or "").strip().lower() in {"1", "true", "yes", "on", "sim"}


def registrar_shadow_v2_sinal(decisao: dict, df_sinal: pd.DataFrame, *, mercado: str, symbol: str, timeframe: str, usuario: str, v1_latency_ms: float | None = None) -> dict:
    event_id = obter_event_id_fluxo("live_shadow")
    log_evento_operacional(event_id, "live_shadow", "start")
    if not signal_v2_shadow_enabled():
        log_evento_operacional(event_id, "live_shadow", "disabled")
        return {"ok": True, "status": "shadow_disabled"}
    try:
        from signal_engine.live_shadow import V2_CONFIG_HASH, run_live_shadow
        if V2_CONFIG_HASH != "792460b7":
            return {"ok": False, "status": "shadow_error", "error_code": "BLOCKED_CONFIG_CHANGED"}
        resultado_shadow = run_live_shadow(
            enabled=True,
            insert_func=_sb_post,
            official_signal_id=str(decisao.get("id_sinal") or ""),
            user_id=str(usuario or decisao.get("usuario") or "").strip().lower(),
            market=mercado,
            symbol=symbol,
            timeframe=timeframe,
            v1_decision=decisao,
            candles=df_sinal,
            v1_latency_ms=v1_latency_ms,
        )
        log_evento_operacional(event_id, "live_shadow", str(resultado_shadow.get("status") or "ok"))
        return resultado_shadow
    except Exception as exc:
        log_evento_operacional(event_id, "live_shadow", "error", exc)
        logging.getLogger(__name__).warning("shadow_v2_isolado_falhou: %s", type(exc).__name__)
        return {"ok": False, "status": "shadow_error", "error_code": type(exc).__name__}


QUANT_SHADOW_SUPPORTED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"}


def parse_quant_shadow_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "sim"}


def resolve_quant_shadow_flag() -> tuple[bool, str]:
    missing = object()
    try:
        valor = st.secrets.get("R03_QUANT_SHADOW_ENABLED", missing)
    except Exception:
        valor = missing
    if valor is missing:
        valor = os.getenv("R03_QUANT_SHADOW_ENABLED")
        source = "ENV" if valor is not None else "DEFAULT"
    else:
        source = "STREAMLIT_SECRET"
    return parse_quant_shadow_flag(valor), source


def quant_shadow_production_enabled() -> bool:
    return resolve_quant_shadow_flag()[0]


def quant_shadow_normalize_gate(mercado: str, symbol: str, timeframe: str) -> tuple[str, str, str]:
    market_aliases = {"cripto": "Cripto", "crypto": "Cripto"}
    symbol_aliases = {
        "BITCOIN": "BTCUSDT", "BTC": "BTCUSDT", "BTCUSDT": "BTCUSDT", "BTC/USDT": "BTCUSDT", "BTC-USDT": "BTCUSDT",
        "ETHEREUM": "ETHUSDT", "ETH": "ETHUSDT", "ETHUSDT": "ETHUSDT", "ETH/USDT": "ETHUSDT", "ETH-USDT": "ETHUSDT",
        "BNB": "BNBUSDT", "BNBUSDT": "BNBUSDT", "BNB/USDT": "BNBUSDT", "BNB-USDT": "BNBUSDT",
        "SOLANA": "SOLUSDT", "SOL": "SOLUSDT", "SOLUSDT": "SOLUSDT", "SOL/USDT": "SOLUSDT", "SOL-USDT": "SOLUSDT",
        "XRP": "XRPUSDT", "XRPUSDT": "XRPUSDT", "XRP/USDT": "XRPUSDT", "XRP-USDT": "XRPUSDT",
    }
    timeframe_aliases = {"1m": "1m", "1M": "1m", "1min": "1m", "1 minuto": "1m", "60": "1m"}
    return (
        market_aliases.get(str(mercado or "").strip().lower(), str(mercado or "").strip()),
        symbol_aliases.get(str(symbol or "").strip().upper(), str(symbol or "").strip().upper()),
        timeframe_aliases.get(str(timeframe or "").strip(), str(timeframe or "").strip()),
    )


def quant_shadow_eligible(mercado: str, symbol: str, timeframe: str) -> bool:
    market, canonical_symbol, canonical_timeframe = quant_shadow_normalize_gate(mercado, symbol, timeframe)
    return market == "Cripto" and canonical_timeframe == "1m" and canonical_symbol in QUANT_SHADOW_SUPPORTED_SYMBOLS


def buscar_candles_binance_quant_shadow(symbol: str, limit: int = 2400) -> pd.DataFrame:
    """Busca histórico exclusivo do Shadow; usa somente candles 1m completamente fechados."""
    endpoints = [
        "https://api.binance.com/api/v3/klines",
        "https://api1.binance.com/api/v3/klines",
        "https://api2.binance.com/api/v3/klines",
        "https://api3.binance.com/api/v3/klines",
        "https://data-api.binance.vision/api/v3/klines",
    ]
    pages, remaining, end_time = [], int(limit), None
    headers = {"User-Agent": "Mozilla/5.0 Streamlit-Quant-Shadow/1.0"}
    while remaining > 0:
        page_limit = min(1000, remaining)
        params = {"symbol": symbol, "interval": "1m", "limit": page_limit}
        if end_time is not None:
            params["endTime"] = end_time
        data = None
        for url in endpoints:
            try:
                response = _HTTP_SESSION.get(url, params=params, headers=headers, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    break
            except Exception:
                continue
        if not data:
            raise RuntimeError("QUANT_SHADOW_MARKET_DATA_UNAVAILABLE")
        pages.extend(data)
        remaining -= len(data)
        end_time = int(data[0][0]) - 1
        if len(data) < page_limit:
            break
    frame = pd.DataFrame(pages, columns=[
        "Open time", "Open", "High", "Low", "Close", "Volume", "Close time",
        "Quote asset volume", "Number of trades", "Taker buy base", "Taker buy quote", "Ignore",
    ])
    frame = frame.drop_duplicates(subset=["Open time"]).sort_values("Open time")
    for col in ["Open", "High", "Low", "Close", "Volume", "Quote asset volume", "Number of trades", "Taker buy base", "Taker buy quote"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["Open time"] = pd.to_datetime(frame["Open time"], unit="ms", utc=True)
    frame["Close time"] = pd.to_datetime(frame["Close time"], unit="ms", utc=True)
    frame = frame[frame["Close time"] <= pd.Timestamp.now(tz="UTC")].tail(limit).reset_index(drop=True)
    if len(frame) < 2160:
        raise RuntimeError("QUANT_SHADOW_HISTORY_INSUFFICIENT")
    return frame


def _executar_quant_shadow_isolado(decisao: dict, *, symbol: str, timeframe: str, event_id: str, request_id: str,
                                   supabase_base_url: str, supabase_key: str) -> dict:
    logging.getLogger(__name__).warning(
        "R03_SHADOW_STARTED symbol=%s timeframe=%s event_id=%s request_id=%s",
        symbol, timeframe, event_id, request_id,
    )
    try:
        from signal_engine.shadow_quant.adapter import run_shadow_observer
        from signal_engine.shadow_quant.contracts import ShadowContext

        candles = buscar_candles_binance_quant_shadow(symbol)
        context = ShadowContext(
            official_signal_id=str(decisao.get("id_sinal") or ""), event_id=str(event_id or ""),
            symbol=symbol, timestamp=pd.Timestamp(candles["Open time"].iloc[-1]), timeframe=timeframe,
            candles=candles, request_id=str(request_id or ""),
        )
        started = time.perf_counter()

        persisted_record = {}

        def persist(record: dict) -> None:
            record["v2_latency_ms"] = round((time.perf_counter() - started) * 1000, 6)
            try:
                logging.getLogger(__name__).warning(
                    "R03_SHADOW_PERSIST_START event_id=%s request_id=%s symbol=%s timeframe=%s",
                    event_id, request_id, symbol, timeframe,
                )
                response = _http_request(
                    "POST", f"{supabase_base_url}/sma_signal_shadow",
                    headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}",
                             "Content-Type": "application/json", "Prefer": "resolution=ignore-duplicates,return=representation"},
                    params={"on_conflict": "official_signal_id,v2_engine_version"},
                    json=record, timeout=25,
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"Supabase POST sma_signal_shadow: {response.status_code} {response.text[:300]}")
                persisted_record.update(record)
                try:
                    saved_rows = response.json()
                    if isinstance(saved_rows, list) and saved_rows:
                        persisted_record["id"] = saved_rows[0].get("id")
                except Exception:
                    pass
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "R03_SHADOW_PERSISTENCE_ERROR error_code=%s detail=%s event_id=%s request_id=%s symbol=%s timeframe=%s",
                    type(exc).__name__, sanitizar_erro_observabilidade(str(exc)), event_id, request_id, symbol, timeframe,
                )
                raise

        _, observation = run_shadow_observer(decisao, context, enabled=True, persist=persist)
        if persisted_record:
            observation["_persisted_record"] = persisted_record
        if observation.get("status") == "shadow_success":
            logging.getLogger(__name__).warning("R03_SHADOW_MODEL_LOADED event_id=%s", event_id)
            logging.getLogger(__name__).warning("R03_SHADOW_FEATURES_READY event_id=%s feature_count=54", event_id)
            logging.getLogger(__name__).warning("R03_SHADOW_INFERENCE_OK event_id=%s", event_id)
        if observation.get("persistence_status") == "persisted":
            logging.getLogger(__name__).warning(
                "R03_SHADOW_PERSISTED event_id=%s request_id=%s symbol=%s timeframe=%s",
                event_id, request_id, symbol, timeframe,
            )
        elif observation.get("persistence_status") != "persisted":
            logging.getLogger(__name__).warning(
                "R03_SHADOW_ERROR stage=inference_or_persistence error_type=%s event_id=%s request_id=%s symbol=%s timeframe=%s",
                observation.get("error_code") or observation.get("persistence_error_code") or "UNKNOWN",
                event_id, request_id, symbol, timeframe,
            )
        logging.getLogger(__name__).info(
            "quant_shadow status=%s persistence=%s event_id=%s",
            observation.get("status"), observation.get("persistence_status"), event_id,
        )
        return observation
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "R03_SHADOW_ERROR stage=worker error_type=%s detail=%s event_id=%s request_id=%s symbol=%s timeframe=%s",
            type(exc).__name__, sanitizar_erro_observabilidade(str(exc)), event_id, request_id, symbol, timeframe,
        )
        logging.getLogger(__name__).warning("quant_shadow_fail_open error=%s event_id=%s", type(exc).__name__, event_id)
        return {"status": "shadow_error", "error_code": type(exc).__name__}


_R03_EVALUATION_SCHEDULED = set()
_R03_EVALUATION_LOCK = threading.Lock()


def _executar_avaliacao_quant_shadow_isolada(record: dict, *, supabase_base_url: str, supabase_key: str) -> dict:
    from signal_engine.shadow_quant.lifecycle import candles_for_record, evaluate_pending_record

    candles = buscar_candles_binance_quant_shadow(str(record.get("symbol") or ""))
    by_stage, closed = candles_for_record(record, candles)
    patch = evaluate_pending_record(record, by_stage, closed)
    if not patch or "evaluated_at" not in patch:
        return {"status": "pending"}
    identity_params = {"evaluated_at": "is.null"}
    if record.get("id") is not None:
        identity_params["id"] = f"eq.{record['id']}"
    else:
        identity_params["official_signal_id"] = f"eq.{record['official_signal_id']}"
        identity_params["v2_engine_version"] = f"eq.{record['v2_engine_version']}"
    response = _http_request(
        "PATCH", f"{supabase_base_url}/sma_signal_shadow",
        headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}",
                 "Content-Type": "application/json", "Prefer": "return=representation"},
        params=identity_params, json=patch, timeout=25,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"R03_EVALUATION_PATCH_FAILED:{response.status_code}")
    logging.getLogger(__name__).warning(
        "R03_SHADOW_EVALUATED id=%s resultado_official=%s resultado_shadow=%s",
        record.get("id"), patch["resultado_official"], patch["resultado_shadow"],
    )
    return {"status": "evaluated", **patch}


def _agendar_avaliacao_quant_shadow_isolada(record: dict, *, supabase_base_url: str, supabase_key: str) -> None:
    key = str(record.get("id") or record.get("official_signal_id") or "")
    with _R03_EVALUATION_LOCK:
        if not key or key in _R03_EVALUATION_SCHEDULED:
            return
        _R03_EVALUATION_SCHEDULED.add(key)

    def worker():
        try:
            p3 = pd.Timestamp(record["p3_iso"])
            if p3.tzinfo is None:
                p3 = p3.tz_localize("UTC")
            wait_seconds = max(0.0, (p3.tz_convert("UTC") + pd.Timedelta(minutes=1) - pd.Timestamp.now(tz="UTC")).total_seconds())
            if wait_seconds:
                time.sleep(wait_seconds)
            for _ in range(6):
                result = _executar_avaliacao_quant_shadow_isolada(record, supabase_base_url=supabase_base_url, supabase_key=supabase_key)
                if result.get("status") == "evaluated":
                    return
                time.sleep(15)
        except Exception as exc:
            logging.getLogger(__name__).warning("R03_SHADOW_EVALUATION_ERROR error_code=%s id=%s", type(exc).__name__, record.get("id"))
        finally:
            with _R03_EVALUATION_LOCK:
                _R03_EVALUATION_SCHEDULED.discard(key)

    threading.Thread(target=worker, name=f"r03-eval-{key}", daemon=True).start()


def agendar_quant_shadow(decisao: dict, *, mercado: str, symbol: str, timeframe: str, event_id: str, request_id: str) -> dict:
    enabled, flag_source = resolve_quant_shadow_flag()
    market, canonical_symbol, canonical_timeframe = quant_shadow_normalize_gate(mercado, symbol, timeframe)
    eligible = market == "Cripto" and canonical_timeframe == "1m" and canonical_symbol in QUANT_SHADOW_SUPPORTED_SYMBOLS
    skip_reason = None
    if not enabled:
        skip_reason = "FLAG_DISABLED"
    elif market != "Cripto":
        skip_reason = "MARKET_INELIGIBLE"
    elif canonical_symbol not in QUANT_SHADOW_SUPPORTED_SYMBOLS:
        skip_reason = "ASSET_INELIGIBLE"
    elif canonical_timeframe != "1m":
        skip_reason = "TIMEFRAME_INELIGIBLE"
    elif not event_id or not request_id:
        skip_reason = "MISSING_EVENT_ID"
    logging.getLogger(__name__).warning(
        "R03_SHADOW_GATE event_id=%s request_id=%s market_raw=%s market=%s asset_raw=%s asset=%s timeframe_raw=%s timeframe=%s flag_effective=%s eligible=%s skip_reason=%s",
        event_id, request_id, mercado, market, symbol, canonical_symbol, timeframe, canonical_timeframe,
        str(enabled).lower(), str(eligible and skip_reason is None).lower(), skip_reason or "NONE",
    )
    logging.getLogger(__name__).warning(
        "R03_FLAG_SOURCE source=%s effective=%s event_id=%s", flag_source, str(enabled).lower(), event_id,
    )
    if skip_reason:
        logging.getLogger(__name__).warning(
            "R03_SHADOW_SKIP reason=%s event_id=%s request_id=%s", skip_reason, event_id, request_id,
        )
    if not enabled:
        return {"status": "shadow_disabled"}
    if not eligible or skip_reason:
        return {"status": "shadow_not_eligible"}
    symbol, timeframe = canonical_symbol, canonical_timeframe
    try:
        supabase_base_url = _supabase_base_url()
        supabase_key = _supabase_service_role_key()
        logging.getLogger(__name__).warning(
            "R03_SUPABASE_CONFIG supabase_url_present=%s service_role_present=%s key_source=SERVICE_ROLE event_id=%s",
            str(bool(supabase_base_url and not supabase_base_url.startswith("/"))).lower(),
            str(bool(supabase_key)).lower(), event_id,
        )
        if not supabase_base_url or not supabase_key:
            raise RuntimeError("SERVICE_ROLE_MISSING" if not supabase_key else "SUPABASE_URL_MISSING")
        logging.getLogger(__name__).warning(
            "R03_SHADOW_SCHEDULED symbol=%s timeframe=%s event_id=%s request_id=%s model_version=%s",
            symbol, timeframe, event_id, request_id, "r03-quant-shadow-r02.1-20261002",
        )
        observation = _executar_quant_shadow_isolado(
            dict(decisao), symbol=symbol, timeframe=timeframe, event_id=event_id, request_id=request_id,
            supabase_base_url=supabase_base_url, supabase_key=supabase_key,
        )
        persisted_record = observation.pop("_persisted_record", None)
        if observation.get("persistence_status") == "persisted" and persisted_record:
            _agendar_avaliacao_quant_shadow_isolada(
                persisted_record, supabase_base_url=supabase_base_url, supabase_key=supabase_key,
            )
        return {"status": "shadow_completed", "shadow_status": observation.get("status")}
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "R03_SHADOW_ERROR stage=schedule error_type=%s detail=%s event_id=%s request_id=%s symbol=%s timeframe=%s",
            type(exc).__name__, sanitizar_erro_observabilidade(str(exc)), event_id, request_id, symbol, timeframe,
        )
        logging.getLogger(__name__).warning("quant_shadow_schedule_fail_open error=%s event_id=%s", type(exc).__name__, event_id)
        return {"status": "shadow_error", "error_code": type(exc).__name__}


@st.fragment(run_every="60s")
def _r03_shadow_evaluation_tick():
    if not quant_shadow_production_enabled():
        return
    base_url, service_key = _supabase_base_url(), _supabase_service_role_key()
    if not base_url or not service_key:
        return
    try:
        response = _http_request(
            "GET", f"{base_url}/sma_signal_shadow",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            params={"select": "*", "or": "(resultado_official.eq.PENDENTE,resultado_shadow.eq.PENDENTE)",
                    "shadow_status": "eq.shadow_success", "order": "created_at.asc", "limit": "20"}, timeout=25,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"R03_EVALUATION_LOAD_FAILED:{response.status_code}")
        for record in response.json():
            _agendar_avaliacao_quant_shadow_isolada(record, supabase_base_url=base_url, supabase_key=service_key)
    except Exception as exc:
        logging.getLogger(__name__).warning("R03_SHADOW_EVALUATION_SCAN_ERROR error_code=%s", type(exc).__name__)


_r03_shadow_evaluation_tick()


def carteira_criar_solicitacao(email: str, nome: str, plano: str, metodo_pagamento: str = "PIX", observacao: str = "") -> tuple[bool, str]:
    plano = str(plano or "").strip().lower()
    if plano not in CARTEIRA_FREEMIUM_PLANOS:
        return False, "Plano de carteira inválido."
    if not supabase_configurado():
        return False, "Supabase não configurado. Solicitação não registrada."
    payload = {
        "email": str(email or "").strip().lower(),
        "nome": nome or email,
        "plano": plano,
        "tipo": carteira_tipo_plano(plano),
        "quantidade": carteira_creditos_plano(plano),
        "valor": carteira_preco_plano(plano),
        "status": "PAGAMENTO_PENDENTE",
        "metodo_pagamento": metodo_pagamento or "PIX",
        "observacao": observacao or "Solicitação de carteira freemium.",
    }
    try:
        rows = _sb_post("sma_carteira_compras", payload)
        sid = rows[0].get("id") if rows else ""
        return True, f"Solicitação registrada. Aguarde confirmação do pagamento. ID: {sid}"
    except Exception:
        return False, "Solicitação não confirmada no servidor."


def carteira_admin_confirmar_compra(compra_id: int) -> tuple[bool, str]:
    if not st.session_state.get("authenticated") or not usuario_e_admin(st.session_state.get("usuario_logado", "")):
        return False, "Acesso exclusivo da administração."
    try:
        dados = _sb_rpc("sma_carteira_confirmar_compra", {"p_compra_id": int(compra_id), "p_admin_email": str(st.session_state.get("usuario_logado", "")).strip().lower()})
        return bool(dados.get("ok")), str(dados.get("mensagem") or dados.get("status") or "Confirmação processada.")
    except Exception:
        return False, "Confirmação não processada no servidor."




MERCADOPAGO_CHECKOUT_PRODUCAO_URL = "https://tigccvvuemverlbczpoh.supabase.co/functions/v1/mercadopago-checkout"


def mercado_pago_checkout_criar(produto: str, nova_tentativa: bool = False) -> tuple[bool, str, dict]:
    """Cria checkout Mercado Pago usando somente a sessão opaca Smart Money AI já autenticada."""
    event_id = obter_event_id_fluxo("checkout")
    log_evento_operacional(event_id, "checkout", "start")
    if not restaurar_login_persistente():
        log_evento_operacional(event_id, "checkout", "auth_required")
        return False, "Faça login novamente para comprar sinais.", {}
    produto = str(produto or "").strip().lower()
    if produto not in CARTEIRA_FREEMIUM_PLANOS:
        log_evento_operacional(event_id, "checkout", "invalid_product")
        return False, "Produto inválido.", {}
    auth_token = str(st.session_state.get("auth_token", "") or "")
    auth_binding = str(st.session_state.get("auth_binding", "") or "")
    session_token = str(st.session_state.get("session_token", "") or "")
    usuario = str(st.session_state.get("usuario_logado", "") or "").strip().lower()
    if not auth_token or not auth_binding or not usuario:
        log_evento_operacional(event_id, "checkout", "invalid_session")
        return False, "Sessão inválida. Faça login novamente.", {}
    ambiente = str(st.secrets.get("SMA_ENV", "producao")).strip().lower()
    endpoint_configurado = str(st.secrets.get("MERCADOPAGO_CHECKOUT_URL", "")).strip()
    if ambiente in {"homologacao", "staging", "sandbox"} and not endpoint_configurado:
        log_evento_operacional(event_id, "checkout", "staging_endpoint_missing")
        return False, "Checkout de homologação não configurado.", {}
    endpoint = endpoint_configurado or MERCADOPAGO_CHECKOUT_PRODUCAO_URL
    if ambiente in {"homologacao", "staging", "sandbox"} and "tigccvvuemverlbczpoh" in endpoint:
        log_evento_operacional(event_id, "checkout", "production_endpoint_blocked")
        return False, "Checkout de produção bloqueado neste ambiente.", {}
    if "otqloermcjoposokoprg" in endpoint or not endpoint.startswith("https://tigccvvuemverlbczpoh.supabase.co/functions/v1/mercadopago-checkout"):
        log_evento_operacional(event_id, "checkout", "endpoint_blocked")
        return False, "Checkout Mercado Pago indisponível.", {}
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "x-sma-user": usuario,
        "x-sma-binding": auth_binding,
        "x-sma-session": session_token,
        "x-sma-fingerprint": fingerprint_dispositivo(),
    }
    try:
        r = _http_request("POST", endpoint, headers=headers, json={"produto": produto, "nova_tentativa": bool(nova_tentativa)}, timeout=35)
        dados = r.json() if r.text else {}
        if r.status_code >= 400 or not dados.get("ok"):
            log_evento_operacional(event_id, "checkout", "external_error")
            return False, "Não foi possível iniciar o checkout Mercado Pago. Tente novamente em instantes.", {}
        checkout_url = str(dados.get("init_point") or dados.get("checkout_url") or "").strip()
        if not checkout_url.startswith("https://"):
            log_evento_operacional(event_id, "checkout", "invalid_response")
            return False, "Checkout Mercado Pago não retornou link válido.", {}
        log_evento_operacional(event_id, "checkout", "ok")
        return True, "Checkout Mercado Pago criado. Abra o link para concluir o pagamento.", {**dados, "checkout_url": checkout_url}
    except Exception as exc:
        log_evento_operacional(event_id, "checkout", "error", exc)
        return False, "Não foi possível iniciar o checkout Mercado Pago. Tente novamente em instantes.", {}

# =============================
# SITE DE COMPRA / VENDAS
# =============================
def site_compra_url() -> str:
    """URL pública da página de vendas/compra do Smart Money AI.
    Pode ser sobrescrita no Streamlit Secrets com COMPRA_URL ou SALES_URL.
    """
    try:
        url = str(st.secrets.get("COMPRA_URL", st.secrets.get("SALES_URL", "https://smart-money-ai-4.streamlit.app/"))).strip()
    except Exception:
        url = "https://smart-money-ai-4.streamlit.app/"
    return url or "https://smart-money-ai-4.streamlit.app/"


def link_site_compra(ref: str = "") -> str:
    """Monta o link de compra. Quando houver afiliado, adiciona ?ref=CODIGO.
    Se o link já tiver parâmetros, usa &ref=.
    """
    url = site_compra_url().strip()
    ref = normalizar_codigo_afiliado(ref) if ref else ""
    if not ref:
        return url
    separador = "&" if "?" in url else "?"
    return f"{url}{separador}ref={urllib.parse.quote(ref)}"


# =============================
# AFILIADOS / INDICAÇÕES
# =============================
def normalizar_codigo_afiliado(valor: str) -> str:
    """Gera um código simples, seguro e compatível com URL."""
    valor = (valor or "").strip().lower()
    valor = valor.replace("@", "_").replace(".", "_")
    permitido = []
    for ch in valor:
        if ch.isalnum():
            permitido.append(ch)
        elif ch in ["_", "-", " "]:
            permitido.append("_")
    codigo = "".join(permitido).strip("_")
    while "__" in codigo:
        codigo = codigo.replace("__", "_")
    return codigo[:40] or f"afiliado_{uuid.uuid4().hex[:8]}"


def codigo_afiliado_cliente(email: str, nome: str = "") -> str:
    base = nome or email.split("@")[0] if email else ""
    codigo = normalizar_codigo_afiliado(base)
    if not codigo or codigo == "afiliado":
        codigo = normalizar_codigo_afiliado(email)
    return codigo


def link_afiliado_cliente(codigo: str) -> str:
    """Link do afiliado apontando para a página pública de compra/vendas."""
    codigo = normalizar_codigo_afiliado(codigo)
    return link_site_compra(codigo)


def capturar_codigo_indicacao_url() -> None:
    """Captura ?ref=CODIGO e mantém na sessão até o cadastro/renovação."""
    try:
        ref = st.query_params.get("ref", "")
        if isinstance(ref, list):
            ref = ref[0] if ref else ""
        ref = normalizar_codigo_afiliado(str(ref))
        if ref:
            st.session_state["codigo_indicacao"] = ref
    except Exception:
        try:
            params = st.experimental_get_query_params()
            ref = params.get("ref", [""])[0]
            ref = normalizar_codigo_afiliado(str(ref))
            if ref:
                st.session_state["codigo_indicacao"] = ref
        except Exception:
            pass


def garantir_afiliado_cliente(email: str, nome: str = "") -> dict:
    """Cria ou atualiza o registro de afiliado de um cliente."""
    email = (email or "").strip().lower()
    nome = (nome or email).strip()
    if not email or not supabase_configurado():
        return {}
    try:
        existente = _sb_get("afiliados", params={"select": "*", "email": f"eq.{email}", "limit": "1"})
        agora = datetime.now(fuso_br).isoformat()
        if existente:
            row = existente[0]
            if not row.get("codigo"):
                codigo = codigo_afiliado_cliente(email, nome)
                _sb_patch("afiliados", {"codigo": codigo, "usuario": nome, "atualizado_em": agora}, params={"email": f"eq.{email}"})
                row["codigo"] = codigo
            return row

        codigo_base = codigo_afiliado_cliente(email, nome)
        codigo = codigo_base
        tentativa = 1
        while _sb_get("afiliados", params={"select": "id", "codigo": f"eq.{codigo}", "limit": "1"}):
            tentativa += 1
            codigo = f"{codigo_base}_{tentativa}"

        payload = {
            "usuario": nome,
            "email": email,
            "codigo": codigo,
            "indicacoes": 0,
            "comissao_pct": 10,
            "saldo_estimado": 0,
            "ativo": True,
            "criado_em": agora,
            "atualizado_em": agora,
        }
        rows = _sb_post("afiliados", payload)
        return rows[0] if rows else payload
    except Exception:
        return {}


def obter_afiliado_por_codigo(codigo: str) -> dict:
    codigo = normalizar_codigo_afiliado(codigo)
    if not codigo or not supabase_configurado():
        return {}
    try:
        dados = _sb_get("afiliados", params={"select": "*", "codigo": f"eq.{codigo}", "limit": "1"})
        return dados[0] if dados else {}
    except Exception:
        return {}


def registrar_indicacao_afiliado(codigo: str, novo_email: str, novo_nome: str = "", plano: str = "", valor: float = 0.0, status: str = "PENDENTE") -> tuple[bool, str]:
    """Registra indicação evitando duplicidade por novo_email."""
    codigo = normalizar_codigo_afiliado(codigo)
    novo_email = (novo_email or "").strip().lower()
    if not codigo:
        return False, "Código de afiliado vazio."
    if not novo_email:
        return False, "E-mail indicado vazio."
    if not supabase_configurado():
        return False, "Supabase não configurado."

    try:
        afiliado = obter_afiliado_por_codigo(codigo)
        if not afiliado:
            return False, "Código de afiliado não encontrado."
        if str(afiliado.get("email", "")).strip().lower() == novo_email:
            return False, "O cliente não pode indicar o próprio e-mail."

        existente = _sb_get("indicacoes", params={"select": "id", "novo_email": f"eq.{novo_email}", "limit": "1"})
        if existente:
            return False, "Este e-mail já possui indicação registrada."

        agora = datetime.now(fuso_br).isoformat()
        payload = {
            "afiliado_codigo": codigo,
            "afiliado_email": afiliado.get("email", ""),
            "novo_usuario": novo_nome or novo_email,
            "novo_email": novo_email,
            "plano": plano or "",
            "valor": float(valor or 0),
            "status": status or "PENDENTE",
            "criado_em": agora,
            "atualizado_em": agora,
        }
        _sb_post("indicacoes", payload, prefer="return=minimal")

        indicacoes_atual = int(afiliado.get("indicacoes") or 0) + 1
        saldo_atual = float(afiliado.get("saldo_estimado") or 0)
        comissao_pct = float(afiliado.get("comissao_pct") or 10)
        saldo_novo = saldo_atual + (float(valor or 0) * comissao_pct / 100.0)
        _sb_patch(
            "afiliados",
            {"indicacoes": indicacoes_atual, "saldo_estimado": saldo_novo, "atualizado_em": agora},
            params={"codigo": f"eq.{codigo}"}
        )
        return True, "Indicação registrada com sucesso."
    except Exception as e:
        return False, f"Erro ao registrar indicação: {e}"


def listar_afiliados_db() -> pd.DataFrame:
    if not supabase_configurado():
        return pd.DataFrame()
    try:
        dados = _sb_get("afiliados", params={"select": "*", "order": "indicacoes.desc,atualizado_em.desc"})
        return pd.DataFrame(dados)
    except Exception as e:
        st.warning(f"Não foi possível listar afiliados: {e}")
        return pd.DataFrame()


def listar_indicacoes_db() -> pd.DataFrame:
    if not supabase_configurado():
        return pd.DataFrame()
    try:
        dados = _sb_get("indicacoes", params={"select": "*", "order": "criado_em.desc"})
        return pd.DataFrame(dados)
    except Exception as e:
        st.warning(f"Não foi possível listar indicações: {e}")
        return pd.DataFrame()


# =============================
# DEMONSTRAÇÕES / RASTREAMENTO DE CONVIDADOS
# =============================
PAGINAS_VISITADAS_FILE = Path("paginas_visitadas.csv")


def registrar_pagina_visitada(usuario: str, pagina: str, detalhe: str = "") -> None:
    """Registra eventos importantes de navegação/demonstração.
    Observação: em Streamlit, tabs são renderizadas no servidor mesmo sem clique;
    por isso registramos eventos reais: login, entrada na plataforma e ações principais.
    """
    usuario = (usuario or "").strip().lower()
    pagina = (pagina or "").strip()
    if not usuario or not pagina:
        return
    try:
        ctx = obter_contexto_cliente()
    except Exception:
        ctx = {}
    agora = datetime.now(fuso_br)
    # IMPORTANTE: no Supabase a coluna data_hora é timestamptz.
    # Por isso enviamos ISO, não DD/MM/AAAA, para evitar erro 400.
    registro = {
        "usuario": usuario,
        "pagina": pagina,
        "detalhe": detalhe or "",
        "data_hora": agora.isoformat(),
        "data_hora_iso": agora.isoformat(),
        "dispositivo": fingerprint_dispositivo(),
        "navegador": str(ctx.get("user_agent", "indisponivel"))[:180],
    }
    try:
        if supabase_configurado():
            try:
                _sb_post("paginas_visitadas", registro, prefer="return=minimal")
                return
            except Exception:
                # Compatibilidade: se a tabela ainda não tiver a coluna detalhe,
                # tenta gravar sem ela em vez de perder o evento.
                registro_sem_detalhe = {k: v for k, v in registro.items() if k != "detalhe"}
                _sb_post("paginas_visitadas", registro_sem_detalhe, prefer="return=minimal")
                return
    except Exception:
        pass
    # Fallback local em CSV se Supabase falhar.
    registro["data_hora"] = agora.strftime("%d/%m/%Y %H:%M:%S")
    registros = carregar_lista_csv(PAGINAS_VISITADAS_FILE)
    registros.insert(0, registro)
    salvar_lista_csv(PAGINAS_VISITADAS_FILE, registros[:5000])


def listar_paginas_visitadas_db(limite: int = 500) -> pd.DataFrame:
    """Lista eventos de demonstração e páginas/eventos visitados."""
    try:
        if supabase_configurado():
            dados = _sb_get(
                "paginas_visitadas",
                params={"select": "*", "order": "data_hora_iso.desc", "limit": str(int(limite))},
            )
            return pd.DataFrame(dados)
    except Exception as e:
        st.warning(f"Não foi possível consultar paginas_visitadas no Supabase: {e}")
    df = pd.DataFrame(carregar_lista_csv(PAGINAS_VISITADAS_FILE))
    if df.empty:
        return df
    return df.head(int(limite))


def limpar_paginas_visitadas_db() -> tuple[bool, str]:
    """Limpa os eventos de demonstração/páginas visitadas."""
    try:
        if supabase_configurado():
            _sb_delete("paginas_visitadas", params={"id": "gte.0"})
            return True, "Histórico de demonstrações limpo com sucesso."
        salvar_lista_csv(PAGINAS_VISITADAS_FILE, [])
        return True, "Histórico local de demonstrações limpo com sucesso."
    except Exception as e:
        return False, f"Erro ao limpar demonstrações: {e}"


def registrar_evento_demo_unico(pagina: str, detalhe: str = "") -> None:
    """Evita gravar o mesmo evento repetidamente a cada rerun do Streamlit."""
    usuario = st.session_state.get("usuario_logado", "")
    chave = f"demo_evt_{normalizar_codigo_afiliado(pagina)}"
    if usuario and not st.session_state.get(chave):
        registrar_pagina_visitada(usuario, pagina, detalhe)
        st.session_state[chave] = True



def email_notificacao_demo() -> str:
    """E-mail que receberá aviso quando um convidado DEMO acessar.
    Ordem de prioridade no Streamlit Secrets:
    DEMO_NOTIFY_EMAIL -> ADMIN_NOTIFY_EMAIL -> SMTP_FROM -> SMTP_USER -> primeiro ADMINS.
    """
    try:
        for chave in ["DEMO_NOTIFY_EMAIL", "ADMIN_NOTIFY_EMAIL", "SMTP_FROM", "SMTP_USER"]:
            valor = str(st.secrets.get(chave, "")).strip()
            if valor and "@" in valor:
                return valor
        admins = st.secrets.get("ADMINS", [])
        for adm in admins:
            adm = str(adm).strip()
            if "@" in adm:
                return adm
    except Exception:
        pass
    return ""



def whatsapp_demo_configurado() -> bool:
    """Verifica se a notificação por WhatsApp via CallMeBot está configurada nos Secrets.
    Configure:
    DEMO_WHATSAPP_PHONE="5521999999999"
    DEMO_WHATSAPP_APIKEY="SUA_APIKEY"
    """
    try:
        phone = str(st.secrets.get("DEMO_WHATSAPP_PHONE", "")).strip()
        apikey = str(st.secrets.get("DEMO_WHATSAPP_APIKEY", "")).strip()
        return bool(phone and apikey)
    except Exception:
        return False


def enviar_whatsapp_callmebot(mensagem: str) -> tuple[bool, str]:
    """Envia WhatsApp via CallMeBot.
    Não interrompe o app caso falhe.
    """
    try:
        phone = str(st.secrets.get("DEMO_WHATSAPP_PHONE", "")).strip()
        apikey = str(st.secrets.get("DEMO_WHATSAPP_APIKEY", "")).strip()
        if not phone or not apikey:
            return False, "WhatsApp não configurado. Configure DEMO_WHATSAPP_PHONE e DEMO_WHATSAPP_APIKEY."

        phone = "".join(ch for ch in phone if ch.isdigit())
        if not phone:
            return False, "Número de WhatsApp inválido."

        texto = urllib.parse.quote(mensagem or "")
        url = f"https://api.callmebot.com/whatsapp.php?phone={phone}&text={texto}&apikey={urllib.parse.quote(apikey)}"
        r = requests.get(url, timeout=20)
        if 200 <= r.status_code < 300:
            return True, "WhatsApp enviado com sucesso."
        return False, f"Falha CallMeBot {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"Erro ao enviar WhatsApp: {e}"


def _telegram_alertas_config() -> tuple[str, str, bool]:
    """Lê configuração do Telegram nos Secrets.

    Aceita os nomes novos TELEGRAM_* e os nomes antigos DEMO_TELEGRAM_*.
    Também aceita quando o usuário colocou as chaves por engano dentro de [USERS]
    no TOML do Streamlit Secrets. Isso evita o problema de salvar assim:

    [USERS]
    "admin@email.com"="senha"
    TELEGRAM_BOT_TOKEN="..."

    Nesse caso o Streamlit interpreta TELEGRAM_BOT_TOKEN como parte da seção USERS.
    """
    def _secret_value(*nomes: str) -> str:
        for nome in nomes:
            try:
                valor = st.secrets.get(nome, "")
                if valor not in [None, ""]:
                    return str(valor).strip()
            except Exception:
                pass
        # fallback: chaves colocadas acidentalmente dentro de [USERS]
        for secao in ["USERS", "TELEGRAM", "telegram"]:
            try:
                bloco = st.secrets.get(secao, {})
                if bloco:
                    bloco = dict(bloco)
                    for nome in nomes:
                        valor = bloco.get(nome, "")
                        if valor not in [None, ""]:
                            return str(valor).strip()
            except Exception:
                pass
        return ""

    def _secret_bool(nome: str, padrao: bool = True) -> bool:
        valor = _secret_value(nome)
        if valor == "":
            return padrao
        return str(valor).strip().lower() not in ["false", "0", "no", "não", "nao", "off", "desativado"]

    try:
        enabled = _secret_bool("TELEGRAM_ALERTS_ENABLED", True)
        token = _secret_value("TELEGRAM_BOT_TOKEN", "DEMO_TELEGRAM_BOT_TOKEN")
        chat_id = _secret_value("TELEGRAM_CHAT_ID", "DEMO_TELEGRAM_CHAT_ID")
        return token, chat_id, enabled
    except Exception:
        return "", "", False


def telegram_demo_configurado() -> bool:
    """Verifica se a notificação por Telegram está configurada nos Secrets.
    Aceita:
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
    ou
    DEMO_TELEGRAM_BOT_TOKEN / DEMO_TELEGRAM_CHAT_ID
    """
    token, chat_id, enabled = _telegram_alertas_config()
    return bool(enabled and token and chat_id)


def enviar_telegram_demo(mensagem: str) -> tuple[bool, str]:
    """Envia aviso por Telegram. Não interrompe o app caso falhe."""
    try:
        token, chat_id, enabled = _telegram_alertas_config()
        if not enabled:
            return False, "Telegram desativado em TELEGRAM_ALERTS_ENABLED."
        if not token or not chat_id:
            return False, "Telegram não configurado. Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nos Secrets."

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": mensagem or "",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=20)
        if 200 <= r.status_code < 300:
            return True, "Telegram enviado com sucesso."
        return False, f"Falha Telegram {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return False, f"Erro ao enviar Telegram: {e}"


def cliente_e_demo(cliente: dict | None) -> bool:
    """Identifica cliente DEMO/convidado sem depender apenas da coluna tipo_usuario."""
    if not cliente:
        return False
    tipo = normalizar_tipo_usuario(cliente.get("tipo_usuario", ""))
    plano = str(cliente.get("plano", "")).strip().lower()
    return tipo == "DEMO" or plano in ["demo", "demonstração", "demonstracao", "teste"]


def notificar_admin_acesso_demo(usuario: str, cliente: dict | None = None) -> tuple[bool, str]:
    """Envia aviso quando um convidado DEMO acessa a plataforma.
    Envia por e-mail quando DEMO_NOTIFY_EMAIL estiver configurado, por WhatsApp
    quando DEMO_WHATSAPP_PHONE + DEMO_WHATSAPP_APIKEY estiverem no Secrets,
    e por Telegram quando DEMO_TELEGRAM_BOT_TOKEN + DEMO_TELEGRAM_CHAT_ID estiverem no Secrets.
    A função nunca bloqueia o login caso alguma notificação falhe.
    """
    if not cliente_e_demo(cliente):
        return False, "Usuário não é DEMO."

    try:
        ctx = obter_contexto_cliente()
    except Exception:
        ctx = {}

    agora_txt = datetime.now(fuso_br).strftime("%d/%m/%Y %H:%M:%S")
    nome = (cliente or {}).get("nome") or usuario
    plano = (cliente or {}).get("plano") or "demo"
    vencimento = formatar_data_br((cliente or {}).get("vencimento", ""))
    dispositivo = fingerprint_dispositivo()
    navegador = str(ctx.get("user_agent", "indisponivel"))[:300]
    ip = str(ctx.get("ip", "indisponivel"))

    assunto = "👀 Convidado DEMO acessou o Smart Money AI"
    corpo = f"""Olá!

Um convidado DEMO acabou de acessar o Smart Money AI.

Nome: {nome}
E-mail/Login: {usuario}
Plano: {plano}
Vencimento do demo: {vencimento}
Data/Hora: {agora_txt}
Dispositivo: {dispositivo}
IP: {ip}
Navegador: {navegador}

Acompanhe o histórico em:
Admin → Demonstrações / convidados

Smart Money AI
"""

    msg_whats = f"""🚨 Novo acesso DEMO - Smart Money AI

Nome: {nome}
Login: {usuario}
Plano: {plano}
Vencimento: {vencimento}
Horário: {agora_txt}
Dispositivo: {dispositivo}
IP: {ip}"""

    resultados = []

    destinatario = email_notificacao_demo()
    if destinatario:
        ok_email, msg_email = enviar_email(destinatario, assunto, corpo)
        resultados.append(("E-mail", ok_email, msg_email))

    if whatsapp_demo_configurado():
        ok_wpp, msg_wpp = enviar_whatsapp_callmebot(msg_whats)
        resultados.append(("WhatsApp", ok_wpp, msg_wpp))

    if telegram_demo_configurado():
        ok_tg, msg_tg = enviar_telegram_demo(msg_whats)
        resultados.append(("Telegram", ok_tg, msg_tg))

    if not resultados:
        resultados.append(("Notificação", False, "Nenhum canal configurado. Configure DEMO_NOTIFY_EMAIL, DEMO_WHATSAPP_PHONE/DEMO_WHATSAPP_APIKEY ou DEMO_TELEGRAM_BOT_TOKEN/DEMO_TELEGRAM_CHAT_ID."))

    try:
        resumo = " | ".join([f"{canal}: {'OK' if ok else 'ERRO'} - {msg[:80]}" for canal, ok, msg in resultados])
        registrar_pagina_visitada(usuario, "Notificação Demo", resumo)
    except Exception:
        pass

    algum_ok = any(ok for _, ok, _ in resultados)
    return algum_ok, " | ".join([f"{canal}: {msg}" for canal, _, msg in resultados])



def limpar_solicitacoes_renovacao_db() -> tuple[bool, str]:
    """Remove todas as solicitações de renovação via PIX do Supabase."""
    if not supabase_configurado():
        return False, "Supabase não configurado."
    try:
        _sb_delete("solicitacoes_renovacao", params={"id": "gte.0"})
        return True, "Lista de solicitações de renovação via PIX limpa com sucesso."
    except Exception as e:
        return False, f"Erro ao limpar solicitações de renovação: {e}"


def limpar_historico_cobrancas_db() -> tuple[bool, str]:
    """Remove todo o histórico de cobranças."""
    try:
        if supabase_configurado():
            _sb_delete("cobrancas", params={"id": "gte.0"})
            return True, "Histórico de cobranças limpo com sucesso."
        init_cobrancas_db()
        conn = sqlite3.connect(COBRANCAS_DB)
        cur = conn.cursor()
        cur.execute("DELETE FROM cobrancas")
        conn.commit()
        conn.close()
        return True, "Histórico de cobranças local limpo com sucesso."
    except Exception as e:
        return False, f"Erro ao limpar histórico de cobranças: {e}"


def excluir_afiliado_db(codigo_ou_email: str, apagar_indicacoes: bool = True) -> tuple[bool, str]:
    """Exclui um afiliado pelo código ou e-mail. Opcionalmente remove indicações ligadas a ele."""
    if not supabase_configurado():
        return False, "Supabase não configurado."
    chave = (codigo_ou_email or "").strip().lower()
    if not chave:
        return False, "Informe o código ou e-mail do afiliado."
    try:
        campo = "email" if "@" in chave else "codigo"
        rows = _sb_get("afiliados", params={"select": "*", campo: f"eq.{chave}", "limit": "1"})
        if not rows:
            return False, "Afiliado não encontrado."
        afiliado = rows[0]
        codigo = str(afiliado.get("codigo") or "").strip().lower()
        email = str(afiliado.get("email") or "").strip().lower()
        if apagar_indicacoes:
            if codigo:
                _sb_delete("indicacoes", params={"afiliado_codigo": f"eq.{codigo}"})
            if email:
                _sb_delete("indicacoes", params={"afiliado_email": f"eq.{email}"})
        _sb_delete("afiliados", params={campo: f"eq.{chave}"})
        return True, "Afiliado excluído com sucesso."
    except Exception as e:
        return False, f"Erro ao excluir afiliado: {e}"


# Captura links de indicação vindos da jornada comercial interna.
try:
    capturar_codigo_indicacao_url()
except Exception:
    pass




def _bool_db(v) -> bool:
    """Converte valores vindos do Supabase/SQLite para booleano real.
    Corrige o erro: name '_bool_db' is not defined.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in ["1", "true", "sim", "yes", "ativo"]


def normalizar_tipo_usuario(valor: str) -> str:
    """Padroniza tipo de usuário para separar cliente real, demo e admin."""
    tipo = str(valor or "CLIENTE").strip().upper()
    aliases = {
        "TESTE": "DEMO",
        "DEMONSTRACAO": "DEMO",
        "DEMONSTRAÇÃO": "DEMO",
        "CONVIDADO": "DEMO",
        "CLIENTE DEMO": "DEMO",
        "ADMINISTRADOR": "ADMIN",
    }
    return aliases.get(tipo, tipo if tipo in ["CLIENTE", "DEMO", "ADMIN", "AFILIADO"] else "CLIENTE")


def clientes_tem_coluna_tipo_usuario() -> bool:
    """Detecta se a coluna tipo_usuario já existe no Supabase.
    Se ainda não existir, o app continua funcionando sem quebrar.
    """
    if not supabase_configurado():
        return False
    try:
        _sb_get("clientes", params={"select": "tipo_usuario", "limit": "1"})
        return True
    except Exception:
        return False


def valor_cliente_para_receita(plano: str, tipo_usuario: str = "CLIENTE") -> float:
    """Demos e admins não entram na receita estimada."""
    tipo = normalizar_tipo_usuario(tipo_usuario)
    plano_norm = str(plano or "").strip().lower()
    if tipo in ["DEMO", "ADMIN"] or plano_norm in ["demo", "demonstração", "demonstracao", "teste"]:
        return 0.0
    return float(_precos_planos_padrao().get(plano_norm, 0.0) or 0.0)


def diagnosticar_supabase() -> dict:
    """Teste rápido de conexão com o Supabase para evitar salvar em SQLite sem perceber."""
    info = {
        "configurado": False,
        "url": "",
        "key_detectada": False,
        "clientes_ok": False,
        "cobrancas_ok": False,
        "automacoes_ok": False,
        "erro": "",
    }
    try:
        url = str(st.secrets.get("SUPABASE_URL", "")).strip()
        key = _supabase_key()
        info["url"] = url
        info["key_detectada"] = bool(key)
        info["configurado"] = bool(url and key)
        if not info["configurado"]:
            info["erro"] = "SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY ausente nos Secrets."
            return info
        _sb_get("clientes", params={"select": "email", "limit": "1"})
        info["clientes_ok"] = True
        _sb_get("cobrancas", params={"select": "id", "limit": "1"})
        info["cobrancas_ok"] = True
        _sb_get("automacoes_log", params={"select": "id", "limit": "1"})
        info["automacoes_ok"] = True
    except Exception as e:
        info["erro"] = str(e)
    return info


def exigir_supabase_operacional() -> None:
    """Se Supabase estiver configurado, não deixa o app cair silenciosamente no SQLite."""
    if not supabase_configurado():
        return
    diag = diagnosticar_supabase()
    if not (diag.get("clientes_ok") and diag.get("cobrancas_ok") and diag.get("automacoes_ok")):
        st.error("⚠️ Supabase configurado, mas a conexão/tabelas falharam. Corrija antes de cadastrar clientes.")
        st.code(str(diag), language="text")
        st.stop()


def atualizar_senha_cliente_db(email: str, nova_senha: str, hash_anterior: str | None = None) -> tuple[bool, str]:
    email = (email or "").strip().lower()
    valido, mensagem = validar_senha_nova(nova_senha)
    if not valido:
        return False, mensagem
    try:
        cliente = cliente_autorizado(email)
        if not cliente:
            return False, "Conta não disponível."
        anterior = hash_anterior if hash_anterior is not None else str(cliente["senha"])
        if not _gravar_hash_confirmado(email, _hash_senha(nova_senha), anterior):
            return False, "Troca de senha não confirmada; os dados podem ter mudado."
        _revogar_acessos_usuario(email)
        return True, "Senha atualizada e sessões antigas invalidadas. Faça login novamente."
    except Exception as exc:
        logging.getLogger(__name__).warning("Atualização de senha não confirmada: %s", type(exc).__name__)
        return False, "Não foi possível confirmar todas as etapas. Faça login novamente ou recupere o acesso."


# Guardamos referências ao SQLite original para fallback.
_init_clientes_db_sqlite = init_clientes_db
_upsert_cliente_sqlite = upsert_cliente
_listar_clientes_db_sqlite = listar_clientes_db
_init_cobrancas_db_sqlite = init_cobrancas_db
_criar_cobranca_sqlite = criar_cobranca
_listar_cobrancas_sqlite = listar_cobrancas
_atualizar_status_cobranca_sqlite = atualizar_status_cobranca
_remover_cobranca_sqlite = remover_cobranca
_obter_cliente_db_sqlite = obter_cliente_db
_remover_cliente_db_sqlite = remover_cliente_db
_bloquear_cliente_db_sqlite = bloquear_cliente_db
_renovar_cliente_dias_sqlite = renovar_cliente_dias
_init_automacoes_db_sqlite = init_automacoes_db
_registrar_automacao_sqlite = registrar_automacao
_automacao_ja_executada_hoje_sqlite = automacao_ja_executada_hoje
_listar_automacoes_log_sqlite = listar_automacoes_log


def init_clientes_db() -> None:
    if not supabase_configurado():
        return _init_clientes_db_sqlite()
    return None


def upsert_cliente(email: str, nome: str, senha: str | None, vencimento: str, plano: str = "mensal", ativo: bool = True, tipo_usuario: str = "CLIENTE") -> None:
    if not supabase_configurado():
        return _upsert_cliente_sqlite(email, nome, senha, vencimento, plano, ativo)
    email = (email or "").strip().lower()
    if not email:
        return
    tipo_usuario = normalizar_tipo_usuario(tipo_usuario)
    try:
        if usuario_master_protegido(email):
            ativo = True
            vencimento = "2099-12-31"
            plano = plano or "admin/base"
            tipo_usuario = "ADMIN"
    except Exception:
        pass
    agora_iso = datetime.now(fuso_br).isoformat()
    existente = obter_cliente_db(email)
    if not existente or senha is not None:
        valido, mensagem = validar_senha_nova(senha or "")
        if not valido:
            raise ValueError(mensagem)
    senha_hash = existente.get("senha") if existente and senha is None else _hash_senha(senha)
    criado_em = existente.get("criado_em") if existente else agora_iso
    payload = {
        "email": email,
        "nome": nome or email,
        "senha_hash": senha_hash,
        "vencimento": str(vencimento or "2099-12-31"),
        "ativo": bool(ativo),
        "plano": plano or "mensal",
        "criado_em": criado_em or agora_iso,
        "atualizado_em": agora_iso,
    }
    if clientes_tem_coluna_tipo_usuario():
        payload["tipo_usuario"] = tipo_usuario
    _sb_post("clientes", payload, params={"on_conflict": "email"}, prefer="resolution=merge-duplicates,return=representation")


def listar_clientes_db() -> pd.DataFrame:
    if not supabase_configurado():
        return _listar_clientes_db_sqlite()
    try:
        dados = _sb_get("clientes", params={"select": "*", "order": "atualizado_em.desc"})
        df = pd.DataFrame(dados)
        cols_base = ["email", "nome", "vencimento", "ativo", "plano", "tipo_usuario", "criado_em", "atualizado_em"]
        if df.empty:
            return pd.DataFrame(columns=cols_base)
        if "tipo_usuario" not in df.columns:
            df["tipo_usuario"] = df["plano"].astype(str).str.lower().apply(lambda p: "DEMO" if p in ["demo", "demonstração", "demonstracao", "teste"] else "CLIENTE")
        df["tipo_usuario"] = df["tipo_usuario"].apply(normalizar_tipo_usuario)
        df["ativo"] = df["ativo"].apply(_bool_db)
        return df[[c for c in cols_base if c in df.columns]]
    except Exception as e:
        raise RuntimeError("Não foi possível confirmar a base de clientes no Supabase.") from e


def obter_cliente_db(email: str) -> dict | None:
    if not supabase_configurado():
        return _obter_cliente_db_sqlite(email)
    email = (email or "").strip().lower()
    if not email:
        return None
    dados = _sb_get("clientes", params={"select": "*", "email": f"eq.{email}", "limit": "1"})
    if not dados:
        return None
    row = dados[0]
    return {
        "email": row.get("email"),
        "nome": row.get("nome"),
        "senha": row.get("senha_hash"),
        "senha_hash": row.get("senha_hash"),
        "vencimento": row.get("vencimento"),
        "ativo": _bool_db(row.get("ativo")),
        "plano": row.get("plano"),
        "tipo_usuario": normalizar_tipo_usuario(row.get("tipo_usuario") or ("DEMO" if str(row.get("plano", "")).lower() in ["demo", "teste", "demonstração", "demonstracao"] else "CLIENTE")),
        "criado_em": row.get("criado_em"),
        "atualizado_em": row.get("atualizado_em"),
    }


def remover_cliente_db(email: str) -> None:
    if not supabase_configurado():
        return _remover_cliente_db_sqlite(email)
    email = (email or "").strip().lower()
    if usuario_master_protegido(email):
        st.warning("Conta master/admin protegida: não pode ser removida.")
        return
    _sb_delete("clientes", params={"email": f"eq.{email}"})


def bloquear_cliente_db(email: str, ativo: bool) -> tuple[bool, str]:
    if not supabase_configurado():
        return _bloquear_cliente_db_sqlite(email, ativo)
    email = (email or "").strip().lower()
    if not ativo and usuario_master_protegido(email):
        return False, "Conta master/admin protegida: não pode ser bloqueada."
    _sb_patch("clientes", {"ativo": bool(ativo), "atualizado_em": datetime.now(fuso_br).isoformat()}, params={"email": f"eq.{email}"})
    return True, "Cliente ativado." if ativo else "Cliente bloqueado."


def renovar_cliente_dias(email: str, dias: int = 30) -> tuple[bool, str]:
    if not supabase_configurado():
        return _renovar_cliente_dias_sqlite(email, dias)
    email = (email or "").strip().lower()
    if not email:
        return False, "E-mail vazio."
    cliente = obter_cliente_db(email)
    if not cliente:
        return False, "Cliente não encontrado."
    hoje = datetime.now(fuso_br).date()
    try:
        venc_atual = datetime.strptime(str(cliente.get("vencimento", "")), "%Y-%m-%d").date()
    except Exception:
        venc_atual = hoje
    base = venc_atual if venc_atual >= hoje else hoje
    novo_venc = base + timedelta(days=int(dias or 30))
    _sb_patch("clientes", {"vencimento": str(novo_venc), "ativo": True, "atualizado_em": datetime.now(fuso_br).isoformat()}, params={"email": f"eq.{email}"})
    return True, str(novo_venc)


def init_cobrancas_db() -> None:
    if not supabase_configurado():
        return _init_cobrancas_db_sqlite()
    return None


def criar_cobranca(email: str, nome: str, plano: str, valor: float, vencimento_cobranca: str, observacao: str = "") -> int:
    if not supabase_configurado():
        return _criar_cobranca_sqlite(email, nome, plano, valor, vencimento_cobranca, observacao)
    agora_iso = datetime.now(fuso_br).isoformat()
    payload = {
        "email": (email or "").strip().lower(),
        "nome": nome or email,
        "plano": plano,
        "valor": float(valor or 0),
        "vencimento_cobranca": vencimento_cobranca,
        "status": "PENDENTE",
        "observacao": observacao or "",
        "criado_em": agora_iso,
        "atualizado_em": agora_iso,
    }
    rows = _sb_post("cobrancas", payload)
    return int(rows[0].get("id", 0)) if rows else 0


def listar_cobrancas() -> pd.DataFrame:
    if not supabase_configurado():
        return _listar_cobrancas_sqlite()
    dados = _sb_get("cobrancas", params={"select": "*", "order": "atualizado_em.desc,id.desc"})
    return pd.DataFrame(dados)


def atualizar_status_cobranca(cobranca_id: int, status: str, metodo: str = "", data_pagamento: str = "", observacao: str = "") -> None:
    if not supabase_configurado():
        return _atualizar_status_cobranca_sqlite(cobranca_id, status, metodo, data_pagamento, observacao)
    _sb_patch("cobrancas", {
        "status": status,
        "metodo": metodo or "",
        "data_pagamento": data_pagamento or "",
        "observacao": observacao or "",
        "atualizado_em": datetime.now(fuso_br).isoformat(),
    }, params={"id": f"eq.{int(cobranca_id)}"})


def remover_cobranca(cobranca_id: int) -> None:
    if not supabase_configurado():
        return _remover_cobranca_sqlite(cobranca_id)
    _sb_delete("cobrancas", params={"id": f"eq.{int(cobranca_id)}"})


def init_automacoes_db() -> None:
    if not supabase_configurado():
        return _init_automacoes_db_sqlite()
    return None


def registrar_automacao(tipo: str, email: str, nome: str, plano: str, vencimento: str, dias_restantes: int | None, acao: str, status: str, detalhe: str = "") -> None:
    if not supabase_configurado():
        return _registrar_automacao_sqlite(tipo, email, nome, plano, vencimento, dias_restantes, acao, status, detalhe)
    hoje = datetime.now(fuso_br).date().isoformat()
    payload = {
        "tipo": str(tipo),
        "email": (email or "").strip().lower(),
        "nome": nome or email,
        "plano": plano or "",
        "vencimento": vencimento or "",
        "dias_restantes": dias_restantes,
        "acao": acao,
        "status": status,
        "detalhe": detalhe or "",
        "data_ref": hoje,
        "criado_em": datetime.now(fuso_br).isoformat(),
    }
    _sb_post("automacoes_log", payload, prefer="return=minimal")


def automacao_ja_executada_hoje(tipo: str, email: str) -> bool:
    if not supabase_configurado():
        return _automacao_ja_executada_hoje_sqlite(tipo, email)
    hoje = datetime.now(fuso_br).date().isoformat()
    dados = _sb_get("automacoes_log", params={"select": "id", "tipo": f"eq.{tipo}", "email": f"eq.{(email or '').strip().lower()}", "data_ref": f"eq.{hoje}", "limit": "1"})
    return bool(dados)


def listar_automacoes_log(limite: int = 200) -> pd.DataFrame:
    if not supabase_configurado():
        return _listar_automacoes_log_sqlite(limite)
    dados = _sb_get("automacoes_log", params={"select": "*", "order": "criado_em.desc,id.desc", "limit": str(int(limite))})
    return pd.DataFrame(dados)


def migrar_sqlite_local_para_supabase() -> dict:
    """Migração manual: copia dados SQLite locais ainda existentes para o Supabase.
    Útil apenas no DEV/produção imediatamente após ativar Supabase.
    """
    if not supabase_configurado():
        return {"ok": False, "msg": "Supabase não configurado nos Secrets."}
    total_clientes = total_cobrancas = 0
    try:
        dfc = _listar_clientes_db_sqlite()
        for _, r in dfc.iterrows():
            email = str(r.get("email", "")).strip().lower()
            if not email:
                continue
            senha_hash = None
            try:
                antigo = _obter_cliente_db_sqlite(email)
                senha_hash = antigo.get("senha") if antigo else None
            except Exception:
                pass
            if not senha_hash:
                raise ValueError("Cliente sem hash de senha: migração interrompida sem criar senha padrão.")
            payload = {
                "email": email,
                "nome": r.get("nome") or email,
                "senha_hash": senha_hash,
                "vencimento": str(r.get("vencimento") or "2099-12-31"),
                "ativo": _bool_db(r.get("ativo", True)),
                "plano": r.get("plano") or "mensal",
                "criado_em": r.get("criado_em") or datetime.now(fuso_br).isoformat(),
                "atualizado_em": r.get("atualizado_em") or datetime.now(fuso_br).isoformat(),
            }
            _sb_post("clientes", payload, params={"on_conflict": "email"}, prefer="resolution=merge-duplicates,return=minimal")
            total_clientes += 1
    except Exception as e:
        return {"ok": False, "msg": f"Falha ao migrar clientes: {e}"}
    try:
        dfx = _listar_cobrancas_sqlite()
        for _, r in dfx.iterrows():
            payload = {
                "email": str(r.get("email", "")).strip().lower(),
                "nome": r.get("nome") or r.get("email"),
                "plano": r.get("plano") or "",
                "valor": float(r.get("valor") or 0),
                "vencimento_cobranca": r.get("vencimento_cobranca") or "",
                "data_pagamento": r.get("data_pagamento") or "",
                "metodo": r.get("metodo") or "",
                "status": r.get("status") or "PENDENTE",
                "observacao": r.get("observacao") or "",
                "criado_em": r.get("criado_em") or datetime.now(fuso_br).isoformat(),
                "atualizado_em": r.get("atualizado_em") or datetime.now(fuso_br).isoformat(),
            }
            _sb_post("cobrancas", payload, prefer="return=minimal")
            total_cobrancas += 1
    except Exception:
        pass
    return {"ok": True, "msg": f"Migração concluída: {total_clientes} clientes e {total_cobrancas} cobranças copiadas."}




# =============================
# SESSÕES ATIVAS PERSISTENTES / ANTI-COMPARTILHAMENTO
# =============================
def listar_sessoes_ativas_db() -> list[dict]:
    resultado = _seguranca_operar("listar_sessoes")
    if not resultado["ok"]:
        raise RuntimeError("Consulta de sessões indisponível.")
    return [{"usuario": row["usuario"], "token": row["sessao_id"], "dispositivo": row["fingerprint"],
             "last_seen_iso": datetime.fromtimestamp(row["visto_em"], fuso_br).isoformat(),
             "last_seen": datetime.fromtimestamp(row["visto_em"], fuso_br).strftime("%d/%m/%Y %H:%M:%S")}
            for row in resultado.get("registros", [])]


def salvar_sessao_ativa_db(registro: dict) -> None:
    raise RuntimeError("Sessões só podem ser emitidas após validar credenciais.")


def remover_sessao_ativa_db(usuario: str | None = None, token: str | None = None) -> None:
    atual = st.session_state.get("usuario_logado", "")
    admin = bool(st.session_state.get("authenticated")) and usuario_e_admin(atual)
    if not admin and (not token or token != st.session_state.get("session_token")):
        raise PermissionError("Remoção de sessão não autorizada.")
    if token:
        resultado = _seguranca_operar("revogar_sessao", {"sessao_id": token})
    elif usuario and admin:
        resultado = _seguranca_operar("revogar_usuario", {"usuario": usuario.strip().lower()})
    else:
        return
    if not resultado["ok"]:
        raise RuntimeError("Revogação não confirmada.")


def limpar_sessoes_ativas_db() -> None:
    if not st.session_state.get("authenticated") or not usuario_e_admin(st.session_state.get("usuario_logado", "")):
        raise PermissionError("Manutenção exclusiva da administração.")
    if not _seguranca_operar("limpar_sessoes")["ok"]:
        raise RuntimeError("Revogação global não confirmada.")


def limpar_sessoes_expiradas_db(ttl_minutos: int) -> None:
    if not _seguranca_operar("expirar")["ok"]:
        raise RuntimeError("Expiração de sessões não confirmada.")

def _senha_confere(senha_digitada: str, senha_salva: str) -> bool:
    if not senha_digitada or not senha_salva or len(senha_digitada) > 1024:
        return False
    try:
        if senha_salva.startswith("scrypt$"):
            _, n, r, p, salt, esperado = senha_salva.split("$")
            if (n, r, p) != ("131072", "8", "1") or len(salt) != 32 or len(esperado) != 64:
                return False
            digest = hashlib.scrypt(senha_digitada.encode("utf-8"), salt=bytes.fromhex(salt), n=131072, r=8, p=1, dklen=32, maxmem=268435456)
            return hmac.compare_digest(digest, bytes.fromhex(esperado))
        if senha_salva.startswith("sha256:"):
            digest = hashlib.sha256(senha_digitada.encode("utf-8")).hexdigest()
            return hmac.compare_digest("sha256:" + digest, senha_salva)
        # Compatibilidade temporária: senha simples antiga migra apenas após login válido.
        if senha_salva.startswith(("scrypt", "$argon", "pbkdf2$")):
            return False
        return hmac.compare_digest(senha_digitada.encode("utf-8"), senha_salva.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _data_valida(usuario: str, cliente: dict | None = None) -> tuple[bool, str]:
    if cliente is None:
        cliente = cliente_autorizado(usuario)
    if not cliente or not cliente.get("ativo"):
        return False, "Acesso não autorizado"
    venc = str(cliente.get("vencimento") or "")
    try:
        return datetime.now(fuso_br).date() <= datetime.strptime(venc, "%Y-%m-%d").date(), venc
    except (ValueError, TypeError):
        return False, venc


def obter_contexto_cliente() -> dict:
    """Coleta informações disponíveis do visitante para auditoria comercial.
    No Streamlit Cloud, IP real pode variar conforme proxy, mas ajuda a identificar compartilhamento.
    """
    ip = "indisponivel"
    user_agent = "indisponivel"
    try:
        headers = dict(getattr(st, "context", {}).headers) if hasattr(st, "context") else {}
        ip = (
            headers.get("x-forwarded-for")
            or headers.get("x-real-ip")
            or headers.get("cf-connecting-ip")
            or "indisponivel"
        )
        user_agent = headers.get("user-agent", "indisponivel")
        if isinstance(ip, str) and "," in ip:
            ip = ip.split(",")[0].strip()
    except Exception:
        pass
    return {"ip": ip, "user_agent": user_agent}


def fingerprint_dispositivo() -> str:
    ctx = obter_contexto_cliente()
    base = f"{ctx.get('ip')}|{ctx.get('user_agent')}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]

def _get_query_param(nome: str) -> str:
    try:
        valor = st.query_params.get(nome, "")
        if isinstance(valor, list):
            return str(valor[0]) if valor else ""
        return str(valor or "")
    except Exception:
        try:
            valor = st.experimental_get_query_params().get(nome, [""])
            return str(valor[0]) if valor else ""
        except Exception:
            return ""


def _set_query_param(nome: str, valor: str) -> None:
    try:
        if valor:
            st.query_params[nome] = valor
        else:
            try:
                del st.query_params[nome]
            except Exception:
                pass
    except Exception:
        try:
            if valor:
                st.experimental_set_query_params(**{nome: valor})
            else:
                st.experimental_set_query_params()
        except Exception:
            pass


def _agendar_sessao_browser(op: str, record: dict | None = None) -> None:
    st.session_state["_browser_session_command"] = {
        "request_id": str(uuid.uuid4()), "op": op, "record": record,
    }


def _sincronizar_sessao_browser() -> bool:
    """Hydrate untrusted credentials once; existing server validation still decides access."""
    if "_browser_session_command" not in st.session_state:
        _agendar_sessao_browser("read")
    command = st.session_state["_browser_session_command"]
    reply = browser_session_command(command)
    if not isinstance(reply, dict) or reply.get("request_id") != command["request_id"]:
        return False
    if st.session_state.get("_browser_session_ack") == command["request_id"]:
        return True
    st.session_state["_browser_session_ack"] = command["request_id"]
    if reply.get("status") != "ok":
        st.session_state["_browser_storage_unavailable"] = True
        return True  # In-memory login remains available; never bypass server checks.
    st.session_state["_browser_storage_unavailable"] = False
    if command["op"] == "read":
        record = parse_browser_record(reply.get("record"), time.time())
        if record:
            for field in ("usuario", "auth_token", "auth_binding", "session_token"):
                target = "usuario_logado" if field == "usuario" else field
                st.session_state[target] = record[field]
            st.session_state["_browser_session_expires_at"] = float(record["expires_at"])
            st.session_state.authenticated = False
            st.session_state["_browser_record_present"] = True
        elif reply.get("record") is not None:
            _agendar_sessao_browser("clear")
            st.rerun()
    elif command["op"] == "write":
        st.session_state["_browser_record_present"] = True
    elif command["op"] == "clear":
        st.session_state["_browser_record_present"] = False
    # Do not retain a second copy of bearer credentials in component arguments.
    if command["op"] == "write":
        _agendar_sessao_browser("read")
        st.rerun()
    return True


def _limpeza_browser_pendente() -> bool:
    command = st.session_state.get("_browser_session_command", {})
    return command.get("op") == "clear" and st.session_state.get("_browser_session_ack") != command.get("request_id")


def criar_login_persistente(usuario: str, hash_verificado: str, cliente_verificado: dict | None = None) -> str:
    """Existing session, persisted only for server-approved lifetime; never in URL or CSV."""
    event_id = obter_event_id_fluxo("auth")
    log_evento_operacional(event_id, "auth.login", "start")
    usuario = (usuario or "").strip().lower()
    cliente = cliente_verificado if cliente_verificado is not None else cliente_autorizado(usuario)
    if not cliente or not _data_valida(usuario, cliente)[0]:
        log_evento_operacional(event_id, "auth.login", "unauthorized")
        raise RuntimeError("Conta não autorizada.")
    if not hmac.compare_digest(str(cliente.get("senha") or ""), hash_verificado):
        log_evento_operacional(event_id, "auth.login", "credential_changed")
        raise RuntimeError("Credencial alterada durante o login; autentique novamente.")
    token = py_secrets.token_urlsafe(32)
    vinculo = py_secrets.token_urlsafe(32)
    sessao_id = str(uuid.uuid4())
    resultado = _seguranca_operar("emitir_sessao", {
        "usuario": usuario, "token_hash": _hash_token_acesso(token), "sessao_id": sessao_id,
        "vinculo_hash": _hash_token_acesso(vinculo), "credencial_versao": _versao_credencial(cliente),
        "fingerprint": fingerprint_dispositivo(),
        "unica": bool(st.secrets.get("REQUIRE_SINGLE_SESSION", True)) and not usuario_e_admin(usuario),
        "ttl": int(st.secrets.get("SESSION_TTL_MINUTES", 3)) * 60,
    })
    if not resultado["ok"]:
        log_evento_operacional(event_id, "auth.login", "session_active")
        raise RuntimeError("Sessão ativa: encerre a anterior com suas credenciais ou aguarde o prazo de inatividade.")
    st.session_state.update(auth_token=token, auth_binding=vinculo, session_token=sessao_id, usuario_logado=usuario)
    _agendar_sessao_browser("write", {
        "version": 1, "usuario": usuario, "auth_token": token, "auth_binding": vinculo,
        "session_token": sessao_id, "expires_at": bounded_expiry(resultado.get("expira_em"), time.time()),
    })
    _set_query_param("sm_session", "")
    log_evento_operacional(event_id, "auth.login", "ok")
    return sessao_id  # identificador não autenticante, nunca o token


def restaurar_login_persistente() -> bool:
    """Revalida conta, token, versão da senha, expiração, revogação e vínculo."""
    event_id = obter_event_id_fluxo("auth")
    log_evento_operacional(event_id, "auth.session", "start")
    _set_query_param("sm_session", "")  # tokens antigos de URL não são aceitos
    try:
        usuario = st.session_state.get("usuario_logado", "")
        token = st.session_state.get("auth_token", "")
        vinculo = st.session_state.get("auth_binding", "")
        cliente = cliente_autorizado(usuario)
        if not token or not vinculo or not cliente or not _data_valida(usuario, cliente)[0]:
            log_evento_operacional(event_id, "auth.session", "missing_or_invalid")
            _limpar_autenticacao_local()
            return False
        resultado = _seguranca_operar("validar_sessao", {
            "usuario": usuario, "token_hash": _hash_token_acesso(token), "vinculo_hash": _hash_token_acesso(vinculo),
            "credencial_versao": _versao_credencial(cliente), "fingerprint": fingerprint_dispositivo(),
        })
        if not resultado["ok"] or resultado.get("sessao_id") != st.session_state.get("session_token"):
            log_evento_operacional(event_id, "auth.session", "invalid")
            _limpar_autenticacao_local()
            return False
        if resultado.get("fingerprint_alterado"):
            logging.getLogger(__name__).info("Contexto do dispositivo mudou em sessão com vínculo válido.")
        # Extend only the browser copy when the authoritative backend returns a
        # new expiry. Tokens remain opaque and are never logged or placed in URLs.
        backend_expiry = resultado.get("expira_em")
        browser_expiry = float(st.session_state.get("_browser_session_expires_at", 0) or 0)
        if backend_expiry and float(backend_expiry) > time.time() and browser_expiry < time.time() + 21600:
            _agendar_sessao_browser("write", {
                "version": 1, "usuario": usuario, "auth_token": token,
                "auth_binding": vinculo, "session_token": st.session_state.get("session_token"),
                "expires_at": bounded_expiry(backend_expiry, time.time()),
            })
            st.session_state["_browser_session_expires_at"] = bounded_expiry(backend_expiry, time.time())
        st.session_state.authenticated = True
        log_evento_operacional(event_id, "auth.session", "ok")
        return True
    except Exception as exc:
        log_evento_operacional(event_id, "auth.session", "error", exc)
        logging.getLogger(__name__).warning("Sessão negada: %s", type(exc).__name__)
        _limpar_autenticacao_local()
        return False


def remover_login_persistente() -> None:
    token = st.session_state.get("auth_token", "")
    try:
        if token and not _seguranca_operar("revogar_token", {"token_hash": _hash_token_acesso(token)})["ok"]:
            raise RuntimeError("Revogação não confirmada.")
    finally:
        _limpar_autenticacao_local()


def aplicar_marca_dagua_usuario() -> None:
    usuario = st.session_state.get("usuario_logado", "") or "cliente"
    try:
        cliente = cliente_autorizado(usuario) or {}
        plano = cliente.get("plano", "")
        venc = cliente.get("vencimento", "")
        texto = f"Smart Money AI • Licenciado para {usuario} • Plano {plano} • vence {venc}"
    except Exception:
        texto = f"Smart Money AI • Licenciado para {usuario}"
    safe = str(texto).replace("'", "").replace('"', "")
    st.markdown(
        f"""
        <style>
        .watermark-client {{
            position: fixed;
            right: 14px;
            bottom: 12px;
            z-index: 999999;
            background: rgba(0,0,0,0.68);
            color: rgba(255,255,255,0.92);
            padding: 7px 10px;
            border-radius: 8px;
            font-size: 11px;
            pointer-events: none;
            max-width: 70vw;
        }}
        .watermark-client-top {{
            position: fixed;
            right: 14px;
            top: 72px;
            z-index: 999999;
            color: rgba(34,197,94,0.13);
            font-size: 13px;
            transform: rotate(-10deg);
            pointer-events: none;
        }}
        </style>
        <div class="watermark-client">🔐 {safe}</div>
        <div class="watermark-client-top">{safe}</div>
        """,
        unsafe_allow_html=True,
    )


def registrar_acesso(usuario: str, status: str) -> None:
    registros = carregar_lista_csv(ACESSOS_FILE)
    ctx = obter_contexto_cliente()
    registros.insert(0, {
        "data_hora": datetime.now(fuso_br).strftime("%d/%m/%Y %H:%M:%S"),
        "usuario": usuario,
        "status": status,
        "ip": ctx.get("ip", "indisponivel"),
        "dispositivo": fingerprint_dispositivo(),
        "navegador": str(ctx.get("user_agent", "indisponivel"))[:180],
    })
    salvar_lista_csv(ACESSOS_FILE, registros[:5000])



# =============================
# LOGS COMPLETOS / BACKUP / CUPONS
# =============================

def registrar_acesso(usuario: str, status: str) -> None:
    """Registra acessos em Supabase quando disponível e mantém CSV como fallback."""
    agora = datetime.now(fuso_br)
    ctx = obter_contexto_cliente()
    registro = {
        "data_hora": agora.strftime("%d/%m/%Y %H:%M:%S"),
        "data_hora_iso": agora.isoformat(),
        "usuario": usuario,
        "status": status,
        "ip": ctx.get("ip", "indisponivel"),
        "dispositivo": fingerprint_dispositivo(),
        "navegador": str(ctx.get("user_agent", "indisponivel"))[:180],
    }
    try:
        if supabase_configurado():
            _sb_post("acessos_log", registro, prefer="return=minimal")
            return
    except Exception:
        pass
    registros = carregar_lista_csv(ACESSOS_FILE)
    registros.insert(0, registro)
    salvar_lista_csv(ACESSOS_FILE, registros[:5000])


def listar_acessos_log(limite: int = 500) -> pd.DataFrame:
    """Lista logs de acesso, preferindo Supabase."""
    try:
        if supabase_configurado():
            dados = _sb_get("acessos_log", params={"select": "*", "order": "data_hora_iso.desc", "limit": str(int(limite))})
            return pd.DataFrame(dados)
    except Exception:
        pass
    return pd.DataFrame(carregar_lista_csv(ACESSOS_FILE))



def limpar_acessos_log_db() -> tuple[bool, str]:
    """Limpa o histórico de logins e tentativas de acesso."""
    try:
        if supabase_configurado():
            _sb_delete("acessos_log", params={"id": "gte.0"})
            return True, "Histórico de logins e tentativas limpo com sucesso."
        salvar_lista_csv(ACESSOS_FILE, [])
        return True, "Histórico local de logins e tentativas limpo com sucesso."
    except Exception as e:
        return False, f"Erro ao limpar logins e tentativas: {e}"


def criar_cupom_db(codigo: str, desconto_pct: float, ativo: bool = True, validade: str = "", observacao: str = "") -> tuple[bool, str]:
    codigo = (codigo or "").strip().upper().replace(" ", "")
    if not codigo:
        return False, "Informe o código do cupom."
    payload = {
        "codigo": codigo,
        "desconto_pct": float(desconto_pct or 0),
        "ativo": bool(ativo),
        "validade": validade or None,
        "observacao": observacao or "",
        "atualizado_em": datetime.now(fuso_br).isoformat(),
        "criado_em": datetime.now(fuso_br).isoformat(),
    }
    try:
        if supabase_configurado():
            _sb_post("cupons", payload, params={"on_conflict": "codigo"}, prefer="resolution=merge-duplicates,return=minimal")
            return True, f"Cupom {codigo} salvo."
        return False, "Supabase não configurado. Cupons precisam do banco persistente."
    except Exception as e:
        return False, f"Erro ao salvar cupom: {e}"


def listar_cupons_db() -> pd.DataFrame:
    try:
        if supabase_configurado():
            dados = _sb_get("cupons", params={"select": "*", "order": "atualizado_em.desc"})
            return pd.DataFrame(dados)
    except Exception:
        pass
    return pd.DataFrame()


# =============================
# RENOVAÇÃO / PIX MANUAL
# =============================
def planos_renovacao_padrao() -> dict:
    return {
        "mensal": 30,
        "trimestral": 90,
        "semestral": 180,
        "anual": 365,
        "vitalicio_v35": 0,
    }


def nome_plano_exibicao(plano: str) -> str:
    plano = str(plano or "").strip().lower()
    nomes = {
        "mensal": "Mensal",
        "trimestral": "Trimestral",
        "semestral": "Semestral",
        "anual": "Anual",
        "vitalicio_v35": "Vitalício V3.5",
        "teste": "Teste",
        "vitalicio": "Vitalício V3.5",
    }
    return nomes.get(plano, plano.replace("_", " ").title())


def plano_vitalicio(plano: str) -> bool:
    plano = str(plano or "").strip().lower()
    return plano in ["vitalicio", "vitalicio_v35", "vitalicio_v3_5", "vitalicio_35"]


def _secret_valor_pix(*nomes: str, padrao: str = "") -> str:
    """Busca configurações PIX em Secrets mesmo se foram colocadas sem querer dentro de uma seção TOML.

    Exemplo do problema comum:
    [ACCESS_EXPIRES]
    "admin@email.com" = "2099-12-31"
    ADMINS = ["admin@email.com"]
    PIX_KEY = "..."

    Nesse caso, o Streamlit entende PIX_KEY como parte de ACCESS_EXPIRES.
    Esta função aceita top-level e também seções para evitar mensagens falsas de configuração.
    Também lê CARD_PAYMENT_* dentro de [PLAN_PRICES], porque no TOML tudo abaixo de [PLAN_PRICES]
    fica dentro dessa seção até começar outro bloco.
    """
    try:
        for nome in nomes:
            valor = st.secrets.get(nome, "")
            if valor:
                return str(valor).strip()
        for secao in ["PIX", "PAYMENT", "PAGAMENTO", "ACCESS_EXPIRES", "PLAN_PRICES", "CARD_PAYMENT", "CHECKOUT"]:
            bloco = st.secrets.get(secao, {})
            try:
                bloco = dict(bloco) if bloco else {}
            except Exception:
                bloco = {}
            for nome in nomes:
                valor = bloco.get(nome, "")
                if valor:
                    return str(valor).strip()
    except Exception:
        pass
    return str(padrao).strip()


def pix_chave_pagamento() -> str:
    return _secret_valor_pix("PIX_KEY", "PIX_CHAVE", "CHAVE_PIX", "PIX_EMAIL", padrao="contato.ultrasinais@gmail.com")


def pix_titular_pagamento() -> str:
    return _secret_valor_pix("PIX_TITULAR", "PIX_RECEIVER_NAME", "PIX_NOME", padrao="Smart Money AI")


def pix_banco_pagamento() -> str:
    return _secret_valor_pix("PIX_BANK_NAME", "PIX_BANCO", "BANK_NAME", padrao="")


def link_cartao_pagamento(plano: str) -> str:
    """Retorna link de checkout/cartão por plano.

    Configure no Streamlit Secrets, por exemplo:
    CARD_PAYMENT_MENSAL="https://..."
    CARD_PAYMENT_TRIMESTRAL="https://..."
    CARD_PAYMENT_SEMESTRAL="https://..."
    CARD_PAYMENT_ANUAL="https://..."

    Também aceita aliases em português e links colocados dentro de seções por engano.
    """
    plano = str(plano or "").strip().lower()
    mapa = {
        "pacote_5": ["CARD_PAYMENT_PACOTE_5", "CARTAO_PACOTE_5", "LINK_CARTAO_PACOTE_5", "CHECKOUT_PACOTE_5", "PAYMENT_LINK_PACOTE_5"],
        "pacote_10": ["CARD_PAYMENT_PACOTE_10", "CARTAO_PACOTE_10", "LINK_CARTAO_PACOTE_10", "CHECKOUT_PACOTE_10", "PAYMENT_LINK_PACOTE_10"],
        "pacote_20": ["CARD_PAYMENT_PACOTE_20", "CARTAO_PACOTE_20", "LINK_CARTAO_PACOTE_20", "CHECKOUT_PACOTE_20", "PAYMENT_LINK_PACOTE_20"],
        "pacote_40": ["CARD_PAYMENT_PACOTE_40", "CARTAO_PACOTE_40", "LINK_CARTAO_PACOTE_40", "CHECKOUT_PACOTE_40", "PAYMENT_LINK_PACOTE_40"],
        "ilimitado_mensal": ["CARD_PAYMENT_ILIMITADO_MENSAL", "CARTAO_ILIMITADO_MENSAL", "LINK_CARTAO_ILIMITADO_MENSAL", "CHECKOUT_ILIMITADO_MENSAL", "PAYMENT_LINK_ILIMITADO_MENSAL"],
        "ilimitado_anual": ["CARD_PAYMENT_ILIMITADO_ANUAL", "CARTAO_ILIMITADO_ANUAL", "LINK_CARTAO_ILIMITADO_ANUAL", "CHECKOUT_ILIMITADO_ANUAL", "PAYMENT_LINK_ILIMITADO_ANUAL"],
        "mensal": ["CARD_PAYMENT_MENSAL", "CARTAO_MENSAL", "LINK_CARTAO_MENSAL", "CHECKOUT_MENSAL", "PAYMENT_LINK_MENSAL"],
        "trimestral": ["CARD_PAYMENT_TRIMESTRAL", "CARTAO_TRIMESTRAL", "LINK_CARTAO_TRIMESTRAL", "CHECKOUT_TRIMESTRAL", "PAYMENT_LINK_TRIMESTRAL"],
        "anual": ["CARD_PAYMENT_ANUAL", "CARTAO_ANUAL", "LINK_CARTAO_ANUAL", "CHECKOUT_ANUAL", "PAYMENT_LINK_ANUAL"],
    }
    nomes = mapa.get(plano, [])
    if not nomes:
        return ""
    return _secret_valor_pix(*nomes, padrao="")


def bucket_comprovantes() -> str:
    """Bucket de comprovantes: deve ser privado, confirmado antes do uso."""
    try:
        return str(st.secrets.get("SUPABASE_COMPROVANTES_BUCKET", "comprovantes")).strip() or "comprovantes"
    except Exception:
        return "comprovantes"


def limpar_nome_arquivo(nome: str) -> str:
    nome = str(nome or "comprovante").strip().replace(" ", "_")
    permitido = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    nome = "".join(ch for ch in nome if ch in permitido)
    return nome[:120] or "comprovante"


def upload_comprovante_supabase(email: str, comprovante_file) -> tuple[bool, str, str, str, str]:
    if comprovante_file is None:
        return True, '', '', '', ''
    try:
        if not restaurar_login_persistente() or (email or '').strip().lower() != st.session_state.get('usuario_logado'):
            raise PermissionError('Conta não autorizada para este envio.')
        conteudo = comprovante_file.getvalue()
        ext, mime = validar_comprovante(conteudo,comprovante_file.name,comprovante_file.type)
        bucket = bucket_comprovantes()
        base, headers = _storage_privado(bucket)
        path = uuid.uuid4().hex + '/' + uuid.uuid4().hex + ext
        headers.update({'Content-Type':mime,'x-upsert':'false'})
        r = requests.post(base+'/storage/v1/object/'+urllib.parse.quote(bucket,safe='')+'/'+path,headers=headers,data=conteudo,timeout=40)
        if r.status_code not in (200,201):
            raise RuntimeError('Upload não confirmado.')
        return True, 'comprovante'+ext, mime, path, ''
    except (ValueError,PermissionError) as exc:
        return False, str(exc), '', '', ''
    except Exception:
        return False, 'Storage privado ou upload indisponível; solicitação não enviada.', '', '', ''


def valor_plano(plano: str) -> float:
    precos = _precos_planos_padrao()
    return float(precos.get(str(plano or "").strip().lower(), 0.0) or 0.0)


def calcular_novo_vencimento_previsto(vencimento_atual: str, plano: str) -> str:
    if plano_vitalicio(plano):
        return "2099-12-31"
    hoje = datetime.now(fuso_br).date()
    try:
        venc = datetime.strptime(str(vencimento_atual), "%Y-%m-%d").date()
    except Exception:
        venc = hoje
    base = venc if venc >= hoje else hoje
    dias = planos_renovacao_padrao().get(str(plano).strip().lower(), 30)
    return str(base + timedelta(days=int(dias)))


def criar_solicitacao_renovacao(email: str, nome: str, plano_atual: str, plano_solicitado: str, vencimento_atual: str, observacao: str = "", comprovante_file=None, metodo_pagamento: str = "NAO_INFORMADO") -> tuple[bool, str]:
    email = (email or "").strip().lower()
    if not email:
        return False, "Cliente não identificado."
    if not supabase_configurado():
        return False, "Supabase não configurado. Solicitações de renovação precisam do banco persistente."
    plano_solicitado = str(plano_solicitado or "mensal").strip().lower()
    valor = valor_plano(plano_solicitado)
    novo_venc = calcular_novo_vencimento_previsto(vencimento_atual, plano_solicitado)
    agora = datetime.now(fuso_br).isoformat()
    comprovante_nome = ""
    comprovante_mime = ""
    comprovante_base64 = ""
    comprovante_enviado_em = ""
    comprovante_path = ""
    comprovante_url = ""
    comprovante_bytes = b""

    if not restaurar_login_persistente() or email != st.session_state.get('usuario_logado'):
        return False, 'Conta não autorizada.'
    if plano_solicitado not in planos_renovacao_padrao():
        return False, 'Plano não reconhecido.'
    if metodo_pagamento not in ('PIX','Cartão','Transferência','Dinheiro','Outro','NAO_INFORMADO'):
        return False, 'Origem de pagamento não reconhecida.'
    if comprovante_file is not None:
        ok_upload, nome_up, mime_up, path_up, url_up = upload_comprovante_supabase(email, comprovante_file)
        if not ok_upload:
            return False, nome_up
        comprovante_nome = nome_up
        comprovante_mime = mime_up
        comprovante_path = path_up
        comprovante_url = url_up
        comprovante_enviado_em = agora if comprovante_nome else ""

    payload = {
        "email": email,
        "nome": nome or email,
        "plano_atual": plano_atual or "",
        "plano_solicitado": plano_solicitado,
        "valor": float(valor),
        "vencimento_atual": vencimento_atual or "",
        "novo_vencimento_previsto": novo_venc,
        "pix_chave": pix_chave_pagamento(),
        "status": "PENDENTE_CONFIRMACAO",
        "observacao": observacao or "Cliente solicitou renovação pela área Minha Conta.",
        "comprovante_nome": comprovante_nome,
        "comprovante_mime": comprovante_mime,
        "comprovante_base64": comprovante_base64,
        "comprovante_path": comprovante_path,
        "comprovante_bucket": bucket_comprovantes() if comprovante_path else "",
        "metodo_pagamento": metodo_pagamento,
        "comprovante_url": comprovante_url,
        "comprovante_enviado_em": comprovante_enviado_em,
        "criado_em": agora,
        "atualizado_em": agora,
    }
    try:
        rows = _sb_post("solicitacoes_renovacao", payload)
        solicitacao_id = int(rows[0].get("id", 0)) if rows else 0
        if not solicitacao_id:
            return False, "Gravação não confirmada; consulte o suporte antes de reenviar."
        admin_email = suporte_email()
        valor_txt = f"R$ {float(valor or 0):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        linha_link = f"Link do comprovante: {comprovante_url}\n" if comprovante_url else ""
        corpo_admin = f"""Nova solicitação de renovação no Smart Money AI.

Cliente: {nome or email}
E-mail: {email}
Plano atual: {plano_atual}
Plano solicitado: {plano_solicitado}
Valor: {valor_txt}
Vencimento atual: {formatar_data_br(vencimento_atual)}
Novo vencimento previsto: {formatar_data_br(novo_venc)}
Status: aguardando confirmação de pagamento
Comprovante anexado: {"SIM - " + comprovante_nome if comprovante_nome else "NÃO"}
{linha_link}
Confirme o pagamento no painel Comercial para renovar o cliente.
"""
        if email_configurado() and admin_email:
            if comprovante_bytes and comprovante_nome:
                enviar_email_com_anexo(
                    admin_email,
                    "Nova solicitação de renovação - Smart Money AI",
                    corpo_admin,
                    comprovante_bytes,
                    comprovante_nome,
                    comprovante_mime or "application/octet-stream",
                )
            else:
                enviar_email(admin_email, "Nova solicitação de renovação - Smart Money AI", corpo_admin)
        registrar_acesso(email, f"SOLICITACAO_RENOVACAO_{solicitacao_id}")
        registrar_pagina_visitada(email, "Renovar Plano", f"Solicitação de renovação #{solicitacao_id}")
        return True, f"Solicitação enviada com sucesso. Após confirmar o pagamento, o suporte liberará sua renovação. Protocolo: {solicitacao_id}"
    except Exception as e:
        return False, f"Erro ao criar solicitação: {e}"


def listar_solicitacoes_renovacao(status: str | None = None, limite: int = 300) -> pd.DataFrame:
    if not supabase_configurado():
        return pd.DataFrame()
    try:
        params = {"select": "*", "order": "criado_em.desc", "limit": str(int(limite))}
        if status:
            params["status"] = f"eq.{status}"
        return pd.DataFrame(_sb_get("solicitacoes_renovacao", params=params))
    except Exception:
        return pd.DataFrame()


def atualizar_status_solicitacao_renovacao(solicitacao_id: int, status: str, observacao: str = '') -> tuple[bool, str]:
    if status != 'RECUSADA':
        return False, 'Transição não permitida por este fluxo.'
    try:
        result = _rpc_comercial('recusar_renovacao', {'solicitacao_id':int(solicitacao_id),'observacao':observacao})
        return result.get('ok') is True and result.get('status') == 'RECUSADA', str(result.get('mensagem') or 'Resultado não confirmado.')
    except Exception:
        return False, 'Recusa não confirmada; consulte o protocolo.'


def confirmar_solicitacao_renovacao(solicitacao_id: int) -> tuple[bool, str]:
    try:
        result = _rpc_comercial('confirmar_renovacao', {'solicitacao_id':int(solicitacao_id)})
        if not result.get('ok'):
            return False, str(result.get('mensagem') or 'Solicitação não está pendente; nenhum efeito foi repetido.')
        if (result.get('status') != 'RENOVADA' or result.get('solicitacao_id') != int(solicitacao_id)
                or not result.get('cobranca_id') or not result.get('novo_vencimento')):
            return False, 'Estado final não confirmado. Consulte o protocolo antes de repetir.'
        # O e-mail é posterior ao commit e não participa da transação financeira.
        try:
            ok_mail, _ = enviar_email(result['email'], 'Renovação confirmada - Smart Money AI',
                f"Seu pagamento foi confirmado. Plano: {result['plano']}. Novo vencimento: {formatar_data_br(result['novo_vencimento'])}. Acesse: {url_do_app()}")
        except Exception:
            ok_mail = False
        return True, f"Renovação confirmada. Novo vencimento: {formatar_data_br(result['novo_vencimento'])}." + ('' if ok_mail else ' E-mail não confirmado; a renovação está concluída.')
    except Exception as exc:
        logging.getLogger(__name__).warning('Renovação não confirmada: %s', type(exc).__name__)
        return False, 'Transação não confirmada. Consulte o status do protocolo; repetir a mesma solicitação não reaplica efeitos.'


def calcular_dashboard_executivo() -> dict:
    clientes = enriquecer_clientes_comercial(listar_clientes_db())
    cobrancas = listar_cobrancas()
    hoje = datetime.now(fuso_br).date()
    mes_atual = hoje.strftime("%Y-%m")
    receita_recebida_mes = 0.0
    renovacoes_mes = 0
    pendentes = vencidas = 0
    if not cobrancas.empty:
        dfc = cobrancas.copy()
        dfc["valor"] = pd.to_numeric(dfc.get("valor", 0), errors="coerce").fillna(0.0)
        status = dfc.get("status", "").astype(str).str.upper()
        if "data_pagamento" in dfc.columns:
            data_pag = pd.to_datetime(dfc["data_pagamento"], errors="coerce")
            pagas_mes = dfc[(status == "PAGA") & (data_pag.dt.strftime("%Y-%m") == mes_atual)]
            receita_recebida_mes = float(pagas_mes["valor"].sum())
            renovacoes_mes = int(len(pagas_mes))
        pendentes = int((status == "PENDENTE").sum())
        if "vencimento_cobranca" in dfc.columns:
            venc_data = pd.to_datetime(dfc["vencimento_cobranca"], errors="coerce").dt.date
            vencidas = int(((status == "PENDENTE") & (venc_data < hoje)).sum())
    total_clientes = int(len(clientes)) if not clientes.empty else 0
    ativos = int(((clientes["ativo"].astype(bool)) & (clientes["status_comercial"] != "VENCIDO")).sum()) if not clientes.empty else 0
    taxa_ativos = (ativos / total_clientes * 100) if total_clientes else 0.0
    return {
        "clientes_total": total_clientes,
        "clientes_ativos": ativos,
        "taxa_ativos": taxa_ativos,
        "receita_recebida_mes": receita_recebida_mes,
        "renovacoes_mes": renovacoes_mes,
        "cobrancas_pendentes": pendentes,
        "cobrancas_vencidas": vencidas,
    }


def _moeda_br(valor: float) -> str:
    try:
        return f"R$ {float(valor or 0):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return "R$ 0,00"


def calcular_painel_financeiro_master() -> dict:
    """Painel financeiro real baseado em cobranças e solicitações de renovação.
    Calcula recebidos/pendentes, ticket médio e receita por método/status.
    """
    hoje = datetime.now(fuso_br).date()
    mes_atual = hoje.strftime("%Y-%m")
    cobrancas = listar_cobrancas()
    solicitacoes = listar_solicitacoes_renovacao(limite=2000)

    resumo = {
        "receita_mes": 0.0,
        "receita_ano": 0.0,
        "receita_total": 0.0,
        "pendente_total": 0.0,
        "vencido_total": 0.0,
        "ticket_medio": 0.0,
        "pagamentos_pagos": 0,
        "pagamentos_pendentes": 0,
        "pagamentos_vencidos": 0,
        "renovacoes_pendentes": 0,
        "renovacoes_aprovadas": 0,
        "renovacoes_recusadas": 0,
        "df_cobrancas": cobrancas,
        "df_solicitacoes": solicitacoes,
    }

    if cobrancas is not None and not cobrancas.empty:
        df = cobrancas.copy()
        df["valor"] = pd.to_numeric(df.get("valor", 0), errors="coerce").fillna(0.0)
        status = df.get("status", "").astype(str).str.upper()
        data_pag = pd.to_datetime(df.get("data_pagamento", ""), errors="coerce")
        venc = pd.to_datetime(df.get("vencimento_cobranca", ""), errors="coerce").dt.date

        pagas = df[status == "PAGA"].copy()
        resumo["pagamentos_pagos"] = int(len(pagas))
        resumo["receita_total"] = float(pagas["valor"].sum())
        resumo["receita_mes"] = float(pagas[data_pag.dt.strftime("%Y-%m") == mes_atual]["valor"].sum()) if not pagas.empty else 0.0
        resumo["receita_ano"] = float(pagas[data_pag.dt.strftime("%Y") == str(hoje.year)]["valor"].sum()) if not pagas.empty else 0.0
        resumo["ticket_medio"] = float(pagas["valor"].mean()) if len(pagas) else 0.0

        pendentes = df[status == "PENDENTE"].copy()
        resumo["pagamentos_pendentes"] = int(len(pendentes))
        resumo["pendente_total"] = float(pendentes["valor"].sum())
        vencidas = df[(status == "PENDENTE") & (venc < hoje)].copy()
        resumo["pagamentos_vencidos"] = int(len(vencidas))
        resumo["vencido_total"] = float(vencidas["valor"].sum())

    if solicitacoes is not None and not solicitacoes.empty and "status" in solicitacoes.columns:
        sts = solicitacoes["status"].astype(str).str.upper()
        resumo["renovacoes_pendentes"] = int(sts.isin(["PENDENTE", "PENDENTE_CONFIRMACAO", "AGUARDANDO_PAGAMENTO"]).sum())
        resumo["renovacoes_aprovadas"] = int(sts.isin(["RENOVADA", "APROVADA", "PAGA"]).sum())
        resumo["renovacoes_recusadas"] = int(sts.isin(["RECUSADA", "REJEITADA"]).sum())

    return resumo


def enviar_email_recusa_renovacao(sol: dict, motivo: str = "") -> None:
    """Envia e-mail simples ao cliente quando uma solicitação de renovação é recusada."""
    try:
        email = str(sol.get("email", "")).strip().lower()
        if not email or not email_configurado():
            return
        nome = sol.get("nome") or email
        plano = sol.get("plano_solicitado") or ""
        motivo_txt = motivo or "O comprovante não pôde ser validado. Confira os dados e envie novamente, ou fale com o suporte."
        corpo = f"""Olá, {nome}!

Sua solicitação de renovação no Smart Money AI foi recusada.

Plano solicitado: {nome_plano_exibicao(plano)}
Motivo: {motivo_txt}

Você pode enviar uma nova solicitação pela área Minha Conta ou falar com o suporte.

Acesse: {url_do_app()}

Smart Money AI
"""
        enviar_email(email, "Solicitação de renovação recusada - Smart Money AI", corpo)
    except Exception:
        pass


def gerar_backup_completo() -> bytes:
    """Exportação administrativa verificável, não backup completo restaurável."""
    import io
    from contextlib import closing
    _exigir_admin_comercial()
    buffer = io.BytesIO()
    manifest = {'versao':1,'tipo':'exportacao_administrativa','restauracao_completa':False,
                'criado_em':datetime.now(fuso_br).isoformat(),'arquivos':{},'erros':{},
                'excluidos':['Secrets e chaves','tokens/sessões de autenticação','schema/constraints/RLS','binários Storage'],
                'consistencia':'Paginação não é snapshot transacional entre páginas ou tabelas.'}
    tabelas = {'clientes':'email.asc','cobrancas':'id.asc','automacoes_log':'id.asc','acessos_log':'id.asc',
               'cupons':'codigo.asc','solicitacoes_renovacao':'id.asc','afiliados':'codigo.asc','indicacoes':'id.asc',
               'paginas_visitadas':'id.asc','auditoria_sinais_51':'id_sinal.asc',
               'sma_obrigacoes':'chave.asc','sma_avisos':'chave.asc','sma_avisos_eventos':'id.asc'}
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        def gravar(nome,dados,contagem):
            raw = dados if isinstance(dados,bytes) else json.dumps(dados,ensure_ascii=False,default=str,indent=2).encode('utf-8')
            z.writestr(nome,raw)
            manifest['arquivos'][nome] = {'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'contagem':contagem}
        if supabase_configurado():
            for tabela,ordem in tabelas.items():
                try:
                    rows = _exportar_tabela_paginada(tabela,ordem)
                    gravar('supabase/'+tabela+'.json',rows,len(rows))
                except Exception as exc:
                    manifest['erros'][tabela] = type(exc).__name__
        # Dados locais conhecidos; nunca copiar Secrets ou arquivos arbitrários.
        db = Path(CLIENTES_DB)
        if db.exists():
            try:
                with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
                    conn.execute('BEGIN')
                    nomes = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                    for tabela in nomes:
                        quoted = '"'+tabela.replace('"','""')+'"'
                        cur = conn.execute('SELECT * FROM '+quoted)
                        cols = [c[0] for c in cur.description]
                        rows = [dict(zip(cols,r)) for r in cur.fetchall()]
                        gravar('sqlite/'+tabela+'.json',rows,len(rows))
            except Exception as exc:
                manifest['erros']['sqlite'] = type(exc).__name__
        for path in (AUDITORIA_FILE,HISTORICO_FILE,USO_SINAIS_FILE,ACESSOS_FILE,PAGINAS_VISITADAS_FILE):
            path = Path(path)
            if path.exists():
                try:
                    raw = path.read_bytes()
                    import csv
                    count = max(0,len(list(csv.reader(io.StringIO(raw.decode('utf-8-sig')))))-1)
                    gravar('local/'+path.name,raw,count)
                except Exception as exc:
                    manifest['erros'][path.name] = type(exc).__name__
        manifest['status'] = 'PARCIAL' if manifest['erros'] else 'EXPORTADO_COM_EXCLUSOES'
        z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    return buffer.getvalue()

def validar_sessao_unica(usuario: str) -> bool:
    # A admissão única é atômica no ledger. Mesmo admin revalida token/revogação.
    return usuario == st.session_state.get("usuario_logado") and restaurar_login_persistente()


def validar_cadastro_publico(nome: str, email: str, senha: str, confirmar: str) -> tuple[bool, str]:
    """Valida dados públicos sem confiar em campos comerciais enviados pelo cliente."""
    if not str(nome or "").strip() or not str(email or "").strip() or "@" not in str(email):
        return False, "Informe nome e e-mail válidos."
    valido, msg = validar_senha_nova(str(senha or ""))
    if not valido:
        return False, msg
    if senha != confirmar:
        return False, "As senhas não conferem."
    return True, "OK"


def criar_cadastro_publico_backend(nome: str, email: str, senha: str, captcha_token: str = "") -> tuple[bool, str]:
    """Cria cadastro via Edge Function usando hash nativo e prova interna opcional."""
    if not supabase_configurado():
        return False, "Cadastro temporariamente indisponível."
    correlation_id = uuid.uuid4().hex[:12]
    try:
        endpoint = str(st.secrets.get("PUBLIC_SIGNUP_FUNCTION_URL", f"{_supabase_base_url().split('/rest/v1')[0]}/functions/v1/sma-public-signup")).strip()
        anon = str(st.secrets.get("SUPABASE_ANON_KEY", "")).strip()
        internal_secret = str(st.secrets.get("PUBLIC_SIGNUP_INTERNAL_HMAC_SECRET", "")).strip()
        payload = {"nome": nome, "email": email, "confirmar": senha, "captcha_token": captcha_token}
        if internal_secret:
            senha_hash = _hash_senha(senha)
            prova_timestamp = int(time.time())
            assinatura = hmac.new(internal_secret.encode("utf-8"), f"{email.strip().lower()}\n{senha_hash}\n{prova_timestamp}".encode("utf-8"), hashlib.sha256).hexdigest()
            payload.update({"senha_hash": senha_hash, "senha_proof": assinatura, "senha_proof_timestamp": prova_timestamp})
        else:
            payload["senha"] = senha
        headers = {"Content-Type": "application/json"}
        if anon:
            headers.update({"apikey": anon, "Authorization": f"Bearer {anon}"})
        resposta_http = _http_request("POST", endpoint, headers=headers, json=payload, timeout=25)
        resposta = resposta_http.json() if resposta_http.text else {}
        if resposta_http.status_code < 400 and resposta.get("ok"):
            logging.getLogger(__name__).info("PUBLIC_SIGNUP_OK stage=response status_http=%s correlation_id=%s", resposta_http.status_code, correlation_id)
            return True, "Cadastro recebido com sucesso! Enviamos um link de confirmação para seu e-mail. Verifique também a pasta de spam."
        logging.getLogger(__name__).warning("PUBLIC_SIGNUP_ERROR stage=response status_http=%s business_ok=%s correlation_id=%s", resposta_http.status_code, bool(resposta.get("ok")), correlation_id)
        return False, "Não foi possível concluir o cadastro agora. Tente novamente mais tarde."
    except Exception as exc:
        logging.getLogger(__name__).warning("PUBLIC_SIGNUP_ERROR stage=request error_type=%s correlation_id=%s", type(exc).__name__, correlation_id)
        return False, "Não foi possível concluir o cadastro agora. Tente novamente mais tarde."


def pagina_comercial_publica() -> None:
    """Apresenta planos e permite cadastro público somente pelo backend."""
    st.markdown("<div class='public-shell'><h1>Smart Money AI — Planos</h1>", unsafe_allow_html=True)
    st.caption("Escolha uma opção e continue pelo checkout seguro após entrar ou criar seu acesso.")
    cols = st.columns(3)
    for idx, (codigo, plano) in enumerate(CARTEIRA_FREEMIUM_PLANOS.items()):
        with cols[idx % len(cols)]:
            st.markdown(f"<div class='public-plan-card'><h3>{plano['nome']}</h3><div class='public-price'>R$ {plano['preco']:.2f}</div>", unsafe_allow_html=True)
            st.caption("Ilimitado" if not plano['creditos'] else f"{plano['creditos']} créditos")
            st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("<div class='public-signup-card'><h2>Criar acesso gratuito</h2>", unsafe_allow_html=True)
    with st.form("form_cadastro_publico"):
        nome = st.text_input("Nome", key="public_signup_name")
        email = st.text_input("E-mail", key="public_signup_email").strip().lower()
        senha = st.text_input("Senha (mínimo 8 caracteres)", type="password", key="public_signup_password")
        confirmar = st.text_input("Confirmar senha", type="password", key="public_signup_confirm")
        site_key = str(st.secrets.get("TURNSTILE_SITE_KEY", "")).strip()
        if site_key:
            turnstile_result = render_turnstile(site_key)
            captcha_token = str(getattr(turnstile_result, "token", "") or "")
        else:
            captcha_token = ""
            st.warning("Verificação antiabuso indisponível. O cadastro permanecerá bloqueado até configurar o Turnstile.")
        cadastrar = st.form_submit_button("Criar acesso", use_container_width=True)
    if cadastrar:
        tentativas = int(st.session_state.get("public_signup_attempts", 0))
        if tentativas >= 5:
            st.error("Limite temporário de tentativas atingido. Aguarde e tente novamente.")
        else:
            st.session_state["public_signup_attempts"] = tentativas + 1
            valido, msg = validar_cadastro_publico(nome, email, senha, confirmar)
            if not valido and (not nome.strip() or not email or "@" not in email):
                st.error(msg)
            elif not valido:
                st.error(msg)
            elif senha != confirmar:
                st.error("As senhas não conferem.")
            else:
                try:
                    ok_cadastro, msg_cadastro = criar_cadastro_publico_backend(nome, email, senha, captcha_token)
                    if not ok_cadastro:
                        st.error(msg_cadastro)
                        return
                    st.session_state["public_signup_attempts"] = 0
                    st.success(msg_cadastro)
                except Exception as exc:
                    logging.getLogger(__name__).warning("Cadastro público não confirmado: %s", type(exc).__name__)
                    st.error("Não foi possível concluir o cadastro agora. Tente novamente mais tarde.")
    st.markdown("</div></div>", unsafe_allow_html=True)
    if st.button("Voltar para login", key="btn_public_commercial_back"):
        st.session_state.pop("_public_commercial_view", None)
        st.rerun()


def tela_login() -> None:
    """Tela de login premium para clientes do Smart Money AI.
    Mantém a mesma validação de usuário, senha, vencimento e bloqueio.
    """
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at 18% 18%, rgba(34,197,94,0.18), transparent 28%),
                radial-gradient(circle at 85% 78%, rgba(22,163,74,0.14), transparent 28%),
                linear-gradient(135deg, #030504 0%, #06120c 45%, #010201 100%);
            position: relative;
            overflow: hidden;
        }
        [data-testid="stHeader"] { background: rgba(0,0,0,0); }
        /* Camada visual: candles gigantes e quase transparentes, sem depender de imagem externa */
        .candles-bg {
            position: fixed;
            inset: 0;
            z-index: 0;
            pointer-events: none;
            opacity: 0.16;
            overflow: hidden;
        }
        .candles-bg::before {
            content: "";
            position: absolute;
            left: 7%;
            top: 15%;
            width: 86vw;
            height: 62vh;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1400' height='650' viewBox='0 0 1400 650'%3E%3Crect width='1400' height='650' fill='none'/%3E%3Cg stroke='%2322c55e' stroke-width='6' opacity='0.95'%3E%3Cline x1='75' y1='350' x2='75' y2='520'/%3E%3Cline x1='165' y1='280' x2='165' y2='480'/%3E%3Cline x1='255' y1='315' x2='255' y2='560'/%3E%3Cline x1='345' y1='230' x2='345' y2='440'/%3E%3Cline x1='435' y1='200' x2='435' y2='390'/%3E%3Cline x1='525' y1='260' x2='525' y2='470'/%3E%3Cline x1='615' y1='190' x2='615' y2='410'/%3E%3Cline x1='705' y1='165' x2='705' y2='355'/%3E%3Cline x1='795' y1='205' x2='795' y2='435'/%3E%3Cline x1='885' y1='145' x2='885' y2='330'/%3E%3Cline x1='975' y1='125' x2='975' y2='300'/%3E%3Cline x1='1065' y1='155' x2='1065' y2='345'/%3E%3Cline x1='1155' y1='95' x2='1155' y2='275'/%3E%3Cline x1='1245' y1='115' x2='1245' y2='305'/%3E%3C/g%3E%3Cg opacity='0.85'%3E%3Crect x='48' y='390' width='54' height='82' rx='6' fill='%2322c55e'/%3E%3Crect x='138' y='330' width='54' height='96' rx='6' fill='%2316a34a'/%3E%3Crect x='228' y='390' width='54' height='115' rx='6' fill='%23ef4444'/%3E%3Crect x='318' y='280' width='54' height='92' rx='6' fill='%2322c55e'/%3E%3Crect x='408' y='245' width='54' height='95' rx='6' fill='%2322c55e'/%3E%3Crect x='498' y='330' width='54' height='84' rx='6' fill='%23ef4444'/%3E%3Crect x='588' y='235' width='54' height='95' rx='6' fill='%2322c55e'/%3E%3Crect x='678' y='205' width='54' height='92' rx='6' fill='%2322c55e'/%3E%3Crect x='768' y='275' width='54' height='92' rx='6' fill='%23ef4444'/%3E%3Crect x='858' y='185' width='54' height='86' rx='6' fill='%2322c55e'/%3E%3Crect x='948' y='160' width='54' height='80' rx='6' fill='%2322c55e'/%3E%3Crect x='1038' y='215' width='54' height='76' rx='6' fill='%23ef4444'/%3E%3Crect x='1128' y='135' width='54' height='82' rx='6' fill='%2322c55e'/%3E%3Crect x='1218' y='165' width='54' height='78' rx='6' fill='%2322c55e'/%3E%3C/g%3E%3Cpath d='M40 500 C190 420, 255 505, 345 350 S520 390, 620 280 S810 295, 900 220 S1080 205, 1260 165' fill='none' stroke='%2322c55e' stroke-width='5' opacity='0.8'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-size: contain;
            background-position: center;
            filter: blur(0.2px);
            transform: rotate(-5deg) scale(1.18);
        }
        .candles-bg::after {
            content: "SMART MONEY AI";
            position: absolute;
            left: 50%;
            top: 43%;
            transform: translate(-50%, -50%) rotate(-18deg);
            font-size: min(13vw, 170px);
            font-weight: 900;
            letter-spacing: 4px;
            color: rgba(34,197,94,0.055);
            white-space: nowrap;
            text-shadow: 0 0 25px rgba(34,197,94,0.08);
        }
        .usp-login-wrap, .usp-login-card, .usp-risk-card { position: relative; z-index: 2; }
        /* Labels de e-mail e senha mais visíveis */
        div[data-testid="stTextInput"] label,
        div[data-testid="stTextInput"] label p {
            color: #ffffff !important;
            font-weight: 900 !important;
            text-shadow: 0 0 8px rgba(34,197,94,0.35) !important;
            font-size: 15px !important;
        }
        div[data-testid="stTextInput"] input {
            background: rgba(255,255,255,0.96) !important;
            color: #020617 !important;
            border: 1px solid rgba(34,197,94,0.65) !important;
            box-shadow: 0 0 12px rgba(34,197,94,0.13) !important;
        }
        div[data-testid="stTextInput"] input::placeholder {
            color: #334155 !important;
            opacity: 0.8 !important;
        }
        .usp-login-wrap {
            max-width: 1180px;
            margin: 1rem auto 0 auto;
            border: 1px solid rgba(34,197,94,0.28);
            border-radius: 24px;
            padding: 34px;
            background: rgba(2, 6, 4, 0.78);
            box-shadow: 0 0 45px rgba(34,197,94,0.12), inset 0 0 25px rgba(34,197,94,0.04);
        }
        .usp-brand-title {
            font-size: 52px;
            line-height: 0.95;
            font-weight: 900;
            letter-spacing: 1px;
            color: #f8fafc;
            text-shadow: 0 0 18px rgba(34,197,94,0.35);
            margin-bottom: 12px;
        }
        .usp-brand-title span { color: #22c55e; }
        .usp-subtitle {
            color: #d1d5db;
            font-size: 19px;
            margin-bottom: 28px;
        }
        .usp-subtitle b { color: #22c55e; }
        .usp-feature {
            display: flex;
            gap: 14px;
            align-items: flex-start;
            margin: 18px 0;
            color: #e5e7eb;
            font-size: 17px;
        }
        .usp-feature-icon {
            min-width: 38px;
            height: 38px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #22c55e;
            border: 1px solid rgba(34,197,94,0.35);
            background: rgba(34,197,94,0.08);
            font-size: 20px;
        }
        .usp-feature small { color: #9ca3af; display: block; font-size: 14px; margin-top: 3px; }
        .usp-login-card {
            border: 1px solid rgba(34,197,94,0.45);
            border-radius: 22px;
            padding: 28px 30px 22px 30px;
            background: linear-gradient(180deg, rgba(3, 15, 9, 0.94), rgba(2, 6, 4, 0.92));
            box-shadow: 0 0 35px rgba(34,197,94,0.13);
        }
        .usp-login-heading {
            text-align: center;
            font-size: 30px;
            font-weight: 900;
            color: #f9fafb;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }
        .usp-login-line {
            width: 110px;
            height: 3px;
            background: linear-gradient(90deg, transparent, #22c55e, transparent);
            margin: 0 auto 18px auto;
        }
        .usp-security-row {
            margin-top: 18px;
            display: flex;
            justify-content: center;
            gap: 12px;
            flex-wrap: wrap;
        }
        .usp-pill {
            border: 1px solid rgba(34,197,94,0.22);
            border-radius: 999px;
            padding: 7px 13px;
            color: #d1d5db;
            background: rgba(255,255,255,0.03);
            font-size: 13px;
        }
        .usp-risk-card {
            margin-top: 28px;
            border: 1px solid rgba(250,204,21,0.28);
            border-radius: 18px;
            padding: 20px 22px;
            background: linear-gradient(90deg, rgba(250,204,21,0.10), rgba(34,197,94,0.05));
            color: #e5e7eb;
        }
        .usp-risk-card h3 {
            color: #facc15;
            margin: 0 0 8px 0;
            font-size: 20px;
        }
        .usp-risk-card p {
            color: #d1d5db;
            font-size: 15px;
            line-height: 1.55;
            margin: 0;
        }
        .usp-risk-card b { color: #22c55e; }
        .usp-footer-mini {
            text-align: center;
            color: #9ca3af;
            font-size: 13px;
            margin-top: 18px;
        }

        .support-actions {
            margin-top: 12px;
            display: flex;
            gap: 10px;
            justify-content: center;
            flex-wrap: wrap;
        }
        .support-link {
            display: inline-block;
            padding: 8px 13px;
            border-radius: 999px;
            border: 1px solid rgba(34,197,94,0.34);
            background: rgba(34,197,94,0.08);
            color: #ffffff !important;
            font-weight: 800;
            text-decoration: none !important;
            font-size: 13px;
        }
        .support-link:hover {
            background: rgba(34,197,94,0.18);
            color: #22c55e !important;
        }
        .forgot-caption {
            text-align: center;
            color: #f9fafb;
            font-size: 14px;
            font-weight: 800;
            margin: 12px 0 8px 0;
            text-shadow: 0 0 8px rgba(255,255,255,0.18);
        }
        .usp-login-card label,
        .usp-login-card [data-testid="stWidgetLabel"],
        .usp-login-card [data-testid="stWidgetLabel"] p {
            color: #ffffff !important;
            font-weight: 900 !important;
            font-size: 14px !important;
            text-shadow: 0 0 10px rgba(34,197,94,0.35);
        }
        .usp-login-card input {
            color: #111827 !important;
            background: rgba(255,255,255,0.96) !important;
            border: 1px solid rgba(34,197,94,0.65) !important;
            font-weight: 700 !important;
        }
        .usp-login-card input::placeholder {
            color: #6b7280 !important;
            opacity: 1 !important;
        }
        .usp-login-card details summary,
        .usp-login-card [data-testid="stExpander"] summary,
        .usp-login-card [data-testid="stExpander"] p {
            color: #ffffff !important;
            font-weight: 900 !important;
        }
        .usp-login-card [data-testid="stExpander"] {
            border: 1px solid rgba(34,197,94,0.30) !important;
            border-radius: 12px !important;
            background: rgba(34,197,94,0.06) !important;
            margin-top: 10px !important;
        }
        .support-card-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-top: 8px;
        }
        .support-card-row a, .support-card-row .support-disabled {
            display: block;
            text-align: center;
            padding: 12px 10px;
            border-radius: 12px;
            border: 1px solid rgba(34,197,94,0.42);
            background: rgba(3,15,9,0.86);
            color: #ffffff !important;
            font-weight: 900;
            text-decoration: none !important;
            box-shadow: 0 0 16px rgba(34,197,94,0.10);
        }
        .support-card-row a:hover {
            background: rgba(34,197,94,0.16);
            color: #22c55e !important;
        }
        .support-disabled {
            color: #facc15 !important;
            border-color: rgba(250,204,21,0.42) !important;
        }
        @media (max-width: 700px) {
            .support-card-row { grid-template-columns: 1fr; }
        }
        div.stButton > button:first-child {
            background: linear-gradient(90deg, #16a34a, #22c55e) !important;
            color: white !important;
            border-radius: 12px !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
            padding: 0.75rem 2rem !important;
            font-weight: 900 !important;
            letter-spacing: 0.5px;
            box-shadow: 0 0 20px rgba(34,197,94,0.25);
        }
        div.stButton > button:first-child:hover {
            transform: translateY(-1px);
            filter: brightness(1.08);
        }

        /* Melhor contraste para labels e campos do login */
        label, [data-testid="stWidgetLabel"] p {
            color: #ffffff !important;
            font-weight: 900 !important;
            text-shadow: 0 0 8px rgba(255,255,255,0.25);
        }
        input {
            color: #ffffff !important;
            font-weight: 700 !important;
        }
        input::placeholder {
            color: rgba(255,255,255,0.62) !important;
        }
        /* Realce do Esqueci minha senha */
        div[data-testid="stExpander"] details summary p,
        div[data-testid="stExpander"] summary {
            color: #FFD700 !important;
            font-weight: 900 !important;
            font-size: 16px !important;
            text-shadow: 0 0 10px rgba(255,215,0,0.55);
        }
        .login-inline-error {
            margin-top: 0px;
            padding: 4px 7px;
            border-radius: 8px;
            border: 1px solid rgba(248,113,113,0.42);
            background: rgba(127,29,29,0.30);
            color: #fecaca;
            font-weight: 800;
            text-align: left;
            box-shadow: none;
            font-size: 11px;
            line-height: 1.15;
            min-height: 34px;
            display: flex;
            align-items: center;
        }
        .login-inline-error .dot {
            display: inline-block;
            min-width: 6px;
            width: 6px;
            height: 6px;
            border-radius: 999px;
            background: #ff2d2d;
            margin-right: 5px;
            box-shadow: 0 0 5px rgba(255,45,45,0.55);
        }
        /* Campo ativo bem visível no login */
        .usp-login-card div[data-testid="stTextInput"] input:focus,
        .usp-login-card input:focus,
        div[data-testid="stTextInput"] input:focus {
            color: #020617 !important;
            background: #ffffff !important;
            border: 3px solid #00ff88 !important;
            outline: 3px solid rgba(0,255,136,0.28) !important;
            box-shadow: 0 0 0 4px rgba(0,255,136,0.18), 0 0 24px rgba(0,255,136,0.95) !important;
        }
        .usp-login-card div[data-testid="stTextInput"]:focus-within label p,
        .usp-login-card div[data-testid="stTextInput"]:focus-within label {
            color: #00ff88 !important;
            text-shadow: 0 0 12px rgba(0,255,136,0.9) !important;
        }
        </style>
        <div class="candles-bg"></div>
        <div class="usp-login-wrap">
        """,
        unsafe_allow_html=True,
    )

    apply_mobile_styles(st)
    if st.session_state.get("_public_commercial_view"):
        st.markdown("""
        <style>
        [data-testid='stAppViewContainer'] { background: radial-gradient(circle at 15% 10%, rgba(34,197,94,.18), transparent 30%), linear-gradient(135deg,#030504,#06120c 48%,#010201); color:#f8fafc; }
        .public-shell { max-width:1120px; margin:0 auto; padding:18px 8px 40px; }
        .public-shell h1 { color:#f8fafc; font-size:clamp(30px,4vw,46px); margin:0 0 8px; }
        .public-plan-card,.public-signup-card { background:rgba(2,12,7,.86); border:1px solid rgba(34,197,94,.4); border-radius:16px; padding:18px; box-shadow:0 0 24px rgba(34,197,94,.12); }
        .public-plan-card { min-height:132px; margin-bottom:14px; }
        .public-plan-card h3 { color:#fff; font-size:20px; margin:0 0 12px; }
        .public-price { color:#22c55e; font-weight:900; font-size:30px; }
        .public-signup-card { margin-top:20px; }
        .public-signup-card h2 { color:#fff; font-size:30px; margin:0 0 8px; }
        .public-signup-card label, .public-signup-card [data-testid='stWidgetLabel'] p { color:#fff !important; font-size:16px !important; font-weight:800 !important; }
        .public-signup-card input { background:#fff !important; color:#111827 !important; font-size:16px !important; }
        [data-testid="stFormSubmitButton"] button,
        [data-testid="stFormSubmitButton"] button:hover,
        [data-testid="stFormSubmitButton"] button:focus { background:linear-gradient(90deg,#16a34a,#22c55e) !important; color:#03140a !important; border:1px solid #4ade80 !important; font-weight:900 !important; }
        [data-testid="stFormSubmitButton"] button:disabled { opacity:.55 !important; color:#cbd5e1 !important; background:#1f2937 !important; border-color:#475569 !important; }
        @media (max-width:700px) { .public-shell { padding:10px 2px 24px; } .public-signup-card { padding:14px; } }
        </style>
        """, unsafe_allow_html=True)
        pagina_comercial_publica()
        return
    col_esq, col_dir = st.columns([1.08, 1], gap="large")

    with col_esq:
        st.markdown(
            """
            <div class="usp-brand-title"><span>SMART</span><br>MONEY AI</div>
            <div class="usp-subtitle">Inteligência Institucional para Traders</div>

            <div class="usp-feature"><div class="usp-feature-icon">🎯</div><div><b>Sinais Scalp em Tempo Real</b><small>Entradas rápidas com leitura técnica e fluxo de mercado.</small></div></div>
            <div class="usp-feature"><div class="usp-feature-icon">📊</div><div><b>Tendência Institucional</b><small>Leitura macro para identificar força, direção e oportunidade.</small></div></div>
            <div class="usp-feature"><div class="usp-feature-icon">🛡️</div><div><b>Auditoria de Sinais</b><small>Acompanhamento de desempenho, WIN, P1, P2 e LOSS.</small></div></div>
            <div class="usp-feature"><div class="usp-feature-icon">⚡</div><div><b>Análise de Fluxo Institucional</b><small>Monitoramento de preço, volume, momentum e comportamento institucional.</small></div></div>
            <div class="usp-feature"><div class="usp-feature-icon">🔐</div><div><b>Acesso Multiplataforma</b><small>Disponível em celular, tablet e computador pelo navegador.</small></div></div>
            """,
            unsafe_allow_html=True,
        )

    with col_dir:
        st.markdown(
            """
            <div style="
                background:#000000;
                color:#00ff88;
                padding:12px;
                text-align:center;
                font-size:18px;
                font-weight:900;
                letter-spacing:.6px;
                border-radius:10px;
                margin-bottom:14px;
                box-shadow:0 0 20px rgba(0,255,136,.18);
                border:1px solid rgba(0,255,136,.35);
            ">
                🚀 SMART MONEY AI 4.7 CALIBRADO TESTE
            </div>
            <div class="usp-login-card">
            <div class="usp-login-heading">🔐 LOGIN</div>
            <div class="usp-login-line"></div>
            """,
            unsafe_allow_html=True,
        )
        with st.form("form_login"):
            usuario = st.text_input("E-mail", placeholder="Digite seu e-mail").strip().lower()
            senha = st.text_input("Senha", placeholder="Digite sua senha", type="password")
            encerrar_anteriores = st.checkbox("Encerrar sessões anteriores ao entrar", value=False, key="login_encerrar_anteriores")
            col_btn_login, col_msg_login = st.columns([1.15, 1.55])
            with col_btn_login:
                entrar = st.form_submit_button("ENTRAR  →")
            with col_msg_login:
                erro_login_box = st.empty()

        if st.button("🚀 ASSINAR AGORA", use_container_width=True, key="btn_assinar_agora"):
            st.session_state["_public_commercial_view"] = True
            st.rerun()
        if st.button("🆓 CRIAR CONTA GRÁTIS", use_container_width=True, key="btn_criar_conta_gratis"):
            st.session_state["_public_commercial_view"] = True
            st.rerun()
        st.caption("Ainda não tem acesso? Clique para conhecer os planos e comprar pelo site oficial.")

        st.caption("A sessão é restaurada neste dispositivo enquanto válida. Use Sair para encerrá-la.")
        if entrar:
            ok_login, msg_login = autenticar_credenciais(usuario, senha, encerrar_anteriores)
            if not ok_login:
                erro_login_box.error(msg_login)
            else:
                registrar_acesso(usuario, "LOGIN_OK")
                registrar_pagina_visitada(usuario, "Login", "Login realizado com sucesso")
                try:
                    cliente = st.session_state.pop("_cliente_login_confirmado", None) or cliente_autorizado(usuario)
                    if cliente_e_demo(cliente):
                        notificar_admin_acesso_demo(usuario, cliente)
                except Exception:
                    pass
                st.rerun()

        with st.expander("🔑 ESQUECI MINHA SENHA", expanded=False):
            st.caption("Solicite um código por e-mail. Sua senha só será alterada após a confirmação abaixo.")
            with st.form("form_recuperar_senha"):
                email_reset = st.text_input("E-mail cadastrado", placeholder="seuemail@gmail.com", key="email_reset_login").strip().lower()
                enviar_reset = st.form_submit_button("Enviar código de recuperação")
            if enviar_reset:
                ok_reset, msg_reset = redefinir_senha_cliente_por_email(email_reset)
                if ok_reset:
                    st.success(msg_reset)
                else:
                    st.error(msg_reset)
            with st.form("form_confirmar_recuperacao"):
                st.caption("Confirmar recuperação — código válido por 20 minutos.")
                email_confirmacao = st.text_input("E-mail da recuperação", key="reset_confirm_email")
                codigo_reset = st.text_input("Código recebido por e-mail", type="password", key="reset_codigo")
                senha_reset = st.text_input("Nova senha (mínimo 8 caracteres)", type="password", key="reset_nova_senha")
                senha_reset_confirm = st.text_input("Confirme a nova senha", type="password", key="reset_confirm_senha")
                confirmar_reset = st.form_submit_button("Confirmar nova senha")
            if confirmar_reset:
                ok_reset, msg_reset = concluir_recuperacao_senha(email_confirmacao, codigo_reset, senha_reset, senha_reset_confirm)
                if ok_reset:
                    st.success(msg_reset)
                else:
                    st.error(msg_reset)

        st.markdown("<div class='forgot-caption'>Precisa de ajuda?</div>", unsafe_allow_html=True)
        link_whats = suporte_whatsapp_link()
        email_link = suporte_email_link()
        if link_whats:
            whats_html = f'<a href="{link_whats}" target="_blank">💬 WhatsApp Suporte</a>'
        else:
            whats_html = '<div class="support-disabled">⚠ WhatsApp não configurado</div>'
        st.markdown(
            f'''<div class="support-card-row">
                {whats_html}
                <a href="{email_link}" target="_blank">📧 Suporte por e-mail</a>
            </div>''',
            unsafe_allow_html=True,
        )

        st.markdown(
            """
            <div class="usp-security-row">
                <div class="usp-pill">👤 Acesso individual</div>
                <div class="usp-pill">🛡️ Ambiente seguro</div>
                <div class="usp-pill">🧠 Smart Money AI</div>
            </div>
            <div class="usp-footer-mini">🔒 Acesso protegido. Sessões simultâneas podem ser bloqueadas.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(
        """
        <div class="usp-risk-card">
            <h3>🛡️ Gestão de Risco Inteligente</h3>
            <p>
            Esta plataforma utiliza algoritmos avançados de inteligência artificial para identificar oportunidades de mercado com base em indicadores técnicos, fluxo de preço e comportamento de participantes institucionais.
            Apesar da tecnologia empregada e dos processos de validação dos sinais, o mercado financeiro envolve riscos e resultados futuros não podem ser garantidos.
            Para preservar seu capital, mantenha uma gestão de risco disciplinada, evite decisões impulsivas e considere limitar sua exposição a <b>até 2% do capital por operação</b>.
            Lembre-se: a consistência no mercado depende tanto da qualidade dos sinais quanto da gestão adequada do risco.
            </p>
        </div>
        <div class="usp-footer-mini">© 2026 Smart Money AI 4.3 FASE 5.1+ AUDITORIA SUPABASE COM COLUNAS • Ambiente de desenvolvimento</div>
        <div style="text-align:center;font-size:13px;color:#9ca3af;margin-top:12px;line-height:1.8;">
            <a href="?legal=privacidade" target="_self" style="color:#00ff88;text-decoration:none;font-weight:700;">Política de Privacidade</a>
            &nbsp;&nbsp;|&nbsp;&nbsp;
            <a href="?legal=termos" target="_self" style="color:#00ff88;text-decoration:none;font-weight:700;">Termos de Uso</a>
            &nbsp;&nbsp;|&nbsp;&nbsp;
            <a href="mailto:contato.ultrasinais@gmail.com" style="color:#00ff88;text-decoration:none;font-weight:700;">Contato de suporte</a>
            <br>
            <span style="font-size:12px;color:#7f8ea3;">LGPD: seus dados são utilizados apenas para controle de acesso, suporte, cobranças e segurança da conta.</span>
        </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.stop()


def voltar_para_login_botao(key_suffix: str = "") -> None:
    if st.button("⬅️ Voltar para Login", key=f"btn_voltar_login_{key_suffix}"):
        try:
            remover_login_persistente()
        except Exception:
            pass  # a limpeza local ocorre no finally; nenhum acesso é autorizado
        st.rerun()


def render_resultado_confirmacao_email() -> None:
    """Exibe o resultado do redirecionamento sem repetir a confirmação."""
    try:
        resultado = str(st.query_params.get("signup_confirmation", "")).strip().lower()
    except Exception:
        resultado = ""
    if resultado not in {"success", "failed"}:
        return
    chave = f"email_confirmation_result:{resultado}"
    if st.session_state.get("_email_confirmation_result_seen") == chave:
        return
    st.session_state["_email_confirmation_result_seen"] = chave
    try:
        st.query_params.clear()
    except Exception:
        pass
    st.markdown("<div class='public-shell'>", unsafe_allow_html=True)
    if resultado == "success":
        st.success("Cadastro confirmado")
        st.write("Seu e-mail foi confirmado. Agora você pode entrar no Smart Money AI.")
        if st.button("Entrar", key="btn_confirmacao_entrar", use_container_width=True):
            st.session_state["authenticated"] = False
            st.session_state["usuario_logado"] = ""
            st.rerun()
    else:
        st.error("Link inválido ou expirado")
        st.write("Solicite um novo cadastro para receber outro link de confirmação.")
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()


def _base_autorizacao_disponivel() -> bool:
    """Verifica disponibilidade da base com consulta minima, sem listar clientes."""
    if supabase_configurado():
        # A consulta autenticada confirma a disponibilidade da base mesmo quando
        # a tabela ainda não possui linhas. O conteúdo da resposta não é um
        # requisito de disponibilidade.
        _sb_get("clientes", params={"select": "email", "limit": "1"})
        return True
    return not listar_clientes_db().empty


def exigir_login() -> None:
    """Fail closed em ambos os backends; não existe modo livre sem login."""
    try:
        if not _seguranca_operar("saude")["ok"] or not _base_autorizacao_disponivel():
            raise RuntimeError("Configuração/base de acesso não disponível.")
    except Exception as exc:
        logging.getLogger(__name__).warning("Autorização indisponível: %s", type(exc).__name__)
        _limpar_autenticacao_local()
        if _limpeza_browser_pendente():
            st.rerun()
        st.error("Acesso temporariamente indisponível. A configuração e a base de autorização precisam estar disponíveis.")
        st.stop()
    if not restaurar_login_persistente():
        if _limpeza_browser_pendente():
            st.rerun()
        tela_login()
        st.stop()


def botao_logout_sidebar() -> None:
    usuario = st.session_state.get("usuario_logado")
    if usuario:
        st.sidebar.success(f"🔐 Logado: {usuario}")
        if st.sidebar.button("Sair", key="btn_logout"):
            try:
                remover_login_persistente()
            except Exception:
                st.warning("Sessão local encerrada; revogação remota não confirmada. O vínculo local foi descartado.")
            st.rerun()

if not _sincronizar_sessao_browser():
    st.info("Preparando a sessão neste dispositivo...")
    st.stop()
if st.session_state.get("_browser_storage_unavailable"):
    st.warning("O armazenamento deste navegador está indisponível. O login funcionará nesta abertura, mas poderá ser solicitado novamente ao reabrir.")
render_pagina_legal_publica()
render_resultado_confirmacao_email()
exigir_login()
try:
    registrar_evento_demo_unico("Entrada na Plataforma", "Usuário acessou a área interna")
except Exception:
    pass
# Segurança: se o Supabase estiver configurado, validamos antes de operar para não salvar clientes em banco temporário.
try:
    if usuario_logado_e_admin():
        # Admin verá o diagnóstico na aba Administração; não bloqueia a tela inteira aqui.
        pass
except Exception:
    pass
aplicar_marca_dagua_usuario()

st.markdown("""
<style>
.main { background-color: #0e1117; }
div.stButton > button:first-child {
    background-color: #2e7d32; color: white; border-radius: 8px;
    border: none; padding: 0.5rem 2rem; font-weight: bold; width: 100%;
}
div.stButton > button:first-child:hover { background-color: #1b5e20; }
.bloco-sinal {
    padding: 15px; border-radius: 8px; border: 1px solid #2e7d32;
    text-align: center; font-weight: bold; font-size: 20px; margin-bottom: 15px;
}
div[data-testid="stLinkButton"] a {
    background: linear-gradient(90deg, #16a34a, #22c55e) !important;
    color: #ffffff !important;
    border-radius: 10px !important;
    border: 1px solid rgba(255,255,255,0.15) !important;
    font-weight: 900 !important;
    text-decoration: none !important;
}
</style>
""", unsafe_allow_html=True)

# Gemini agora é OPCIONAL.
# O app funciona sem GEMINI_KEY usando o motor técnico local.
GEMINI_OK = False
try:
    API_KEY = st.secrets.get("GEMINI_KEY", "")
    if API_KEY:
        genai.configure(api_key=API_KEY)
        GEMINI_OK = True
except Exception:
    GEMINI_OK = False

if "resultado_scalp" not in st.session_state:
    st.session_state.resultado_scalp = None
if "resultado_macro" not in st.session_state:
    st.session_state.resultado_macro = None
if "historico_sinais" not in st.session_state:
    st.session_state.historico_sinais = carregar_lista_csv(HISTORICO_FILE)
if "auditoria_sinais" not in st.session_state:
    st.session_state.auditoria_sinais = []  # carregada após funções persistentes serem definidas

# =============================
# FUNÇÕES DE MERCADO
# =============================

def nome_ativo(symbol: str) -> str:
    if "BTC" in symbol:
        return "BITCOIN"
    if "ETH" in symbol:
        return "ETHEREUM"
    if "SOL" in symbol:
        return "SOLANA"
    if "BNB" in symbol:
        return "BNB"
    return symbol


@st.cache_data(ttl=20, show_spinner=False)
def buscar_candles_binance(symbol: str, interval: str, limit: int = 210) -> pd.DataFrame:
    # No Streamlit Cloud alguns IPs podem falhar em api.binance.com.
    # Por isso tentamos vários endpoints públicos oficiais/espelhos da Binance.
    endpoints = [
        "https://api.binance.com/api/v3/klines",
        "https://api1.binance.com/api/v3/klines",
        "https://api2.binance.com/api/v3/klines",
        "https://api3.binance.com/api/v3/klines",
        "https://data-api.binance.vision/api/v3/klines",
    ]
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    headers = {"User-Agent": "Mozilla/5.0 Streamlit-Sinais/1.0"}

    ultimo_erro = None
    dados = None

    for url in endpoints:
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 200:
                dados = r.json()
                break
            ultimo_erro = f"{url} retornou HTTP {r.status_code}: {r.text[:180]}"
        except Exception as e:
            ultimo_erro = f"{url} falhou: {e}"

    if dados is None:
        raise RuntimeError(
            "Não consegui buscar candles da Binance neste momento. "
            "Isso costuma acontecer quando o Streamlit Cloud bloqueia ou é bloqueado pela Binance. "
            f"Último erro: {ultimo_erro}"
        )

    df = pd.DataFrame(dados, columns=[
        "Open time", "Open", "High", "Low", "Close", "Volume",
        "Close time", "Quote asset volume", "Number of trades",
        "Taker buy base", "Taker buy quote", "Ignore"
    ])
    for col in ["Open", "High", "Low", "Close", "Volume", "Taker buy base", "Taker buy quote"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Open time"] = pd.to_datetime(df["Open time"], unit="ms", utc=True).dt.tz_convert(fuso_br)
    df["Close time"] = pd.to_datetime(df["Close time"], unit="ms", utc=True).dt.tz_convert(fuso_br)
    return df


# =============================
# MULTIMERCADO 4.2 FASE 5 — CRIPTO, OPÇÕES, FOREX E B3
# =============================
FOREX_ATIVOS = {
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "AUDUSD=X": "AUD/USD",
    "USDCAD=X": "USD/CAD",
    "USDCHF=X": "USD/CHF",
    "NZDUSD=X": "NZD/USD",
    "EURJPY=X": "EUR/JPY",
    "GBPJPY=X": "GBP/JPY",
}

B3_ATIVOS = {
    "PETR4.SA": "PETR4",
    "VALE3.SA": "VALE3",
    "ITUB4.SA": "ITUB4",
    "BBDC4.SA": "BBDC4",
    "ABEV3.SA": "ABEV3",
    "BBAS3.SA": "BBAS3",
    "WEGE3.SA": "WEGE3",
    "MGLU3.SA": "MGLU3",
    "BOVA11.SA": "BOVA11",
    "^BVSP": "IBOV",
}

OPCOES_BINARIAS_ATIVOS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LTCUSDT"
]

def lista_ativos_por_mercado(mercado: str, lista_cripto: list) -> list:
    mercado = (mercado or "Cripto").lower()
    if "op" in mercado:
        return OPCOES_BINARIAS_ATIVOS
    if "forex" in mercado:
        return list(FOREX_ATIVOS.keys())
    if "b3" in mercado:
        return list(B3_ATIVOS.keys())
    return lista_cripto


def nome_ativo_multimercado(symbol: str, mercado: str = "Cripto") -> str:
    mercado = mercado or "Cripto"
    if mercado == "Forex":
        return FOREX_ATIVOS.get(symbol, symbol)
    if mercado == "B3":
        return B3_ATIVOS.get(symbol, symbol.replace(".SA", ""))
    return nome_ativo(symbol)


def ordem_label_por_mercado(direcao: str, mercado: str = "Cripto") -> str:
    if mercado == "Opções Binárias":
        return "🟢 CALL" if direcao == "COMPRA" else "🔴 PUT"
    return "🟢 COMPRA" if direcao == "COMPRA" else "🔴 VENDA"


@st.cache_data(ttl=20, show_spinner=False)
def buscar_candles_yahoo(symbol: str, interval: str, limit: int = 210) -> pd.DataFrame:
    """Busca candles em fonte pública Yahoo para Forex e B3.
    É um módulo de teste: a disponibilidade/atraso pode variar conforme o ativo.
    Para produção em B3, o ideal é contratar um fornecedor oficial de dados em tempo real.
    """
    intervalo = interval if interval in ["1m", "5m"] else "5m"
    range_param = "1d" if intervalo == "1m" else "5d"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
    params = {"interval": intervalo, "range": range_param, "includePrePost": "true"}
    headers = {"User-Agent": "Mozilla/5.0 Streamlit-Sinais/1.0"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    if r.status_code >= 400:
        raise RuntimeError(f"Yahoo retornou HTTP {r.status_code}: {r.text[:160]}")
    data = r.json()
    result = (data.get("chart", {}) or {}).get("result") or []
    if not result:
        erro = (data.get("chart", {}) or {}).get("error")
        raise RuntimeError(f"Sem dados para {symbol}. Detalhe: {erro}")
    res = result[0]
    ts = res.get("timestamp") or []
    quote = (res.get("indicators", {}) or {}).get("quote", [{}])[0]
    if not ts or not quote:
        raise RuntimeError(f"Sem candles suficientes para {symbol}.")
    df = pd.DataFrame({
        "Open time": pd.to_datetime(ts, unit="s", utc=True).tz_convert(fuso_br),
        "Open": quote.get("open", []),
        "High": quote.get("high", []),
        "Low": quote.get("low", []),
        "Close": quote.get("close", []),
        "Volume": quote.get("volume", []),
    })
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).tail(limit).copy()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Volume"] = df["Volume"].fillna(0)
    # Forex normalmente vem sem volume real; usamos volume sintético neutro para não distorcer o motor.
    if float(df["Volume"].sum() or 0) <= 0:
        df["Volume"] = 1.0
    df["Close time"] = df["Open time"] + pd.to_timedelta(int(intervalo.replace("m", "")), unit="m")
    df["Taker buy base"] = df["Volume"] / 2
    df["Taker buy quote"] = df["Volume"] * df["Close"] / 2
    return df


def buscar_candles_mercado(mercado: str, symbol: str, interval: str, limit: int = 210) -> pd.DataFrame:
    if mercado in ["Forex", "B3"]:
        return buscar_candles_yahoo(symbol, interval, limit=limit)
    return buscar_candles_binance(symbol, interval, limit=limit)


def calcular_horarios_sinal_mercado(timeframe: str, mercado: str = "Cripto"):
    return calcular_horarios_sinal(timeframe)


def aviso_mercado_teste(mercado: str) -> str:
    if mercado == "Opções Binárias":
        return "Módulo de teste: COMPRA vira CALL e VENDA vira PUT. Use conta demo para validar."
    if mercado == "Forex":
        return "Módulo Forex em teste com dados públicos. Para produção, recomendamos feed profissional."
    if mercado == "B3":
        return "Módulo B3 em teste com dados públicos/possível atraso. Para produção, use fornecedor oficial de tempo real."
    return "Cripto com candles públicos da Binance."


def calcular_rsi(close: pd.Series, periodo: int) -> pd.Series:
    """Calcula RSI simples para períodos diferentes.
    Na versão 4.0 TESTE usamos RSI3/RSI5 para leitura rápida de scalp
    e mantemos RSI14 para referência clássica.
    """
    delta = close.diff()
    ganho = delta.where(delta > 0, 0).rolling(periodo).mean()
    perda = (-delta.where(delta < 0, 0)).rolling(periodo).mean()
    rs = ganho / (perda + 1e-10)
    return 100 - (100 / (1 + rs))


def calcular_indicadores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()

    # RSI clássico e RSI curto para scalp.
    df["RSI3"] = calcular_rsi(df["Close"], 3)
    df["RSI5"] = calcular_rsi(df["Close"], 5)
    df["RSI14"] = calcular_rsi(df["Close"], 14)

    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_HIST"] = df["MACD"] - df["MACD_SIGNAL"]

    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    # Em Forex/B3 de teste pode não haver volume real. Se volume vier zerado, usamos média típica como VWAP neutra.
    if float(df["Volume"].fillna(0).sum() or 0) <= 0:
        df["Volume"] = 1.0
    df["VWAP"] = (typical * df["Volume"]).cumsum() / (df["Volume"].cumsum() + 1e-10)
    df["VOL_MEDIA20"] = df["Volume"].rolling(20).mean()
    df["VOL_RELATIVO"] = df["Volume"] / (df["VOL_MEDIA20"] + 1e-10)

    # ATR14 para bloquear, no futuro, mercado travado ou volatilidade exagerada.
    high_low = df["High"] - df["Low"]
    high_close_prev = (df["High"] - df["Close"].shift()).abs()
    low_close_prev = (df["Low"] - df["Close"].shift()).abs()
    true_range = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
    df["ATR14"] = true_range.rolling(14).mean()
    df["ATR_PCT"] = (df["ATR14"] / (df["Close"] + 1e-10)) * 100

    # Bollinger Bands 20,2: usado como filtro de exaustão.
    # Em scalp, compra colada na banda superior pode estar atrasada;
    # venda colada na banda inferior pode estar atrasada.
    df["BB_MEDIA20"] = df["Close"].rolling(20).mean()
    df["BB_STD20"] = df["Close"].rolling(20).std()
    df["BB_SUPERIOR"] = df["BB_MEDIA20"] + (df["BB_STD20"] * 2)
    df["BB_INFERIOR"] = df["BB_MEDIA20"] - (df["BB_STD20"] * 2)
    df["BB_POSICAO"] = (df["Close"] - df["BB_INFERIOR"]) / ((df["BB_SUPERIOR"] - df["BB_INFERIOR"]) + 1e-10)

    # Stoch RSI 14,3,3: leitura rápida de exaustão.
    rsi_base = df["RSI14"]
    rsi_min = rsi_base.rolling(14).min()
    rsi_max = rsi_base.rolling(14).max()
    stoch_raw = ((rsi_base - rsi_min) / ((rsi_max - rsi_min) + 1e-10)) * 100
    df["STOCH_RSI_K"] = stoch_raw.rolling(3).mean()
    df["STOCH_RSI_D"] = df["STOCH_RSI_K"].rolling(3).mean()

    df["CORPO"] = df["Close"] - df["Open"]
    df["AMPLITUDE"] = df["High"] - df["Low"]
    df["FORCA_CANDLE"] = df["CORPO"] / (df["AMPLITUDE"] + 1e-10)

    # FASE 5.1 — fluxo + auditoria simplificado sem DOM profissional.
    # Aproxima pressão compradora/vendedora usando direção do candle, fechamento
    # dentro da amplitude e volume relativo. Isso NÃO substitui Times & Trades,
    # mas ajuda a filtrar rompimentos fracos no 1m/5m.
    pos_fechamento = (df["Close"] - df["Low"]) / ((df["High"] - df["Low"]) + 1e-10)
    df["POS_FECHAMENTO"] = pos_fechamento.clip(0, 1)
    df["PRESSAO_COMPRA"] = ((df["POS_FECHAMENTO"] * 60) + ((df["CORPO"] > 0).astype(int) * 25) + (df["VOL_RELATIVO"].clip(0, 2.5) * 6)).clip(0, 100)
    df["PRESSAO_VENDA"] = (((1 - df["POS_FECHAMENTO"]) * 60) + ((df["CORPO"] < 0).astype(int) * 25) + (df["VOL_RELATIVO"].clip(0, 2.5) * 6)).clip(0, 100)
    df["FLUXO_DELTA"] = df["PRESSAO_COMPRA"] - df["PRESSAO_VENDA"]

    # Velocidade do movimento: distância do preço para EMA9 e variação curta.
    df["RETORNO_1"] = df["Close"].pct_change() * 100
    df["RETORNO_3"] = df["Close"].pct_change(3) * 100
    df["DIST_EMA9_PCT"] = ((df["Close"] - df["EMA9"]) / (df["Close"] + 1e-10)) * 100
    return df


def calcular_setup_score_preview(df: pd.DataFrame) -> tuple[int, str]:
    """Pontuação inicial apenas para leitura na tela.
    Nesta fase não bloqueia nem muda o sinal; serve para comparar com a 3.5.
    """
    if df is None or df.empty:
        return 0, "sem dados"
    u = df.iloc[-1]
    score = 0
    motivos = []

    if float(u.get("Close", 0)) > float(u.get("VWAP", 0)):
        score += 20; motivos.append("acima VWAP")
    else:
        motivos.append("abaixo VWAP")

    if float(u.get("EMA9", 0)) > float(u.get("EMA21", 0)):
        score += 20; motivos.append("EMA alta")
    else:
        motivos.append("EMA baixa")

    if float(u.get("MACD_HIST", 0)) > 0:
        score += 15; motivos.append("MACD positivo")
    else:
        motivos.append("MACD negativo")

    rsi3 = float(u.get("RSI3", 50) or 50)
    rsi5 = float(u.get("RSI5", 50) or 50)
    if 20 <= rsi3 <= 80 and 20 <= rsi5 <= 80:
        score += 15; motivos.append("RSI curto saudável")
    else:
        motivos.append("RSI curto extremo")

    vol_rel = float(u.get("VOL_RELATIVO", 0) or 0)
    if vol_rel >= 1.2:
        score += 15; motivos.append("volume forte")
    else:
        motivos.append("volume fraco")

    atr_pct = float(u.get("ATR_PCT", 0) or 0)
    if 0.03 <= atr_pct <= 1.50:
        score += 15; motivos.append("ATR saudável")
    else:
        motivos.append("ATR fora da faixa")

    return int(min(score, 100)), "; ".join(motivos)


def calcular_datetimes_sinal(timeframe: str, agora: datetime | None = None) -> dict:
    """Define uma única entrada e deriva todos os prazos no fuso atual."""
    agora = agora if agora is not None else datetime.now(fuso_br)
    if agora.tzinfo is None:
        agora = fuso_br.localize(agora)
    agora = agora.astimezone(fuso_br)
    minutos = int(timeframe.replace("m", ""))
    if minutos not in (1, 5):
        raise ValueError("Timeframe de sinal inválido.")
    base = agora.replace(second=0, microsecond=0)
    entrada = base + timedelta(minutes=minutos - (base.minute % minutos))
    return {
        "entrada": entrada,
        "p1": entrada + timedelta(minutes=minutos),
        "p2": entrada + timedelta(minutes=minutos * 2),
        "p3": entrada + timedelta(minutes=minutos * 3),
        "p5": entrada + timedelta(minutes=minutos * 5),
    }


def calcular_horarios_sinal(timeframe: str):
    horarios = calcular_datetimes_sinal(timeframe)
    return tuple(horarios[campo].strftime("%H:%M") for campo in ("entrada", "p1", "p2"))


def score_tecnico_local(df: pd.DataFrame) -> tuple[str, int, int, str, str]:
    """Motor técnico 4.2 FASE 5 — tendência + volume + fluxo simplificado.

    O app continua SEMPRE entregando COMPRA ou VENDA.
    A Fase 5 melhora a qualidade do sinal com:
    - força estrutural da tendência;
    - volume relativo inteligente;
    - pressão compradora/vendedora estimada;
    - velocidade do candle;
    - exaustão reduzindo confiança, sem bloquear.
    """
    u = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else u
    ult3 = df.tail(3)
    ult5 = df.tail(5)

    close = float(u.get("Close", 0) or 0)
    open_ = float(u.get("Open", close) or close)
    ema9 = float(u.get("EMA9", close) or close)
    ema21 = float(u.get("EMA21", close) or close)
    ema50 = float(u.get("EMA50", close) or close)
    vwap = float(u.get("VWAP", close) or close)
    macd_hist = float(u.get("MACD_HIST", 0) or 0)
    rsi14 = float(u.get("RSI14", 50) or 50)
    rsi5 = float(u.get("RSI5", 50) or 50)
    rsi3 = float(u.get("RSI3", 50) or 50)
    vol_rel = float(u.get("VOL_RELATIVO", 1) or 1)
    atr_pct = float(u.get("ATR_PCT", 0) or 0)
    bb_pos = float(u.get("BB_POSICAO", 0.5) or 0.5)
    stoch_k = float(u.get("STOCH_RSI_K", 50) or 50)
    stoch_d = float(u.get("STOCH_RSI_D", 50) or 50)
    stoch_k_prev = float(prev.get("STOCH_RSI_K", stoch_k) or stoch_k)
    pressao_compra = float(u.get("PRESSAO_COMPRA", 50) or 50)
    pressao_venda = float(u.get("PRESSAO_VENDA", 50) or 50)
    fluxo_delta = float(u.get("FLUXO_DELTA", 0) or 0)
    retorno_3 = float(u.get("RETORNO_3", 0) or 0)
    dist_ema9_pct = float(u.get("DIST_EMA9_PCT", 0) or 0)

    corpos_positivos = int((ult3["CORPO"] > 0).sum())
    corpos_negativos = int((ult3["CORPO"] < 0).sum())
    candle_comprador = close > open_
    candle_vendedor = close < open_

    # 1) Direção obrigatória: COMPRA ou VENDA.
    compra_score = 0
    venda_score = 0
    motivos_direcao = []

    if close > vwap:
        compra_score += 22; motivos_direcao.append("preço acima da VWAP")
    else:
        venda_score += 22; motivos_direcao.append("preço abaixo da VWAP")

    if ema9 > ema21:
        compra_score += 20; motivos_direcao.append("EMA9 acima da EMA21")
    else:
        venda_score += 20; motivos_direcao.append("EMA9 abaixo da EMA21")

    if close > ema9:
        compra_score += 12; motivos_direcao.append("preço acima da EMA9")
    else:
        venda_score += 12; motivos_direcao.append("preço abaixo da EMA9")

    if macd_hist > 0:
        compra_score += 14; motivos_direcao.append("MACD positivo")
    else:
        venda_score += 14; motivos_direcao.append("MACD negativo")

    if rsi14 >= 52:
        compra_score += 10; motivos_direcao.append("RSI14 favorece compra")
    elif rsi14 <= 48:
        venda_score += 10; motivos_direcao.append("RSI14 favorece venda")

    if corpos_positivos >= 2:
        compra_score += 10; motivos_direcao.append("últimos candles compradores")
    elif corpos_negativos >= 2:
        venda_score += 10; motivos_direcao.append("últimos candles vendedores")

    # Fase 5: força de tendência por estrutura e alinhamento.
    tendencia_alta_forte = close > ema9 > ema21 and close > vwap and macd_hist > 0
    tendencia_baixa_forte = close < ema9 < ema21 and close < vwap and macd_hist < 0
    tendencia_alta_media = close > ema21 and ema9 > ema21
    tendencia_baixa_media = close < ema21 and ema9 < ema21

    if tendencia_alta_forte:
        compra_score += 16; motivos_direcao.append("tendência compradora forte")
    elif tendencia_alta_media:
        compra_score += 8; motivos_direcao.append("tendência compradora média")

    if tendencia_baixa_forte:
        venda_score += 16; motivos_direcao.append("tendência vendedora forte")
    elif tendencia_baixa_media:
        venda_score += 8; motivos_direcao.append("tendência vendedora média")

    # Fase 5: fluxo/pressão estimada pelo candle + volume.
    if fluxo_delta >= 18:
        compra_score += 12; motivos_direcao.append("pressão compradora dominante")
    elif fluxo_delta <= -18:
        venda_score += 12; motivos_direcao.append("pressão vendedora dominante")
    elif fluxo_delta >= 8:
        compra_score += 6; motivos_direcao.append("leve pressão compradora")
    elif fluxo_delta <= -8:
        venda_score += 6; motivos_direcao.append("leve pressão vendedora")

    # Fase 5: velocidade curta do movimento.
    if retorno_3 > 0.04:
        compra_score += 5; motivos_direcao.append("velocidade curta positiva")
    elif retorno_3 < -0.04:
        venda_score += 5; motivos_direcao.append("velocidade curta negativa")

    direcao = "COMPRA" if compra_score >= venda_score else "VENDA"
    score = int(compra_score if direcao == "COMPRA" else venda_score)

    # 2) Qualidade/probabilidade da entrada.
    confianca = 50
    ajustes = []
    vantagem = abs(compra_score - venda_score)
    confianca += min(24, int(vantagem * 0.42))

    if direcao == "COMPRA":
        if close > vwap: confianca += 8
        if ema9 > ema21: confianca += 8
        if macd_hist > 0: confianca += 6
        if candle_comprador: confianca += 4
        if tendencia_alta_forte: confianca += 9
        elif tendencia_alta_media: confianca += 4
        if fluxo_delta >= 18: confianca += 8; ajustes.append("pressão compradora forte")
        elif fluxo_delta >= 8: confianca += 4; ajustes.append("pressão compradora leve")
        if fluxo_delta <= -15: confianca -= 8; ajustes.append("fluxo contra a compra")
    else:
        if close < vwap: confianca += 8
        if ema9 < ema21: confianca += 8
        if macd_hist < 0: confianca += 6
        if candle_vendedor: confianca += 4
        if tendencia_baixa_forte: confianca += 9
        elif tendencia_baixa_media: confianca += 4
        if fluxo_delta <= -18: confianca += 8; ajustes.append("pressão vendedora forte")
        elif fluxo_delta <= -8: confianca += 4; ajustes.append("pressão vendedora leve")
        if fluxo_delta >= 15: confianca -= 8; ajustes.append("fluxo contra a venda")

    # Volume relativo inteligente.
    if vol_rel >= 2.0:
        confianca += 10; ajustes.append(f"volume muito forte ({vol_rel:.2f}x)")
    elif vol_rel >= 1.5:
        confianca += 7; ajustes.append(f"volume forte ({vol_rel:.2f}x)")
    elif vol_rel >= 1.0:
        confianca += 3; ajustes.append(f"volume normal ({vol_rel:.2f}x)")
    elif vol_rel < 0.65:
        confianca -= 14; ajustes.append(f"volume fraco ({vol_rel:.2f}x)")
    elif vol_rel < 0.85:
        confianca -= 8; ajustes.append(f"volume abaixo da média ({vol_rel:.2f}x)")

    # ATR: mercado travado ou muito violento reduz qualidade.
    if 0.03 <= atr_pct <= 1.50:
        confianca += 3; ajustes.append("ATR saudável")
    else:
        confianca -= 8; ajustes.append("ATR fora da faixa ideal")

    # Exaustão não bloqueia, só reduz confiança.
    compra_esticada = (bb_pos >= 0.88) or (stoch_k >= 82 and stoch_k <= stoch_k_prev) or (rsi3 >= 88 or rsi5 >= 78)
    venda_esticada = (bb_pos <= 0.12) or (stoch_k <= 18 and stoch_k >= stoch_k_prev) or (rsi3 <= 12 or rsi5 <= 22)

    if direcao == "COMPRA" and compra_esticada:
        penalidade = 10
        if bb_pos >= 0.95: penalidade += 5
        if candle_vendedor: penalidade += 4
        if tendencia_alta_forte: penalidade -= 5
        confianca -= max(5, penalidade)
        ajustes.append("compra esticada; possível pullback curto")

    if direcao == "VENDA" and venda_esticada:
        penalidade = 10
        if bb_pos <= 0.05: penalidade += 5
        if candle_comprador: penalidade += 4
        if tendencia_baixa_forte: penalidade -= 5
        confianca -= max(5, penalidade)
        ajustes.append("venda esticada; possível repique curto")

    # Distância excessiva da EMA9 indica entrada atrasada.
    if direcao == "COMPRA" and dist_ema9_pct > 0.18:
        confianca -= 5; ajustes.append("preço afastado da EMA9")
    if direcao == "VENDA" and dist_ema9_pct < -0.18:
        confianca -= 5; ajustes.append("preço afastado da EMA9")

    # Divergência curta: direção principal contra os últimos candles.
    if direcao == "COMPRA" and corpos_negativos >= 2:
        confianca -= 6; ajustes.append("realização nos últimos candles")
    if direcao == "VENDA" and corpos_positivos >= 2:
        confianca -= 6; ajustes.append("repique nos últimos candles")

    confianca = int(max(45, min(94, confianca)))

    if confianca >= 82:
        risco = "BAIXO"
        qualidade = "sinal forte"
    elif confianca >= 70:
        risco = "MÉDIO"
        qualidade = "sinal normal"
    elif confianca >= 60:
        risco = "MÉDIO/ALTO"
        qualidade = "sinal arriscado"
    else:
        risco = "ALTO"
        qualidade = "probabilidade baixa"

    # Texto curto para aparecer no app e no Telegram.
    fluxo_txt = "pressão compradora" if fluxo_delta > 8 else ("pressão vendedora" if fluxo_delta < -8 else "fluxo neutro")
    motivos_principais = motivos_direcao[:4]
    motivo_final = f"{qualidade}; {fluxo_txt}; " + "; ".join(motivos_principais)
    if ajustes:
        motivo_final += "; alerta: " + "; ".join(ajustes[:4])

    return direcao, score, confianca, risco, motivo_final


def recalcular_risco_por_confianca(confianca: int) -> str:
    """Converte confiança calibrada em risco visível ao cliente."""
    try:
        confianca = int(confianca)
    except Exception:
        confianca = 50
    if confianca >= 82:
        return "BAIXO"
    if confianca >= 70:
        return "MÉDIO"
    if confianca >= 60:
        return "MÉDIO/ALTO"
    return "ALTO"


def aplicar_calibracao_estatistica(mercado: str, timeframe: str, direcao: str, confianca: int, risco: str, motivo: str, ultimo_candle: pd.Series) -> tuple[int, str, str]:
    """Ajustes pós-auditoria.

    Baseado nos primeiros CSVs de teste:
    - Forex performou abaixo de Cripto/Binárias, então reduzimos confiança.
    - VENDA exigiu mais confirmação de fluxo/tendência.
    - No 1m, P2 se mostrou mais confiável que P1, então adicionamos aviso educativo.
    """
    ajustes = []
    try:
        confianca = int(confianca)
    except Exception:
        confianca = 50

    mercado_norm = str(mercado or "").strip().lower()
    timeframe_norm = str(timeframe or "").strip().lower()
    direcao_norm = str(direcao or "").strip().upper()

    if "forex" in mercado_norm:
        confianca -= 10
        ajustes.append("calibração estatística: Forex em teste com confiança reduzida")

    if direcao_norm == "VENDA":
        try:
            close = float(ultimo_candle.get("Close", 0) or 0)
            vwap = float(ultimo_candle.get("VWAP", close) or close)
            ema9 = float(ultimo_candle.get("EMA9", close) or close)
            ema21 = float(ultimo_candle.get("EMA21", close) or close)
            macd_hist = float(ultimo_candle.get("MACD_HIST", 0) or 0)
            fluxo_delta = float(ultimo_candle.get("FLUXO_DELTA", 0) or 0)
            rsi5 = float(ultimo_candle.get("RSI5", 50) or 50)
            bb_pos = float(ultimo_candle.get("BB_POSICAO", 0.5) or 0.5)
            venda_confirmada = (close < vwap and ema9 < ema21 and macd_hist < 0 and fluxo_delta <= -8)
            venda_em_fundo = (bb_pos <= 0.18 or rsi5 <= 25)
            if venda_confirmada and not venda_em_fundo:
                confianca += 2
                ajustes.append("venda com confirmação de tendência e fluxo")
            else:
                confianca -= 7
                ajustes.append("venda exige confirmação maior; confiança reduzida")
        except Exception:
            confianca -= 5
            ajustes.append("venda com dados incompletos; confiança reduzida")

    if timeframe_norm == "1m":
        ajustes.append("auditoria inicial: no 1m, P2 tem sido mais confiável que P1")

    confianca = int(max(45, min(94, confianca)))
    risco = recalcular_risco_por_confianca(confianca)
    if ajustes:
        motivo = (motivo or "").rstrip(". ") + "; " + "; ".join(ajustes[:4])
    return confianca, risco, motivo


# Filtros calibrados pelo replay historico read-only (reports/scalp_performance_summary.md).
# Regra conservadora: cortes ruins reduzem confianca; cortes fortes recebem pequeno bonus.
SCALP_PERFORMANCE_FILTERS = {
    ("CRIPTO", "BTCUSDT", "1M", "COMPRA"): {"sinais": 38, "win_rate": 0.7105},
    ("CRIPTO", "BTCUSDT", "1M", "VENDA"): {"sinais": 26, "win_rate": 0.8462},
    ("CRIPTO", "ETHUSDT", "1M", "COMPRA"): {"sinais": 28, "win_rate": 0.8571},
    ("CRIPTO", "ETHUSDT", "1M", "VENDA"): {"sinais": 29, "win_rate": 0.9655},
    ("FOREX", "EURUSD=X", "1M", "COMPRA"): {"sinais": 8, "win_rate": 0.0},
    ("FOREX", "EURUSD=X", "1M", "VENDA"): {"sinais": 3, "win_rate": 0.0},
    ("FOREX", "EURUSD=X", "5M", "VENDA"): {"sinais": 3, "win_rate": 0.3333},
}


def aplicar_filtro_performance_scalp(mercado: str, symbol: str, timeframe: str, direcao: str, confianca: int, risco: str, motivo: str) -> tuple[int, str, str]:
    key = (
        str(mercado or "").strip().upper(),
        str(symbol or "").strip().upper(),
        str(timeframe or "").strip().upper(),
        str(direcao or "").strip().upper(),
    )
    stats = SCALP_PERFORMANCE_FILTERS.get(key)
    if not stats:
        return confianca, risco, motivo

    sinais = int(stats.get("sinais") or 0)
    win_rate = float(stats.get("win_rate") or 0)
    ajuste = 0
    regra = None

    if sinais >= 8 and win_rate < 0.50:
        ajuste = -18
        regra = f"corte historico critico ({sinais} sinais, {win_rate:.0%} de acerto)"
    elif sinais >= 3 and win_rate < 0.50:
        ajuste = -8
        regra = f"corte historico fraco ({sinais} sinais, {win_rate:.0%} de acerto)"
    elif sinais >= 10 and win_rate >= 0.80:
        ajuste = 4
        regra = f"corte historico forte ({sinais} sinais, {win_rate:.0%} de acerto)"
    elif sinais >= 10 and win_rate >= 0.70:
        ajuste = 2
        regra = f"corte historico positivo ({sinais} sinais, {win_rate:.0%} de acerto)"

    if ajuste == 0:
        return confianca, risco, motivo

    confianca = int(max(45, min(94, int(confianca) + ajuste)))
    risco = recalcular_risco_por_confianca(confianca)
    motivo = (motivo or "").rstrip(". ") + f"; filtro estatistico: {regra}"
    return confianca, risco, motivo

def montar_resposta_bloqueada(symbol: str, timeframe: str, entrada: str, p1: str, p2: str, confianca: int, motivo: str) -> str:
    return (
        f"⛔ SINAL BLOQUEADO — SMART MONEY AI 4.7 CALIBRADO TESTE\n"
        f"⭐️ATIVO: {nome_ativo(symbol)}\n"
        f"⏰ TIMEFRAME: {timeframe.upper()}\n"
        f"🎯 ENTRADA ANALISADA: {entrada}\n"
        f"P1: {p1}\n"
        f"P2: {p2}\n"
        f"📌 FORÇA DO SETUP: {confianca}%\n"
        f"⚠️ RISCO: ALTO\n"
        f"🧠 MOTIVO: {motivo}\n"
        f"✅ Ação recomendada: não entrar neste candle; aguardar pullback, rompimento com volume ou novo sinal."
    )

def direcao_fallback(df: pd.DataFrame) -> str:
    return score_tecnico_local(df)[0]


def montar_resposta_sinal(symbol: str, timeframe: str, entrada: str, p1: str, p2: str, direcao: str, confianca: int, risco: str, motivo: str, mercado: str = "Cripto") -> str:
    emoji = ordem_label_por_mercado(direcao, mercado)
    if confianca < 62:
        alerta = "🚨 ALERTA: probabilidade baixa no momento. Sinal entregue, porém com risco alto."
    elif confianca < 70:
        alerta = "⚠️ ALERTA: probabilidade moderada. Pode haver pullback ou repique antes do movimento."
    else:
        alerta = "✅ ALERTA: confluência aceitável para o próximo candle."
    return (
        f"✅ (https://ioxbroker.com) TRADE CONFIRMADO ✅\n"
        f"🌍 MERCADO: {mercado}\n"
        f"⭐️ATIVO: {nome_ativo_multimercado(symbol, mercado)}\n"
        f"⏰ EXPIRAÇÃO: {timeframe.upper()}\n"
        f"📊 ORDEM: {emoji}\n"
        f"🎯 ENTRADA: {entrada}\n"
        f"P1: {p1}\n"
        f"P2: {p2}\n"
        f"📌 CONFIANÇA: {confianca}%\n"
        f"⚠️ RISCO: {risco}\n"
        f"{alerta}\n"
        f"🧠 MOTIVO: {motivo}"
    )


def gerar_sinal_scalp_gemini(symbol: str, timeframe: str, mercado: str = "Cripto") -> tuple[dict, pd.DataFrame]:
    df = buscar_candles_mercado(mercado, symbol, timeframe, limit=210)
    df = calcular_indicadores(df)
    criado_em = datetime.now(fuso_br)
    horarios = calcular_datetimes_sinal(timeframe, criado_em)
    entrada, p1, p2, p3 = (horarios[campo].strftime("%H:%M") for campo in ("entrada", "p1", "p2", "p3"))
    identidade = {
        "id_sinal": criar_id_sinal(mercado, symbol, timeframe),
        "usuario": _usuario_auditoria_atual(),
        "criado_em_iso": criado_em.isoformat(),
        "mercado": mercado,
        "ativo": symbol,
        "timeframe": timeframe,
        **{f"{campo}_iso": valor.isoformat() for campo, valor in horarios.items()},
    }
    ult = df.tail(12).copy()
    u = df.iloc[-1]

    resumo_candles = []
    for _, row in ult.iterrows():
        resumo_candles.append({
            "hora": row["Close time"].strftime("%H:%M"),
            "open": round(float(row["Open"]), 4),
            "high": round(float(row["High"]), 4),
            "low": round(float(row["Low"]), 4),
            "close": round(float(row["Close"]), 4),
            "volume": round(float(row["Volume"]), 2),
            "forca_candle": round(float(row["FORCA_CANDLE"]), 3),
        })

    prompt = f"""
Você é uma IA de scalp para prever APENAS o próximo candle.

MERCADO: {mercado}
ATIVO: {symbol} ({nome_ativo_multimercado(symbol, mercado)})
TIMEFRAME: {timeframe}
HORÁRIO BRASÍLIA AGORA: {datetime.now(fuso_br).strftime('%H:%M:%S')}
HORÁRIO DE ENTRADA JÁ CALCULADO PELO SISTEMA: {entrada}
P1 JÁ CALCULADO: {p1}
P2 JÁ CALCULADO: {p2}
P3 JÁ CALCULADO: {p3}

DADOS ATUAIS:
Preço: {u['Close']:.6f}
EMA9: {u['EMA9']:.6f}
EMA21: {u['EMA21']:.6f}
EMA50: {u['EMA50']:.6f}
EMA200: {u['EMA200']:.6f}
RSI14: {u['RSI14']:.2f}
RSI5: {u['RSI5']:.2f}
RSI3: {u['RSI3']:.2f}
MACD: {u['MACD']:.6f}
MACD_SIGNAL: {u['MACD_SIGNAL']:.6f}
MACD_HIST: {u['MACD_HIST']:.6f}
VWAP: {u['VWAP']:.6f}
Volume atual: {u['Volume']:.2f}
Volume médio 20: {u['VOL_MEDIA20']:.2f}
Volume relativo: {u['VOL_RELATIVO']:.2f}x
ATR14: {u['ATR14']:.6f}
ATR%: {u['ATR_PCT']:.3f}%
Bollinger superior: {u['BB_SUPERIOR']:.6f}
Bollinger inferior: {u['BB_INFERIOR']:.6f}
Posição Bollinger: {u['BB_POSICAO']:.2f}
Stoch RSI K: {u['STOCH_RSI_K']:.2f}
Stoch RSI D: {u['STOCH_RSI_D']:.2f}
Força candle atual: {u['FORCA_CANDLE']:.3f}
Pressão compradora estimada: {u.get('PRESSAO_COMPRA', 50):.1f}
Pressão vendedora estimada: {u.get('PRESSAO_VENDA', 50):.1f}
Delta de fluxo estimado: {u.get('FLUXO_DELTA', 0):.1f}
Retorno curto 3 candles: {u.get('RETORNO_3', 0):.4f}%
Distância da EMA9: {u.get('DIST_EMA9_PCT', 0):.4f}%

ÚLTIMOS 12 CANDLES:
{resumo_candles}

REGRAS OBRIGATÓRIAS:
- Responda SEMPRE com COMPRA ou VENDA. Em Opções Binárias, COMPRA será exibida como CALL e VENDA como PUT.
- NUNCA responda AGUARDAR.
- O objetivo é prever a cor/direção do PRÓXIMO candle.
- Use momentum, EMA9/EMA21, RSI, MACD, VWAP, volume, força dos candles, Bollinger e Stoch RSI.
- Evite COMPRA se o preço estiver esticado na Bollinger superior com Stoch RSI caindo.
- Evite VENDA se o preço estiver esticado na Bollinger inferior com Stoch RSI subindo.
- Mantenha exatamente os horários informados pelo sistema: ENTRADA {entrada}, P1 {p1}, P2 {p2}, P3 {p3}.
- Responda somente no formato abaixo.

FORMATO OBRIGATÓRIO:
✅ (https://ioxbroker.com) TRADE CONFIRMADO ✅
🌍 MERCADO: {mercado}
⭐️ATIVO: {nome_ativo_multimercado(symbol, mercado)}
⏰ EXPIRAÇÃO: {timeframe.upper()}
📊 ORDEM: [🟢 COMPRA ou 🔴 VENDA]
🎯 ENTRADA: {entrada}
P1: {p1}
P2: {p2}
📌 CONFIANÇA: [0-100]%
⚠️ RISCO: [BAIXO, MÉDIO ou ALTO]
🧠 MOTIVO: [máximo 3 linhas, curto e direto]
"""
    usar_gemini = bool(st.session_state.get("usar_gemini_scalp", False)) and GEMINI_OK

    # Primeiro gera sempre um sinal local. Assim o app nunca para por cota da Gemini.
    direcao_local, score, confianca, risco, motivo_local = score_tecnico_local(df)
    # A partir desta fase, o motor local não bloqueia mais sinal:
    # se algum fallback antigo retornar BLOQUEADO, convertemos para COMPRA/VENDA pela tendência.
    if direcao_local == "BLOQUEADO":
        direcao_local = "COMPRA" if float(u.get("Close", 0)) >= float(u.get("VWAP", 0)) else "VENDA"
        risco = "ALTO"
        confianca = min(int(confianca or 55), 60)
        motivo_local = f"probabilidade baixa; {motivo_local}"

    # Calibração estatística após análise dos primeiros 100+ sinais:
    # reduzimos confiança em Forex, exigimos mais confirmação para VENDA
    # e adicionamos aviso de que P2 vem performando melhor que P1 no 1m.
    confianca, risco, motivo_local = aplicar_calibracao_estatistica(
        mercado, timeframe, direcao_local, confianca, risco, motivo_local, u
    )
    confianca, risco, motivo_local = aplicar_filtro_performance_scalp(
        mercado, symbol, timeframe, direcao_local, confianca, risco, motivo_local
    )

    decisao = {
        **identidade,
        "direcao": direcao_local,
        "confianca": confianca,
        "risco": risco,
        "motivo": f"Motor técnico 4.7 calibrado: {motivo_local}.",
    }

    if usar_gemini:
        try:
            modelo = genai.GenerativeModel("gemini-2.5-flash")
            resposta = modelo.generate_content(prompt).text.strip()
            campos = validar_campos_sinal_gemini(resposta)
            if campos is not None:
                decisao.update(campos)
        except Exception:
            decisao["motivo"] = f"Gemini indisponível ou sem cota. Sinal gerado pelo motor técnico 4.7 calibrado: {motivo_local}."

    # A apresentação e a auditoria usam os mesmos campos, inclusive após latência da IA.
    decisao["texto"] = montar_resposta_sinal(
        symbol, timeframe, entrada, p1, p2, decisao["direcao"],
        decisao["confianca"], decisao["risco"], decisao["motivo"], mercado
    )
    return decisao, df



def _campo_unico_sinal(texto: str, nome: str) -> str | None:
    """Lê uma única linha rotulada; palavras no MOTIVO nunca são campos."""
    import re
    linhas = re.findall(
        rf"^[ \t]*(?:[📊⚠️📌🧠][ \t]*)*{nome}[ \t]*[:：][ \t]*(.*?)[ \t]*$",
        texto or "", flags=re.IGNORECASE | re.MULTILINE,
    )
    return linhas[0].strip() if len(linhas) == 1 else None


def extrair_direcao_sinal(texto: str) -> str:
    """Compatibilidade: somente campo ORDEM explícito e sem ambiguidade."""
    valor = _campo_unico_sinal(texto, "ORDEM")
    if valor is None:
        return "INDEFINIDO"
    valor = valor.upper().removeprefix("🟢").removeprefix("🔴").strip()
    return {"COMPRA": "COMPRA", "VENDA": "VENDA", "CALL": "COMPRA", "PUT": "VENDA"}.get(valor, "INDEFINIDO")


def validar_campos_sinal_gemini(texto: str) -> dict | None:
    """Saída inválida mantém a decisão técnica local, sem alterar seus pesos."""
    import re
    direcao = extrair_direcao_sinal(texto)
    risco = extrair_risco_sinal(texto)
    confianca_txt = _campo_unico_sinal(texto, "CONFIAN[ÇC]A")
    motivo = _campo_unico_sinal(texto, "MOTIVO")
    if direcao == "INDEFINIDO" or risco == "N/I" or not motivo:
        return None
    if confianca_txt is None or not re.fullmatch(r"\d{1,3}\s*%", confianca_txt):
        return None
    confianca = int(confianca_txt.replace("%", "").strip())
    if not 0 <= confianca <= 100:
        return None
    # O formato original permite MOTIVO em até três linhas; preserve a continuação.
    linhas = (texto or "").splitlines()
    for indice, linha in enumerate(linhas):
        if _campo_unico_sinal(linha, "MOTIVO") is not None:
            continuacao = []
            for proxima in linhas[indice + 1:]:
                if any(_campo_unico_sinal(proxima, campo) is not None for campo in ("ORDEM", "RISCO", "CONFIAN[ÇC]A", "ENTRADA", "P1", "P2")):
                    break
                continuacao.append(proxima)
            motivo = "\n".join([motivo, *continuacao]).strip()
            break
    return {"direcao": direcao, "risco": risco, "confianca": confianca, "motivo": motivo}


def extrair_confianca_sinal(texto: str) -> int:
    """Extrai a confiança exibida no sinal para auditoria estatística."""
    import re
    t = texto or ""
    padroes = [r"CONFIAN[ÇC]A\s*[:：]\s*(\d{1,3})\s*%", r"(\d{1,3})\s*%"]
    for padrao in padroes:
        m = re.search(padrao, t, flags=re.IGNORECASE)
        if m:
            try:
                return max(0, min(100, int(m.group(1))))
            except Exception:
                pass
    return 0


def extrair_risco_sinal(texto: str) -> str:
    """Lê apenas RISCO, preservando inclusive a classificação MÉDIO/ALTO."""
    valor = _campo_unico_sinal(texto, "RISCO")
    if valor is None:
        return "N/I"
    valor = valor.upper().replace("MEDIO", "MÉDIO")
    return valor if valor in ("BAIXO", "MÉDIO", "MÉDIO/ALTO", "ALTO") else "N/I"


def criar_id_sinal(mercado: str, symbol: str, timeframe: str) -> str:
    """Identidade independente de usuário, ativo e resolução do relógio."""
    return str(uuid.uuid4())


def status_candle(row: pd.Series, direcao: str) -> str:
    if row is None:
        return "PENDENTE"
    abertura = float(row["Open"])
    fechamento = float(row["Close"])
    if fechamento == abertura:
        return "DOJI"
    if direcao == "COMPRA":
        return "WIN" if fechamento > abertura else "LOSS"
    if direcao == "VENDA":
        return "WIN" if fechamento < abertura else "LOSS"
    return "INDEFINIDO"


def criar_registro_auditoria(symbol: str, timeframe: str, decisao: dict, df_sinal: pd.DataFrame, mercado: str = "Cripto") -> dict:
    """Cria registro completo da Fase 5.1 para auditoria automática.
    Salva a decisão, a confiança, o risco e os indicadores no momento do sinal.
    Depois o avaliador preenche P1, P2, P3 ou LOSS com resultado exclusivo.
    """
    if (decisao.get("ativo"), decisao.get("timeframe"), decisao.get("mercado")) != (symbol, timeframe, mercado):
        raise ValueError("Decisão não corresponde ao ativo/timeframe/mercado da auditoria.")
    direcao = decisao["direcao"]
    risco = decisao["risco"]
    if direcao not in ("COMPRA", "VENDA") or risco not in ("BAIXO", "MÉDIO", "MÉDIO/ALTO", "ALTO"):
        raise ValueError("Direção ou risco estruturado inválido.")
    confianca = int(decisao["confianca"])
    agora = datetime.fromisoformat(decisao["criado_em_iso"])
    entrada_dt, p1_dt, p2_dt, p5_dt = (
        datetime.fromisoformat(decisao[f"{campo}_iso"]) for campo in ("entrada", "p1", "p2", "p5")
    )
    p3_dt = datetime.fromisoformat(decisao["p3_iso"]) if decisao.get("p3_iso") else p3_from_entry(entrada_dt, timeframe)
    if any(dt.tzinfo is None for dt in (agora, entrada_dt, p1_dt, p2_dt, p3_dt, p5_dt)):
        raise ValueError("Horários da decisão precisam de timezone.")
    if not entrada_dt < p1_dt < p2_dt < p3_dt < p5_dt:
        raise ValueError("Cronologia da decisão inválida.")
    entrada_txt, p1_txt, p2_txt = (dt.strftime("%H:%M") for dt in (entrada_dt, p1_dt, p2_dt))
    setup_score, setup_motivo = calcular_setup_score_preview(df_sinal)
    u = df_sinal.iloc[-1]

    def fv(nome, padrao=0.0):
        try:
            val = u.get(nome, padrao)
            if pd.isna(val):
                return padrao
            return float(val)
        except Exception:
            return padrao

    return {
        "id_sinal": decisao["id_sinal"],
        "usuario": decisao["usuario"],
        "criado_em": agora.strftime("%d/%m/%Y %H:%M:%S"),
        "criado_em_iso": agora.isoformat(),
        "mercado": mercado,
        "ativo": symbol,
        "nome_ativo": nome_ativo_multimercado(symbol, mercado),
        "timeframe": timeframe,
        "direcao": direcao,
        "confianca": confianca,
        "risco": risco,
        "setup_score": int(setup_score),
        "setup_motivo": setup_motivo,
        "entrada": entrada_txt,
        "p1": p1_txt,
        "p2": p2_txt,
        "p3": p3_dt.strftime("%H:%M"),
        "p5": p5_dt.strftime("%H:%M"),
        "entrada_iso": entrada_dt.isoformat(),
        "p1_iso": p1_dt.isoformat(),
        "p2_iso": p2_dt.isoformat(),
        "p3_iso": p3_dt.isoformat(),
        "p5_iso": p5_dt.isoformat(),
        "preco_geracao": fv("Close"),
        "open_geracao": fv("Open"),
        "high_geracao": fv("High"),
        "low_geracao": fv("Low"),
        "alvo": decisao.get("alvo"),
        "stop": decisao.get("stop"),
        "volume_geracao": fv("Volume"),
        "vwap": fv("VWAP"),
        "ema9": fv("EMA9"),
        "ema21": fv("EMA21"),
        "ema50": fv("EMA50"),
        "ema200": fv("EMA200"),
        "rsi3": fv("RSI3", 50),
        "rsi5": fv("RSI5", 50),
        "rsi14": fv("RSI14", 50),
        "macd_hist": fv("MACD_HIST"),
        "atr14": fv("ATR14"),
        "atr_pct": fv("ATR_PCT"),
        "volume_relativo": fv("VOL_RELATIVO", 1),
        "bb_posicao": fv("BB_POSICAO", 0.5),
        "bb_superior": fv("BB_SUPERIOR"),
        "bb_inferior": fv("BB_INFERIOR"),
        "stoch_rsi_k": fv("STOCH_RSI_K", 50),
        "stoch_rsi_d": fv("STOCH_RSI_D", 50),
        "pressao_compra": fv("PRESSAO_COMPRA", 50),
        "pressao_venda": fv("PRESSAO_VENDA", 50),
        "fluxo_delta": fv("FLUXO_DELTA"),
        "retorno_3": fv("RETORNO_3"),
        "dist_ema9_pct": fv("DIST_EMA9_PCT"),
        "forca_tendencia": int(setup_score),
        "alerta": "ALTA PROBABILIDADE" if confianca >= 80 else ("PROBABILIDADE MODERADA" if confianca >= 65 else "PROBABILIDADE BAIXA"),
        "resultado": "PENDENTE",
        "resultado_expandido": "PENDENTE",
        "resultado_direto": "PENDENTE",
        "resultado_p1": "PENDENTE",
        "resultado_p2": "PENDENTE",
        "resultado_p3": "PENDENTE",
        "resultado_p5": "PENDENTE",
        "open_entrada": None,
        "close_entrada": None,
        "open_p1": None,
        "close_p1": None,
        "open_p2": None,
        "close_p2": None,
        "open_p3": None,
        "close_p3": None,
        "open_p5": None,
        "close_p5": None,
    }

def encontrar_candle_por_abertura(df: pd.DataFrame, alvo_dt: datetime):
    if alvo_dt.tzinfo is None:
        alvo_dt = fuso_br.localize(alvo_dt)
    alvo_floor = pd.Timestamp(alvo_dt).tz_convert("UTC").floor("min")
    tmp = df.copy()
    tmp["Open time floor"] = pd.to_datetime(tmp["Open time"], utc=True).dt.floor("min")
    achado = tmp[tmp["Open time floor"] == alvo_floor]
    if achado.empty:
        return None
    return achado.iloc[0]


def avaliar_um_sinal(reg: dict) -> dict:
    """Avalia P1 -> P2 -> P3 -> LOSS com resultado final exclusivo."""
    try:
        symbol = reg["ativo"]
        timeframe = reg["timeframe"]
        mercado = reg.get("mercado", "Cripto")
        minutos = int(timeframe.replace("m", ""))
        agora = datetime.now(fuso_br)
        entrada_dt = datetime.fromisoformat(reg["entrada_iso"])
        p1_dt = datetime.fromisoformat(reg["p1_iso"])
        p2_dt = datetime.fromisoformat(reg["p2_iso"])
        p3_dt = datetime.fromisoformat(reg["p3_iso"]) if reg.get("p3_iso") else p3_from_entry(entrada_dt, timeframe)
        reg["p3_iso"] = p3_dt.isoformat()
        reg["p3"] = p3_dt.strftime("%H:%M")

        df = buscar_candles_mercado(mercado, symbol, timeframe, limit=700)
        ultimo_open_dt = None
        try:
            if not df.empty:
                ultimo_open_dt = pd.to_datetime(df["Open time"], utc=True).max().tz_convert(fuso_br).to_pydatetime()
        except Exception:
            ultimo_open_dt = None

        def candle_fechado(alvo_dt: datetime) -> bool:
            alvo = fuso_br.localize(alvo_dt) if alvo_dt.tzinfo is None else alvo_dt
            fechamento = alvo + timedelta(minutes=minutos)
            if agora >= fechamento:
                return True
            if ultimo_open_dt is None:
                return False
            ultimo = fuso_br.localize(ultimo_open_dt) if ultimo_open_dt.tzinfo is None else ultimo_open_dt
            return ultimo >= fechamento

        candles = {}
        fechados = set()
        for etapa, dt_etapa in (("P1", p1_dt), ("P2", p2_dt), ("P3", p3_dt)):
            if candle_fechado(dt_etapa):
                fechados.add(etapa)
                candle = encontrar_candle_por_abertura(df, dt_etapa)
                if candle is not None:
                    candle_dict = dict(candle)
                    candles[etapa] = candle_dict
                    etapa_lower = etapa.lower()
                    reg[f"open_{etapa_lower}"] = float(candle_dict["Open"])
                    reg[f"close_{etapa_lower}"] = float(candle_dict["Close"])
                    reg[f"high_{etapa_lower}"] = float(candle_dict["High"])
                    reg[f"low_{etapa_lower}"] = float(candle_dict["Low"])
                else:
                    candles[etapa] = None

        avaliado = evaluate_sequential_audit(reg, candles, fechados)
        avaliado["resultado_expandido"] = avaliado.get("resultado", "PENDENTE")
        avaliado["resultado_direto"] = "PENDENTE"
        avaliado["resultado_p5"] = str(reg.get("resultado_p5", "PENDENTE"))
        return avaliado
    except Exception as e:
        reg["erro_avaliacao"] = type(e).__name__
        reg["resultado"] = str(reg.get("resultado", "PENDENTE") or "PENDENTE")
        reg["resultado_expandido"] = str(reg.get("resultado_expandido", reg.get("resultado", "PENDENTE")) or "PENDENTE")
        return reg

def avaliar_lista_sinais(lista: list[dict]) -> list[dict]:
    return [avaliar_um_sinal(dict(reg)) for reg in lista]


def _dt_from_iso_safe(valor: str):
    try:
        if not valor:
            return None
        dt = datetime.fromisoformat(str(valor))
        if dt.tzinfo is None:
            dt = fuso_br.localize(dt)
        return dt
    except Exception:
        return None


def sinal_pendente_ja_pode_ser_avaliado(reg: dict, usar_p5: bool = False) -> bool:
    """Retorna True quando o sinal já tem tempo suficiente para avaliação até P3."""
    try:
        timeframe = str(reg.get("timeframe", "1m"))
        minutos = int(timeframe.replace("m", ""))
    except Exception:
        minutos = 1
    agora = datetime.now(fuso_br)
    alvo_iso = reg.get("p3_iso") or reg.get("p2_iso")
    alvo_dt = _dt_from_iso_safe(alvo_iso)
    if alvo_dt is None:
        alvo_dt = _dt_from_iso_safe(reg.get("entrada_iso"))
    if alvo_dt is None:
        return False
    # A avaliação final ainda depende de candle fechado; aqui apenas libera tentativa controlada.
    return True


def carregar_pendentes_supabase(limite: int = 80) -> list[dict]:
    """Busca pendentes diretamente do Supabase, sem depender da lista em memória."""
    try:
        if supabase_configurado():
            try:
                dados = _sb_get(
                    AUDITORIA_SUPABASE_TABLE,
                    params={
                        "select": "usuario,dados",
                        "or": "(resultado.eq.PENDENTE,resultado_expandido.eq.PENDENTE)",
                        "order": "criado_em.asc",
                        "limit": str(int(limite)),
                    },
                )
            except Exception:
                dados = _sb_get(
                    AUDITORIA_SUPABASE_TABLE,
                    params={
                        "select": "usuario,dados",
                        "resultado": "eq.PENDENTE",
                        "order": "criado_em.asc",
                        "limit": str(int(limite)),
                    },
                )
            regs = []
            for row in dados:
                reg = _registro_auditoria_da_linha(row)
                if isinstance(reg, dict):
                    regs.append(reg)
            return regs
    except Exception:
        pass
    base = st.session_state.get("auditoria_sinais", []) or carregar_lista_csv(AUDITORIA_FILE)
    return [r for r in base if str(r.get("resultado", "PENDENTE")).upper() == "PENDENTE"][:int(limite)]


def avaliar_pendentes_maduros(maximo: int = 25, buscar_limite: int = 100) -> dict:
    """Conta como salvo somente o resultado confirmado pela persistência."""
    candidatos = carregar_pendentes_supabase(limite=buscar_limite)
    maduros = [r for r in candidatos if sinal_pendente_ja_pode_ser_avaliado(r, usar_p5=False)]
    selecionados = maduros[:int(maximo)]
    resultados = []
    for reg in selecionados:
        avaliado = avaliar_um_sinal(dict(reg))
        resultado = salvar_registro_auditoria_persistente(avaliado)
        resultados.append(resultado)
        if not resultado["sucesso"]:
            st.session_state.setdefault("auditoria_nao_salva", {})[reg["id_sinal"]] = avaliado
    try:
        st.session_state.auditoria_sinais = carregar_auditoria_persistente(limite=100)
    except Exception:
        pass
    return {
        "avaliados": len(selecionados),
        "salvos": sum(r["sucesso"] for r in resultados),
        "falhas": [r for r in resultados if not r["sucesso"]],
        "ignorados": max(0, len(candidatos) - len(maduros)),
    }


# =============================
# FASE 5.1+ — AUDITORIA SUPABASE COM COLUNAS
# =============================
def _usuario_auditoria_atual() -> str:
    try:
        return str(st.session_state.get("usuario_logado", "")).strip().lower() or "anonimo"
    except Exception:
        return "anonimo"


def carregar_auditoria_persistente(limite: int = 200) -> list[dict]:
    """Carrega a auditoria do Supabase para não perder sinais em reboot/rerun.
    Se a tabela ainda não existir, usa o CSV local como fallback temporário.
    """
    try:
        if supabase_configurado():
            dados = _sb_get(
                AUDITORIA_SUPABASE_TABLE,
                params={
                    "select": "usuario,dados",
                    "order": "criado_em.desc",
                    "limit": str(int(limite)),
                },
            )
            saida = []
            for row in dados:
                reg = _registro_auditoria_da_linha(row)
                if isinstance(reg, dict):
                    saida.append(reg)
            return saida
    except Exception as e:
        try:
            st.sidebar.warning(f"Auditoria persistente indisponível. Usando memória local/CSV. Detalhe: {str(e)[:120]}")
        except Exception:
            pass
    return carregar_lista_csv(AUDITORIA_FILE)


def carregar_auditoria_csv_completo(limite: int = 10000) -> pd.DataFrame:
    """Carrega auditoria completa sob demanda para download, sem pesar a abertura da aba."""
    try:
        if supabase_configurado():
            dados = _sb_get(
                AUDITORIA_SUPABASE_TABLE,
                params={
                    "select": "usuario,dados",
                    "order": "criado_em.desc",
                    "limit": str(int(limite)),
                },
            )
            regs = []
            for row in dados:
                reg = _registro_auditoria_da_linha(row)
                if isinstance(reg, dict):
                    regs.append(reg)
            return pd.DataFrame(regs)
    except Exception:
        pass
    return pd.DataFrame(carregar_lista_csv(AUDITORIA_FILE))


def filtrar_auditoria_df(df: pd.DataFrame, ativo: str = "Todos", timeframe: str = "Todos", resultado: str = "Todos", confianca_min: int = 0) -> pd.DataFrame:
    """Filtro leve para deixar a auditoria rápida mesmo com muitos sinais."""
    if df is None or df.empty:
        return pd.DataFrame()
    saida = df.copy()
    if ativo != "Todos" and "ativo" in saida.columns:
        saida = saida[saida["ativo"].astype(str) == str(ativo)]
    if timeframe != "Todos" and "timeframe" in saida.columns:
        saida = saida[saida["timeframe"].astype(str) == str(timeframe)]
    if resultado != "Todos" and "resultado" in saida.columns:
        saida = saida[saida["resultado"].astype(str) == str(resultado)]
    if "confianca" in saida.columns:
        saida["confianca"] = pd.to_numeric(saida["confianca"], errors="coerce").fillna(0)
        saida = saida[saida["confianca"] >= int(confianca_min or 0)]
    return saida


def _registro_auditoria_da_linha(row: dict) -> dict | None:
    dados = row.get("dados")
    if not isinstance(dados, dict):
        return None
    reg = dict(dados)
    # Não atribuir registros legados ao usuário que os consultou/avaliou.
    reg["usuario"] = str(row.get("usuario") or dados.get("usuario") or "").strip().lower()
    return reg


def _resultado_persistencia_auditoria(sucesso: bool, motivo: str, id_sinal: str = "") -> dict:
    resultado = {"sucesso": sucesso, "motivo": motivo, "id_sinal": id_sinal, "destino": "Supabase"}
    if not sucesso:
        logging.getLogger(__name__).error("Auditoria não confirmada id=%s: %s", id_sinal, motivo)
    return resultado


def salvar_registro_auditoria_persistente(reg: dict) -> dict:
    """Confirma a representação gravada; sem fallback silencioso em CSV."""
    if not isinstance(reg, dict) or not reg.get("id_sinal"):
        return _resultado_persistencia_auditoria(False, "Registro sem id_sinal válido.")
    id_sinal = str(reg["id_sinal"])
    if not supabase_configurado():
        return _resultado_persistencia_auditoria(False, "Supabase não configurado. Auditoria não salva remotamente.", id_sinal)
    etapa = "consulta do registro original"
    try:
        existentes = _sb_get(AUDITORIA_SUPABASE_TABLE, params={"select": "id_sinal,usuario,dados", "id_sinal": f"eq.{id_sinal}", "limit": "1"})
        autor_recebido = str(reg.get("usuario") or "").strip().lower()
        autor = str(existentes[0].get("usuario") or (existentes[0].get("dados") or {}).get("usuario") or "").strip().lower() if existentes else autor_recebido
        if not autor:
            return _resultado_persistencia_auditoria(False, "Autor original ausente; gravação recusada sem atribuir o avaliador.", id_sinal)
        if existentes and autor_recebido and autor_recebido != autor:
            return _resultado_persistencia_auditoria(False, "Autor divergente do registro original; alteração recusada.", id_sinal)
        reg = dict(reg, usuario=autor)
        agora_iso = datetime.now(fuso_br).isoformat()
        payload = {
            "id_sinal": str(reg.get("id_sinal")),
            "usuario": autor,
            "mercado": str(reg.get("mercado", "")),
            "ativo": str(reg.get("ativo", "")),
            "timeframe": str(reg.get("timeframe", "")),
            "direcao": str(reg.get("direcao", "")),
            "confianca": float(reg.get("confianca") or 0),
            "risco": str(reg.get("risco", "")),
            "resultado": str(reg.get("resultado", "PENDENTE")),
            "resultado_expandido": str(reg.get("resultado_expandido", "PENDENTE")),
            "resultado_direto": str(reg.get("resultado_direto", "PENDENTE")),
            "resultado_p1": str(reg.get("resultado_p1", "PENDENTE")),
            "resultado_p2": str(reg.get("resultado_p2", "PENDENTE")),
            "resultado_p5": str(reg.get("resultado_p5", "PENDENTE")),
            "score_setup": float(reg.get("setup_score") or 0),
            "volume_relativo": float(reg.get("volume_relativo") or 0),
            "forca_tendencia": float(reg.get("forca_tendencia") or reg.get("setup_score") or 0),
            "alerta": str(reg.get("alerta", "")),
            "setup_motivo": str(reg.get("setup_motivo", "")),
            "preco_geracao": float(reg.get("preco_geracao") or 0),
            "vwap": float(reg.get("vwap") or 0),
            "ema9": float(reg.get("ema9") or 0),
            "ema21": float(reg.get("ema21") or 0),
            "rsi3": float(reg.get("rsi3") or 0),
            "rsi5": float(reg.get("rsi5") or 0),
            "rsi14": float(reg.get("rsi14") or 0),
            "macd_hist": float(reg.get("macd_hist") or 0),
            "atr_pct": float(reg.get("atr_pct") or 0),
            "pressao_compra": float(reg.get("pressao_compra") or 0),
            "pressao_venda": float(reg.get("pressao_venda") or 0),
            "fluxo_delta": float(reg.get("fluxo_delta") or 0),
            "criado_em": str(reg.get("criado_em_iso") or agora_iso),
            "atualizado_em": agora_iso,
            "dados": reg,
        }
        if existentes:
            # PATCH não escreve a coluna usuario nem recria registros removidos.
            payload.pop("usuario")
            payload.pop("criado_em")
            etapa = "atualização PATCH"
            rows = _sb_patch(AUDITORIA_SUPABASE_TABLE, payload, params={"id_sinal": f"eq.{id_sinal}"}, prefer="return=representation")
        else:
            # INSERT sem merge: conflito jamais sobrescreve outro autor/sinal.
            # Uma nova tentativa do mesmo UUID consulta o registro acima.
            etapa = "inserção POST"
            rows = _sb_post(AUDITORIA_SUPABASE_TABLE, payload, prefer="return=representation")
        if (len(rows) != 1 or str(rows[0].get("id_sinal")) != id_sinal
                or str(rows[0].get("usuario") or "").strip().lower() != autor
                or rows[0].get("dados") != reg
                or any(rows[0].get(campo) != payload[campo] for campo in (
                    "direcao", "risco", "confianca", "mercado", "ativo", "timeframe",
                    "resultado", "resultado_expandido", "resultado_direto", "resultado_p1", "resultado_p2", "resultado_p5"
                ))):
            return _resultado_persistencia_auditoria(False, "Resposta sem confirmação íntegra da gravação. Verifique antes de tentar novamente.", id_sinal)
        return _resultado_persistencia_auditoria(True, "Auditoria salva e confirmada no Supabase.", id_sinal)
    except Exception as exc:
        # Não incluir corpo HTTP, URL ou credenciais no diagnóstico exibido.
        motivo = f"{etapa} falhou ({type(exc).__name__}). Persistência remota não confirmada."
        return _resultado_persistencia_auditoria(False, motivo, id_sinal)


def salvar_auditoria_persistente(lista: list[dict]) -> dict:
    """Resultado explícito de cada gravação; nenhum CSV é confirmação remota."""
    resultados = [salvar_registro_auditoria_persistente(reg) for reg in lista]
    sucesso = all(r["sucesso"] for r in resultados)
    return {
        "sucesso": sucesso,
        "motivo": "Lote confirmado no Supabase." if sucesso else "Lote com falhas de persistência; consulte resultados.",
        "resultados": resultados,
    }


def limpar_auditoria_persistente(confirmacao: bool = False) -> tuple[bool, str]:
    """Manutenção global somente para admin autenticado e confirmação explícita."""
    usuario = st.session_state.get("usuario_logado", "")
    if not st.session_state.get("authenticated") or not usuario_e_admin(usuario):
        return False, "Acesso não autorizado: manutenção exclusiva da administração."
    if confirmacao is not True:
        return False, "Confirmação explícita obrigatória. Nada foi apagado."
    try:
        remoto = supabase_configurado()
        if remoto:
            _sb_delete(AUDITORIA_SUPABASE_TABLE, params={"id_sinal": "not.is.null"})
        # Limpeza local somente após sucesso remoto, se houver configuração.
        if AUDITORIA_FILE.exists():
            AUDITORIA_FILE.write_text("", encoding="utf-8-sig")
        return True, "Auditoria persistente limpa no Supabase." if remoto else "Auditoria local limpa."
    except Exception as exc:
        logging.getLogger(__name__).error("Falha na limpeza administrativa da auditoria: %s", type(exc).__name__)
        return False, "Limpeza não concluída integralmente. Verifique o estado da auditoria antes de repetir."


# Carrega a auditoria depois que as funções persistentes existem.
if not st.session_state.get("auditoria_sinais"):
    st.session_state.auditoria_sinais = carregar_auditoria_persistente(limite=50)


def calcular_metricas_auditoria(lista: list[dict]) -> dict:
    contagens = calculate_audit_counts(lista)
    return {
        **contagens,
        "total_fechados": contagens["p1"] + contagens["p2"] + contagens["p3"] + contagens["loss"],
        "win_direto": 0,
        "win_p1": contagens["p1"],
        "win_p2": contagens["p2"],
        "win_p3": contagens["p3"],
        "win_p5": 0,
        "loss": contagens["loss"],
    }


def faixa_confianca(valor) -> str:
    try:
        v = int(float(valor))
    except Exception:
        return "N/I"
    if v >= 90:
        return "90-100"
    if v >= 85:
        return "85-89"
    if v >= 80:
        return "80-84"
    if v >= 75:
        return "75-79"
    if v >= 70:
        return "70-74"
    if v >= 60:
        return "60-69"
    return "<60"


def preparar_resumos_auditoria(aud_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Gera resumos automáticos por confiança, ativo/timeframe e risco."""
    if aud_df is None or aud_df.empty or "resultado" not in aud_df.columns:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    base = aud_df.copy()
    base["resultado_normalizado"] = base["resultado"].apply(normalize_audit_result)
    base = base[base["resultado_normalizado"].isin(["P1", "P2", "P3", "LOSS"])].copy()
    if base.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    base["win_ate_p3"] = base["resultado_normalizado"].isin(["P1", "P2", "P3"])
    if "confianca" in base.columns:
        base["faixa_confianca"] = base["confianca"].apply(faixa_confianca)
    else:
        base["faixa_confianca"] = "N/I"

    resumo_conf = base.groupby("faixa_confianca").agg(
        sinais=("resultado_normalizado", "count"),
        wins_ate_p3=("win_ate_p3", "sum"),
    ).reset_index()
    resumo_conf["assertividade_p3_%"] = (resumo_conf["wins_ate_p3"] / resumo_conf["sinais"] * 100).round(1)

    grupos = [c for c in ["mercado", "ativo", "timeframe", "direcao"] if c in base.columns]
    resumo_ativo = base.groupby(grupos).agg(
        sinais=("resultado_normalizado", "count"),
        wins_ate_p3=("win_ate_p3", "sum"),
        confianca_media=("confianca", "mean") if "confianca" in base.columns else ("resultado_normalizado", "count"),
    ).reset_index()
    resumo_ativo["assertividade_p3_%"] = (resumo_ativo["wins_ate_p3"] / resumo_ativo["sinais"] * 100).round(1)
    if "confianca_media" in resumo_ativo.columns:
        resumo_ativo["confianca_media"] = pd.to_numeric(resumo_ativo["confianca_media"], errors="coerce").round(1)

    if "risco" in base.columns:
        resumo_risco = base.groupby("risco").agg(
            sinais=("resultado_normalizado", "count"),
            wins_ate_p3=("win_ate_p3", "sum"),
        ).reset_index()
        resumo_risco["assertividade_p3_%"] = (resumo_risco["wins_ate_p3"] / resumo_risco["sinais"] * 100).round(1)
    else:
        resumo_risco = pd.DataFrame()
    return resumo_conf, resumo_ativo, resumo_risco



# =============================
# LISTA AUTOMÁTICA DE MOEDAS
# =============================
MOEDAS_FALLBACK = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT",
    "LINKUSDT", "SUIUSDT", "TRXUSDT", "LTCUSDT", "BCHUSDT", "DOTUSDT", "ATOMUSDT", "NEARUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "PEPEUSDT", "UNIUSDT", "AAVEUSDT", "FILUSDT", "ETCUSDT",
    "INJUSDT", "SEIUSDT", "FETUSDT", "RNDRUSDT", "WIFUSDT", "TONUSDT", "MATICUSDT", "ICPUSDT",
    "ALGOUSDT", "VETUSDT", "HBARUSDT", "ARUSDT", "TIAUSDT", "JUPUSDT", "ORDIUSDT", "1000SHIBUSDT"
]

@st.cache_data(ttl=60 * 30, show_spinner=False)
def buscar_moedas_usdt_binance(max_symbols: int = 60) -> list:
    """Busca pares USDT ativos e ordena por maior volume de 24h.
    O app continua analisando somente a moeda selecionada, então uma lista maior não pesa no sinal.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 Streamlit-Sinais/1.0"}
        exchange_url = "https://api.binance.com/api/v3/exchangeInfo"
        ticker_url = "https://api.binance.com/api/v3/ticker/24hr"
        info = requests.get(exchange_url, headers=headers, timeout=12).json()
        tickers = requests.get(ticker_url, headers=headers, timeout=12).json()

        ativos = set()
        bloqueios = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
        for item in info.get("symbols", []):
            sym = item.get("symbol", "")
            if (
                item.get("status") == "TRADING"
                and item.get("quoteAsset") == "USDT"
                and item.get("isSpotTradingAllowed", True)
                and not sym.endswith(bloqueios)
            ):
                ativos.add(sym)

        tabela = []
        for t in tickers:
            sym = t.get("symbol", "")
            if sym in ativos:
                try:
                    volume = float(t.get("quoteVolume", 0))
                except Exception:
                    volume = 0
                tabela.append((sym, volume))

        tabela.sort(key=lambda x: x[1], reverse=True)
        lista = [sym for sym, _ in tabela[:max_symbols]]

        # Garante que as principais estejam sempre no topo mesmo se a API oscilar.
        principais = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
        final = []
        for sym in principais + lista:
            if sym not in final:
                final.append(sym)
        return final[:max_symbols]
    except Exception:
        return MOEDAS_FALLBACK[:max_symbols]

# =============================
# SIDEBAR
# =============================
with st.sidebar:
    botao_logout_sidebar()
    if usuario_logado_e_admin():
        st.success("👑 Perfil: Administrador")
    else:
        st.info("👤 Perfil: Cliente")
    st.header("🧠 Smart Money AI")
    st.caption("Inteligência Institucional para Traders")
    st.caption("Mercado monitorado em tempo real")
    st.caption(f"🛡️ Limite: 1 sinal a cada {intervalo_minimo_sinal_segundos()}s")
    if GEMINI_OK:
        st.success("Gemini configurada como reforço opcional")
    else:
        st.info("Sem GEMINI_KEY: operando 100% pelo motor técnico local")
    if supabase_configurado():
        st.success("Banco Supabase persistente ativo")
    else:
        st.warning("Banco local temporário: configure Supabase para não perder clientes no reboot")
    st.markdown("---")

    if st.session_state.resultado_scalp:
        st.markdown("⚡ **Último Sinal Scalp:**")
        st.code(st.session_state.resultado_scalp, language="text")
        if st.button("🗑️ Limpar Sinal Scalp", key="btn_limpar_scalp"):
            st.session_state.resultado_scalp = None
            st.rerun()
        st.markdown("---")

    if st.session_state.resultado_macro:
        st.markdown("📊 **Último Sinal Macro:**")
        st.code(st.session_state.resultado_macro, language="text")
        if st.button("🗑️ Limpar Histórico Macro", key="btn_limpar_macro"):
            st.session_state.resultado_macro = None
            st.rerun()
        st.markdown("---")

    st.subheader("🎯 Configuração Scalp / Multimercado")
    lista_moedas = buscar_moedas_usdt_binance(max_symbols=60)
    mercado_scalp = st.selectbox("Mercado:", ["Cripto", "Opções Binárias", "Forex", "B3"], key="mercado_scalp_key")
    lista_scalp = lista_ativos_por_mercado(mercado_scalp, lista_moedas)
    moeda_scalp = st.selectbox("Ativo:", lista_scalp, format_func=lambda s: nome_ativo_multimercado(s, mercado_scalp), key="moeda_scalp_key")
    tempo_scalp = st.selectbox("Timeframe:", ["1m", "5m"], key="tempo_scalp_key")
    st.caption(aviso_mercado_teste(mercado_scalp))
    st.checkbox(
        "Usar Gemini como reforço",
        value=False,
        key="usar_gemini_scalp",
        disabled=not GEMINI_OK,
        help="Opcional. Deixe desligado para economizar cota. O sinal principal é gerado pelo motor técnico local."
    )
    st.markdown("---")

    st.subheader("📢 Configuração Macro")
    moeda_macro = st.selectbox("Ativo Macro:", lista_moedas, key="moeda_macro_key")
    tempo_macro = st.selectbox("Timeframe Macro:", ["1h", "4h", "1d"], key="tempo_macro_key")



# =============================
# ÁREA DE TREINAMENTO SMART MONEY AI 4.7 CALIBRADO TESTE
# =============================
def render_area_treinamento() -> None:
    """Área exclusiva de treinamento para usuários logados.
    Os links das aulas ficam dentro da plataforma, sem aparecer na landing page pública.
    """
    st.subheader("📚 Treinamento Smart Money AI 4.3 FASE 5.1+ AUDITORIA SUPABASE COM COLUNAS")
    st.markdown(
        """
        <div style="background:linear-gradient(135deg, rgba(0,255,136,.14), rgba(0,0,0,.55));border:1px solid rgba(0,255,136,.35);border-radius:18px;padding:22px;margin-bottom:18px;">
            <h2 style="color:#00ff88;margin:0 0 8px 0;">🎓 Módulo Inicial Exclusivo</h2>
            <p style="color:#f4f4f4;font-size:17px;line-height:1.6;margin:0;">
                Assista às aulas essenciais para utilizar a plataforma com segurança, interpretar os sinais e aplicar gestão de risco antes de operar.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    aulas = [
        {
            "icone": "🚀",
            "titulo": "Aula 1 – Apresentação da Plataforma Smart Money AI 4.3 FASE 5.1+ AUDITORIA SUPABASE COM COLUNAS",
            "descricao": "Conheça a estrutura da plataforma, menus principais, recursos disponíveis e como navegar pela área do cliente.",
            "link": "https://youtu.be/1A2ogUEuwN8",
        },
        {
            "icone": "📈",
            "titulo": "Aula 2 – Como Interpretar os Sinais",
            "descricao": "Aprenda a interpretar sinais de compra, venda, direção da operação, timeframes e principais cuidados antes da entrada.",
            "link": "https://youtu.be/l1UaqUJJD_w",
        },
        {
            "icone": "🛡️",
            "titulo": "Aula 3 – Gestão de Risco Profissional",
            "descricao": "Entenda como proteger sua banca, controlar risco por operação e evitar os erros que mais quebram traders iniciantes.",
            "link": "https://youtu.be/mQIwbtp2Bpk",
        },
        {
            "icone": "🎯",
            "titulo": "Aula 4 – Como Executar uma Operação",
            "descricao": "Veja como acompanhar um sinal do Smart Money AI, confirmar tendência, entender P1/P2 e manter disciplina operacional.",
            "link": "https://youtu.be/y5uB3FoR0EM",
        },
    ]

    for aula in aulas:
        st.markdown(
            f"""
            <div style="background:rgba(0,0,0,.48);border:1px solid rgba(0,255,136,.28);border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 0 18px rgba(0,255,136,.08);">
                <div style="font-size:23px;font-weight:900;color:#00ff88;margin-bottom:6px;">{aula['icone']} {aula['titulo']}</div>
                <div style="color:#ffffff;font-size:16px;line-height:1.55;margin-bottom:14px;">{aula['descricao']}</div>
                <a href="{aula['link']}" target="_blank" style="display:inline-block;background:#00ff88;color:#001b0d;padding:11px 18px;border-radius:10px;text-decoration:none;font-weight:900;">
                    ▶ Assistir aula
                </a>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(
        """
        <div style="background:rgba(0,255,136,.08);border:1px solid rgba(0,255,136,.25);border-radius:16px;padding:18px;margin-top:22px;">
            <h3 style="color:#00ff88;margin-top:0;">📱 Comunidade Oficial</h3>
            <p style="color:white;font-size:16px;">Entre no Telegram oficial para receber avisos, atualizações da plataforma, novas aulas e conteúdos exclusivos.</p>
            <a href="https://t.me/smartmoneyaioficial" target="_blank" style="display:inline-block;background:#00ff88;color:#001b0d;padding:11px 18px;border-radius:10px;text-decoration:none;font-weight:900;">
                📱 Entrar no Telegram Oficial
            </a>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.info("🔒 Esta área é exclusiva para usuários logados. Os vídeos estão como não listados no YouTube e devem ser acessados pela plataforma.")

st.title("🧠 Smart Money AI")
st.caption("Inteligência Institucional para Traders")
try:
    usuario_atual = st.session_state.get("usuario_logado", "")
    registrar_evento_demo_unico("Minha Conta", "Área de conta carregada")
    cliente_atual = cliente_autorizado(usuario_atual) or {}
    if usuario_atual:
        st.caption(f"👤 Cliente: {usuario_atual} | Plano: {cliente_atual.get('plano','')} | Vencimento: {formatar_data_br(cliente_atual.get('vencimento',''))}")
        if usuario_logado_e_admin():
            st.success("👑 Modo Administrador ativo: áreas Comercial e Administração liberadas.")
        else:
            pass
except Exception:
    pass
st.markdown("---")

abas_nomes = ["⚡ Scalper AI", "📊 Tendência Institucional", "🎯 Auditoria de Sinais", "📋 Histórico Operacional", "📚 Treinamento", "👤 Minha Conta"]
if usuario_logado_e_admin():
    abas_nomes.extend(["💰 Central Comercial", "👑 Administração"])
abas_criadas = st.tabs(abas_nomes)
aba_scalp, aba_macro, aba_auditoria, aba_hist, aba_treinamento, aba_minha_conta = abas_criadas[:6]
aba_comercial = None
aba_admin = None
if len(abas_criadas) > 6:
    aba_comercial = abas_criadas[6]
    aba_admin = abas_criadas[7]

with aba_scalp:
    registrar_evento_demo_unico("Scalper AI", "Aba carregada/visualizada")
    st.subheader("⚡ Scalper AI Multimercado em Tempo Real")
    st.write(f"Mercado: **{mercado_scalp}** | Ativo: **{nome_ativo_multimercado(moeda_scalp, mercado_scalp)}** | Timeframe: **{tempo_scalp}**")
    st.info("Ao clicar, o app aplica o motor 4.2 FASE 5. Ele SEMPRE entrega COMPRA/VENDA; em Opções Binárias exibe CALL/PUT. A confiança agora considera tendência, volume relativo, fluxo estimado, velocidade do candle e exaustão.")
    st.caption(aviso_mercado_teste(mercado_scalp))

    usuario_uso = st.session_state.get("usuario_logado", "cliente")
    resumo_uso = resumo_uso_usuario(usuario_uso)
    resumo_carteira_scalp = carteira_resumo_usuario(usuario_uso)
    pode_sinal, segundos_restantes, msg_limite = pode_gerar_sinal(usuario_uso)

    colA, colB, colC = st.columns([2, 1, 1])
    with colA:
        gerar = st.button("🚀 GERAR SINAL AO VIVO", key="btn_scalp_live", disabled=not pode_sinal)
    with colB:
        st.caption(f"Horário BR: {datetime.now(fuso_br).strftime('%H:%M:%S')}")
        st.caption(f"Sinais técnicos hoje: {resumo_uso['gerados_hoje']}")
        st.caption(f"Franquia gratuita: {resumo_carteira_scalp.get('gratuito_hoje', 'Indisponível')}")
    with colC:
        if pode_sinal:
            st.success("Intervalo técnico liberado")
        else:
            st.warning(f"Próximo sinal em {segundos_restantes}s")
        if st.button("🔄 Atualizar tempo", key="btn_atualizar_tempo_scalp"):
            st.rerun()

    if not pode_sinal:
        st.caption("🛡️ Aguarde completar 1 minuto entre sinais. Clique em 🔄 Atualizar tempo para verificar a liberação sem recarregar automaticamente a página.")

    if gerar:
        event_id_sinal = obter_event_id_fluxo("sinal_oficial")
        log_evento_operacional(event_id_sinal, "sinal.oficial", "start")
        if not restaurar_login_persistente():
            st.error("Sessão expirada. Faça login novamente antes de gerar sinal.")
            st.stop()
        pode_agora, falta_agora, msg_agora = pode_gerar_sinal(usuario_uso)
        if not pode_agora:
            registrar_uso_sinal(usuario_uso, moeda_scalp, tempo_scalp, "BLOQUEADO_RATE_LIMIT", msg_agora)
            st.warning(msg_agora)
        else:
            request_id_carteira = str(uuid.uuid4())
            inicio_reserva_carteira = datetime.now().timestamp()
            st.session_state["event_id_carteira"] = event_id_sinal
            st.session_state["event_id_live_shadow"] = event_id_sinal
            reserva_carteira = carteira_reservar_sinal(usuario_uso, request_id_carteira)
            st.session_state["carteira_ultima_reserva_ms"] = round((datetime.now().timestamp() - inicio_reserva_carteira) * 1000, 2)
            if not reserva_carteira.get("ok"):
                registrar_uso_sinal(usuario_uso, moeda_scalp, tempo_scalp, "BLOQUEADO_CARTEIRA", str(reserva_carteira.get("status", reserva_carteira.get("erro", "BLOQUEADO"))))
                erro_carteira = str(reserva_carteira.get("erro") or reserva_carteira.get("status") or "").strip().upper()
                if erro_carteira == "SEM_CREDITOS":
                    st.warning("Você já utilizou seu sinal gratuito de hoje e não possui créditos disponíveis.")
                    st.info("Para continuar, acesse Minha Conta → Comprar sinais, escolha um pacote e clique em comprar.")
                else:
                    st.warning(reserva_carteira.get("mensagem") or "Saldo insuficiente para gerar sinal.")
            else:
                try:
                    with st.spinner("Buscando candles reais e gerando sinal..."):
                        inicio_v1_sinal = datetime.now().timestamp()
                        decisao, df_sinal = gerar_sinal_scalp_gemini(moeda_scalp, tempo_scalp, mercado_scalp)
                        v1_latency_ms = round((datetime.now().timestamp() - inicio_v1_sinal) * 1000, 2)
                        inicio_confirma_carteira = datetime.now().timestamp()
                        if reserva_carteira.get("origem") == "ADMIN_NO_CHARGE":
                            confirmacao_carteira = {"ok": True, "status": "CONFIRMADO", "origem": "ADMIN_NO_CHARGE", "delta_credito": 0}
                        else:
                            confirmacao_carteira = carteira_confirmar_sinal(request_id_carteira, decisao.get("id_sinal", ""))
                        st.session_state["carteira_ultima_confirmacao_ms"] = round((datetime.now().timestamp() - inicio_confirma_carteira) * 1000, 2)
                        if not confirmacao_carteira.get("ok"):
                            carteira_estornar_sinal(request_id_carteira, "confirmacao_falhou_antes_da_entrega")
                            st.error("Sinal não entregue: confirmação de consumo não foi concluída.")
                            st.stop()
                        sinal = decisao["texto"]
                        registrar_uso_sinal(usuario_uso, moeda_scalp, tempo_scalp, "GERADO", "Sinal gerado com sucesso")
                        registrar_pagina_visitada(usuario_uso, "Scalper AI", f"Gerou sinal em {mercado_scalp} {moeda_scalp} {tempo_scalp}")
                        st.session_state.resultado_scalp = sinal
                        st.session_state.historico_sinais.insert(0, {
                            "data_hora": datetime.now(fuso_br).strftime("%d/%m/%Y %H:%M:%S"),
                            "mercado": mercado_scalp,
                            "ativo": moeda_scalp,
                            "timeframe": tempo_scalp,
                            "sinal": sinal,
                            "id_sinal": decisao["id_sinal"],
                            "usuario": decisao["usuario"],
                            "direcao": decisao["direcao"],
                            "risco": decisao["risco"],
                            "confianca": decisao["confianca"],
                            **{f"{campo}_iso": decisao[f"{campo}_iso"] for campo in ("entrada", "p1", "p2", "p3", "p5")},
                            "preco": float(df_sinal["Close"].iloc[-1])
                        })
                        novo_registro_auditoria = criar_registro_auditoria(moeda_scalp, tempo_scalp, decisao, df_sinal, mercado_scalp)
                        st.session_state.auditoria_sinais.insert(0, novo_registro_auditoria)
                        salvar_lista_csv(HISTORICO_FILE, st.session_state.historico_sinais[:1000])
                        resultado_gravacao = salvar_registro_auditoria_persistente(novo_registro_auditoria)
                        st.session_state["auditoria_ultima_gravacao"] = resultado_gravacao
                        if not resultado_gravacao["sucesso"]:
                            st.session_state.setdefault("auditoria_nao_salva", {})[decisao["id_sinal"]] = novo_registro_auditoria
                        try:
                            shadow_resultado = registrar_shadow_v2_sinal(
                                decisao, df_sinal, mercado=mercado_scalp, symbol=moeda_scalp, timeframe=tempo_scalp, usuario=usuario_uso, v1_latency_ms=v1_latency_ms
                            )
                            st.session_state["signal_v2_shadow_last_status"] = shadow_resultado.get("status")
                        except Exception as shadow_exc:
                            logging.getLogger(__name__).warning("shadow_v2_nao_bloqueante: %s", type(shadow_exc).__name__)
                        quant_shadow_status = agendar_quant_shadow(
                            decisao, mercado=mercado_scalp, symbol=moeda_scalp, timeframe=tempo_scalp,
                            event_id=event_id_sinal, request_id=request_id_carteira,
                        )
                        st.session_state["r03_quant_shadow_last_status"] = quant_shadow_status.get("status")
                        log_evento_operacional(event_id_sinal, "sinal.oficial", "ok")
                        st.rerun()
                except Exception as exc:
                    log_evento_operacional(event_id_sinal, "sinal.oficial", "error", exc)
                    if reserva_carteira.get("origem") != "ADMIN_NO_CHARGE":
                        carteira_estornar_sinal(request_id_carteira, type(exc).__name__)
                    registrar_uso_sinal(usuario_uso, moeda_scalp, tempo_scalp, "ESTORNADO_ERRO_GERACAO", type(exc).__name__)
                    st.error("Sinal não entregue. Reserva estornada por falha na geração.")

    if st.session_state.resultado_scalp:
        st.info("Sinal gerado. A confirmação de gravação da auditoria aparece abaixo.")
        st.code(st.session_state.resultado_scalp, language="text")
        gravacao = st.session_state.get("auditoria_ultima_gravacao")
        if gravacao:
            if gravacao["sucesso"]:
                st.success(gravacao["motivo"])
            else:
                st.error(gravacao["motivo"])
                st.warning("Sinal mantido na sessão; a auditoria remota não está confirmada. Use a aba Auditoria para tentar novamente.")

    try:
        df_preview = calcular_indicadores(buscar_candles_mercado(mercado_scalp, moeda_scalp, tempo_scalp, limit=120))
        u = df_preview.iloc[-1]
        setup_score, setup_motivo = calcular_setup_score_preview(df_preview)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Preço", f"{u['Close']:.4f}")
        c2.metric("RSI14", f"{u['RSI14']:.2f}")
        c3.metric("EMA9 / EMA21", f"{u['EMA9']:.2f} / {u['EMA21']:.2f}")
        c4.metric("Volume", f"{u['Volume']:.2f}")

        st.markdown("### 🧪 Indicadores 4.0 TESTE — filtros de exaustão ativos")
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("RSI3", f"{u['RSI3']:.2f}")
        d2.metric("RSI5", f"{u['RSI5']:.2f}")
        d3.metric("ATR14", f"{u['ATR14']:.4f}")
        d4.metric("Volume Relativo", f"{u['VOL_RELATIVO']:.2f}x")
        d5.metric("Setup Score", f"{setup_score}/100")
        e1, e2, e3 = st.columns(3)
        e1.metric("Stoch RSI K/D", f"{u['STOCH_RSI_K']:.1f} / {u['STOCH_RSI_D']:.1f}")
        e2.metric("Bollinger posição", f"{u['BB_POSICAO']:.2f}")
        e3.metric("BB Sup/Inf", f"{u['BB_SUPERIOR']:.2f} / {u['BB_INFERIOR']:.2f}")
        st.caption(f"Leitura do score: {setup_motivo}. Fase 5.1 ativa: o app sempre entrega COMPRA ou VENDA, ajusta confiança/risco e salva auditoria automática completa.")

        st.dataframe(
            df_preview[["Open time", "Open", "High", "Low", "Close", "Volume", "VOL_RELATIVO", "EMA9", "EMA21", "RSI3", "RSI5", "RSI14", "STOCH_RSI_K", "STOCH_RSI_D", "BB_POSICAO", "BB_SUPERIOR", "BB_INFERIOR", "ATR14", "ATR_PCT", "MACD_HIST", "VWAP"]].tail(12),
            use_container_width=True
        )
    except Exception as e:
        st.warning(f"Não foi possível carregar prévia dos candles: {e}")

with aba_macro:
    registrar_evento_demo_unico("Tendência Institucional", "Aba carregada/visualizada")
    st.subheader("📈 Tendência Institucional e Médio Prazo")
    st.write(f"Ativo Selecionado: **{moeda_macro}** | Timeframe: **{tempo_macro}**")
    rodar_varredura = st.button("🔍 Executar Varredura de Tendência Institucional", key="btn_macro_scan")

    if rodar_varredura:
        try:
            df = calcular_indicadores(buscar_candles_binance(moeda_macro, tempo_macro, limit=210))
            u = df.iloc[-1]
            prompt_macro = f"""
Ativo: {moeda_macro}
Timeframe: {tempo_macro}
Preço: {u['Close']:.6f}
EMA20 aproximada pela EMA21: {u['EMA21']:.6f}
EMA50: {u['EMA50']:.6f}
EMA200: {u['EMA200']:.6f}
RSI: {u['RSI14']:.2f}
MACD_HIST: {u['MACD_HIST']:.6f}
VWAP: {u['VWAP']:.6f}
Responda apenas COMPRA, VENDA ou AGUARDAR para tendência macro.
"""
            direcao_simples = "AGUARDAR"
            try:
                if GEMINI_OK:
                    modelo = genai.GenerativeModel("gemini-2.5-flash")
                    resposta_ia = modelo.generate_content(prompt_macro).text.upper()
                    if "COMPRA" in resposta_ia:
                        direcao_simples = "COMPRA"
                    elif "VENDA" in resposta_ia:
                        direcao_simples = "VENDA"
                else:
                    direcao_simples = "COMPRA" if u["Close"] > u["EMA21"] and u["RSI14"] >= 50 else "VENDA"
            except Exception:
                direcao_simples = "COMPRA" if u["Close"] > u["EMA21"] and u["RSI14"] >= 50 else "VENDA"

            preco_atual = u["Close"]
            take_profit = preco_atual * 1.015 if direcao_simples == "COMPRA" else preco_atual * 0.985
            stop_loss = preco_atual * 0.9925 if direcao_simples == "COMPRA" else preco_atual * 1.0075
            if direcao_simples == "AGUARDAR":
                take_profit = preco_atual
                stop_loss = preco_atual
            st.session_state.resultado_macro = f"ATIVO: {moeda_macro}\nDIREÇÃO: {direcao_simples}\nPREÇO: ${preco_atual:,.2f}\nTAKE PROFIT: ${take_profit:,.2f}\nSTOP LOSS: ${stop_loss:,.2f}"
            st.success("Varredura macro concluída")
            st.code(st.session_state.resultado_macro, language="text")
            st.dataframe(df[["Open time", "Close", "EMA21", "EMA50", "EMA200", "RSI14", "MACD_HIST", "VWAP"]].tail(20), use_container_width=True)
        except Exception as e:
            st.error(f"Erro na varredura macro: {e}")

with aba_auditoria:
    registrar_evento_demo_unico("Auditoria de Sinais", "Aba carregada/visualizada")
    st.subheader("🎯 Auditoria Inteligente 5.1+ FAST 4.8")
    st.info("O avaliador grava automaticamente confiança, risco, indicadores e resultado exclusivo P1, P2, P3 ou LOSS. Depois basta baixar o CSV e me enviar.")
    if supabase_configurado():
        st.info("Supabase configurado. Cada gravação de auditoria precisa de confirmação; pendentes vencidos são avaliados por lote.")
    else:
        st.warning("Supabase não configurado: novos registros de auditoria ficam somente na sessão, sem confirmação remota e sem fallback automático em CSV.")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 Atualizar lista", key="btn_atualizar_auditoria"):
            # Apenas recarrega os últimos registros do Supabase; não avalia candles automaticamente.
            st.session_state.auditoria_sinais = carregar_auditoria_persistente(limite=100)
            st.rerun()
    with col2:
        if st.button("🧪 Avaliar pendentes vencidos", key="btn_avaliar_pendentes_fast"):
            # Correção 4.8: busca pendentes direto do Supabase e avalia apenas
            # operações que já tiveram tempo suficiente para fechar P3.
            with st.spinner("Avaliando pendentes vencidos sem travar o sinal..."):
                resumo_avaliacao = avaliar_pendentes_maduros(maximo=30, buscar_limite=120)
            st.session_state["auditoria_ultima_avaliacao"] = resumo_avaliacao
            st.rerun()
    with col3:
        st.caption(f"Horário BR: {datetime.now(fuso_br).strftime('%H:%M:%S')}")

    resumo_avaliacao = st.session_state.get("auditoria_ultima_avaliacao")
    if resumo_avaliacao:
        st.info(f"Avaliados: {resumo_avaliacao['avaliados']} | gravações confirmadas: {resumo_avaliacao['salvos']}")
        for falha in resumo_avaliacao["falhas"]:
            st.error(f"{falha['id_sinal']}: {falha['motivo']}")

    nao_salvos = st.session_state.get("auditoria_nao_salva", {})
    if nao_salvos:
        st.warning(f"{len(nao_salvos)} registro(s) sem confirmação remota, mantidos apenas nesta sessão. Não feche a sessão antes de resolver.")
        if st.button("Tentar salvar auditorias não confirmadas", key="btn_retry_auditoria"):
            resultados_retry = []
            for id_sinal, reg in list(nao_salvos.items()):
                resultado_retry = salvar_registro_auditoria_persistente(reg)
                resultados_retry.append(resultado_retry)
                if resultado_retry["sucesso"]:
                    del nao_salvos[id_sinal]
                if st.session_state.get("auditoria_ultima_gravacao", {}).get("id_sinal") == id_sinal:
                    st.session_state["auditoria_ultima_gravacao"] = resultado_retry
            st.session_state["auditoria_resultados_retry"] = resultados_retry
            st.rerun()
    for resultado_retry in st.session_state.get("auditoria_resultados_retry", []):
        if resultado_retry["sucesso"]:
            st.success(f"{resultado_retry['id_sinal']}: {resultado_retry['motivo']}")
        else:
            st.error(f"{resultado_retry['id_sinal']}: {resultado_retry['motivo']}")

    # FAST: a aba de auditoria NÃO avalia automaticamente todos os sinais.
    # Isso evita travamento. A avaliação dos pendentes agora é manual e limitada.
    if st.session_state.auditoria_sinais:
        aud_df = pd.DataFrame(st.session_state.auditoria_sinais)

        st.markdown("### ⚡ Auditoria rápida")
        f1, f2, f3, f4 = st.columns(4)
        with f1:
            qtd_tela = st.selectbox("Mostrar na tela", [25, 50, 100, 200], index=1, key="aud_qtd_tela")
        with f2:
            ativos = ["Todos"] + sorted(aud_df["ativo"].dropna().astype(str).unique().tolist()) if "ativo" in aud_df.columns else ["Todos"]
            filtro_ativo = st.selectbox("Ativo", ativos, key="aud_filtro_ativo")
        with f3:
            tempos = ["Todos"] + sorted(aud_df["timeframe"].dropna().astype(str).unique().tolist()) if "timeframe" in aud_df.columns else ["Todos"]
            filtro_tempo = st.selectbox("Timeframe", tempos, key="aud_filtro_timeframe")
        with f4:
            filtro_confianca = st.slider("Confiança mínima", 0, 100, 0, 5, key="aud_filtro_confianca")

        r1, r2 = st.columns(2)
        with r1:
            resultados = ["Todos"] + sorted(aud_df["resultado"].dropna().astype(str).unique().tolist()) if "resultado" in aud_df.columns else ["Todos"]
            filtro_resultado = st.selectbox("Resultado", resultados, key="aud_filtro_resultado")
        with r2:
            st.caption("A tela mostra registros paginados/filtrados. O Supabase continua guardando tudo.")

        aud_filtrado = filtrar_auditoria_df(aud_df, filtro_ativo, filtro_tempo, filtro_resultado, filtro_confianca)
        aud_tela = aud_filtrado.head(int(qtd_tela))

        metricas = calcular_metricas_auditoria(aud_filtrado.to_dict("records"))
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("P1", metricas["p1"])
        m2.metric("P2", metricas["p2"])
        m3.metric("P3", metricas["p3"])
        m4.metric("LOSS", metricas["loss"])
        m5.metric("Pendentes", metricas["pendentes"])
        m6.metric("Sem dados", metricas["sem_dados"])

        colunas = [
            "id_sinal", "criado_em", "mercado", "ativo", "timeframe", "direcao", "confianca", "risco", "setup_score",
            "entrada", "p1", "p2", "p3", "resultado", "motivo_auditoria", "resultado_expandido",
            "resultado_direto", "resultado_p1", "resultado_p2", "resultado_p3",
            "preco_geracao", "vwap", "ema9", "ema21", "rsi3", "rsi5", "rsi14", "macd_hist", "atr_pct", "volume_relativo",
            "bb_posicao", "stoch_rsi_k", "stoch_rsi_d", "pressao_compra", "pressao_venda", "fluxo_delta",
            "open_entrada", "close_entrada", "open_p1", "close_p1", "open_p2", "close_p2", "open_p3", "close_p3"
        ]
        st.dataframe(aud_tela[[c for c in colunas if c in aud_tela.columns]], use_container_width=True, height=360)

        csv_tela = aud_filtrado.to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ Baixar CSV filtrado", csv_tela, "auditoria_sinais_filtrada.csv", "text/csv", use_container_width=True)

        with st.expander("⬇️ Preparar CSV completo do Supabase", expanded=False):
            st.warning("Use esta opção somente quando quiser me enviar o arquivo completo. Ela pode levar alguns segundos se houver muitos sinais.")
            if st.button("Preparar CSV completo agora", key="btn_csv_completo_auditoria"):
                with st.spinner("Carregando auditoria completa do Supabase..."):
                    df_full = carregar_auditoria_csv_completo(limite=10000)
                if df_full.empty:
                    st.warning("Não encontrei registros para exportar.")
                else:
                    csv_full = df_full.to_csv(index=False).encode("utf-8-sig")
                    st.download_button("⬇️ Baixar auditoria completa CSV", csv_full, "auditoria_sinais_completa.csv", "text/csv", use_container_width=True)
                    st.success(f"CSV completo preparado com {len(df_full)} registros.")

        st.markdown("### 📊 Resumos automáticos 5.1")
        try:
            resumo_conf, resumo_ativo, resumo_risco = preparar_resumos_auditoria(aud_filtrado)
            if not resumo_conf.empty:
                st.markdown("#### Assertividade por faixa de confiança")
                st.dataframe(resumo_conf, use_container_width=True, height=220)
            if not resumo_risco.empty:
                st.markdown("#### Assertividade por risco")
                st.dataframe(resumo_risco, use_container_width=True, height=220)
            if not resumo_ativo.empty:
                st.markdown("#### Desempenho por mercado, ativo, timeframe e direção")
                st.dataframe(resumo_ativo, use_container_width=True, height=260)
        except Exception as e:
            st.caption(f"Resumo avançado ainda indisponível: {e}")

    else:
        st.warning("Ainda não há sinais para auditar. Gere alguns sinais na aba Scalp Ao Vivo.")

with aba_hist:
    registrar_evento_demo_unico("Histórico Operacional", "Aba carregada/visualizada")
    st.subheader("📋 Histórico Operacional de sinais da sessão")
    if st.session_state.historico_sinais:
        if st.button("🧹 Limpar Histórico", key="btn_limpar_hist_persistente"):
            st.session_state.historico_sinais = []
            salvar_lista_csv(HISTORICO_FILE, st.session_state.historico_sinais)
            st.rerun()
        hist_df = pd.DataFrame(st.session_state.historico_sinais)
        st.dataframe(hist_df, use_container_width=True)
        csv = hist_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ Baixar histórico CSV", csv, "historico_sinais.csv", "text/csv")
    else:
        st.info("Nenhum sinal gerado nesta sessão ainda.")

with aba_treinamento:
    registrar_evento_demo_unico("Treinamento", "Aba carregada/visualizada")
    render_area_treinamento()

with aba_minha_conta:
    registrar_evento_demo_unico("Minha Conta", "Aba carregada/visualizada")
    st.subheader("👤 Minha Conta")
    usuario_atual = st.session_state.get("usuario_logado", "")
    cliente_atual = cliente_autorizado(usuario_atual) or {}
    st.markdown("""
    <style>
    .minha-conta-card {
        border: 1px solid rgba(34,197,94,0.22);
        background: rgba(3,15,9,0.55);
        border-radius: 12px;
        padding: 10px 12px;
        min-height: 64px;
    }
    .minha-conta-label {
        color: #9ca3af;
        font-size: 12px;
        font-weight: 700;
        margin-bottom: 4px;
    }
    .minha-conta-value {
        color: #f9fafb;
        font-size: 14px;
        font-weight: 800;
        overflow-wrap: anywhere;
    }
    .minha-conta-small {
        color: #d1d5db;
        font-size: 13px;
        font-weight: 700;
    }
    </style>
    """, unsafe_allow_html=True)

    mc1, mc2, mc3, mc4 = st.columns([1.9, 1, 1, 1])
    with mc1:
        st.markdown(f"<div class='minha-conta-card'><div class='minha-conta-label'>E-mail</div><div class='minha-conta-value'>{usuario_atual or '-'}</div></div>", unsafe_allow_html=True)
    with mc2:
        st.markdown(f"<div class='minha-conta-card'><div class='minha-conta-label'>Plano</div><div class='minha-conta-value'>{str(cliente_atual.get('plano', '-'))}</div></div>", unsafe_allow_html=True)
    with mc3:
        st.markdown(f"<div class='minha-conta-card'><div class='minha-conta-label'>Vencimento</div><div class='minha-conta-value'>{formatar_data_br(cliente_atual.get('vencimento', '-'))}</div></div>", unsafe_allow_html=True)
    with mc4:
        st.markdown(f"<div class='minha-conta-card'><div class='minha-conta-label'>Status</div><div class='minha-conta-small'>{texto_dias_restantes(str(cliente_atual.get('vencimento', '')))}</div></div>", unsafe_allow_html=True)

    uso_cliente = resumo_uso_usuario(usuario_atual)
    u1, u2, u3 = st.columns(3)
    u1.metric("Sinais hoje", uso_cliente["gerados_hoje"])
    u2.metric("Bloqueios hoje", uso_cliente["bloqueados_hoje"])
    u3.metric("Total sinais", uso_cliente["total_gerados"])

    st.markdown("### 🎟️ Carteira de sinais")
    resumo_carteira = carteira_resumo_usuario(usuario_atual)
    cw1, cw2, cw3, cw4 = st.columns(4)
    cw1.metric("Plano de acesso", resumo_carteira.get("plano_acesso", "GRÁTIS"))
    cw2.metric("Gratuito de hoje", resumo_carteira.get("gratuito_hoje", "Indisponível"))
    cw3.metric("Saldo comprado", int(resumo_carteira.get("saldo_creditos", 0) or 0))
    cw4.metric("Ilimitado", resumo_carteira.get("ilimitado_ate") or "Inativo")
    if resumo_carteira.get("erro"):
        st.warning("Carteira indisponível no servidor. Por segurança, novos sinais serão bloqueados até normalizar.")
    if st.button("🔄 Atualizar carteira", key="btn_atualizar_carteira_minha_conta", use_container_width=True):
        st.rerun()

    st.markdown("### 🛒 Comprar sinais")
    st.caption("Grátis: 1 sinal/dia — R$ 0,00. Pacotes comprados não expiram; ilimitado vale pelo período contratado.")
    planos_carteira = list(CARTEIRA_FREEMIUM_PLANOS.keys())
    plano_carteira = st.selectbox("Opção", planos_carteira, format_func=lambda p: CARTEIRA_FREEMIUM_PLANOS[p]["nome"], key="minha_conta_plano_carteira")
    info_carteira = CARTEIRA_FREEMIUM_PLANOS[plano_carteira]
    valor_carteira_txt = f"R$ {info_carteira['preco']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    st.info(f"{info_carteira['nome']} — {valor_carteira_txt}")
    metodo_carteira = st.selectbox("Forma de pagamento", ["Mercado Pago", "PIX", "Transferência", "Outro"], key="minha_conta_metodo_carteira")
    if metodo_carteira == "Mercado Pago":
        st.caption("Pagamento automático via Mercado Pago. A liberação dos créditos ocorre somente após confirmação pelo webhook.")
        if st.button("💳 Comprar com Mercado Pago", key="btn_mp_checkout_carteira", use_container_width=True):
            ok_mp, msg_mp, dados_mp = mercado_pago_checkout_criar(plano_carteira)
            st.session_state["mp_checkout_carteira"] = dados_mp if ok_mp else {}
            if ok_mp:
                st.success(msg_mp)
            else:
                st.error(msg_mp)
        checkout_atual = st.session_state.get("mp_checkout_carteira") or {}
        checkout_url_atual = str(checkout_atual.get("checkout_url") or checkout_atual.get("init_point") or "")
        if checkout_url_atual:
            st.link_button("Ir para o Mercado Pago", checkout_url_atual, use_container_width=True)
    elif metodo_carteira == "PIX":
        chave_pix_carteira = pix_chave_pagamento()
        st.success(f"Chave PIX: {chave_pix_carteira}")
        st.text_input("Copiar chave PIX", value=chave_pix_carteira, disabled=True, key="minha_conta_pix_carteira")
        st.caption(f"Valor a pagar: {valor_carteira_txt}. Após o pagamento, envie a solicitação para confirmação administrativa.")
    if metodo_carteira != "Mercado Pago":
        obs_carteira = st.text_input("Observação da compra", key="minha_conta_obs_carteira")
        if st.button("✅ Solicitar compra de sinais", key="btn_solicitar_carteira", use_container_width=True):
            ok_cart, msg_cart = carteira_criar_solicitacao(usuario_atual, str(cliente_atual.get("nome", usuario_atual)), plano_carteira, metodo_carteira, obs_carteira)
            if ok_cart:
                st.success(msg_cart)
            else:
                st.error(msg_cart)

    st.markdown("### 🤝 Programa de Indicações")
    try:
        afiliado_atual = garantir_afiliado_cliente(usuario_atual, cliente_atual.get("nome", usuario_atual))
        codigo_atual = afiliado_atual.get("codigo") or codigo_afiliado_cliente(usuario_atual, cliente_atual.get("nome", ""))
        link_atual = link_afiliado_cliente(codigo_atual)
        ia1, ia2, ia3 = st.columns([2, 1, 1])
        with ia1:
            st.text_input("Seu link de indicação", value=link_atual, disabled=True, key="minha_conta_link_afiliado")

            link_seguro = link_atual.replace("'", "%27").replace('"', "%22")
            msg_whatsapp = urllib.parse.quote(f"Conheça o Smart Money AI: {link_atual}")
            wa_share = f"https://wa.me/?text={msg_whatsapp}"

            btn_copiar_html = f"""
            <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:8px;margin-bottom:8px;">
                <button onclick="navigator.clipboard.writeText('{link_seguro}').then(function(){{alert('Link copiado com sucesso!');}}).catch(function(){{alert('Não foi possível copiar automaticamente. Copie o link no campo acima.');}});"
                    style="background:#00ff88;color:#001b0d;border:0;border-radius:10px;padding:10px 16px;font-weight:900;cursor:pointer;">
                    📋 Copiar link
                </button>
                <a href="{wa_share}" target="_blank"
                    style="background:#101820;color:#00ff88;border:1px solid #00ff88;border-radius:10px;padding:10px 16px;font-weight:900;text-decoration:none;display:inline-block;">
                    📤 Compartilhar no WhatsApp
                </a>
            </div>
            """
            st.markdown(btn_copiar_html, unsafe_allow_html=True)
            st.caption("Compartilhe este link. Quando um novo cliente vier por ele, a indicação aparecerá no painel administrativo.")
        with ia2:
            st.metric("Seu código", codigo_atual)
        with ia3:
            st.metric("Indicações", int(afiliado_atual.get("indicacoes") or 0))
    except Exception as e:
        st.info("Programa de indicações indisponível no momento. Verifique as tabelas afiliados e indicacoes no Supabase.")
        st.caption(str(e))


    st.markdown("### 🔐 Alterar senha")
    st.caption("Informe a senha atual e escolha uma nova senha com pelo menos 8 caracteres. A troca encerrará as sessões antigas.")
    with st.form("form_minha_conta_alterar_senha"):
        senha_atual = st.text_input("Senha atual", type="password", key="minha_conta_senha_atual")
        nova_senha = st.text_input("Nova senha", type="password", key="minha_conta_nova_senha")
        confirmar_senha = st.text_input("Confirmar nova senha", type="password", key="minha_conta_confirmar_senha")
        trocar = st.form_submit_button("💾 Salvar nova senha")
    if trocar:
        ok_troca, msg_troca = alterar_senha_cliente_logado(usuario_atual, senha_atual, nova_senha, confirmar_senha)
        if ok_troca:
            st.success(msg_troca)
            st.stop()
        else:
            st.error(msg_troca)
            if not st.session_state.get("authenticated", False):
                st.stop()

    st.markdown("### 🆘 Suporte")
    st.caption("Use os canais abaixo para suporte, renovação ou dúvidas sobre acesso.")
    link_whats_conta = suporte_whatsapp_link()
    email_sup_conta = suporte_email()
    s1, s2 = st.columns(2)
    with s1:
        if link_whats_conta:
            st.link_button("💬 Falar no WhatsApp", link_whats_conta, use_container_width=True)
        else:
            st.warning("WhatsApp de suporte não configurado no Secrets. Adicione SUPPORT_WHATSAPP com DDI+DDD+número.")
    with s2:
        st.link_button("📧 Enviar e-mail ao suporte", f"mailto:{email_sup_conta}", use_container_width=True)

st.caption("© Smart Money AI • Tecnologia de análise institucional, fluxo de capital e inteligência artificial. Uso educacional e de apoio à decisão.")

if aba_comercial is not None:
    with aba_comercial:
        registrar_evento_demo_unico("Central Comercial", "Aba administrativa carregada")
        bloquear_area_admin_para_cliente()
        st.subheader("💰 Dashboard Comercial PRO")
        
        st.info("Resumo executivo para acompanhar clientes ativos, vencimentos, receita mensal/anual estimada, cobranças e renovações.")

        st.markdown("### 🎟️ Carteira de sinais")
        try:
            compras_cart = _sb_get("sma_carteira_compras", params={"select": "id,email,nome,plano,tipo,quantidade,valor,status,metodo_pagamento,criado_em", "order": "criado_em.desc", "limit": "100"}) if supabase_configurado() else []
            if compras_cart:
                df_compras_cart = pd.DataFrame(compras_cart)
                st.dataframe(df_compras_cart, use_container_width=True, height=220)
                pend_cart = [int(r["id"]) for r in compras_cart if str(r.get("status")) == "PAGAMENTO_PENDENTE"]
                if pend_cart:
                    compra_id_cart = st.selectbox("Confirmar pagamento da carteira", pend_cart, key="admin_carteira_compra_id")
                    if st.button("✅ Confirmar compra de sinais", key="btn_admin_confirmar_carteira", use_container_width=True):
                        ok_cart_admin, msg_cart_admin = carteira_admin_confirmar_compra(int(compra_id_cart))
                        if ok_cart_admin:
                            st.success(msg_cart_admin)
                            st.session_state["carteira_admin_ultima_confirmacao"] = {"compra_id": int(compra_id_cart), "ok": True, "msg": msg_cart_admin}
                            st.rerun()
                        else:
                            st.error(msg_cart_admin)
                else:
                    st.info("Nenhuma compra de carteira pendente.")
            else:
                st.info("Nenhuma solicitação de carteira registrada.")
        except Exception as e:
            st.warning(f"Carteira administrativa indisponível: {type(e).__name__}")

        st.markdown("### 🧭 Dashboard Executivo")
        try:
            dash_exec = calcular_dashboard_executivo()
            de1, de2, de3, de4, de5 = st.columns(5)
            de1.metric("Clientes ativos", dash_exec["clientes_ativos"])
            de2.metric("Taxa ativa", f"{dash_exec['taxa_ativos']:.1f}%")
            de3.metric("Recebido no mês", f"R$ {dash_exec['receita_recebida_mes']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            de4.metric("Renovações no mês", dash_exec["renovacoes_mes"])
            de5.metric("Cobranças vencidas", dash_exec["cobrancas_vencidas"])
        except Exception as e:
            st.warning(f"Dashboard executivo indisponível: {e}")


        metricas_com = calcular_metricas_comerciais()
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Clientes totais", metricas_com["total_clientes"])
        c2.metric("Ativos", metricas_com["ativos"])
        c3.metric("Vencem em 7 dias", metricas_com["vencem_7d"])
        c4.metric("Vencidos", metricas_com["vencidos"])
        c5.metric("Bloqueados", metricas_com["bloqueados"])

        r1, r2 = st.columns(2)
        r1.metric("Receita ativa estimada", f"R$ {metricas_com['receita_ativa_estim']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
        r2.metric("Receita total cadastrada", f"R$ {metricas_com['receita_total_estim']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))

        clientes_com = metricas_com["clientes_df"]

        # =============================
        # MÉTRICAS COMERCIAIS PRO
        # =============================
        try:
            clientes_com_pro = clientes_com.copy()
            divisor_plano = {
                "mensal": 1,
                "trimestral": 3,
                "semestral": 6,
                "anual": 12,
                "teste": 0,
                "vitalicio": 0,
                "vitalicio_v35": 0,
                "admin/base": 0,
                "secrets": 0,
            }
            if not clientes_com_pro.empty:
                clientes_com_pro["divisor_receita"] = clientes_com_pro["plano_norm"].map(divisor_plano).fillna(1)
                clientes_com_pro["ativo_receita"] = (clientes_com_pro["ativo"].astype(bool)) & (clientes_com_pro["status_comercial"] != "VENCIDO")
                clientes_com_pro["receita_mensal_recorrente"] = clientes_com_pro.apply(
                    lambda r: (float(r.get("valor_estimado", 0) or 0) / float(r.get("divisor_receita", 1)))
                    if bool(r.get("ativo_receita")) and float(r.get("divisor_receita", 0) or 0) > 0 else 0.0,
                    axis=1,
                )
                receita_mensal_recorrente = float(clientes_com_pro["receita_mensal_recorrente"].sum())
                receita_anual_recorrente = receita_mensal_recorrente * 12
                vencem_3d_top = int(((clientes_com_pro["ativo"].astype(bool)) & (clientes_com_pro["dias_restantes"].between(0, 3, inclusive="both"))).sum())
            else:
                receita_mensal_recorrente = 0.0
                receita_anual_recorrente = 0.0
                vencem_3d_top = 0
        except Exception:
            receita_mensal_recorrente = 0.0
            receita_anual_recorrente = 0.0
            vencem_3d_top = 0

        st.markdown("### 📈 Receita recorrente estimada")
        pr1, pr2, pr3 = st.columns(3)
        pr1.metric("Receita mensal estimada", f"R$ {receita_mensal_recorrente:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
        pr2.metric("Receita anual estimada", f"R$ {receita_anual_recorrente:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
        pr3.metric("Vencendo em 3 dias", vencem_3d_top)

        st.markdown("### 💼 Painel Financeiro Master")
        try:
            fin = calcular_painel_financeiro_master()
            f1, f2, f3, f4, f5 = st.columns(5)
            f1.metric("Recebido no mês", _moeda_br(fin["receita_mes"]))
            f2.metric("Recebido no ano", _moeda_br(fin["receita_ano"]))
            f3.metric("Pendente", _moeda_br(fin["pendente_total"]))
            f4.metric("Vencido", _moeda_br(fin["vencido_total"]))
            f5.metric("Ticket médio", _moeda_br(fin["ticket_medio"]))

            f6, f7, f8 = st.columns(3)
            f6.metric("Renovações pendentes", fin["renovacoes_pendentes"])
            f7.metric("Renovações aprovadas", fin["renovacoes_aprovadas"])
            f8.metric("Renovações recusadas", fin["renovacoes_recusadas"])

            with st.expander("📜 Histórico financeiro detalhado", expanded=False):
                cobr_hist = fin.get("df_cobrancas", pd.DataFrame())
                if cobr_hist is None or cobr_hist.empty:
                    st.info("Ainda não há cobranças/pagamentos no histórico.")
                else:
                    cobr_view = formatar_colunas_datas(cobr_hist.copy())
                    cols_hist = ["id", "email", "nome", "plano", "valor", "status", "metodo", "vencimento_cobranca", "data_pagamento", "observacao", "criado_em", "atualizado_em"]
                    st.dataframe(cobr_view[[c for c in cols_hist if c in cobr_view.columns]], use_container_width=True)
                    st.download_button(
                        "⬇️ Baixar histórico financeiro CSV",
                        cobr_hist.to_csv(index=False).encode("utf-8-sig"),
                        "historico_financeiro.csv",
                        "text/csv",
                        key="download_historico_financeiro_master",
                        use_container_width=True,
                    )
        except Exception as e:
            st.warning(f"Painel financeiro indisponível: {e}")

        if clientes_com.empty:
            st.warning("Ainda não há clientes cadastrados no banco SQLite.")
        else:
            st.markdown("### 📋 Carteira de clientes")
            cols = ["email", "nome", "plano", "valor_estimado", "vencimento", "dias_restantes", "ativo", "status_comercial", "atualizado_em"]
            clientes_com_view = formatar_colunas_datas(clientes_com)
            st.dataframe(clientes_com_view[[c for c in cols if c in clientes_com_view.columns]], use_container_width=True)

            st.markdown("### 📊 Clientes por plano")
            try:
                por_plano = clientes_com.groupby("plano_norm").agg(
                    clientes=("email", "count"),
                    ativos=("ativo", "sum"),
                    receita_estim=("valor_estimado", "sum"),
                ).reset_index().rename(columns={"plano_norm": "plano"})
                st.dataframe(por_plano, use_container_width=True)
            except Exception:
                pass

            st.markdown("### ⏳ Próximos vencimentos")
            try:
                prox = clientes_com[(clientes_com["ativo"].astype(bool)) & (clientes_com["dias_restantes"].notna())].sort_values("dias_restantes").head(20)
                prox_view = formatar_colunas_datas(prox)
                st.dataframe(prox_view[["email", "nome", "plano", "vencimento", "dias_restantes", "status_comercial"]], use_container_width=True)
            except Exception:
                pass

            st.markdown("### 📥 Exportações")
            csv_clientes = clientes_com.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Baixar carteira de clientes CSV", csv_clientes, "clientes_comercial.csv", "text/csv")


        st.markdown("---")
        st.markdown("## 🤖 Automação de cobrança e bloqueio")
        st.caption("Executa avisos de vencimento em 7 dias, 3 dias e no dia do vencimento. Clientes vencidos são bloqueados automaticamente, preservando a conta master/admin.")

        auto_col1, auto_col2, auto_col3 = st.columns(3)
        enviar_auto_email = auto_col1.checkbox("Enviar e-mails automáticos", value=True, key="auto_enviar_emails")
        bloquear_auto_vencidos = auto_col2.checkbox("Bloquear vencidos automaticamente", value=True, key="auto_bloquear_vencidos")
        criar_auto_cobranca = auto_col3.checkbox("Criar cobrança automática", value=True, key="auto_criar_cobranca")

        # Roda uma verificação leve uma vez por dia quando o admin abre o painel.
        hoje_auto = datetime.now(fuso_br).date().isoformat()
        if st.session_state.get("ultima_automacao_comercial") != hoje_auto:
            try:
                resumo_auto_silencioso = executar_automacao_cobranca_bloqueio(
                    enviar_emails=enviar_auto_email,
                    bloquear_vencidos=bloquear_auto_vencidos,
                    criar_cobranca_auto=criar_auto_cobranca,
                )
                st.session_state.ultima_automacao_comercial = hoje_auto
                if any(resumo_auto_silencioso.get(k, 0) for k in ["avisos", "bloqueios", "cobrancas", "falhas"]):
                    st.info(
                        f"Automação diária executada: avisos {resumo_auto_silencioso['avisos']} | "
                        f"bloqueios {resumo_auto_silencioso['bloqueios']} | "
                        f"cobranças {resumo_auto_silencioso['cobrancas']} | "
                        f"falhas {resumo_auto_silencioso['falhas']}"
                    )
            except Exception as e:
                st.warning(f"Automação diária não executou: {e}")

        if st.button("▶️ Executar verificação agora", key="btn_executar_automacao_comercial"):
            resumo_auto = executar_automacao_cobranca_bloqueio(
                enviar_emails=enviar_auto_email,
                bloquear_vencidos=bloquear_auto_vencidos,
                criar_cobranca_auto=criar_auto_cobranca,
            )
            st.success(
                f"Verificação concluída. Processados: {resumo_auto['processados']} | "
                f"Avisos: {resumo_auto['avisos']} | Cobranças: {resumo_auto['cobrancas']} | "
                f"Bloqueios: {resumo_auto['bloqueios']} | Falhas: {resumo_auto['falhas']}"
            )
            st.rerun()

        with st.expander("📜 Histórico de automações", expanded=False):
            log_auto = listar_automacoes_log(200)
            if log_auto.empty:
                st.info("Nenhuma automação registrada ainda.")
            else:
                log_view = formatar_colunas_datas(log_auto)
                cols_auto = ["id", "tipo", "email", "nome", "plano", "vencimento", "dias_restantes", "acao", "status", "mensagem", "criado_em"]
                st.dataframe(log_view[[c for c in cols_auto if c in log_view.columns]], use_container_width=True)


        st.markdown("---")

        st.markdown("---")
        st.markdown("## 💳 Solicitações de renovação via PIX")
        st.caption("Solicitações feitas pelo cliente na aba Minha Conta. Confirme o pagamento para renovar automaticamente.")
        solicitacoes = listar_solicitacoes_renovacao(limite=300)
        if solicitacoes.empty:
            st.info("Nenhuma solicitação de renovação registrada ainda.")
        else:
            sol_view = formatar_colunas_datas(solicitacoes.copy())
            cols_sol = ["id", "email", "nome", "plano_atual", "plano_solicitado", "valor", "vencimento_atual", "novo_vencimento_previsto", "status", "comprovante_nome", "metodo_pagamento", "comprovante_enviado_em", "observacao", "criado_em"]
            st.dataframe(sol_view[[c for c in cols_sol if c in sol_view.columns]], use_container_width=True)

            with st.expander("🧹 Limpar lista de solicitações PIX", expanded=False):
                st.warning("Atenção: esta ação apaga TODAS as solicitações de renovação via PIX. Faça backup antes se precisar guardar histórico.")
                confirmar_limpar_pix = st.checkbox("Confirmo que desejo apagar todas as solicitações PIX", key="confirmar_limpar_solicitacoes_pix")
                if st.button("🧹 Limpar solicitações PIX", key="btn_limpar_solicitacoes_pix", disabled=not confirmar_limpar_pix, use_container_width=True):
                    ok_limpar_pix, msg_limpar_pix = limpar_solicitacoes_renovacao_db()
                    if ok_limpar_pix:
                        st.success(msg_limpar_pix)
                        st.rerun()
                    else:
                        st.error(msg_limpar_pix)

            pendentes_sol = solicitacoes[solicitacoes.get("status", "").astype(str).str.upper().isin(["PENDENTE", "PENDENTE_CONFIRMACAO", "AGUARDANDO_PAGAMENTO"])] if "status" in solicitacoes.columns else pd.DataFrame()
            if not pendentes_sol.empty:
                ids_sol = pendentes_sol["id"].astype(int).tolist()
                sol_id = st.selectbox("Selecionar solicitação pendente", ids_sol, key="select_solicitacao_renovacao")
                sol_sel = pendentes_sol[pendentes_sol["id"].astype(int) == int(sol_id)].iloc[0].to_dict()
                if sol_sel.get('comprovante_path'):
                    if st.button("🔎 Autorizar acesso ao comprovante por 2 minutos", key=f"autorizar_comprovante_{sol_id}"):
                        try:
                            link, ttl = obter_link_comprovante(int(sol_id))
                            st.link_button("Abrir comprovante privado", link)
                            st.caption(f"Link temporário: {ttl} segundos. Não compartilhe.")
                        except Exception as exc:
                            st.error(str(exc))
                elif sol_sel.get('comprovante_base64') or sol_sel.get('comprovante_url'):
                    st.info("Comprovante legado preservado. Requer migração autenticada para Storage privado; a URL antiga não é aberta.")
                else:
                    st.info("Esta solicitação não possui comprovante anexado.")
                csol1, csol2 = st.columns(2)
                with csol1:
                    if st.button("✅ Confirmar pagamento e renovar", key="btn_confirmar_solicitacao_renovacao", use_container_width=True):
                        ok_conf, msg_conf = confirmar_solicitacao_renovacao(int(sol_id))
                        if ok_conf:
                            st.success(msg_conf)
                            st.rerun()
                        else:
                            st.error(msg_conf)
                with csol2:
                    motivo_recusa = st.text_input("Motivo da recusa", value="Comprovante não validado. Envie novamente ou fale com o suporte.", key=f"motivo_recusa_{sol_id}")
                    if st.button("❌ Marcar como recusada", key="btn_recusar_solicitacao_renovacao", use_container_width=True):
                        ok_rec, msg_rec = atualizar_status_solicitacao_renovacao(int(sol_id), "RECUSADA", motivo_recusa)
                        if ok_rec:
                            try:
                                enviar_email_recusa_renovacao(sol_sel, motivo_recusa)
                            except Exception:
                                pass
                            st.warning("Solicitação marcada como recusada e cliente avisado por e-mail, se SMTP estiver configurado.")
                            st.rerun()
                        else:
                            st.error(msg_rec)

        st.markdown("## 💳 Cobranças e renovações")
        st.caption("Controle manual de pagamentos: gere cobranças, marque como paga e acompanhe pendências. É a etapa antes de automatizar Pix/Mercado Pago.")
        if email_configurado():
            st.success("📧 Envio de e-mail SMTP configurado.")
        else:
            st.warning("📧 SMTP ainda não configurado. Cadastros e cobranças funcionam, mas os e-mails não serão enviados.")

        # =============================
        # CENTRAL COMERCIAL DE AÇÃO RÁPIDA
        # =============================
        st.markdown("---")
        st.markdown("## 🧭 Central Comercial de Cobranças")
        st.caption("Acompanhe vencimentos, envie cobranças por e-mail e renove clientes rapidamente.")

        try:
            clientes_central = metricas_com["clientes_df"].copy()
        except Exception:
            clientes_central = pd.DataFrame()

        if clientes_central.empty:
            st.info("Ainda não há clientes para a central comercial.")
        else:
            hoje_central = datetime.now(fuso_br).date()
            clientes_central["dias_restantes"] = pd.to_numeric(clientes_central.get("dias_restantes"), errors="coerce")
            vencendo_7 = clientes_central[(clientes_central["ativo"].astype(bool)) & (clientes_central["dias_restantes"].between(0, 7, inclusive="both"))]
            vencendo_3 = clientes_central[(clientes_central["ativo"].astype(bool)) & (clientes_central["dias_restantes"].between(0, 3, inclusive="both"))]
            vencidos = clientes_central[(clientes_central["ativo"].astype(bool)) & (clientes_central["dias_restantes"] < 0)]

            cc1, cc2, cc3 = st.columns(3)
            cc1.metric("Vencendo em 7 dias", int(len(vencendo_7)))
            cc2.metric("Vencendo em 3 dias", int(len(vencendo_3)))
            cc3.metric("Vencidos", int(len(vencidos)))

            with st.expander("📆 Clientes vencendo em até 7 dias", expanded=True):
                if vencendo_7.empty:
                    st.success("Nenhum cliente vencendo nos próximos 7 dias.")
                else:
                    cols_v = ["email", "nome", "plano", "vencimento", "dias_restantes", "valor_estimado", "status_comercial"]
                    vencendo_7_view = formatar_colunas_datas(vencendo_7)
                    st.dataframe(vencendo_7_view[[c for c in cols_v if c in vencendo_7_view.columns]].sort_values("dias_restantes"), use_container_width=True)

            with st.expander("⚠️ Clientes vencidos", expanded=False):
                if vencidos.empty:
                    st.success("Nenhum cliente vencido.")
                else:
                    cols_v = ["email", "nome", "plano", "vencimento", "dias_restantes", "valor_estimado", "status_comercial"]
                    vencidos_view = formatar_colunas_datas(vencidos)
                    st.dataframe(vencidos_view[[c for c in cols_v if c in vencidos_view.columns]].sort_values("dias_restantes"), use_container_width=True)

            st.markdown("### ⚡ Ações rápidas")
            emails_acao = clientes_central["email"].dropna().astype(str).tolist()
            email_acao = st.selectbox("Selecionar cliente para cobrança/renovação", emails_acao, key="central_cliente_acao")
            linha_acao = clientes_central[clientes_central["email"] == email_acao].iloc[0]
            nome_acao = str(linha_acao.get("nome", email_acao))
            plano_acao = str(linha_acao.get("plano", "mensal"))
            venc_acao = str(linha_acao.get("vencimento", ""))
            valor_acao = float(linha_acao.get("valor_estimado", 0.0) or 0.0)
            dias_acao = linha_acao.get("dias_restantes", None)
            st.info(f"Cliente selecionado: {email_acao} | Plano: {plano_acao} | Vencimento: {formatar_data_br(venc_acao)} | Dias restantes: {dias_acao}")

            a1, a2, a3 = st.columns(3)
            with a1:
                if st.button("📧 Enviar cobrança por e-mail", key="central_enviar_cobranca"):
                    venc_cob = (datetime.now(fuso_br) + timedelta(days=3)).date()
                    obs = f"Cobrança enviada pela Central Comercial. Acesso atual vence em {venc_acao}."
                    cobranca_id = criar_cobranca(email_acao, nome_acao, plano_acao, valor_acao, str(venc_cob), obs)
                    ok_email, msg_email = enviar_email(
                        email_acao,
                        "Cobrança Smart Money AI",
                        mensagem_cobranca(nome_acao, email_acao, plano_acao, valor_acao, str(venc_cob), obs),
                    )
                    if ok_email:
                        st.success(f"Cobrança ID {cobranca_id} criada e e-mail enviado.")
                    else:
                        st.error(f"Cobrança ID {cobranca_id} criada, mas o e-mail falhou: {msg_email}")
            with a2:
                if st.button("🔄 Renovar +30 dias", key="central_renovar_30"):
                    ok_ren, novo_venc = renovar_cliente_dias(email_acao, 30)
                    if ok_ren:
                        st.success(f"Cliente renovado até {formatar_data_br(novo_venc)}.")
                        ok_email, msg_email = enviar_email(
                            email_acao,
                            "Acesso renovado - Smart Money AI",
                            f"Olá, {nome_acao}!\n\nSeu acesso ao Smart Money AI foi renovado com sucesso.\n\nNovo vencimento: {novo_venc}\n\nAcesse: {url_do_app()}\n\nObrigado!",
                        )
                        if ok_email:
                            st.info("E-mail de renovação enviado ao cliente.")
                        else:
                            st.warning(f"Renovou, mas não enviou e-mail: {msg_email}")
                        st.rerun()
                    else:
                        st.error(novo_venc)
            with a3:
                if st.button("🚫 Bloquear por vencimento", key="central_bloquear_cliente"):
                    ok_block, msg_block = bloquear_cliente_db(email_acao, False)
                    if ok_block:
                        st.warning(f"Cliente bloqueado: {email_acao}")
                    else:
                        st.info(msg_block)
                    st.rerun()

            st.markdown("### 📣 Cobrança em lote")
            st.caption("Envie cobranças para grupos de clientes sem precisar selecionar um por um. O sistema cria o registro da cobrança e tenta enviar o e-mail automaticamente.")
            lote_opcao = st.selectbox(
                "Grupo para cobrança em lote",
                ["Vencendo em até 3 dias", "Vencendo em até 7 dias", "Vencidos"],
                key="central_lote_opcao",
            )
            if lote_opcao == "Vencendo em até 3 dias":
                lote_df = vencendo_3.copy()
            elif lote_opcao == "Vencendo em até 7 dias":
                lote_df = vencendo_7.copy()
            else:
                lote_df = vencidos.copy()

            st.write(f"Clientes no lote selecionado: **{len(lote_df)}**")
            if not lote_df.empty:
                lote_df_view = formatar_colunas_datas(lote_df)
                st.dataframe(lote_df_view[[c for c in ["email", "nome", "plano", "vencimento", "dias_restantes", "valor_estimado"] if c in lote_df_view.columns]].sort_values("dias_restantes"), use_container_width=True)
                confirmar_lote = st.checkbox("Confirmo que desejo enviar cobrança para este lote", value=False, key="confirmar_cobranca_lote")
                if st.button("📧 Enviar cobrança para o lote", key="btn_enviar_lote_cobranca", disabled=not confirmar_lote):
                    enviados = 0
                    falhas = 0
                    criadas = 0
                    venc_cob_lote = (datetime.now(fuso_br) + timedelta(days=3)).date()
                    for _, cli in lote_df.iterrows():
                        email_l = str(cli.get("email", "")).strip().lower()
                        nome_l = str(cli.get("nome", email_l))
                        plano_l = str(cli.get("plano", "mensal"))
                        valor_l = float(cli.get("valor_estimado", 0.0) or 0.0)
                        venc_l = str(cli.get("vencimento", ""))
                        if not email_l:
                            continue
                        obs_l = f"Cobrança em lote Smart Money AI. Acesso atual vence em {venc_l}."
                        criar_cobranca(email_l, nome_l, plano_l, valor_l, str(venc_cob_lote), obs_l)
                        criadas += 1
                        ok_email, _msg_email = enviar_email(
                            email_l,
                            "Cobrança Smart Money AI",
                            mensagem_cobranca(nome_l, email_l, plano_l, valor_l, str(venc_cob_lote), obs_l),
                        )
                        if ok_email:
                            enviados += 1
                        else:
                            falhas += 1
                    st.success(f"Cobranças criadas: {criadas} | E-mails enviados: {enviados} | Falhas de e-mail: {falhas}")
                    st.rerun()
            else:
                st.success("Nenhum cliente neste grupo no momento.")

        met_cob = calcular_metricas_cobrancas()
        cb1, cb2, cb3, cb4, cb5 = st.columns(5)
        cb1.metric("Pendentes", met_cob["pendentes"])
        cb2.metric("Vencidas", met_cob["vencidas"])
        cb3.metric("Pagas", met_cob["pagas"])
        cb4.metric("A receber", f"R$ {met_cob['receber']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
        cb5.metric("Recebido", f"R$ {met_cob['recebido']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))

        with st.expander("➕ Criar cobrança manual", expanded=False):
            clientes_base = listar_clientes_db()
            if clientes_base.empty:
                st.warning("Cadastre clientes primeiro na aba Administração.")
            else:
                emails_cob = clientes_base["email"].tolist()
                ccol1, ccol2 = st.columns(2)
                with ccol1:
                    cob_email = st.selectbox("Cliente", emails_cob, key="cob_email_cliente")
                    cliente_linha = clientes_base[clientes_base["email"] == cob_email].iloc[0]
                    cob_nome = st.text_input("Nome", value=str(cliente_linha.get("nome", cob_email)), key="cob_nome_cliente")
                    cob_plano = st.selectbox("Plano", ["mensal", "trimestral", "semestral", "anual", "teste", "vitalicio_v35"], index=0, key="cob_plano_cliente")
                with ccol2:
                    precos = _precos_planos_padrao()
                    valor_padrao = float(precos.get(cob_plano, 0.0))
                    cob_valor = st.number_input("Valor R$", min_value=0.0, value=valor_padrao, step=10.0, key="cob_valor_cliente")
                    cob_venc = st.date_input("Vencimento da cobrança", value=(datetime.now(fuso_br) + timedelta(days=3)).date(), key="cob_venc_cliente")
                    cob_obs = st.text_input("Observação", value="", key="cob_obs_cliente")
                enviar_email_cob = st.checkbox("Enviar e-mail de cobrança ao cliente", value=True, key="cob_enviar_email_cliente")
                if st.button("💾 Gerar cobrança", key="btn_gerar_cobranca"):
                    cobranca_id = criar_cobranca(cob_email, cob_nome, cob_plano, float(cob_valor), str(cob_venc), cob_obs)
                    st.success(f"Cobrança criada. ID: {cobranca_id}")
                    if enviar_email_cob:
                        ok_email, msg_email = enviar_email(
                            cob_email,
                            "Cobrança Smart Money AI",
                            mensagem_cobranca(cob_nome, cob_email, cob_plano, float(cob_valor), str(cob_venc), cob_obs),
                        )
                        if ok_email:
                            st.success("E-mail de cobrança enviado ao cliente.")
                            st.info(f"Confira caixa de entrada e Spam de {cob_email}.")
                        else:
                            st.error(msg_email)
                    st.info("Cobrança criada. Se quiser atualizar a lista imediatamente, recarregue a página ou clique em outra aba e volte.")

        cobrancas_df = met_cob["df"]
        if cobrancas_df.empty:
            st.info("Ainda não há cobranças cadastradas.")
        else:
            st.markdown("### 📚 Histórico de cobranças enviadas")
            st.caption("Use esta tabela para acompanhar cobranças pendentes, pagas, vencidas, canceladas e removidas.")
            cols_cob = ["id", "email", "nome", "plano", "valor", "vencimento_cobranca", "data_pagamento", "metodo", "status", "status_real", "observacao", "atualizado_em"]
            cobrancas_view = formatar_colunas_datas(cobrancas_df)
            st.dataframe(cobrancas_view[[c for c in cols_cob if c in cobrancas_view.columns]], use_container_width=True)
            ids_cob = cobrancas_df["id"].astype(int).tolist()
            cob_sel = st.selectbox("Selecionar cobrança", ids_cob, key="cob_id_acao")
            acol1, acol2, acol3, acol4 = st.columns(4)
            with acol1:
                metodo_pag = st.selectbox("Método", ["Pix", "Cartão", "Transferência", "Dinheiro", "Outro"], key="cob_metodo_pag")
            with acol2:
                data_pag = st.date_input("Data pagamento", value=datetime.now(fuso_br).date(), key="cob_data_pag")
            with acol3:
                obs_pag = st.text_input("Obs. pagamento", key="cob_obs_pag")
            with acol4:
                st.write("")
                if st.button("✅ Marcar paga", key="cob_marcar_paga"):
                    atualizar_status_cobranca(cob_sel, "PAGA", metodo_pag, str(data_pag), obs_pag)
                    st.success("Cobrança marcada como paga.")
                    st.rerun()
            bcol1, bcol2, bcol3 = st.columns(3)
            with bcol1:
                if st.button("⏳ Marcar pendente", key="cob_pendente"):
                    atualizar_status_cobranca(cob_sel, "PENDENTE", "", "", obs_pag)
                    st.rerun()
            with bcol2:
                if st.button("🚫 Cancelar cobrança", key="cob_cancelar"):
                    atualizar_status_cobranca(cob_sel, "CANCELADA", "", "", obs_pag)
                    st.rerun()
            with bcol3:
                if st.button("🗑 Remover cobrança", key="cob_remover"):
                    remover_cobranca(cob_sel)
                    st.warning("Cobrança removida.")
                    st.rerun()
            csv_cob = cobrancas_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Baixar cobranças CSV", csv_cob, "cobrancas_clientes.csv", "text/csv")

            with st.expander("🧹 Limpar histórico de cobranças", expanded=False):
                st.warning("Atenção: esta ação apaga TODO o histórico de cobranças. Faça backup antes se precisar guardar histórico.")
                confirmar_limpar_cob = st.checkbox("Confirmo que desejo apagar todo o histórico de cobranças", key="confirmar_limpar_historico_cobrancas")
                if st.button("🧹 Limpar histórico de cobranças", key="btn_limpar_historico_cobrancas", disabled=not confirmar_limpar_cob, use_container_width=True):
                    ok_limpar_cob, msg_limpar_cob = limpar_historico_cobrancas_db()
                    if ok_limpar_cob:
                        st.success(msg_limpar_cob)
                        st.rerun()
                    else:
                        st.error(msg_limpar_cob)

        st.markdown("---")
        with st.expander("⚙️ Como alterar valores e links de pagamento"):
            st.code("""[PLAN_PRICES]
mensal = 97
trimestral = 247
semestral = 397
anual = 697
teste = 0
vitalicio_v35 = 700

# Links de checkout/cartão por plano
CARD_PAYMENT_MENSAL = "https://seu-link-do-plano-mensal"
CARD_PAYMENT_TRIMESTRAL = "https://seu-link-do-plano-trimestral"
CARD_PAYMENT_SEMESTRAL = "https://seu-link-do-plano-semestral"
CARD_PAYMENT_ANUAL = "https://seu-link-do-plano-anual"
CARD_PAYMENT_VITALICIO_V35 = "https://seu-link-do-plano-vitalicio-v35""", language="toml")
            st.caption("Adicione esse bloco no Streamlit Secrets para personalizar valores e exibir o botão de pagamento por cartão na área Minha Conta.")

if aba_admin is not None:
    with aba_admin:
        registrar_evento_demo_unico("Administração", "Aba administrativa carregada")
        bloquear_area_admin_para_cliente()
        with st.expander("🧹 Manutenção global da auditoria", expanded=False):
            st.warning("Esta ação apaga a auditoria de TODOS os usuários. Faça backup antes de confirmar.")
            confirmar_exclusao_auditoria = st.checkbox(
                "Confirmo a exclusão de toda a auditoria", key="confirmar_exclusao_auditoria_global"
            )
            if st.button("Excluir auditoria global", key="btn_limpar_auditoria", disabled=not confirmar_exclusao_auditoria):
                ok_limpar, msg_limpar = limpar_auditoria_persistente(confirmacao=confirmar_exclusao_auditoria)
                if ok_limpar:
                    st.session_state.auditoria_sinais = []
                    st.session_state["auditoria_nao_salva"] = {}
                    for chave in ("auditoria_ultima_gravacao", "auditoria_ultima_avaliacao", "auditoria_resultados_retry"):
                        st.session_state.pop(chave, None)
                    st.success(msg_limpar)
                else:
                    st.error(msg_limpar)

        st.subheader("👑 Administração de Clientes")
        st.info("Cadastre, renove, bloqueie ou remova clientes sem editar o Secrets. Usuários do Secrets continuam funcionando como base/admin.")

        with st.expander("🗄️ Banco persistente Supabase", expanded=True):
            diag = diagnosticar_supabase()
            if supabase_configurado():
                if diag.get("clientes_ok") and diag.get("cobrancas_ok") and diag.get("automacoes_ok"):
                    st.success("✅ Supabase conectado e tabelas OK. Clientes e cobranças permanecem salvos após reboot/redeploy.")
                else:
                    st.error("❌ Supabase configurado, mas houve erro de conexão/tabelas. Não cadastre clientes até corrigir.")
                    st.code(str(diag), language="text")
                st.caption("Use a migração apenas uma vez, caso ainda existam clientes no SQLite local desta sessão.")
                if st.button("🧪 Testar conexão Supabase agora", key="btn_testar_supabase"):
                    st.json(diagnosticar_supabase())
                if st.button("📤 Migrar SQLite local para Supabase", key="btn_migrar_sqlite_supabase"):
                    res_mig = migrar_sqlite_local_para_supabase()
                    if res_mig.get("ok"):
                        st.success(res_mig.get("msg"))
                    else:
                        st.error(res_mig.get("msg"))
            else:
                st.error("Supabase ainda não está configurado. O banco local do Streamlit pode sumir após reboot.")
                st.code('SUPABASE_URL="https://xxxx.supabase.co"\nSUPABASE_SERVICE_ROLE_KEY="<CONFIGURE_NO_AMBIENTE>"', language="toml")
        if st.session_state.get("admin_msg_success"):
            st.success(st.session_state.pop("admin_msg_success"))
        if st.session_state.get("admin_msg_info"):
            st.info(st.session_state.pop("admin_msg_info"))
        if st.session_state.get("admin_msg_error"):
            st.error(st.session_state.pop("admin_msg_error"))

        with st.expander("🛡️ Proteção de uso e histórico de sinais", expanded=False):
            st.caption("Controle para evitar excesso de cliques e proteger o app quando houver muitos clientes simultâneos.")
            st.code('SIGNAL_COOLDOWN_SECONDS=45', language="toml")
            st.caption("Você pode alterar esse valor no Streamlit Secrets. Exemplo: 30, 45 ou 60 segundos.")
            uso_df = dataframe_uso_sinais()
            if uso_df.empty:
                st.info("Ainda não há registros de uso de sinais.")
            else:
                st.dataframe(uso_df.head(300), use_container_width=True)
                csv_uso = uso_df.to_csv(index=False).encode("utf-8-sig")
                st.download_button("⬇️ Baixar histórico de uso CSV", csv_uso, "uso_sinais_clientes.csv", "text/csv")

                try:
                    base = uso_df.copy()
                    resumo_clientes = base[base["status"] == "GERADO"].groupby("usuario").size().reset_index(name="sinais_gerados")
                    resumo_clientes = resumo_clientes.sort_values("sinais_gerados", ascending=False)
                    st.markdown("#### Clientes que mais geraram sinais")
                    st.dataframe(resumo_clientes.head(50), use_container_width=True)
                except Exception:
                    pass



        with st.expander("👀 Demonstrações / convidados", expanded=False):
            st.caption("Acompanhe se um convidado entrou, quando acessou e quais eventos principais realizou. Ideal para usuários demo enviados a traders/parceiros.")

            visitas_df = listar_paginas_visitadas_db(500)
            acessos_df = listar_acessos_log(500)
            sessoes_df = pd.DataFrame(listar_sessoes_ativas_db())

            email_alerta_demo = email_notificacao_demo()
            wpp_ativo_demo = whatsapp_demo_configurado()
            telegram_ativo_demo = telegram_demo_configurado()
            if email_alerta_demo or wpp_ativo_demo or telegram_ativo_demo:
                canais = []
                if email_alerta_demo:
                    canais.append(f"E-mail: {email_alerta_demo}")
                if wpp_ativo_demo:
                    canais.append("WhatsApp: ativo")
                if telegram_ativo_demo:
                    canais.append("Telegram: ativo")
                st.success("🔔 Aviso de acesso DEMO ativo — " + " | ".join(canais))
            else:
                st.warning("🔔 Para receber aviso quando um convidado DEMO acessar, configure DEMO_NOTIFY_EMAIL, DEMO_WHATSAPP_PHONE + DEMO_WHATSAPP_APIKEY ou DEMO_TELEGRAM_BOT_TOKEN + DEMO_TELEGRAM_CHAT_ID nos Secrets do Streamlit.")

            filtro_demo = st.text_input("Filtrar por e-mail/usuário do convidado", value="", key="filtro_demo_convidado").strip().lower()

            def _filtrar_usuario(df: pd.DataFrame) -> pd.DataFrame:
                if df is None or df.empty or not filtro_demo or "usuario" not in df.columns:
                    return df
                return df[df["usuario"].astype(str).str.lower().str.contains(filtro_demo, na=False)]

            visitas_view = _filtrar_usuario(visitas_df)
            acessos_view = _filtrar_usuario(acessos_df)
            sessoes_view = _filtrar_usuario(sessoes_df)

            d1, d2, d3 = st.columns(3)
            d1.metric("Eventos registrados", 0 if visitas_view is None or visitas_view.empty else len(visitas_view))
            d2.metric("Logs de acesso", 0 if acessos_view is None or acessos_view.empty else len(acessos_view))
            d3.metric("Sessões ativas", 0 if sessoes_view is None or sessoes_view.empty else len(sessoes_view))

            st.markdown("#### Últimos eventos do convidado")
            if visitas_view is None or visitas_view.empty:
                st.info("Nenhum evento de demonstração registrado ainda. Faça login com o usuário demo para testar.")
            else:
                cols_vis = ["data_hora", "usuario", "pagina", "detalhe", "dispositivo", "navegador"]
                visitas_exibir = formatar_colunas_datahora_br(visitas_view)
                st.dataframe(visitas_exibir[[c for c in cols_vis if c in visitas_exibir.columns]].head(300), use_container_width=True)
                st.download_button(
                    "⬇️ Baixar eventos de demonstração CSV",
                    visitas_view.to_csv(index=False).encode("utf-8-sig"),
                    "demonstracoes_smart_money_ai.csv",
                    "text/csv",
                    use_container_width=True,
                )

            st.markdown("#### Logins e tentativas")
            if acessos_view is None or acessos_view.empty:
                st.info("Nenhum log de acesso encontrado para o filtro atual.")
            else:
                cols_acc = ["data_hora", "usuario", "status", "ip", "dispositivo", "navegador"]
                acessos_exibir = formatar_colunas_datahora_br(acessos_view)
                st.dataframe(acessos_exibir[[c for c in cols_acc if c in acessos_exibir.columns]].head(300), use_container_width=True)

            st.markdown("#### Sessões ativas / último acesso")
            if sessoes_view is None or sessoes_view.empty:
                st.info("Nenhuma sessão ativa encontrada para o filtro atual.")
            else:
                cols_ses = ["usuario", "last_seen", "last_seen_iso", "ip", "dispositivo", "navegador", "token"]
                sessoes_exibir = formatar_colunas_datahora_br(sessoes_view)
                st.dataframe(sessoes_exibir[[c for c in cols_ses if c in sessoes_exibir.columns]].head(300), use_container_width=True)

            st.markdown("#### Limpeza de históricos")
            st.warning("Use com cuidado: estas ações apagam registros administrativos do Supabase.")
            lcol1, lcol2 = st.columns(2)
            with lcol1:
                confirmar_limpar_demo = st.checkbox("Confirmo limpar últimos eventos do convidado", key="confirmar_limpar_demo")
                if st.button("🧹 Limpar últimos eventos", key="btn_limpar_demo", disabled=not confirmar_limpar_demo, use_container_width=True):
                    ok_demo, msg_demo = limpar_paginas_visitadas_db()
                    if ok_demo:
                        st.success(msg_demo)
                        st.rerun()
                    else:
                        st.error(msg_demo)
            with lcol2:
                confirmar_limpar_login = st.checkbox("Confirmo limpar logins e tentativas", key="confirmar_limpar_login_demo")
                if st.button("🧹 Limpar logins e tentativas", key="btn_limpar_login_demo", disabled=not confirmar_limpar_login, use_container_width=True):
                    ok_login, msg_login = limpar_acessos_log_db()
                    if ok_login:
                        st.success(msg_login)
                        st.rerun()
                    else:
                        st.error(msg_login)

        with st.expander("📜 Logs completos e auditoria", expanded=False):
            st.caption("Registra login, logout, senha errada, bloqueios, recuperação de senha, alterações e eventos importantes.")
            logs_df = listar_acessos_log(500)
            if logs_df.empty:
                st.info("Nenhum log registrado ainda.")
            else:
                logs_view = formatar_colunas_datahora_br(formatar_colunas_datas(logs_df))
                st.dataframe(logs_view, use_container_width=True)
                st.download_button(
                    "⬇️ Baixar logs CSV",
                    logs_df.to_csv(index=False).encode("utf-8-sig"),
                    "logs_acesso_smart_money_ai.csv",
                    "text/csv",
                )

        with st.expander("🎟️ Cupons de desconto", expanded=False):
            st.caption("Crie cupons para campanhas comerciais. Esta etapa registra os cupons no Supabase para uso em vendas e renovações.")
            ccol1, ccol2, ccol3 = st.columns(3)
            with ccol1:
                cupom_codigo = st.text_input("Código do cupom", placeholder="TESTE7", key="cupom_codigo")
            with ccol2:
                cupom_desc = st.number_input("Desconto %", min_value=0.0, max_value=100.0, value=10.0, step=1.0, key="cupom_desc")
            with ccol3:
                cupom_validade = st.date_input("Validade", value=(datetime.now(fuso_br)+timedelta(days=30)).date(), key="cupom_validade")
            cupom_obs = st.text_input("Observação", value="", key="cupom_obs")
            cupom_ativo = st.checkbox("Cupom ativo", value=True, key="cupom_ativo")
            if st.button("💾 Salvar cupom", key="btn_salvar_cupom"):
                ok_c, msg_c = criar_cupom_db(cupom_codigo, cupom_desc, cupom_ativo, str(cupom_validade), cupom_obs)
                if ok_c:
                    st.success(msg_c)
                else:
                    st.error(msg_c)
            cupons_df = listar_cupons_db()
            if cupons_df.empty:
                st.info("Nenhum cupom cadastrado ou tabela de cupons ainda não criada.")
            else:
                st.dataframe(formatar_colunas_datas(cupons_df), use_container_width=True)

        with st.expander("🤝 Afiliados e indicações", expanded=False):
            st.caption("Controle os links de indicação dos clientes e acompanhe quem indicou novos usuários.")
            afiliados_df = listar_afiliados_db()
            indicacoes_df = listar_indicacoes_db()

            st.markdown("#### Afiliados")
            if afiliados_df.empty:
                st.info("Nenhum afiliado registrado ainda. O afiliado é criado automaticamente quando o cliente acessa Minha Conta ou quando o admin salva o cliente.")
            else:
                afiliados_view = afiliados_df.copy()
                if "codigo" in afiliados_view.columns:
                    afiliados_view["link"] = afiliados_view["codigo"].apply(link_afiliado_cliente)
                cols_af = ["usuario", "email", "codigo", "link", "indicacoes", "comissao_pct", "saldo_estimado", "ativo", "criado_em", "atualizado_em"]
                st.dataframe(afiliados_view[[c for c in cols_af if c in afiliados_view.columns]], use_container_width=True)
                csv_af = afiliados_view.to_csv(index=False).encode("utf-8-sig")
                st.download_button("⬇️ Baixar afiliados CSV", csv_af, "afiliados_smart_money_ai.csv", "text/csv", use_container_width=True)

                st.markdown("#### Excluir afiliado")
                opcoes_afiliados = []
                try:
                    for _, af in afiliados_df.iterrows():
                        cod = str(af.get("codigo", "")).strip().lower()
                        eml = str(af.get("email", "")).strip().lower()
                        usu = str(af.get("usuario", "")).strip()
                        if cod or eml:
                            opcoes_afiliados.append(f"{usu} | {eml} | {cod}")
                except Exception:
                    pass
                af_excluir = st.selectbox("Selecione o afiliado para excluir", opcoes_afiliados, key="select_excluir_afiliado") if opcoes_afiliados else ""
                apagar_indicacoes_af = st.checkbox("Apagar também as indicações ligadas a este afiliado", value=True, key="apagar_indicacoes_afiliado")
                confirmar_excluir_af = st.checkbox("Confirmo que desejo excluir este afiliado", key="confirmar_excluir_afiliado")
                if st.button("🗑 Excluir afiliado selecionado", key="btn_excluir_afiliado", disabled=(not af_excluir or not confirmar_excluir_af), use_container_width=True):
                    codigo_excluir = af_excluir.split("|")[-1].strip().lower()
                    ok_exc_af, msg_exc_af = excluir_afiliado_db(codigo_excluir, apagar_indicacoes_af)
                    if ok_exc_af:
                        st.success(msg_exc_af)
                        st.rerun()
                    else:
                        st.error(msg_exc_af)

            st.markdown("#### Indicações")
            if indicacoes_df.empty:
                st.info("Nenhuma indicação registrada ainda.")
            else:
                cols_ind = ["id", "afiliado_codigo", "afiliado_email", "novo_usuario", "novo_email", "plano", "valor", "status", "criado_em", "atualizado_em"]
                st.dataframe(indicacoes_df[[c for c in cols_ind if c in indicacoes_df.columns]], use_container_width=True)
                csv_ind = indicacoes_df.to_csv(index=False).encode("utf-8-sig")
                st.download_button("⬇️ Baixar indicações CSV", csv_ind, "indicacoes_smart_money_ai.csv", "text/csv", use_container_width=True)

            st.markdown("#### Registrar indicação manual")
            cm1, cm2, cm3 = st.columns(3)
            with cm1:
                ind_codigo = st.text_input("Código do afiliado", key="manual_ind_codigo").strip().lower()
            with cm2:
                ind_email = st.text_input("E-mail do novo cliente", key="manual_ind_email").strip().lower()
            with cm3:
                ind_plano = st.selectbox("Plano indicado", ["mensal", "trimestral", "semestral", "anual", "teste", "vitalicio_v35"], key="manual_ind_plano")
            if st.button("➕ Registrar indicação manual", key="btn_registrar_indicacao_manual"):
                valor_ind = _precos_planos_padrao().get(str(ind_plano).lower(), 0.0)
                ok_ind, msg_ind = registrar_indicacao_afiliado(ind_codigo, ind_email, ind_email, ind_plano, valor_ind, "PENDENTE")
                if ok_ind:
                    st.success(msg_ind)
                    st.rerun()
                else:
                    st.error(msg_ind)


        with st.expander("🧰 Exportação administrativa", expanded=False):
            st.caption("Baixe um pacote ZIP com clientes, cobranças, automações, logs, cupons e histórico de uso.")
            try:
                st.caption("Exportação sensível para uso privado. Inclui manifest com hashes, contagens, exclusões e erros. Não substitui backup restaurável de banco e Storage.")
                backup_bytes = gerar_backup_completo()
                import io
                with zipfile.ZipFile(io.BytesIO(backup_bytes)) as export_zip:
                    export_manifest = json.loads(export_zip.read('manifest.json'))
                if export_manifest['erros']:
                    st.warning("Exportação PARCIAL: consulte os erros no manifest antes de utilizá-la.")
                st.download_button(
                    "⬇️ Baixar exportação administrativa ZIP",
                    backup_bytes,
                    f"exportacao_administrativa_{datetime.now(fuso_br).strftime('%Y%m%d_%H%M')}.zip",
                    "application/zip",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Não foi possível gerar backup: {e}")

        with st.expander("📧 Teste de e-mail SMTP", expanded=False):
            st.caption("Use este teste antes de cadastrar clientes. Se houver falha, o erro do Gmail/SMTP aparecerá abaixo.")
            st.json(resumo_config_email())
            email_teste = st.text_input(
                "E-mail para receber teste",
                value=st.session_state.get("usuario_logado", ""),
                key="admin_email_teste_smtp",
            ).strip().lower()
            assunto_teste = "Teste de e-mail - Smart Money AI"
            corpo_teste = f"""Olá!

Este é um teste de envio do Smart Money AI.

Se você recebeu esta mensagem, o SMTP está funcionando.

Link do app:
{url_do_app()}

Horário do teste: {datetime.now(fuso_br).strftime('%d/%m/%Y %H:%M:%S')}
"""
            if st.button("📨 Enviar e-mail de teste", key="btn_teste_smtp"):
                ok, msg = enviar_email(email_teste, assunto_teste, corpo_teste)
                if ok:
                    st.success(msg)
                    st.info(f"Verifique a caixa de entrada e a pasta Spam/Lixo eletrônico de {email_teste}.")
                else:
                    st.error(msg)
                    st.warning("Se for Gmail: confirme se a senha é uma Senha de app e se foi copiada corretamente. O app remove espaços automaticamente.")

        with st.expander("🎁 Criar cliente DEMO / convidado", expanded=True):
            st.caption("Crie acessos temporários para traders, parceiros e influenciadores sem misturar com clientes pagantes.")
            if not clientes_tem_coluna_tipo_usuario():
                st.warning("Para separar DEMO de CLIENTE no banco, execute o SQL de tipo_usuario no Supabase. Mesmo sem ele, o app criará o demo com plano 'demo'.")
            dcol1, dcol2 = st.columns(2)
            with dcol1:
                demo_nome = st.text_input("Nome do convidado", placeholder="Trader Convidado", key="demo_nome_convidado")
                demo_email = st.text_input("E-mail do convidado", placeholder="trader.demo@gmail.com", key="demo_email_convidado").strip().lower()
                demo_dias = st.number_input("Validade do demo (dias)", min_value=1, max_value=30, value=7, step=1, key="demo_dias_validade")
            with dcol2:
                senha_sugerida = st.session_state.get("demo_senha_sugerida") or gerar_senha_temporaria(10)
                st.session_state["demo_senha_sugerida"] = senha_sugerida
                demo_senha = st.text_input("Senha do demo", value=senha_sugerida, key="demo_senha_convidado")
                demo_obs = st.text_input("Observação interna", placeholder="Ex: apresentação para parceiro", key="demo_obs_convidado")
                enviar_email_demo = st.checkbox("Enviar e-mail de acesso ao convidado", value=True, key="demo_enviar_email")

            venc_demo = (datetime.now(fuso_br) + timedelta(days=int(demo_dias))).date()
            st.info(f"O demo será criado com vencimento em {formatar_data_br(venc_demo)} e não entrará na receita estimada.")
            if st.button("🎁 Criar acesso DEMO", key="btn_criar_demo_convidado", use_container_width=True):
                if not demo_email:
                    st.error("Informe o e-mail do convidado.")
                else:
                    try:
                        upsert_cliente(
                            email=demo_email,
                            nome=demo_nome or demo_email,
                            senha=demo_senha,
                            vencimento=str(venc_demo),
                            plano="demo",
                            ativo=True,
                            tipo_usuario="DEMO",
                        )
                    except ValueError as erro_senha:
                        st.error(str(erro_senha))
                        st.stop()
                    try:
                        registrar_pagina_visitada(demo_email, "Demo criado", demo_obs or "Acesso demo criado pelo administrador")
                    except Exception:
                        pass
                    st.success(f"Demo criado: {demo_email} até {formatar_data_br(venc_demo)}")
                    dados_demo = f"""Acesso de demonstração Smart Money AI

Link: {url_do_app()}
Login: {demo_email}
Senha: {demo_senha or senha_sugerida}
Validade: {formatar_data_br(venc_demo)}
"""
                    st.code(dados_demo, language="text")
                    if enviar_email_demo:
                        corpo_demo = f"""Olá, {demo_nome or demo_email}!

Foi criado um acesso de demonstração para você conhecer o Smart Money AI.

Acesse pelo link:
{url_do_app()}

Login: {demo_email}
Senha: {demo_senha or senha_sugerida}
Validade do demo: {formatar_data_br(venc_demo)}

Durante o teste, você poderá conhecer a plataforma por dentro.

Smart Money AI
"""
                        ok_demo_email, msg_demo_email = enviar_email(demo_email, "Acesso demo - Smart Money AI", corpo_demo)
                        if ok_demo_email:
                            st.success("E-mail de demonstração enviado.")
                        else:
                            st.warning(f"Demo criado, mas o e-mail falhou: {msg_demo_email}")
                    st.session_state["demo_senha_sugerida"] = gerar_senha_temporaria(10)

        with st.expander("➕ Adicionar ou renovar cliente", expanded=True):
            col1, col2 = st.columns(2)
            with col1:
                novo_email = st.text_input("E-mail do cliente", key="admin_email_cliente").strip().lower()
                novo_nome = st.text_input("Nome do cliente", key="admin_nome_cliente")
                novo_plano = st.selectbox("Plano", ["mensal", "trimestral", "semestral", "anual", "teste", "vitalicio_v35"], key="admin_plano_cliente")
            with col2:
                nova_senha = st.text_input("Senha nova", type="password", key="admin_senha_cliente", help="Deixe em branco ao renovar para manter a senha atual.")
                venc_default = (datetime.now(fuso_br) + timedelta(days=30)).date()
                novo_venc = st.date_input("Vencimento", value=venc_default, key="admin_venc_cliente")
                novo_ativo = st.checkbox("Cliente ativo", value=True, key="admin_ativo_cliente")
                codigo_indicacao_admin = st.text_input(
                    "Código de afiliado/indicação (opcional)",
                    value=st.session_state.get("codigo_indicacao", ""),
                    key="admin_codigo_indicacao_cliente",
                    help="Exemplo: carlos. Preencha somente se este cliente veio por indicação."
                ).strip().lower()
                st.checkbox("Enviar e-mail de acesso ao salvar", value=True, key="admin_enviar_email_cliente")
            if st.button("💾 Salvar cliente / renovar", key="admin_salvar_cliente"):
                if not novo_email:
                    st.error("Informe o e-mail do cliente.")
                else:
                    try:
                        upsert_cliente(
                            email=novo_email,
                            nome=novo_nome or novo_email,
                            senha=nova_senha if nova_senha else None,
                            vencimento=str(novo_venc),
                            plano=novo_plano,
                            ativo=novo_ativo,
                            tipo_usuario="CLIENTE",
                        )
                    except ValueError as erro_senha:
                        st.error(str(erro_senha))
                        st.stop()
                    st.success(f"Cliente salvo: {novo_email} até {formatar_data_br(novo_venc)}")
                    try:
                        garantir_afiliado_cliente(novo_email, novo_nome or novo_email)
                        if codigo_indicacao_admin:
                            valor_indicacao = _precos_planos_padrao().get(str(novo_plano).lower(), 0.0)
                            ok_ind, msg_ind = registrar_indicacao_afiliado(
                                codigo_indicacao_admin,
                                novo_email,
                                novo_nome or novo_email,
                                novo_plano,
                                valor_indicacao,
                                "PENDENTE"
                            )
                            if ok_ind:
                                st.success(f"Indicação registrada para o afiliado: {codigo_indicacao_admin}")
                            else:
                                st.warning(msg_ind)
                    except Exception as e:
                        st.warning(f"Cliente salvo, mas não foi possível processar afiliado/indicação: {e}")
                    enviar_auto = st.session_state.get("admin_enviar_email_cliente", True)
                    if enviar_auto:
                        ok_email, msg_email = enviar_email(
                            novo_email,
                            "Seu acesso ao Smart Money AI foi liberado",
                            mensagem_boas_vindas(novo_nome or novo_email, novo_email, nova_senha, novo_plano, str(novo_venc)),
                        )
                        if ok_email:
                            st.success("E-mail de acesso enviado ao cliente.")
                            st.info(f"Confira caixa de entrada e Spam de {novo_email}.")
                        else:
                            st.error(msg_email)
                    st.session_state["admin_msg_success"] = f"Cliente salvo/renovado: {novo_email} até {formatar_data_br(novo_venc)}."
                    if enviar_auto and 'ok_email' in locals() and ok_email:
                        st.session_state["admin_msg_info"] = f"E-mail de acesso enviado para {novo_email}."
                    elif enviar_auto and 'msg_email' in locals():
                        st.session_state["admin_msg_error"] = f"Cliente salvo, mas o e-mail falhou: {msg_email}"
                    st.rerun()

        st.markdown("### Clientes cadastrados")
        clientes_df = listar_clientes_db()
        if clientes_df.empty:
            st.warning("Nenhum cliente cadastrado no banco ainda.")
        else:
            st.dataframe(formatar_colunas_datas(clientes_df), use_container_width=True)
            emails = clientes_df["email"].tolist()
            selecionado = st.selectbox("Selecionar cliente para ação", emails, key="admin_cliente_acao")
            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("✅ Ativar", key="admin_ativar"):
                    ok_block, msg_block = bloquear_cliente_db(selecionado, True)
                    st.success(msg_block)
                    st.rerun()
            with c2:
                if st.button("🚫 Bloquear", key="admin_bloquear"):
                    ok_block, msg_block = bloquear_cliente_db(selecionado, False)
                    if ok_block:
                        st.warning("Cliente bloqueado.")
                    else:
                        st.info(msg_block)
                    st.rerun()
            with c3:
                if st.button("🗑 Remover", key="admin_remover"):
                    if usuario_master_protegido(selecionado):
                        st.info("Conta master/admin protegida: não pode ser removida.")
                    else:
                        remover_cliente_db(selecionado)
                        st.warning("Cliente removido.")
                    st.rerun()

        st.markdown("### Registro de acessos")
        acessos = carregar_lista_csv(ACESSOS_FILE)
        if acessos:
            acessos_df = pd.DataFrame(acessos)
            st.dataframe(acessos_df, use_container_width=True)
            csv_acessos = acessos_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Baixar acessos CSV", csv_acessos, "acessos_clientes.csv", "text/csv")
        else:
            st.info("Ainda não há registros de acesso.")

        st.markdown("### Sessões ativas / anti-compartilhamento")
        st.caption("Sessão única ativa por padrão. Sessões sem atividade expiram em poucos minutos, e o cliente pode liberar sessão travada na tela de aviso. Ajuste REQUIRE_SINGLE_SESSION=false no Secrets apenas se quiser desligar.")
        try:
            sessoes = listar_sessoes_ativas_db()
        except Exception as e:
            sessoes = []
            st.error(f"Falha ao listar sessões ativas: {e}")
        if sessoes:
            st.dataframe(pd.DataFrame(sessoes), use_container_width=True)
            if st.button("🧹 Limpar sessões ativas", key="admin_limpar_sessoes"):
                limpar_sessoes_ativas_db()
                st.success("Sessões limpas. Clientes precisarão logar novamente.")
                st.rerun()
        else:
            st.info("Nenhuma sessão ativa registrada agora.")

# Rodapé legal nas áreas internas
try:
    render_rodape_legal()
except Exception:
    pass





