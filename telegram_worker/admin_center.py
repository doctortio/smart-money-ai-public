"""Administrative signal-center controls; fail closed until backend RPCs are deployed."""
from __future__ import annotations

CRYPTO_ASSETS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT")
FOREX_ASSETS = ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/JPY", "GBP/JPY", "EUR/GBP")
AUTO_SLOTS = ("09:00", "14:00", "20:00")

def validate_two_assets(assets: list[str], catalog: tuple[str, ...] = CRYPTO_ASSETS) -> tuple[str, str]:
    chosen = [str(x).strip().upper() for x in assets]
    if len(chosen) != 2 or len(set(chosen)) != 2 or any(x not in catalog for x in chosen):
        raise ValueError("exactly_two_supported_assets_required")
    return chosen[0], chosen[1]

def build_signal_center_config(auto_assets, manual_assets, timeframe: str, auto_enabled: bool, emergency_stop: bool) -> dict:
    return {
        "auto_assets": list(validate_two_assets(auto_assets)),
        "manual_assets": list(validate_two_assets(manual_assets)),
        "timeframe": str(timeframe), "auto_enabled": bool(auto_enabled and not emergency_stop),
        "emergency_stop": bool(emergency_stop), "slots": list(AUTO_SLOTS), "timezone": "America/Sao_Paulo",
    }

def render_signal_center(st):
    st.markdown("## 🎛️ Central de Sinais")
    st.caption("Controles administrativos preparados para homologação. Publicação permanece desativada por padrão.")
    auto_enabled = st.toggle("Ativar publicação automática", value=False, key="signal_center_auto_enabled")
    emergency = st.checkbox("🛑 Parada de emergência", value=True, key="signal_center_emergency_stop")
    a1, a2 = st.columns(2)
    with a1: auto = st.multiselect("Dois ativos do automático", CRYPTO_ASSETS, default=["BTCUSDT", "ETHUSDT"], max_selections=2, key="signal_center_auto_assets")
    with a2: manual = st.multiselect("Dois ativos do envio manual", CRYPTO_ASSETS, default=["BTCUSDT", "ETHUSDT"], max_selections=2, key="signal_center_manual_assets")
    timeframe = st.selectbox("Timeframe homologado", ("5m",), key="signal_center_timeframe")
    st.write("Horários: 09:00, 14:00 e 20:00 — America/Sao_Paulo")
    try: cfg = build_signal_center_config(auto, manual, timeframe, auto_enabled, emergency); st.json(cfg)
    except ValueError as exc: st.warning(str(exc)); return None
    st.info("Prévia manual e publicação ficam bloqueadas até a migration/RPC administrativa e homologação do worker.")
    return cfg
