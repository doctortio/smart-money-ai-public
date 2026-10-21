import json
from datetime import datetime
from pathlib import Path

from telegram_worker.worker import Signal, TZ, format_message, run_publication


def signal_factory(asset, timeframe):
    return Signal(asset, "COMPRA" if asset == "BTCUSDT" else "VENDA", timeframe, "Leitura técnica", "Avalie antes de operar", "2026-10-21T09:00:00-03:00")


def test_dry_run_builds_two_messages_without_sending(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "true")
    called = []
    result = run_publication(datetime(2026, 10, 21, 9, 0, tzinfo=TZ), signal_factory, state_path=tmp_path / "state.json", send=lambda text: called.append(text) or 1)
    assert result.dry_run is True
    assert len(result.messages) == 2
    assert result.sent == 0
    assert called == []
    assert "https://smart-money.streamlit.app/" in result.messages[0]
    assert "Aviso:" in result.messages[0]


def test_duplicate_is_idempotent_after_sent_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "false")
    sent = []
    sender = lambda text: sent.append(text) or 42
    when = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)
    first = run_publication(when, signal_factory, state_path=tmp_path / "state.json", send=sender)
    second = run_publication(when, signal_factory, state_path=tmp_path / "state.json", send=sender)
    assert first.sent == 2
    assert second.sent == 0
    assert second.skipped == 2
    assert len(sent) == 2


def test_missing_signal_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "true")
    result = run_publication(datetime(2026, 10, 21, 20, 0, tzinfo=TZ), lambda *_: None, state_path=tmp_path / "state.json")
    assert result.sent == 0 and result.skipped == 2 and result.messages == ()


def test_dry_run_never_reserves_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "true")
    class Ledger:
        def reserve(self, *args, **kwargs):
            raise AssertionError("DRY_RUN não pode reservar")
    result = run_publication(datetime(2026, 10, 21, 20, 0, tzinfo=TZ), signal_factory,
                             state_path=tmp_path / "state.json", ledger=Ledger())
    assert result.sent == 0 and len(result.messages) == 2
