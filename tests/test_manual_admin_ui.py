"""Streamlit UI interaction tests without Supabase credentials or Telegram network."""
from pathlib import Path
from streamlit.testing.v1 import AppTest

def test_fail_closed_staging_has_no_buttons():
    app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "staging_signal_center.py")).run()
    assert not app.exception
    assert not app.button

def test_manual_ui_mocked_admin_preview_confirm_cancel(tmp_path):
    script = tmp_path / "ui_mock.py"
    script.write_text("""
import streamlit as st
from telegram_worker.manual_admin_ui import render_manual_admin_panel
from telegram_worker.worker import Signal
def rpc(name, args):
    st.session_state['last_rpc'] = name
    return args['p_preview_id'] if name.endswith('create_preview') else args['p_request_id']
def factory(asset, timeframe):
    return Signal(asset, 'COMPRA', timeframe, 'fixture', 'BAIXO', 'fixture-time')
session = {'authenticated': True, 'is_admin': True, 'admin_id': 1, 'email': 'admin@test.invalid'}
render_manual_admin_panel(st, verified_session=session, rpc=rpc, signal_factory=factory)
""", encoding="utf-8")
    app = AppTest.from_file(str(script), default_timeout=15).run()
    assert not app.exception
    assert any("Gerar prévia manual" in b.label for b in app.button)
    app.button(key="signal_manual_preview").click().run()
    assert not app.exception
    assert app.session_state["last_rpc"] == "sma_signal_manual_create_preview"
    app.run()  # The confirmation controls render on the next Streamlit pass.
    assert any("Confirmar e registrar solicitação" in b.label for b in app.button)
    app.button(key="signal_manual_cancel").click().run()
    assert not app.exception
    assert "signal_manual_pending_confirmation" not in app.session_state
    app.run()  # Cancel takes effect on the following Streamlit render.
    assert any(b.key == "signal_manual_preview" for b in app.button)
    app.button(key="signal_manual_preview").click().run()
    app.run()
    app.button(key="signal_manual_confirm").click().run()
    assert not app.exception
    assert app.session_state["last_rpc"] == "sma_signal_manual_confirm_preview"
    assert "signal_manual_pending_confirmation" not in app.session_state
