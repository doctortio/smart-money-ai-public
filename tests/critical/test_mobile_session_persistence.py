"""Exercise actual app auth functions with an isolated SQLite ledger; no live backend."""
import ast
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from browser_session import bounded_expiry, parse_browser_record


class State(dict):
    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


class Rerun(Exception):
    pass


@pytest.fixture
def auth(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("LIVE_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", no_network)
    source = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    names = {
        "_hash_token_acesso", "_versao_credencial", "_seguranca_local", "_limpar_autenticacao_local",
        "_agendar_sessao_browser", "_sincronizar_sessao_browser", "_limpeza_browser_pendente",
        "_data_valida", "criar_login_persistente", "restaurar_login_persistente", "remover_login_persistente",
    }
    functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(functions) == len(names)
    class FakeSt:
        session_state = State()
        secrets = {"REQUIRE_SINGLE_SESSION": True, "SESSION_TTL_MINUTES": 3}

        @staticmethod
        def rerun():
            raise Rerun()
    st = FakeSt()
    clients = {"teste@example.invalid": {
        "email": "teste@example.invalid", "senha": "sha256:" + "a" * 64,
        "ativo": True, "vencimento": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d"),
    }}
    store = {}
    calls = []
    def browser(command):
        if command["op"] == "write":
            store["record"] = dict(command["record"])
        elif command["op"] == "clear":
            store.clear()
        return {"request_id": command["request_id"], "status": "ok",
                "record": dict(store["record"]) if command["op"] == "read" and store else None}
    env = {
        "st": st, "datetime": datetime, "fuso_br": timezone.utc,
        "SEGURANCA_DB": tmp_path / "test-auth.sqlite", "sqlite3": sqlite3,
        "hashlib": hashlib, "hmac": hmac, "uuid": uuid, "py_secrets": secrets,
        "time": time, "logging": logging,
        "cliente_autorizado": lambda user: clients.get(user),
        "usuario_e_admin": lambda user: False,
        "fingerprint_dispositivo": lambda: "test-device",
        "_set_query_param": lambda name, value: None,
        "obter_event_id_fluxo": lambda name: str(uuid.uuid4()),
        "log_evento_operacional": lambda *a, **kw: None,
        "bounded_expiry": bounded_expiry, "parse_browser_record": parse_browser_record,
        "browser_session_command": browser,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), "app-auth-under-test", "exec"), env)
    def backend(action, data=None):
        calls.append(action)
        return env["_seguranca_local"](action, data or {})
    env["_seguranca_operar"] = backend
    env.update(store=store, clients=clients, calls=calls)
    return env


def issue_and_persist(auth):
    client = auth["clients"]["teste@example.invalid"]
    auth["criar_login_persistente"](client["email"], client["senha"], client)
    with pytest.raises(Rerun):
        auth["_sincronizar_sessao_browser"]()
    assert auth["_sincronizar_sessao_browser"]()
    return dict(auth["store"]["record"])


def restart(auth):
    auth["st"].session_state = State()
    assert auth["_sincronizar_sessao_browser"]()


def test_reopen_restores_existing_session_and_requires_backend_validation(auth):
    saved = issue_and_persist(auth)
    restart(auth)
    assert not auth["st"].session_state.authenticated
    assert auth["restaurar_login_persistente"]()
    assert auth["st"].session_state.authenticated
    assert auth["st"].session_state.session_token == saved["session_token"]
    assert auth["calls"].count("emitir_sessao") == 1
    assert "validar_sessao" in auth["calls"]
    assert not ({"senha", "password", "senha_hash", "service_role"} & saved.keys())


def test_logout_revokes_and_deletes_browser_state_before_reopen(auth):
    issue_and_persist(auth)
    auth["remover_login_persistente"]()
    assert auth["_limpeza_browser_pendente"]()
    assert auth["_sincronizar_sessao_browser"]()
    assert not auth["store"]
    assert "revogar_token" in auth["calls"]
    restart(auth)
    assert not auth["restaurar_login_persistente"]()


@pytest.mark.parametrize("failure", ["expired", "revoked", "credential_changed", "account_disabled", "session_id_tampered", "binding_tampered", "backend_unavailable"])
def test_invalid_restored_session_fails_closed_and_queues_browser_deletion(auth, failure):
    saved = issue_and_persist(auth)
    if failure in {"expired", "revoked"}:
        with sqlite3.connect(auth["SEGURANCA_DB"]) as connection:
            if failure == "expired":
                connection.execute("UPDATE auth_tokens SET expira_em=?", (time.time() - 1,))
            else:
                connection.execute("UPDATE auth_tokens SET status='revogado'")
    elif failure == "credential_changed":
        auth["clients"][saved["usuario"]]["senha"] = "sha256:" + "b" * 64
    elif failure == "account_disabled":
        auth["clients"][saved["usuario"]]["ativo"] = False
    elif failure == "session_id_tampered":
        auth["store"]["record"]["session_token"] = str(uuid.uuid4())
    elif failure == "binding_tampered":
        auth["store"]["record"]["auth_binding"] = secrets.token_urlsafe(32)
    else:
        def unavailable(*args, **kwargs):
            raise RuntimeError("UNAVAILABLE")
        auth["_seguranca_operar"] = unavailable
    restart(auth)
    assert not auth["restaurar_login_persistente"]()
    assert not auth["st"].session_state.authenticated
    assert auth["_limpeza_browser_pendente"]()
    assert auth["_sincronizar_sessao_browser"]()
    assert not auth["store"]


def test_logout_local_clear_survives_remote_revocation_failure(auth):
    issue_and_persist(auth)
    auth["_seguranca_operar"] = lambda *a, **kw: {"ok": False}
    with pytest.raises(RuntimeError):
        auth["remover_login_persistente"]()
    assert not auth["st"].session_state.authenticated
    assert auth["_sincronizar_sessao_browser"]()
    assert not auth["store"]


def test_storage_denied_does_not_authorize_a_user(auth):
    def denied(command):
        return {"request_id": command["request_id"], "status": "unavailable", "record": None}
    auth["browser_session_command"] = denied
    assert auth["_sincronizar_sessao_browser"]()
    assert auth["st"].session_state["_browser_storage_unavailable"]
    assert not auth["restaurar_login_persistente"]()


def test_late_browser_response_is_not_applied(auth):
    auth["browser_session_command"] = lambda _: {"request_id": "stale", "status": "ok", "record": {"authenticated": True}}
    assert not auth["_sincronizar_sessao_browser"]()
    assert not auth["st"].session_state.authenticated


def test_browser_expiry_is_bounded_and_invalid_records_are_rejected():
    now = time.time()
    assert bounded_expiry(now + 999999, now) == now + 43200
    assert bounded_expiry(None, now) == now + 43200
    assert bounded_expiry(float("nan"), now) == now
    assert parse_browser_record({"authenticated": True}, now) is None
    assert parse_browser_record({"password": "never-persist"}, now) is None



def test_f5_and_new_tab_restore_same_browser_session(auth):
    saved = issue_and_persist(auth)
    # A new Streamlit session has an empty session_state, while the browser
    # storage record remains shared by tabs on the same origin.
    restart(auth)
    assert auth["restaurar_login_persistente"]()
    assert auth["st"].session_state.usuario_logado == saved["usuario"]
    auth["st"].session_state = State()
    assert auth["_sincronizar_sessao_browser"]()
    assert auth["restaurar_login_persistente"]()


def test_valid_restore_renews_near_expiring_browser_record(auth):
    issue_and_persist(auth)
    auth["store"]["record"]["expires_at"] = time.time() + 30
    assert auth["_sincronizar_sessao_browser"]()
    assert auth["restaurar_login_persistente"]()
    assert auth["st"].session_state.authenticated
    assert auth["st"].session_state.get("_browser_session_expires_at", 0) > time.time()
