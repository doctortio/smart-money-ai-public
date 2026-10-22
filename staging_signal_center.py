"""Isolated Streamlit visual staging of the signal center.

This entrypoint deliberately never imports the production app, accesses secrets,
connects to Supabase, or sends to Telegram. UI verification only.
"""
import streamlit as st
from telegram_worker.manual_admin_ui import render_manual_admin_panel

st.set_page_config(page_title="Smart Money AI | Central de Sinais (Homologação)")
st.title("Smart Money AI — Central de Sinais")
st.warning("HOMOLOGAÇÃO VISUAL: dados simulados. Nenhum sinal real ou envio ao Telegram.")
st.caption("Automação desativada | Parada de emergência ativada")
render_manual_admin_panel(st, verified_session=None, rpc=None, signal_factory=None)
