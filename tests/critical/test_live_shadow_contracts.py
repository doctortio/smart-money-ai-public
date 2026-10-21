from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from signal_engine import live_shadow


class FakeDirection(Enum):
    COMPRA = "COMPRA"
    AGUARDAR = "AGUARDAR"


class FakeRegime(Enum):
    NEUTRO = "NEUTRO"


@dataclass
class FakeDecision:
    direction: FakeDirection
    long_score: float = 0.7
    short_score: float = 0.2
    risk: str = "MEDIO"
    regime: FakeRegime = FakeRegime.NEUTRO
    no_signal_reason: str | None = None


class FakeEngine:
    def __init__(self, direction=FakeDirection.COMPRA):
        self.direction = direction

    def evaluate(self, candles, *, market, symbol, timeframe):
        return FakeDecision(direction=self.direction, no_signal_reason="AGUARDAR" if self.direction == FakeDirection.AGUARDAR else None)


def candles():
    return pd.DataFrame({
        "Open": [1, 2], "High": [2, 3], "Low": [0.5, 1.5], "Close": [1.5, 2.5], "Volume": [10, 11],
        "Close time": pd.to_datetime(["2026-09-26T19:00:00Z", "2026-09-26T19:01:00Z"]),
    })


def v1_decision():
    return {"id_sinal": "official-1", "direcao": "VENDA", "confianca": 45, "risco": "ALTO"}


def test_v2_version_hash_e_feature_flag_contract():
    assert live_shadow.V2_ENGINE_VERSION == "V2_EXPERIMENTAL_003"
    assert live_shadow.V2_CONFIG_HASH == "792460b7"
    assert live_shadow.shadow_enabled("true") is True
    assert live_shadow.shadow_enabled("false") is False
    assert live_shadow.run_live_shadow(enabled=False, insert_func=lambda *a, **k: [])["status"] == "shadow_disabled"


def test_v2_shadow_nao_altera_v1_oficial_nem_consumo_ou_notificacao():
    record = live_shadow.build_shadow_record(
        official_signal_id="official-1",
        user_id="u@example.com",
        market="Cripto",
        symbol="BTCUSDT",
        timeframe="1m",
        v1_decision=v1_decision(),
        candles=candles(),
        engine_factory=lambda: FakeEngine(FakeDirection.COMPRA),
    )
    assert record["official_signal_id"] == "official-1"
    assert record["v1_engine_version"] == "V1_OFFICIAL"
    assert record["v1_direction"] == "VENDA"
    assert record["v2_engine_version"] == "V2_EXPERIMENTAL_003"
    assert record["v2_config_hash"] == "792460b7"
    assert "credit" not in str(record).lower()
    assert "notification" not in str(record).lower()


def test_v2_falha_nao_quebra_fluxo_v1():
    def broken_factory():
        raise RuntimeError("BROKEN_ENGINE")
    result = live_shadow.run_live_shadow(
        enabled=True,
        insert_func=lambda *a, **k: [],
        official_signal_id="official-1",
        user_id="u@example.com",
        market="Cripto",
        symbol="BTCUSDT",
        timeframe="1m",
        v1_decision=v1_decision(),
        candles=candles(),
        engine_factory=broken_factory,
    )
    assert result["ok"] is True
    assert result["status"] == "shadow_error"
    assert result["record"]["shadow_error_code"] == "RuntimeError"


def test_shadow_idempotencia_on_conflict():
    captured = {}
    def insert_func(table, record, **kwargs):
        captured.update({"table": table, "record": record, "kwargs": kwargs})
        return [{"id": 1}]
    record = live_shadow.build_shadow_record(
        official_signal_id="official-1",
        user_id="u@example.com",
        market="Cripto",
        symbol="BTCUSDT",
        timeframe="1m",
        v1_decision=v1_decision(),
        candles=candles(),
        engine_factory=lambda: FakeEngine(),
    )
    saved = live_shadow.persist_shadow_record(record, insert_func)
    assert saved["ok"] is True
    assert captured["table"] == "sma_signal_shadow"
    assert captured["kwargs"]["params"]["on_conflict"] == "official_signal_id,v2_engine_version,v2_config_hash"
    assert "ignore-duplicates" in captured["kwargs"]["prefer"]
