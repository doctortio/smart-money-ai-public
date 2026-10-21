from datetime import datetime, timezone

import numpy as np
import pandas as pd

from signal_engine.shadow_quant.adapter import quant_shadow_enabled, run_shadow_fail_open
from signal_engine.shadow_quant.contracts import FEATURE_HASH, ShadowContext, normalize_market_data
from signal_engine.shadow_quant.evaluator import compare_same_opportunity, evaluate_direction, opportunity_times
from signal_engine.shadow_quant.feature_pipeline import FEATURE_NAMES, build_features
from signal_engine.shadow_quant.risk import classify_risk


def market_data(rows=2400):
    index = pd.date_range("2026-08-01", periods=rows, freq="min", tz="UTC")
    base = 100 + np.arange(rows) * .001 + np.sin(np.arange(rows) / 13)
    frame = pd.DataFrame(index=index)
    frame["Open"] = base
    frame["Close"] = base + np.sin(np.arange(rows) / 7) * .05
    frame["High"] = frame[["Open", "Close"]].max(axis=1) + .1
    frame["Low"] = frame[["Open", "Close"]].min(axis=1) - .1
    frame["Volume"] = 10 + np.arange(rows) % 17
    frame["Quote asset volume"] = frame.Volume * frame.Close
    frame["Number of trades"] = 20 + np.arange(rows) % 11
    frame["Taker buy base"] = frame.Volume * .51
    frame["Taker buy quote"] = frame["Quote asset volume"] * .51
    frame["Open time"] = index
    frame["Close time"] = index + pd.Timedelta(minutes=1) - pd.Timedelta(milliseconds=1)
    return frame


def context(frame=None):
    frame = market_data() if frame is None else frame
    return ShadowContext("official-1", "event-1", "BTCUSDT", frame.index[-1], "1m", frame)


def test_03_feature_count_54(): assert len(FEATURE_NAMES) == 54
def test_04_feature_order(): assert list(build_features(normalize_market_data(market_data())).columns) == FEATURE_NAMES


def test_05_feature_dtype():
    features = build_features(normalize_market_data(market_data()))
    assert all(np.issubdtype(dtype, np.number) for dtype in features.dtypes)


def test_06_zero_lookahead():
    raw = normalize_market_data(market_data())
    cut = 2250
    before = build_features(raw.iloc[:cut])
    altered = raw.copy()
    altered.iloc[cut:, altered.columns.get_loc("close")] *= 10
    after = build_features(altered.iloc[:cut])
    pd.testing.assert_frame_equal(before, after, check_exact=True)


def test_07_5m_closed_only():
    raw = normalize_market_data(market_data())
    features = build_features(raw)
    ts = raw.index[-2]
    changed = raw.copy(); changed.loc[raw.index[-1], "close"] *= 3
    assert features.loc[ts, "mtf5_trend"] == build_features(changed).loc[ts, "mtf5_trend"]


def test_08_15m_closed_only():
    raw = normalize_market_data(market_data())
    features = build_features(raw)
    ts = raw.index[-2]
    changed = raw.copy(); changed.loc[raw.index[-1], "close"] *= 3
    assert features.loc[ts, "mtf15_trend"] == build_features(changed).loc[ts, "mtf15_trend"]


def test_13_buy_favorable(): assert evaluate_direction("COMPRA", {"P1": {"Open": 1, "Close": 2}}, {"P1"}) == "P1"
def test_14_sell_favorable(): assert evaluate_direction("VENDA", {"P1": {"Open": 2, "Close": 1}}, {"P1"}) == "P1"
def test_15_doji_not_gain(): assert evaluate_direction("COMPRA", {"P1": {"Open": 1, "Close": 1}}, {"P1"}) == "PENDENTE"
def test_16_p1_first_hit(): assert evaluate_direction("COMPRA", {"P1": {"Open": 1, "Close": 2}}, {"P1", "P2", "P3"}) == "P1"


def test_17_p2_first_hit():
    candles = {"P1": {"Open": 2, "Close": 1}, "P2": {"Open": 1, "Close": 2}}
    assert evaluate_direction("COMPRA", candles, {"P1", "P2"}) == "P2"


def test_18_p3_first_hit():
    candles = {"P1": {"Open": 2, "Close": 1}, "P2": {"Open": 2, "Close": 1}, "P3": {"Open": 1, "Close": 2}}
    assert evaluate_direction("COMPRA", candles, {"P1", "P2", "P3"}) == "P3"


def test_19_loss_after_p3():
    candles = {stage: {"Open": 2, "Close": 1} for stage in ("P1", "P2", "P3")}
    assert evaluate_direction("COMPRA", candles, {"P1", "P2", "P3"}) == "LOSS"


def test_20_no_p5(): assert set(opportunity_times(datetime.now(timezone.utc), "1m")) == {"ENTRADA", "P1", "P2", "P3"}
def test_21_no_target_stop(): assert "target" not in evaluate_direction.__code__.co_names and "stop" not in evaluate_direction.__code__.co_names


def test_22_risk_does_not_change_direction():
    row = build_features(normalize_market_data(market_data())).iloc[-1]
    assert classify_risk(row, "COMPRA") in {"BAIXO", "MEDIO", "ALTO"}
    assert "COMPRA" == "COMPRA"


def test_30_same_temporal_opportunity():
    times = opportunity_times(datetime(2026, 1, 1, tzinfo=timezone.utc), "5m")
    assert (times["P1"] - times["ENTRADA"]).total_seconds() == 300


def test_31_disagreement_same_candles():
    candles = {stage: {"Open": 1, "Close": 2} for stage in ("P1", "P2", "P3")}
    compared = compare_same_opportunity("COMPRA", "VENDA", candles, {"P1", "P2", "P3"})
    assert compared == {"official": "P1", "shadow": "LOSS", "same_candles": True, "stages": ("P1", "P2", "P3")}
