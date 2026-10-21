"""Frozen R02 feature pipeline. Pure pandas/numpy; no UI, database, or network."""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_NAMES = [
    "dist_ema9", "slope_ema9", "dist_ema21", "slope_ema21",
    "dist_ema50", "slope_ema50", "dist_ema200", "slope_ema200",
    "align_up", "align_down", "cross", "rsi", "stoch_rsi", "macd",
    "macd_hist", "roc", "adx", "di_spread", "atr_pct", "bb_pos",
    "bb_width", "dist_vwap", "rel_volume", "vol_change", "obv_slope",
    "taker_ratio", "taker_quote_ratio", "rel_trades", "dist_resistance",
    "dist_support", "break_up", "break_down", "hh", "hl", "lh", "ll",
    "body_signed", "body_ratio", "range_atr", "wick_upper", "wick_lower",
    "trend", "high_vol", "mtf5_trend", "mtf5_roc", "mtf15_trend",
    "mtf15_roc", "hour_sin", "hour_cos", "dow_sin", "dow_cos", "hour",
    "dow", "session",
]

REQUIRED_COLUMNS = [
    "open", "high", "low", "close", "volume", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "timestamp",
]


def _ew(values: pd.Series, periods: int) -> pd.Series:
    return values.ewm(alpha=1 / periods, adjust=False, min_periods=periods).mean()


def build_features(market_data: pd.DataFrame) -> pd.DataFrame:
    """Build the frozen 54 features from chronological 1-minute candles.

    The index must be a unique, increasing UTC DatetimeIndex. Each row represents
    a completed 1-minute candle. Higher-timeframe context uses only fully closed
    5-minute and 15-minute bars available at the next 1-minute decision boundary.
    """
    missing = sorted(set(REQUIRED_COLUMNS) - set(market_data.columns))
    if missing:
        raise ValueError(f"missing candle columns: {missing}")
    if not isinstance(market_data.index, pd.DatetimeIndex):
        raise TypeError("market_data.index must be a DatetimeIndex")
    if market_data.index.has_duplicates or not market_data.index.is_monotonic_increasing:
        raise ValueError("market_data index must be unique and chronological")

    d = market_data
    f = pd.DataFrame(index=d.index)
    c, o, h, l, v = d.close, d.open, d.high, d.low, d.volume
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = _ew(tr, 14)
    safe = atr.replace(0, np.nan)
    for periods in (9, 21, 50, 200):
        ema = c.ewm(span=periods, adjust=False, min_periods=periods).mean()
        f[f"dist_ema{periods}"] = (c - ema) / safe
        f[f"slope_ema{periods}"] = (ema - ema.shift(5)) / safe
        f[f"ema{periods}"] = ema
    f["align_up"] = ((f.ema9 > f.ema21) & (f.ema21 > f.ema50) & (f.ema50 > f.ema200)).astype(float)
    f["align_down"] = ((f.ema9 < f.ema21) & (f.ema21 < f.ema50) & (f.ema50 < f.ema200)).astype(float)
    spread = f.ema9 - f.ema21
    f["cross"] = np.sign(spread) - np.sign(spread.shift())
    delta = c.diff()
    gain, loss = _ew(delta.clip(lower=0), 14), _ew(-delta.clip(upper=0), 14)
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    rsi = rsi.mask((loss == 0) & (gain > 0), 100).mask((loss == 0) & (gain == 0), 50)
    f["rsi"] = rsi
    f["stoch_rsi"] = (rsi - rsi.rolling(14).min()) / (rsi.rolling(14).max() - rsi.rolling(14).min()).replace(0, np.nan)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    f["macd"] = macd / safe
    f["macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()) / safe
    f["roc"] = c.pct_change(10) * 100
    up, down = h.diff(), -l.diff()
    plus = _ew(up.where((up > down) & (up > 0), 0), 14) / safe * 100
    minus = _ew(down.where((down > up) & (down > 0), 0), 14) / safe * 100
    f["adx"] = _ew(100 * (plus - minus).abs() / (plus + minus).replace(0, np.nan), 14)
    f["di_spread"] = plus - minus
    f["atr_pct"] = atr / c * 100
    mean, std = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    f["bb_pos"] = (c - (mean - 2 * std)) / (4 * std).replace(0, np.nan)
    f["bb_width"] = 4 * std / mean
    day = d.index.floor("D")
    vwap = d.quote_volume.groupby(day).cumsum() / v.groupby(day).cumsum().replace(0, np.nan)
    f["dist_vwap"] = (c - vwap) / safe
    f["rel_volume"] = v / v.shift().rolling(20).mean().replace(0, np.nan)
    f["vol_change"] = v.pct_change().clip(-10, 10)
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    f["obv_slope"] = (obv - obv.shift(10)) / v.shift().rolling(20).mean().replace(0, np.nan)
    f["taker_ratio"] = d.taker_buy_base / v.replace(0, np.nan)
    f["taker_quote_ratio"] = d.taker_buy_quote / d.quote_volume.replace(0, np.nan)
    f["rel_trades"] = d.trades / d.trades.shift().rolling(20).mean().replace(0, np.nan)
    recent_h, recent_l = h.shift().rolling(20).max(), l.shift().rolling(20).min()
    f["dist_resistance"] = (recent_h - c) / safe
    f["dist_support"] = (c - recent_l) / safe
    f["break_up"], f["break_down"] = (c > recent_h).astype(float), (c < recent_l).astype(float)
    for name, values in (("hh", h > h.shift()), ("hl", l > l.shift()), ("lh", h < h.shift()), ("ll", l < l.shift())):
        f[name] = values.astype(float)
    candle_range = (h - l).replace(0, np.nan)
    f["body_signed"] = (c - o) / safe
    f["body_ratio"] = (c - o).abs() / candle_range
    f["range_atr"] = (h - l) / safe
    f["wick_upper"] = (h - d[["open", "close"]].max(axis=1)) / candle_range
    f["wick_lower"] = (d[["open", "close"]].min(axis=1) - l) / candle_range
    f["trend"] = (f.adx >= 25).astype(float)
    f["high_vol"] = (f.atr_pct > f.atr_pct.shift().rolling(1440, min_periods=720).median()).astype(float)
    for period in (5, 15):
        upper = d.resample(f"{period}min", label="right", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "timestamp": "count"}
        )
        upper = upper[upper.timestamp == period]
        ema9 = upper.close.ewm(span=9, adjust=False, min_periods=9).mean()
        ema21 = upper.close.ewm(span=21, adjust=False, min_periods=21).mean()
        context = pd.DataFrame(
            {f"mtf{period}_trend": (ema9 - ema21) / upper.close * 100, f"mtf{period}_roc": upper.close.pct_change(3) * 100},
            index=upper.index,
        )
        context = context.reindex(d.index + pd.Timedelta(minutes=1), method="ffill")
        context.index = d.index
        f = f.join(context)
    f["hour_sin"] = np.sin(2 * np.pi * d.index.hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * d.index.hour / 24)
    f["dow_sin"] = np.sin(2 * np.pi * d.index.dayofweek / 7)
    f["dow_cos"] = np.cos(2 * np.pi * d.index.dayofweek / 7)
    f["hour"], f["dow"], f["session"] = d.index.hour, d.index.dayofweek, d.index.hour // 8
    f = f.drop(columns=[f"ema{periods}" for periods in (9, 21, 50, 200)]).replace([np.inf, -np.inf], np.nan)
    if list(f.columns) != FEATURE_NAMES:
        raise AssertionError("frozen feature order changed")
    return f


def build_features_at(timestamp: pd.Timestamp, market_data: pd.DataFrame) -> pd.Series:
    """Return features at one completed 1-minute candle using no later rows."""
    ts = pd.Timestamp(timestamp)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    history = market_data.loc[:ts]
    if history.empty or history.index[-1] != ts:
        raise KeyError(f"completed candle not found at {ts}")
    return build_features(history).iloc[-1]
