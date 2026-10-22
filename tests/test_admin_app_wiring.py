"""Static integration guardrails: the admin page cannot activate automatic publishing."""
from pathlib import Path

APP = Path("app.py").read_text(encoding="utf-8")

def test_verified_identity_bridge_wired():
    assert "verified_admin = resolve_admin_session(" in APP
    assert "restore_login=restaurar_login_persistente" in APP
    assert "fetch_client=_admin_clients" in APP
    assert '"select": "id,email,tipo_usuario,plano"' in APP

def test_manual_panel_uses_verified_identity():
    assert "render_manual_admin_panel(" in APP
    assert "verified_session=verified_admin" in APP
    assert "signal_factory=_signal_factory" in APP

def test_admin_rpc_allowlist_excludes_auto_set_and_publication():
    start = APP.index("def _signal_rpc(name, payload):")
    end = APP.index("current_config =", start)
    block = APP[start:end]
    assert '"sma_signal_manual_confirm_preview"' in block
    assert '"sma_signal_center_set"' not in block
    assert '"reserve"' not in block and '"mark"' not in block

def test_admin_requires_server_service_role():
    start = APP.index("if aba_admin is not None:")
    end = APP.index('with st.expander("🧹 Manutenção global da auditoria"', start)
    assert "if not _supabase_service_role_key():" in APP[start:end]
