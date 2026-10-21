from datetime import datetime, timezone
from pathlib import Path

import pytest

from signal_engine.shadow_quant.evaluator import evaluate_direction
from signal_engine.shadow_quant.lifecycle import evaluate_pending_record


ALL = {"P1", "P2", "P3"}


def candle(open_, close):
    return {"Open": open_, "Close": close, "High": 999999, "Low": -999999}


@pytest.mark.parametrize("direction,candles,closed,expected", [
    ("COMPRA", {"P1": candle(10, 11)}, {"P1"}, "P1"),
    ("COMPRA", {"P1": candle(10, 9), "P2": candle(10, 11)}, {"P1", "P2"}, "P2"),
    ("COMPRA", {"P1": candle(10, 9), "P2": candle(10, 9), "P3": candle(10, 11)}, ALL, "P3"),
    ("COMPRA", {stage: candle(10, 9) for stage in ALL}, ALL, "LOSS"),
    ("COMPRA", {stage: candle(10, 10) for stage in ALL}, ALL, "LOSS"),
    ("COMPRA", {"P1": candle(10, 9)}, {"P1"}, "PENDENTE"),
    ("COMPRA", {"P1": None}, {"P1"}, "SEM DADOS"),
    ("VENDA", {"P1": candle(10, 9)}, {"P1"}, "P1"),
    ("VENDA", {"P1": candle(10, 11), "P2": candle(10, 9)}, {"P1", "P2"}, "P2"),
    ("VENDA", {"P1": candle(10, 11), "P2": candle(10, 11), "P3": candle(10, 9)}, ALL, "P3"),
    ("VENDA", {stage: candle(10, 11) for stage in ALL}, ALL, "LOSS"),
    ("VENDA", {stage: candle(10, 10) for stage in ALL}, ALL, "LOSS"),
    ("VENDA", {"P1": candle(10, 11)}, {"P1"}, "PENDENTE"),
    ("VENDA", {"P1": None}, {"P1"}, "SEM DADOS"),
])
def test_close_open_temporal_contract(direction, candles, closed, expected):
    assert evaluate_direction(direction, candles, closed) == expected


def test_opposite_directions_use_same_candles_independently():
    record = {"v1_direction": "VENDA", "v2_direction": "COMPRA",
              "resultado_official": "PENDENTE", "resultado_shadow": "PENDENTE"}
    candles = {"P1": candle(10, 11), "P2": candle(10, 12), "P3": candle(10, 13)}
    patch = evaluate_pending_record(record, candles, ALL, evaluated_at=datetime(2026, 10, 3, tzinfo=timezone.utc))
    assert patch["resultado_official"] == "LOSS"
    assert patch["resultado_shadow"] == "P1"


def test_finalized_row_is_immutable_and_idempotent():
    record = {"v1_direction": "VENDA", "v2_direction": "COMPRA",
              "resultado_official": "LOSS", "resultado_shadow": "P1", "evaluated_at": "existing"}
    assert evaluate_pending_record(record, {}, set()) == {}
    assert evaluate_pending_record(record, {}, set()) == {}


def test_invalid_direction_is_real_no_data():
    assert evaluate_direction("", {stage: candle(1, 2) for stage in ALL}, ALL) == "SEM DADOS"


def test_lifecycle_uses_no_target_stop_high_low_or_p5():
    root = Path(__file__).resolve().parents[2]
    source = (root / "signal_engine/shadow_quant/lifecycle.py").read_text(encoding="utf-8")
    evaluator = (root / "signal_engine/shadow_quant/evaluator.py").read_text(encoding="utf-8")
    for forbidden in ('["High"]', '["Low"]', '["target"]', '["stop"]', '"P5"'):
        assert forbidden not in source and forbidden not in evaluator


def test_production_has_automatic_timer_and_pending_recovery():
    source = (Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert 'threading.Thread(target=worker' in source
    assert '@st.fragment(run_every="60s")' in source
    assert '"PATCH", f"{supabase_base_url}/sma_signal_shadow"' in source
    assert '"evaluated_at": "is.null"' in source
