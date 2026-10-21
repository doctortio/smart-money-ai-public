from __future__ import annotations

from datetime import datetime, timedelta

OFFICIAL_RESULTS = ("P1", "P2", "P3", "LOSS", "PENDENTE", "SEM DADOS")
HISTORICAL_WIN_P1 = "WIN P1"
HISTORICAL_WIN_P2 = "WIN P2"
HISTORICAL_WIN_P5 = "WIN P5"
HISTORICAL_WIN_DIRETO = "WIN DIRETO"


def parse_timeframe_minutes(timeframe: str) -> int:
    return int(str(timeframe or "1m").lower().replace("m", ""))


def p3_from_entry(entrada_dt: datetime, timeframe: str) -> datetime:
    return entrada_dt + timedelta(minutes=parse_timeframe_minutes(timeframe) * 3)


def candle_direction_result(candle: dict, direction: str) -> str:
    open_ = float(candle.get("Open", candle.get("open")))
    close = float(candle.get("Close", candle.get("close")))
    direction = str(direction or "").upper()
    if close == open_:
        return "DOJI"
    if direction == "COMPRA":
        return "GAIN" if close > open_ else "MISS"
    if direction == "VENDA":
        return "GAIN" if close < open_ else "MISS"
    return "SEM DADOS"


def evaluate_sequential_audit(record: dict, candles_by_stage: dict[str, dict | None], closed_stages: set[str] | None = None) -> dict:
    result = dict(record)
    closed_stages = set(closed_stages or [])
    for stage in ("P1", "P2", "P3"):
        if stage not in closed_stages:
            result["resultado"] = "PENDENTE"
            result["motivo_auditoria"] = f"{stage}_NOT_CLOSED"
            return result
        candle = candles_by_stage.get(stage)
        if not candle:
            result["resultado"] = "SEM DADOS"
            result["motivo_auditoria"] = f"{stage}_CANDLE_MISSING"
            return result
        stage_result = candle_direction_result(candle, result.get("direcao"))
        if stage_result == "SEM DADOS":
            result["resultado"] = "SEM DADOS"
            result["motivo_auditoria"] = f"{stage}_INVALID_DIRECTION_OR_CANDLE"
            return result
        result[f"resultado_{stage.lower()}"] = stage_result
        if stage_result == "GAIN":
            result["resultado"] = stage
            result["motivo_auditoria"] = "FIRST_DIRECTIONAL_GAIN"
            return result
    result["resultado"] = "LOSS"
    result["motivo_auditoria"] = "NO_DIRECTIONAL_GAIN_AFTER_P3"
    return result


def normalize_audit_result(value: str) -> str:
    value = str(value or "PENDENTE").strip().upper()
    mapping = {
        "P1": "P1",
        "P2": "P2",
        "P3": "P3",
        "LOSS": "LOSS",
        "PENDENTE": "PENDENTE",
        "SEM DADOS": "SEM DADOS",
        HISTORICAL_WIN_P1: "P1",
        HISTORICAL_WIN_P2: "P2",
    }
    return mapping.get(value, "HISTORICO_OUTRO")


def calculate_audit_counts(records: list[dict]) -> dict:
    counts = {"p1": 0, "p2": 0, "p3": 0, "loss": 0, "pendentes": 0, "sem_dados": 0, "historico_outros": 0}
    for record in records:
        normalized = normalize_audit_result(record.get("resultado"))
        if normalized == "P1":
            counts["p1"] += 1
        elif normalized == "P2":
            counts["p2"] += 1
        elif normalized == "P3":
            counts["p3"] += 1
        elif normalized == "LOSS":
            counts["loss"] += 1
        elif normalized == "SEM DADOS":
            counts["sem_dados"] += 1
        elif normalized == "PENDENTE":
            counts["pendentes"] += 1
        else:
            counts["historico_outros"] += 1
    counts["total_considerado"] = sum(counts.values())
    return counts
