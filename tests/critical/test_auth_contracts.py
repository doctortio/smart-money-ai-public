from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class FakeAuthBackend:
    sessions: dict[str, dict] = field(default_factory=dict)
    admins: set[str] = field(default_factory=set)
    unavailable: bool = False

    def validate(self, token: str, usuario: str, now: datetime) -> dict:
        if self.unavailable:
            raise RuntimeError("BACKEND_UNAVAILABLE")
        row = self.sessions.get(token)
        if not row or row["usuario"] != usuario or row["expira_em"] <= now or row.get("status") != "ativo":
            return {"ok": False, "motivo": "invalido"}
        return {"ok": True, "sessao_id": row["sessao_id"]}

    def is_admin(self, email: str) -> bool:
        return email.strip().lower() in self.admins


def authorize(backend: FakeAuthBackend, *, token: str, usuario: str, now: datetime) -> bool:
    try:
        return bool(backend.validate(token, usuario, now).get("ok"))
    except Exception:
        return False


def test_sessao_valida_e_expirada():
    now = datetime.now(timezone.utc)
    backend = FakeAuthBackend(sessions={
        "valid": {"usuario": "u@example.com", "expira_em": now + timedelta(minutes=5), "status": "ativo", "sessao_id": "s1"},
        "expired": {"usuario": "u@example.com", "expira_em": now - timedelta(seconds=1), "status": "ativo", "sessao_id": "s2"},
    })
    assert authorize(backend, token="valid", usuario="u@example.com", now=now) is True
    assert authorize(backend, token="expired", usuario="u@example.com", now=now) is False


def test_token_invalido_backend_indisponivel_e_usuario_nao_autorizado_fail_closed():
    now = datetime.now(timezone.utc)
    backend = FakeAuthBackend(sessions={"valid": {"usuario": "u@example.com", "expira_em": now + timedelta(minutes=5), "status": "ativo", "sessao_id": "s1"}})
    assert authorize(backend, token="invalid", usuario="u@example.com", now=now) is False
    assert authorize(backend, token="valid", usuario="other@example.com", now=now) is False
    backend.unavailable = True
    assert authorize(backend, token="valid", usuario="u@example.com", now=now) is False


def test_admin_autorizado_e_nao_autorizado():
    backend = FakeAuthBackend(admins={"admin@example.com"})
    assert backend.is_admin("ADMIN@example.com") is True
    assert backend.is_admin("user@example.com") is False
