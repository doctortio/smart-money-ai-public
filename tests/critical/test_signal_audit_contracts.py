from __future__ import annotations

from datetime import datetime, timedelta, timezone

from signal_audit import calculate_audit_counts, candle_direction_result, evaluate_sequential_audit, normalize_audit_result, p3_from_entry


def rec(direction="COMPRA"):
    return {"direcao": direction, "resultado": "PENDENTE"}


def candle(open_, high, low, close, minutes=0):
    return {"Open": open_, "High": high, "Low": low, "Close": close, "Open time": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)}


def ev(record, p1=None, p2=None, p3=None, closed=None):
    return evaluate_sequential_audit(record, {"P1": p1, "P2": p2, "P3": p3}, ({"P1", "P2", "P3"} if closed is None else set(closed)))


def test_compra_p1_p2_p3_e_loss_direcional():
    assert ev(rec(), candle(100, 100, 90, 101), candle(100, 200, 1, 90), candle(100, 200, 1, 90))["resultado"] == "P1"
    assert ev(rec(), candle(100, 200, 1, 99), candle(100, 100, 90, 101), candle(100, 200, 1, 90))["resultado"] == "P2"
    assert ev(rec(), candle(100, 200, 1, 99), candle(100, 200, 1, 99), candle(100, 100, 90, 101))["resultado"] == "P3"
    assert ev(rec(), candle(100, 200, 1, 99), candle(100, 200, 1, 99), candle(100, 200, 1, 99))["resultado"] == "LOSS"


def test_venda_p1_p2_p3_e_loss_direcional():
    assert ev(rec("VENDA"), candle(100, 200, 1, 99), candle(100, 200, 1, 101), candle(100, 200, 1, 101))["resultado"] == "P1"
    assert ev(rec("VENDA"), candle(100, 200, 1, 101), candle(100, 200, 1, 99), candle(100, 200, 1, 101))["resultado"] == "P2"
    assert ev(rec("VENDA"), candle(100, 200, 1, 101), candle(100, 200, 1, 101), candle(100, 200, 1, 99))["resultado"] == "P3"
    assert ev(rec("VENDA"), candle(100, 200, 1, 101), candle(100, 200, 1, 101), candle(100, 200, 1, 101))["resultado"] == "LOSS"


def test_pendente_por_horizonte_nao_fechado():
    assert ev(rec(), None, None, None, set())["resultado"] == "PENDENTE"
    assert ev(rec(), candle(100, 110, 90, 99), None, None, {"P1"})["resultado"] == "PENDENTE"
    assert ev(rec(), candle(100, 110, 90, 99), candle(100, 110, 90, 99), None, {"P1", "P2"})["resultado"] == "PENDENTE"


def test_doji_continua_ate_p3_e_loss_se_sem_acerto():
    assert candle_direction_result(candle(100, 200, 1, 100), "COMPRA") == "DOJI"
    assert ev(rec(), candle(100, 200, 1, 100), candle(100, 200, 1, 99), candle(100, 200, 1, 101))["resultado"] == "P3"
    assert ev(rec(), candle(100, 200, 1, 100), candle(100, 200, 1, 100), candle(100, 200, 1, 100))["resultado"] == "LOSS"


def test_primeiro_acerto_encerra_e_high_low_nao_determina_resultado():
    first = ev(rec(), candle(100, 10_000, 1, 101), candle(100, 10_000, 1, 99), candle(100, 10_000, 1, 99))
    assert first["resultado"] == "P1"
    assert "resultado_p2" not in first
    assert ev(rec(), candle(100, 10_000, 1, 99), candle(100, 10_000, 1, 99), candle(100, 10_000, 1, 99))["resultado"] == "LOSS"


def test_sem_dados_somente_quando_candle_necessario_ausente_ou_invalido():
    assert ev(rec(), None, None, None)["resultado"] == "SEM DADOS"
    assert ev({"direcao": "INDEFINIDA"}, candle(100, 110, 90, 101), None, None, {"P1"})["resultado"] == "SEM DADOS"
    assert ev(rec(), candle(100, 110, 90, 101))["resultado"] == "P1"


def test_target_stop_ausentes_sao_compativeis_com_payload_real():
    assert ev({"direcao": "COMPRA"}, candle(10, 999, 1, 11))["resultado"] == "P1"


def test_p3_horizon_timeframes_1m_e_5m():
    entrada = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    assert p3_from_entry(entrada, "1m") == entrada + timedelta(minutes=3)
    assert p3_from_entry(entrada, "5m") == entrada + timedelta(minutes=15)


def test_metricas_exclusivas_p5_e_win_direto_historicos_fora_da_nova_logica():
    records = [
        {"resultado": "P1"}, {"resultado": "P2"}, {"resultado": "P3"}, {"resultado": "LOSS"},
        {"resultado": "PENDENTE"}, {"resultado": "SEM DADOS"}, {"resultado": "WIN P1"}, {"resultado": "WIN P2"},
        {"resultado": "WIN P5"}, {"resultado": "WIN DIRETO"},
    ]
    counts = calculate_audit_counts(records)
    assert counts == {"p1": 2, "p2": 2, "p3": 1, "loss": 1, "pendentes": 1, "sem_dados": 1, "historico_outros": 2, "total_considerado": 10}
    assert sum(v for k, v in counts.items() if k != "total_considerado") == counts["total_considerado"]
    assert normalize_audit_result("WIN P5") == "HISTORICO_OUTRO"
    assert normalize_audit_result("WIN DIRETO") == "HISTORICO_OUTRO"

