"""Optional Streamlit manual preview panel, disabled without trusted backend wiring.

The host must validate the session and inject service-role RPC from the server.
No Telegram send or queue consumer exists in this component.
"""
from __future__ import annotations
from .manual_backend import create_manual_preview, confirm_manual_preview

def render_manual_admin_panel(st, *, verified_session=None, rpc=None, signal_factory=None):
    st.markdown("### Prévia manual de sinais")
    if not verified_session or rpc is None or signal_factory is None:
        st.info("Envio manual indisponível: sessão administrativa e backend homologado obrigatórios.")
        return None
    if not verified_session.get("authenticated") or not verified_session.get("is_admin"):
        st.error("Acesso administrativo não confirmado.")
        return None
    if st.session_state.get("signal_manual_pending_confirmation"):
        p = st.session_state["signal_manual_pending_confirmation"]
        st.warning("Confirme apenas após conferir os dois sinais. Nenhum envio ao Telegram será realizado.")
        st.json(p["signals"])
        if st.button("Confirmar e registrar solicitação", key="signal_manual_confirm"):
            try:
                result = confirm_manual_preview(verified_session, rpc, p["preview_id"])
            except Exception:
                st.error("Confirmação recusada pelo backend. Nenhuma publicação realizada.")
                return None
            st.session_state.pop("signal_manual_pending_confirmation", None)
            st.success("Solicitação registrada na fila de homologação. Não publicada.")
            return result
        if st.button("Cancelar prévia", key="signal_manual_cancel"):
            st.session_state.pop("signal_manual_pending_confirmation", None)
        return None
    assets = st.multiselect("Ativos manuais", ("BTCUSDT", "ETHUSDT"), default=["BTCUSDT", "ETHUSDT"], max_selections=2, key="signal_manual_preview_assets")
    if st.button("Gerar prévia manual", key="signal_manual_preview"):
        try:
            preview = create_manual_preview(verified_session, rpc, signal_factory, assets)
        except Exception:
            st.error("Não foi possível gerar a prévia. Verifique permissões e parada de emergência.")
            return None
        st.session_state["signal_manual_pending_confirmation"] = preview
        st.json(preview["signals"])
        st.info("Prévia salva; confirme na próxima interação. Nenhum envio realizado.")
    return None
