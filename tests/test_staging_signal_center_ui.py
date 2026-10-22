"""Real Streamlit AppTest rendering; no Supabase secrets and no Telegram sends."""
from pathlib import Path
from streamlit.testing.v1 import AppTest

def test_staging_panel_renders_fail_closed():
    app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "staging_signal_center.py"), default_timeout=15).run()
    assert not app.exception, [str(x.message) for x in app.exception]
    assert any("HOMOLOGAÇÃO VISUAL" in str(x.message) for x in app.warning)
    assert any("indisponível" in str(x.message).lower() for x in app.info)
    assert len(app.button) == 0, "unauthenticated staging must expose no publication or confirmation buttons"

def test_staging_entrypoint_never_imports_live_app_or_secrets():
    source = Path("staging_signal_center.py").read_text(encoding="utf-8")
    assert "import app" not in source
    assert "st.secrets" not in source
    assert "requests." not in source
    assert "sendMessage" not in source
