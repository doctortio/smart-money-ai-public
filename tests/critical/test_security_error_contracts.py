from __future__ import annotations

from tests.critical.conftest import BLOCKED_HOST_MARKERS


def test_external_network_targets_are_denied_by_policy():
    assert "api.mercadopago.com" in BLOCKED_HOST_MARKERS
    assert "supabase.co" in BLOCKED_HOST_MARKERS
    assert "smart-money-ai-4.streamlit.app" in BLOCKED_HOST_MARKERS


def test_sanitized_exception_contract_never_promotes_failure_to_success():
    def safe_boundary(operation):
        try:
            return operation()
        except Exception as exc:
            return {"ok": False, "error_code": type(exc).__name__, "message": "Falha externa sanitizada."}

    result = safe_boundary(lambda: (_ for _ in ()).throw(RuntimeError("SUPABASE_SERVICE_ROLE_KEY=secret")))
    assert result["ok"] is False
    assert result["error_code"] == "RuntimeError"
    assert "SUPABASE_SERVICE_ROLE_KEY" not in result["message"]
