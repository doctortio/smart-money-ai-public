from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import pandas as pd

FEATURE_HASH = "0e6972708e835a77f82ea7c330ab71a370b516cd47fb0568723b1cc45e08c5ec"
MODEL_VERSION = "r03-quant-shadow-r02.1-20261002"
ALLOWED_DIRECTIONS = {"COMPRA", "VENDA"}


@dataclass(frozen=True)
class ShadowContext:
    official_signal_id: str
    event_id: str
    symbol: str
    timestamp: pd.Timestamp
    timeframe: str
    candles: pd.DataFrame
    request_id: str = ""

    @property
    def idempotency_key(self) -> str:
        raw = f"{self.official_signal_id}|{self.event_id}|{self.request_id}|{MODEL_VERSION}"
        return sha256(raw.encode()).hexdigest()


def normalize_market_data(candles: pd.DataFrame) -> pd.DataFrame:
    """Adapt the app's Binance candle frame to the frozen R02.1 contract."""
    aliases = {
        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume",
        "Quote asset volume": "quote_volume", "Number of trades": "trades",
        "Taker buy base": "taker_buy_base", "Taker buy quote": "taker_buy_quote",
        "Open time": "open_time", "Close time": "close_time",
    }
    frame = candles.rename(columns={key: value for key, value in aliases.items() if key in candles.columns}).copy()
    if not isinstance(frame.index, pd.DatetimeIndex):
        if "open_time" not in frame:
            raise ValueError("SHADOW_TIMESTAMP_MISSING")
        frame.index = pd.to_datetime(frame["open_time"], utc=True)
    elif frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    if "timestamp" not in frame:
        frame["timestamp"] = frame.index.astype("int64") // 1_000_000
    required = ["open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "timestamp"]
    missing = [name for name in required if name not in frame]
    if missing:
        raise ValueError("SHADOW_COLUMNS_MISSING:" + ",".join(missing))
    for name in required:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.sort_index()
    if len(frame) < 2160:
        raise ValueError("SHADOW_HISTORY_INSUFFICIENT")
    if frame.index.has_duplicates or frame[required].isna().any().any():
        raise ValueError("SHADOW_FEATURE_INPUT_INVALID")
    return frame


def validate_context(context: ShadowContext) -> pd.DataFrame:
    if not context.official_signal_id or not context.event_id:
        raise ValueError("SHADOW_IDENTITY_MISSING")
    if context.timeframe != "1m":
        raise ValueError("SHADOW_TIMEFRAME_UNSUPPORTED")
    frame = normalize_market_data(context.candles)
    timestamp = pd.Timestamp(context.timestamp)
    timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
    if timestamp not in frame.index:
        raise ValueError("SHADOW_DECISION_CANDLE_MISSING")
    return frame.loc[:timestamp]


def fail_result(error: Exception | str) -> dict[str, Any]:
    code = error if isinstance(error, str) else type(error).__name__
    return {"status": "shadow_error", "error_code": str(code), "model_version": MODEL_VERSION, "feature_hash": FEATURE_HASH}
