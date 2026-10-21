from pathlib import Path

import pytest

from scripts import private_test_send as sender
from telegram_worker.worker import Signal


def test_workflow_runs_private_sender_as_module():
    workflow = Path(__file__).parents[1] / ".github" / "workflows" / "telegram-worker.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "run: python -m scripts.private_test_send" in text
    assert "run: python scripts/private_test_send.py" not in text


def factory(asset, timeframe):
    return Signal(asset, "COMPRA", timeframe, "contexto real", "MEDIO", "2026-10-21T12:00:00-03:00")


def setup_env(monkeypatch, chat="6200881966"):
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "false")
    monkeypatch.setenv("PRIVATE_TEST_CONFIRMATION", "PRIVATE_TEST_AUTHORIZED")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("TELEGRAM_PRIVATE_CHAT_ID", chat)


def test_dry_run_never_sends(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "true")
    with pytest.raises(RuntimeError, match="dry_run_enabled"):
        sender.send_once(marker=tmp_path / "sent")


def test_public_destination_blocked(monkeypatch, tmp_path):
    setup_env(monkeypatch, "@smartmoneyaioficial")
    with pytest.raises(RuntimeError, match="private_chat_id_invalid"):
        sender.send_once(marker=tmp_path / "sent")


def test_missing_token_blocked(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "false")
    monkeypatch.setenv("PRIVATE_TEST_CONFIRMATION", "PRIVATE_TEST_AUTHORIZED")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_PRIVATE_CHAT_ID", "6200881966")
    with pytest.raises(RuntimeError, match="bot_token_missing"):
        sender.send_once()


def test_generation_failure_blocks_send(monkeypatch, tmp_path):
    setup_env(monkeypatch)
    monkeypatch.setattr(sender, "prepare_signals", lambda: (_ for _ in ()).throw(RuntimeError("signal_unavailable:BTCUSDT")))
    with pytest.raises(RuntimeError, match="signal_unavailable"):
        sender.send_once(marker=tmp_path / "sent")


def test_authorized_single_send_and_duplicate_block(monkeypatch, tmp_path):
    setup_env(monkeypatch)
    monkeypatch.setattr(sender, "prepare_signals", lambda: [
        {"asset": "BTCUSDT", "direction": "COMPRA", "timeframe": "5m", "context": "c", "risk": "M", "created_at": "t"},
        {"asset": "ETHUSDT", "direction": "VENDA", "timeframe": "5m", "context": "c", "risk": "A", "created_at": "t"},
    ])
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"ok": True, "result": {"message_id": 9}}
    def post(*args, **kwargs):
        calls.append((args, kwargs)); return Response()
    marker = tmp_path / "sent"
    assert sender.send_once(request_post=post, marker=marker) == 9
    with pytest.raises(RuntimeError, match="duplicate_send_blocked"):
        sender.send_once(request_post=post, marker=marker)
    assert len(calls) == 1
    assert calls[0][1]["json"]["chat_id"] == "6200881966"


def test_timeout_has_no_retry(monkeypatch, tmp_path):
    setup_env(monkeypatch)
    monkeypatch.setattr(sender, "prepare_signals", lambda: [
        {"asset": "BTCUSDT", "direction": "COMPRA", "timeframe": "5m", "context": "c", "risk": "M", "created_at": "t"},
        {"asset": "ETHUSDT", "direction": "VENDA", "timeframe": "5m", "context": "c", "risk": "A", "created_at": "t"},
    ])
    calls = []
    def post(*args, **kwargs):
        calls.append(1); raise sender.requests.Timeout()
    with pytest.raises(RuntimeError, match="no_retry"):
        sender.send_once(request_post=post, marker=tmp_path / "sent")
    assert len(calls) == 1
