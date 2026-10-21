from __future__ import annotations

from dataclasses import dataclass
import hashlib, json, math
import pandas as pd
from .features import prepare_features, validate_ohlcv
from .models import ComponentScores, EngineDecision, EngineVersion, MarketRegime, SignalDirection
from .regime import classify_regime

FEATURE_SET_V3 = (
    "EMA9_SLOPE", "EMA21_SLOPE", "MACD_HIST", "RSI14", "RETURN_3",
    "FLOW_DELTA_EST", "REL_VOLUME", "CANDLE_STRENGTH", "CLOSE_POSITION",
    "ATR_PCT", "BB_WIDTH_PCT", "VWAP_DISTANCE_PCT", "EMA9_DISTANCE_PCT",
)

@dataclass(frozen=True)
class V3Config:
    version_label: str = "V2_EXPERIMENTAL_003"
    min_score: float = 56.0
    tie_margin: float = 7.0
    high_confidence_score: float = 70.0
    bad_entry_extreme: float = 0.72
    model_type: str = "RULE_BASED_V3"
    notes: str = "feature_selected_interpretable_score"
    def hash8(self) -> str:
        payload = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]

def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))

def _num(row: pd.Series, key: str, default: float = 0.0) -> float:
    v = row.get(key, default)
    try:
        if pd.isna(v): return default
        return float(v)
    except Exception:
        return default

def _components(row: pd.Series) -> tuple[dict, dict, MarketRegime]:
    close = _num(row, "Close", 1.0) or 1.0
    ema9 = _num(row, "EMA9", close); ema21 = _num(row, "EMA21", close); vwap = _num(row, "VWAP", close)
    ema9_slope = _clip(_num(row, "EMA9_SLOPE"), -0.25, 0.25) / 0.25
    ema21_slope = _clip(_num(row, "EMA21_SLOPE"), -0.20, 0.20) / 0.20
    macd = _clip(_num(row, "MACD_HIST"), -close*0.0015, close*0.0015) / (close*0.0015 + 1e-12)
    rsi = _clip((_num(row, "RSI14", 50.0) - 50.0) / 22.0, -1.0, 1.0)
    ret3 = _clip(_num(row, "RETURN_3"), -0.35, 0.35) / 0.35
    flow = _clip(_num(row, "FLOW_DELTA_EST"), -45, 45) / 45
    relvol = _clip((_num(row, "REL_VOLUME", 1.0) - 1.0) / 1.4, -0.6, 1.0)
    candle = _clip(_num(row, "CANDLE_STRENGTH"), -1.0, 1.0)
    close_pos = _clip((_num(row, "CLOSE_POSITION", 0.5) - 0.5) * 2, -1.0, 1.0)
    atr = _num(row, "ATR_PCT", 0.0); bbw = _num(row, "BB_WIDTH_PCT", 0.0)
    vwap_dist = _clip(((close - vwap) / close) * 100, -0.35, 0.35) / 0.35
    ema9_dist_pct = ((close - ema9) / close) * 100
    ema9_dist = _clip(ema9_dist_pct, -0.35, 0.35) / 0.35
    trend_raw = 0.42*ema9_slope + 0.30*ema21_slope + 0.16*vwap_dist + 0.12*ema9_dist
    momentum_raw = 0.38*macd + 0.28*ret3 + 0.20*rsi + 0.14*candle
    structure_raw = 0.55*close_pos + 0.25*candle + 0.20*ema9_dist
    volume_raw = 0.58*flow + 0.24*relvol + 0.18*candle
    entry_raw = 0.45*candle + 0.35*close_pos - 0.20*abs(ema9_dist)
    if 0.05 <= atr <= 1.25 and 0.15 <= bbw <= 4.5: vol_context = 0.12
    elif atr <= 0 or bbw <= 0 or atr > 2.2 or bbw > 6.0: vol_context = -0.18
    else: vol_context = -0.04
    regime, _, _ = classify_regime(row)
    regime_long = 0.10 if regime.value in {"TRENDING_UP", "BREAKOUT"} else -0.08 if regime.value == "TRENDING_DOWN" else 0.02 if regime.value == "RANGING" else 0.0
    regime_short = 0.10 if regime.value in {"TRENDING_DOWN", "BREAKOUT"} else -0.08 if regime.value == "TRENDING_UP" else 0.02 if regime.value == "RANGING" else 0.0
    groups = {
        "trend": trend_raw, "momentum": momentum_raw, "structure": structure_raw,
        "volume": volume_raw, "volatility": vol_context, "entry": entry_raw,
        "regime_long": regime_long, "regime_short": regime_short,
    }
    weights = {"trend": 0.24, "momentum": 0.21, "structure": 0.17, "volume": 0.14, "entry": 0.14, "volatility": 0.05, "regime": 0.05}
    long_e = weights["trend"]*trend_raw + weights["momentum"]*momentum_raw + weights["structure"]*structure_raw + weights["volume"]*volume_raw + weights["entry"]*entry_raw + weights["volatility"]*vol_context + weights["regime"]*regime_long
    short_e = weights["trend"]*(-trend_raw) + weights["momentum"]*(-momentum_raw) + weights["structure"]*(-structure_raw) + weights["volume"]*(-volume_raw) + weights["entry"]*(-entry_raw) + weights["volatility"]*vol_context + weights["regime"]*regime_short
    long_score = _clip(50.0 + long_e*50.0, 0, 100)
    short_score = _clip(50.0 + short_e*50.0, 0, 100)
    comp = ComponentScores(trend=abs(trend_raw), momentum=abs(momentum_raw), structure=abs(structure_raw), volume=abs(volume_raw), volatility=vol_context, mtf=0.0, entry=abs(entry_raw), regime=max(regime_long, regime_short))
    aux = {"ema9_distance_pct": ema9_dist_pct, "atr_pct": atr, "bb_width_pct": bbw, "long_evidence": long_e, "short_evidence": short_e}
    return {"long_score": long_score, "short_score": short_score, "groups": groups, "component_scores": comp, "aux": aux}, groups, regime

class SignalEngineV3:
    def __init__(self, config: V3Config | None = None):
        self.config = config or V3Config()
        self.version = EngineVersion.V2_EXPERIMENTAL
    def evaluate(self, df: pd.DataFrame, *, market: str = "Cripto", symbol: str = "", timeframe: str = "", higher_df: pd.DataFrame | None = None) -> EngineDecision:
        ok, dq = validate_ohlcv(df, min_rows=80)
        if not ok:
            return EngineDecision(self.version, SignalDirection.NO_TRADE, 0.0, "BAIXA", "ALTO", MarketRegime.UNCERTAIN, 0.0, 0.0, ComponentScores(), ("DATA_QUALITY",), (dq,), dq, {"config": self.config.version_label})
        return self.evaluate_featured(prepare_features(df), market=market, symbol=symbol, timeframe=timeframe)
    def evaluate_featured(self, featured: pd.DataFrame, *, market: str = "Cripto", symbol: str = "", timeframe: str = "") -> EngineDecision:
        row = featured.iloc[-1]
        calc, groups, regime = _components(row)
        long_score = calc["long_score"]; short_score = calc["short_score"]
        best = max(long_score, short_score); gap = abs(long_score - short_score)
        vetoes: list[str] = []
        no_reason = ""
        if best < self.config.min_score:
            no_reason = "DIRECTIONAL_EVIDENCE_INSUFFICIENT"
        elif gap < self.config.tie_margin:
            vetoes.append("LONG_SHORT_TIE"); no_reason = "LONG_SHORT_TIE"
        aux = calc["aux"]
        bad_entry = abs(aux["ema9_distance_pct"]) > self.config.bad_entry_extreme and best < 68
        if bad_entry:
            vetoes.append("BAD_ENTRY_EXTREME"); no_reason = no_reason or "BAD_ENTRY_EXTREME"
        if no_reason:
            direction = SignalDirection.NO_TRADE
        else:
            direction = SignalDirection.BUY if long_score >= short_score else SignalDirection.SELL
        quality = _clip(best / 100.0, 0.0, 1.0)
        confidence = "ALTA" if best >= 72 else "MÉDIA" if best >= 62 else "BAIXA"
        risk = "BAIXO" if best >= 72 and not vetoes else "MÉDIO" if best >= 62 else "ALTO"
        reasons = []
        if direction != SignalDirection.NO_TRADE:
            reasons.append("long_score_selected" if direction == SignalDirection.BUY else "short_score_selected")
        else:
            reasons.append(no_reason)
        return EngineDecision(
            self.version, direction, quality, confidence, risk, regime, long_score, short_score,
            calc["component_scores"], tuple(vetoes), tuple(reasons), no_reason,
            {"market": market, "symbol": symbol, "timeframe": timeframe, "config": self.config.version_label, "config_hash": self.config.hash8(), "model_type": self.config.model_type, "feature_set": "FEATURE_SET_V3"}
        )
