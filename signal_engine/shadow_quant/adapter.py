from __future__ import annotations

import os
from copy import deepcopy
from typing import Callable

from .contracts import ShadowContext, fail_result
from .inference import run_quant_shadow

R03_QUANT_SHADOW_ENABLED_DEFAULT = False


def quant_shadow_enabled(value=None) -> bool:
    try:
        if value is None:
            value = os.getenv("R03_QUANT_SHADOW_ENABLED", "false")
        return str(value).strip().lower() in {"1", "true", "yes", "on", "sim"}
    except Exception:
        return False


def run_shadow_fail_open(official_signal: dict, context: ShadowContext, *, enabled=None, runner: Callable = run_quant_shadow) -> tuple[dict, dict]:
    """Return an untouched official signal and a separate Shadow observation."""
    official_copy = deepcopy(official_signal)
    if not quant_shadow_enabled(enabled):
        return official_copy, {"status": "shadow_disabled", "error_code": None}
    try:
        shadow = runner(context)
    except Exception as exc:
        shadow = fail_result(exc)
    return official_copy, shadow


def run_shadow_observer(official_signal: dict, context: ShadowContext, *, enabled=None, runner: Callable = run_quant_shadow, persist: Callable | None = None) -> tuple[dict, dict]:
    """Prepared production hook: all Shadow and persistence failures remain isolated."""
    official_copy, shadow = run_shadow_fail_open(official_signal, context, enabled=enabled, runner=runner)
    if shadow.get("status") in {"shadow_disabled", "shadow_error"} or persist is None:
        return official_copy, {**shadow, "persistence_status": "not_attempted"}
    try:
        persist(persistence_mapping(official_copy, shadow, context))
        return official_copy, {**shadow, "persistence_status": "persisted"}
    except Exception as exc:
        return official_copy, {**shadow, "persistence_status": "persistence_error", "persistence_error_code": type(exc).__name__}


def persistence_mapping(official_signal: dict, shadow: dict, context: ShadowContext) -> dict:
    """Pure mapping for future reuse of sma_signal_shadow; performs no write."""
    return {
        "official_signal_id": context.official_signal_id,
        "event_id": context.event_id or None, "request_id": context.request_id or None,
        "decision_timestamp": str(context.timestamp), "market": "Cripto", "symbol": context.symbol, "timeframe": context.timeframe,
        "v1_engine_version": "V1_OFFICIAL", "v1_direction": official_signal.get("direcao"),
        "v1_score": official_signal.get("confianca"), "v1_risk": official_signal.get("risco"),
        "v2_engine_version": shadow.get("model_version"), "v2_config_hash": json_config_hash(shadow.get("config_hash")),
        "v2_direction": shadow.get("shadow_direction"), "v2_long_score": shadow.get("buy_primary_probability"),
        "v2_short_score": shadow.get("sell_research_probability"), "v2_risk": shadow.get("risk_class_shadow"),
        "buy_primary_direction": shadow.get("buy_primary_direction"), "buy_primary_probability": shadow.get("buy_primary_probability"),
        "buy_challenger_direction": shadow.get("buy_challenger_direction"), "buy_challenger_probability": shadow.get("buy_challenger_probability"),
        "sell_research_direction": shadow.get("sell_research_direction"), "sell_research_probability": shadow.get("sell_research_probability"),
        "confidence_raw_shadow": max(filter(lambda value: value is not None, [shadow.get("buy_primary_probability"), shadow.get("sell_research_probability")]), default=None),
        "confidence_calibrated_shadow": shadow.get("calibrated_probability"), "feature_hash": shadow.get("feature_hash"),
        "entrada_iso": official_signal.get("entrada_iso"), "p1_iso": official_signal.get("p1_iso"),
        "p2_iso": official_signal.get("p2_iso"), "p3_iso": official_signal.get("p3_iso"),
        "v2_no_trade": False, "v2_no_trade_reason": None, "shadow_status": shadow.get("status"),
        "shadow_error_code": shadow.get("error_code"), "error_message": None,
        "resultado_official": "PENDENTE", "resultado_shadow": "PENDENTE",
        "metadata": {"idempotency_key": context.idempotency_key, "calibrated_probability": shadow.get("calibrated_probability")},
    }


def json_config_hash(value) -> str:
    import hashlib, json
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
