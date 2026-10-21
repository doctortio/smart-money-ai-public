from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Iterable
import pandas as pd
WIN_LABELS = {"WIN", "WIN DIRETO", "WIN P1", "WIN P2", "WIN P5"}; LOSS_LABELS = {"LOSS"}
@dataclass(frozen=True)
class MetricSummary:
    n: int; wins: int; losses: int; doji: int; win_rate: float; ci95_low: float; ci95_high: float; coverage: float

def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0: return 0.0, 0.0
    p = wins / n; denom = 1 + z*z/n; centre = (p + z*z/(2*n)) / denom; margin = z * math.sqrt((p*(1-p) + z*z/(4*n)) / n) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)

def summarize_results(rows: Iterable[dict], total_opportunities: int | None = None, result_key: str = "resultado") -> MetricSummary:
    items = list(rows); n = wins = losses = doji = 0
    for row in items:
        result = str(row.get(result_key) or "").upper()
        if result in WIN_LABELS: n += 1; wins += 1
        elif result in LOSS_LABELS: n += 1; losses += 1
        elif result == "DOJI": n += 1; doji += 1
    low, high = wilson_ci(wins, n); denom = total_opportunities if total_opportunities is not None else len(items)
    return MetricSummary(n, wins, losses, doji, wins/n if n else 0.0, low, high, n/denom if denom else 0.0)

def chronological_split(df: pd.DataFrame, train: float = 0.6, validation: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if train <= 0 or validation < 0 or train + validation >= 1: raise ValueError("invalid chronological split")
    data = df.copy().reset_index(drop=True); n = len(data); a = int(n * train); b = int(n * (train + validation))
    return data.iloc[:a].copy(), data.iloc[a:b].copy(), data.iloc[b:].copy()

def score_candidate(summary: MetricSummary, period_stability: float = 0.0) -> float:
    return summary.win_rate - (1 / math.sqrt(max(summary.n, 1))) - max(0.0, 0.25 - summary.coverage) - max(0.0, period_stability)
