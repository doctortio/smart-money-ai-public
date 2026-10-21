from __future__ import annotations

from datetime import datetime, timedelta


def opportunity_times(entry: datetime, timeframe: str) -> dict[str, datetime]:
    minutes = int(str(timeframe).lower().removesuffix("m"))
    return {"ENTRADA": entry, "P1": entry + timedelta(minutes=minutes), "P2": entry + timedelta(minutes=2 * minutes), "P3": entry + timedelta(minutes=3 * minutes)}


def evaluate_direction(direction: str, candles_by_stage: dict, closed_stages: set[str]) -> str:
    if direction not in {"COMPRA", "VENDA"}:
        return "SEM DADOS"
    for stage in ("P1", "P2", "P3"):
        if stage not in closed_stages:
            return "PENDENTE"
        candle = candles_by_stage.get(stage)
        if candle is None:
            return "SEM DADOS"
        try:
            open_, close = float(candle["Open"]), float(candle["Close"])
        except (KeyError, TypeError, ValueError):
            return "SEM DADOS"
        gain = close > open_ if direction == "COMPRA" else close < open_ if direction == "VENDA" else False
        if gain:
            return stage
    return "LOSS"


def compare_same_opportunity(official_direction: str, shadow_direction: str, candles_by_stage: dict, closed_stages: set[str]) -> dict:
    return {"official": evaluate_direction(official_direction, candles_by_stage, closed_stages),
            "shadow": evaluate_direction(shadow_direction, candles_by_stage, closed_stages),
            "same_candles": True, "stages": ("P1", "P2", "P3")}
