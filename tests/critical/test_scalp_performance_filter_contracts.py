from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")


def test_scalp_performance_filters_are_declared_with_positive_and_negative_cuts():
    assert "SCALP_PERFORMANCE_FILTERS" in SOURCE
    assert '("FOREX", "EURUSD=X", "1M", "COMPRA")' in SOURCE
    assert '"win_rate": 0.0' in SOURCE
    assert '("CRIPTO", "ETHUSDT", "1M", "VENDA")' in SOURCE
    assert '"win_rate": 0.9655' in SOURCE


def test_scalp_performance_filter_is_applied_after_statistical_calibration():
    calibration = SOURCE.index("aplicar_calibracao_estatistica(\n        mercado, timeframe, direcao_local")
    performance_filter = SOURCE.index("aplicar_filtro_performance_scalp(\n        mercado, symbol, timeframe, direcao_local")
    decision = SOURCE.index("decisao = {", performance_filter)
    assert calibration < performance_filter < decision


def test_scalp_performance_filter_is_soft_and_recalculates_risk():
    start = SOURCE.index("def aplicar_filtro_performance_scalp")
    end = SOURCE.index("def montar_resposta_bloqueada", start)
    body = SOURCE[start:end]
    assert "recalcular_risco_por_confianca" in body
    assert "ajuste = -18" in body
    assert "ajuste = 4" in body
    assert "return confianca, risco, motivo" in body
