from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from .evaluator import compare_same_opportunity


FINAL_RESULTS = {"P1", "P2", "P3", "LOSS", "SEM DADOS"}


def evaluate_pending_record(record: dict, candles_by_stage: dict, closed_stages: set[str], *, evaluated_at: datetime | None = None) -> dict:
    official_before = str(record.get("resultado_official") or "PENDENTE")
    shadow_before = str(record.get("resultado_shadow") or "PENDENTE")
    if official_before in FINAL_RESULTS and shadow_before in FINAL_RESULTS:
        return {}
    compared = compare_same_opportunity(
        str(record.get("v1_direction") or ""), str(record.get("v2_direction") or ""),
        candles_by_stage, closed_stages,
    )
    patch = {"resultado_official": compared["official"], "resultado_shadow": compared["shadow"]}
    if patch["resultado_official"] in FINAL_RESULTS and patch["resultado_shadow"] in FINAL_RESULTS:
        patch["evaluated_at"] = (evaluated_at or datetime.now(timezone.utc)).isoformat()
    return patch


def candles_for_record(record: dict, candles: pd.DataFrame, *, now: datetime | None = None) -> tuple[dict, set[str]]:
    now_utc = pd.Timestamp(now or datetime.now(timezone.utc)).tz_convert("UTC")
    minutes = int(str(record.get("timeframe") or "1m").lower().removesuffix("m"))
    frame = candles.copy()
    frame["_open_utc"] = pd.to_datetime(frame["Open time"], utc=True).dt.floor("min")
    found, closed = {}, set()
    for stage in ("P1", "P2", "P3"):
        target = pd.Timestamp(record.get(f"{stage.lower()}_iso"))
        if target.tzinfo is None:
            target = target.tz_localize("UTC")
        else:
            target = target.tz_convert("UTC")
        if now_utc >= target + timedelta(minutes=minutes):
            closed.add(stage)
            rows = frame[frame["_open_utc"] == target.floor("min")]
            found[stage] = None if rows.empty else {"Open": rows.iloc[0]["Open"], "Close": rows.iloc[0]["Close"]}
    return found, closed
