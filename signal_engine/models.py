from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class EngineVersion(str, Enum):
    V2_EXPERIMENTAL = "V2_EXPERIMENTAL"

class SignalDirection(str, Enum):
    BUY = "COMPRA"
    SELL = "VENDA"
    NO_TRADE = "AGUARDAR"

class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    BREAKOUT = "BREAKOUT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNCERTAIN = "UNCERTAIN"

@dataclass(frozen=True)
class ComponentScores:
    trend: float = 0.0
    momentum: float = 0.0
    structure: float = 0.0
    volume: float = 0.0
    volatility: float = 0.0
    mtf: float = 0.0
    entry: float = 0.0
    regime: float = 0.0
    def as_dict(self) -> dict[str, float]:
        return {
            "TREND_SCORE": round(self.trend, 4),
            "MOMENTUM_SCORE": round(self.momentum, 4),
            "STRUCTURE_SCORE": round(self.structure, 4),
            "VOLUME_SCORE": round(self.volume, 4),
            "VOLATILITY_SCORE": round(self.volatility, 4),
            "MTF_SCORE": round(self.mtf, 4),
            "ENTRY_SCORE": round(self.entry, 4),
            "REGIME_SCORE": round(self.regime, 4),
        }

@dataclass(frozen=True)
class EngineDecision:
    engine_version: EngineVersion
    direction: SignalDirection
    signal_quality: float
    confidence_level: str
    risk: str
    regime: MarketRegime
    long_score: float
    short_score: float
    scores: ComponentScores
    vetoes: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    no_signal_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_audit_dict(self) -> dict[str, Any]:
        data = {
            "ENGINE_VERSION": self.engine_version.value,
            "DIRECAO": self.direction.value,
            "SIGNAL_QUALITY": round(self.signal_quality, 4),
            "CONFIDENCE_LEVEL": self.confidence_level,
            "RISCO": self.risk,
            "REGIME": self.regime.value,
            "LONG_SCORE": round(self.long_score, 4),
            "SHORT_SCORE": round(self.short_score, 4),
            "VETOS": list(self.vetoes),
            "MOTIVOS": list(self.reasons),
            "NO_SIGNAL_REASON": self.no_signal_reason,
        }
        data.update(self.scores.as_dict())
        data.update({f"META_{k}": v for k, v in self.metadata.items()})
        return data
