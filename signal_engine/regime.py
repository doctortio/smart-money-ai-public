from __future__ import annotations
from .models import MarketRegime

def classify_regime(row: dict) -> tuple[MarketRegime, float, tuple[str, ...]]:
    close = float(row.get("Close") or 0); ema21 = float(row.get("EMA21") or close); ema50 = float(row.get("EMA50") or close); ema200 = float(row.get("EMA200") or close)
    slope21 = float(row.get("EMA21_SLOPE") or 0); atr_pct = float(row.get("ATR_PCT") or 0); bb_width = float(row.get("BB_WIDTH_PCT") or 0); adx = float(row.get("ADX14") or 0)
    if atr_pct <= 0 or bb_width <= 0:
        return MarketRegime.UNCERTAIN, 0.0, ("dados de volatilidade insuficientes",)
    if atr_pct > 1.6 or bb_width > 4.5:
        return MarketRegime.HIGH_VOLATILITY, min(1.0, max(0.2, atr_pct / 3.0)), ("volatilidade acima do normal",)
    if atr_pct < 0.045 or bb_width < 0.18:
        return MarketRegime.LOW_VOLATILITY, 0.65, ("volatilidade baixa",)
    if close > ema21 > ema50 and ema50 >= ema200 * 0.995 and slope21 > 0.01:
        return MarketRegime.TRENDING_UP, min(1.0, 0.55 + abs(slope21) * 4 + adx / 100), ("preço e EMAs alinhados para alta",)
    if close < ema21 < ema50 and ema50 <= ema200 * 1.005 and slope21 < -0.01:
        return MarketRegime.TRENDING_DOWN, min(1.0, 0.55 + abs(slope21) * 4 + adx / 100), ("preço e EMAs alinhados para baixa",)
    if adx >= 25 and (close > float(row.get("PREV_HIGH_20") or close) or close < float(row.get("PREV_LOW_20") or close)):
        return MarketRegime.BREAKOUT, min(1.0, adx / 40), ("rompimento recente com ADX",)
    if adx < 20:
        return MarketRegime.RANGING, max(0.35, 1 - adx / 30), ("ADX baixo sugere lateralização",)
    return MarketRegime.UNCERTAIN, 0.35, ("regime misto",)
