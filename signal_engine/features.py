from __future__ import annotations
import math
import pandas as pd
REQUIRED_OHLCV = ("Open", "High", "Low", "Close", "Volume")

def validate_ohlcv(df: pd.DataFrame, min_rows: int = 60) -> tuple[bool, str]:
    if df is None or df.empty:
        return False, "DATA_QUALITY_EMPTY"
    missing = [c for c in REQUIRED_OHLCV if c not in df.columns]
    if missing:
        return False, "DATA_QUALITY_MISSING_" + "_".join(missing)
    if len(df) < min_rows:
        return False, "DATA_QUALITY_INSUFFICIENT_ROWS"
    if df[list(REQUIRED_OHLCV)].tail(min_rows).isna().any().any():
        return False, "DATA_QUALITY_NAN"
    return True, "OK"

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / (loss + 1e-12)
    return 100 - (100 / (1 + rs))

def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy().reset_index(drop=True)
    for col in REQUIRED_OHLCV:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    close, high, low = data["Close"], data["High"], data["Low"]
    volume = data["Volume"].fillna(0)
    if float(volume.sum() or 0) <= 0:
        volume = pd.Series([1.0] * len(data), index=data.index)
        data["Volume"] = volume
    for span in (9, 21, 50, 200):
        data[f"EMA{span}"] = close.ewm(span=span, adjust=False).mean()
        data[f"EMA{span}_SLOPE"] = data[f"EMA{span}"].diff(3) / (data[f"EMA{span}"].shift(3).abs() + 1e-12) * 100
    data["RSI14"] = _rsi(close, 14)
    rsi_min = data["RSI14"].rolling(14).min(); rsi_max = data["RSI14"].rolling(14).max()
    data["STOCH_RSI_K"] = ((data["RSI14"] - rsi_min) / (rsi_max - rsi_min + 1e-12) * 100).rolling(3).mean()
    data["STOCH_RSI_D"] = data["STOCH_RSI_K"].rolling(3).mean()
    ema12 = close.ewm(span=12, adjust=False).mean(); ema26 = close.ewm(span=26, adjust=False).mean()
    data["MACD"] = ema12 - ema26
    data["MACD_SIGNAL"] = data["MACD"].ewm(span=9, adjust=False).mean()
    data["MACD_HIST"] = data["MACD"] - data["MACD_SIGNAL"]
    typical = (high + low + close) / 3
    data["VWAP"] = (typical * volume).cumsum() / (volume.cumsum() + 1e-12)
    tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    data["ATR14"] = tr.rolling(14).mean()
    data["ATR_PCT"] = data["ATR14"] / (close.abs() + 1e-12) * 100
    mid = close.rolling(20).mean(); std = close.rolling(20).std()
    data["BB_MID"] = mid; data["BB_UPPER"] = mid + 2 * std; data["BB_LOWER"] = mid - 2 * std
    data["BB_WIDTH_PCT"] = (data["BB_UPPER"] - data["BB_LOWER"]) / (close.abs() + 1e-12) * 100
    data["BB_POSITION"] = (close - data["BB_LOWER"]) / (data["BB_UPPER"] - data["BB_LOWER"] + 1e-12)
    data["BODY"] = close - data["Open"]; data["RANGE"] = high - low
    data["CANDLE_STRENGTH"] = data["BODY"] / (data["RANGE"] + 1e-12)
    data["CLOSE_POSITION"] = ((close - low) / (high - low + 1e-12)).clip(0, 1)
    data["RETURN_1"] = close.pct_change() * 100; data["RETURN_3"] = close.pct_change(3) * 100; data["RETURN_5"] = close.pct_change(5) * 100
    data["VOL_MA20"] = volume.rolling(20).mean(); data["REL_VOLUME"] = volume / (data["VOL_MA20"] + 1e-12)
    data["BUY_PRESSURE_EST"] = ((data["CLOSE_POSITION"] * 60) + ((data["BODY"] > 0).astype(int) * 25) + data["REL_VOLUME"].clip(0, 2.5) * 6).clip(0, 100)
    data["SELL_PRESSURE_EST"] = (((1 - data["CLOSE_POSITION"]) * 60) + ((data["BODY"] < 0).astype(int) * 25) + data["REL_VOLUME"].clip(0, 2.5) * 6).clip(0, 100)
    data["FLOW_DELTA_EST"] = data["BUY_PRESSURE_EST"] - data["SELL_PRESSURE_EST"]
    data["RECENT_HIGH_20"] = high.rolling(20).max(); data["RECENT_LOW_20"] = low.rolling(20).min()
    data["PREV_HIGH_20"] = data["RECENT_HIGH_20"].shift(1); data["PREV_LOW_20"] = data["RECENT_LOW_20"].shift(1)
    up = high.diff(); down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0); minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = data["ATR14"] + 1e-12
    plus_di = 100 * plus_dm.rolling(14).mean() / atr; minus_di = 100 * minus_dm.rolling(14).mean() / atr
    data["ADX14"] = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12)).rolling(14).mean()
    return data.replace([math.inf, -math.inf], pd.NA)
