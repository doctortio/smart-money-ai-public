from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Callable
import pandas as pd

from .engine_v3 import SignalEngineV3, V3Config
from .evaluation import wilson_ci

V2_ENGINE_VERSION = "V2_EXPERIMENTAL_003"
V2_CONFIG_HASH = "792460b7"
SUPPORTED_MARKETS = {"Cripto"}
SUPPORTED_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
SUPPORTED_TIMEFRAMES = {"1m", "5m"}
SHADOW_TABLE = "sma_signal_shadow"


def assert_v2_config_hash() -> str:
    got = V3Config().hash8()
    if got != V2_CONFIG_HASH:
        raise RuntimeError(f"V2_CONFIG_HASH_CHANGED:{got}")
    return got


def shadow_enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "sim"}


def _num(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _market_ts(candles: pd.DataFrame) -> str | None:
    for col in ("Close time", "Open time"):
        if col in candles.columns and len(candles):
            try:
                return pd.Timestamp(candles[col].iloc[-1]).isoformat()
            except Exception:
                return None
    return None


def _base_record(*, official_signal_id: str, user_id: str | None, market: str, symbol: str, timeframe: str, decision_timestamp: str, market_data_timestamp: str | None, v1_decision: dict, v1_latency_ms: float | None) -> dict:
    return {
        "official_signal_id": str(official_signal_id),
        "user_id": str(user_id or "") or None,
        "decision_timestamp": decision_timestamp,
        "market_data_timestamp": market_data_timestamp,
        "market": str(market or ""),
        "symbol": str(symbol or ""),
        "timeframe": str(timeframe or ""),
        "v1_engine_version": "V1_OFFICIAL",
        "v1_direction": str(v1_decision.get("direcao") or v1_decision.get("direction") or ""),
        "v1_score": _num(v1_decision.get("confianca") or v1_decision.get("score")),
        "v1_risk": str(v1_decision.get("risco") or ""),
        "v2_engine_version": V2_ENGINE_VERSION,
        "v2_config_hash": V2_CONFIG_HASH,
        "v1_latency_ms": _num(v1_latency_ms),
        "result_direct": "PENDENTE",
        "result_p1": "PENDENTE",
        "result_p2": "PENDENTE",
        "result_p5": "PENDENTE",
        "evaluated_at": None,
        "metadata": {},
    }


def build_shadow_record(*, official_signal_id: str, user_id: str | None, market: str, symbol: str, timeframe: str, v1_decision: dict, candles: pd.DataFrame, v1_latency_ms: float | None = None, engine_factory: Callable[[], SignalEngineV3] | None = None) -> dict:
    assert_v2_config_hash()
    now = datetime.now(timezone.utc).isoformat()
    base = _base_record(official_signal_id=official_signal_id, user_id=user_id, market=market, symbol=symbol, timeframe=timeframe, decision_timestamp=now, market_data_timestamp=_market_ts(candles), v1_decision=v1_decision, v1_latency_ms=v1_latency_ms)
    if market not in SUPPORTED_MARKETS or symbol not in SUPPORTED_SYMBOLS or timeframe not in SUPPORTED_TIMEFRAMES:
        return {**base, "shadow_status": "shadow_unsupported", "shadow_error_code": "V2_SHADOW_UNSUPPORTED_MARKET", "v2_no_trade": True, "v2_no_trade_reason": "V2_SHADOW_UNSUPPORTED_MARKET"}
    try:
        start = time.perf_counter()
        engine = engine_factory() if engine_factory else SignalEngineV3(V3Config())
        decision = engine.evaluate(candles, market=market, symbol=symbol, timeframe=timeframe)
        latency = (time.perf_counter() - start) * 1000
        no_trade = decision.direction.value == "AGUARDAR"
        return {**base,
            "v2_direction": decision.direction.value,
            "v2_long_score": round(float(decision.long_score), 6),
            "v2_short_score": round(float(decision.short_score), 6),
            "v2_risk": decision.risk,
            "v2_no_trade": no_trade,
            "v2_no_trade_reason": decision.no_signal_reason or None,
            "v2_regime": decision.regime.value,
            "v2_latency_ms": round(latency, 6),
            "shadow_status": "shadow_no_trade" if no_trade else "shadow_success",
            "shadow_error_code": None,
            "metadata": {"confidence_layer": "NOT_VALIDATED"},
        }
    except Exception as exc:
        return {**base, "shadow_status": "shadow_error", "shadow_error_code": type(exc).__name__, "v2_no_trade": True, "v2_no_trade_reason": "SHADOW_EXCEPTION"}


def persist_shadow_record(record: dict, insert_func: Callable[..., list[dict]]) -> dict:
    return {
        "ok": True,
        "rows": insert_func(
            SHADOW_TABLE,
            record,
            params={"on_conflict": "official_signal_id,v2_engine_version,v2_config_hash"},
            prefer="resolution=ignore-duplicates,return=representation",
        ),
    }


def run_live_shadow(*, enabled: bool, insert_func: Callable[..., list[dict]], **kwargs) -> dict:
    if not enabled:
        return {"ok": True, "status": "shadow_disabled"}
    try:
        record = build_shadow_record(**kwargs)
        saved = persist_shadow_record(record, insert_func)
        return {"ok": True, "status": record.get("shadow_status"), "record": record, "saved": saved}
    except Exception as exc:
        return {"ok": False, "status": "shadow_error", "error_code": type(exc).__name__}


def summarize_shadow_records(records: list[dict]) -> dict:
    total = len(records)
    accepted = [r for r in records if not r.get("v2_no_trade") and r.get("result_direct") in {"WIN", "LOSS", "DOJI"}]
    wins = sum(1 for r in accepted if r.get("result_direct") == "WIN")
    low, high = wilson_ci(wins, len(accepted))
    errors = sum(1 for r in records if r.get("shadow_status") == "shadow_error")
    no_trade = sum(1 for r in records if r.get("v2_no_trade"))
    return {
        "TOTAL_PAIRS": total,
        "V2_SIGNALS": len(accepted),
        "V2_NO_TRADE": no_trade,
        "V2_WIN_RATE": wins / len(accepted) if accepted else 0.0,
        "V2_IC95": [low, high],
        "SHADOW_ERRORS": errors,
        "AVG_V2_LATENCY_MS": sum(float(r.get("v2_latency_ms") or 0) for r in records) / total if total else 0.0,
    }
