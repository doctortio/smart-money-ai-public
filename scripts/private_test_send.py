"""Single, fail-closed private Telegram test send.

The script performs exactly one Bot API call. It never targets the public
channel, never uses Supabase, and never retries an uncertain response.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Callable

import requests

from telegram_worker.adapter import existing_motor_factory

PUBLIC_CHANNEL = "@smartmoneyaioficial"
PRIVATE_CONFIRMATION = "PRIVATE_TEST_AUTHORIZED"
ASSETS = ("BTCUSDT", "ETHUSDT")
TIMEFRAME = "5m"
PLATFORM_URL = "https://smart-money.streamlit.app/"


def _private_chat_id() -> str:
    value = os.getenv("TELEGRAM_PRIVATE_CHAT_ID", "").strip()
    if not re.fullmatch(r"-?\d+", value) or value == "0":
        raise RuntimeError("private_chat_id_invalid")
    if value.lower() == PUBLIC_CHANNEL.lower():
        raise RuntimeError("public_channel_blocked")
    return value


def _require_authorization() -> tuple[str, str]:
    if os.getenv("TELEGRAM_DRY_RUN", "true").strip().lower() == "true":
        raise RuntimeError("dry_run_enabled")
    if os.getenv("PRIVATE_TEST_CONFIRMATION", "").strip() != PRIVATE_CONFIRMATION:
        raise RuntimeError("private_confirmation_missing")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("bot_token_missing")
    return token, _private_chat_id()


def _message(signals: list[dict]) -> str:
    lines = ["<b>TESTE PRIVADO — SMART MONEY AI</b>", ""]
    for signal in signals:
        lines.extend([
            f"Ativo: <b>{signal['asset']}</b>",
            f"Direção: <b>{signal['direction']}</b>",
            f"Timeframe: {signal['timeframe']}",
            f"Risco: {signal['risk']}",
            f"Contexto: {signal['context']}",
            f"Horário: {signal['created_at']}",
            "",
        ])
    lines.extend([PLATFORM_URL, "Aviso: sinais são apoio à análise; o mercado financeiro envolve riscos."])
    return "\n".join(lines)


def prepare_signals(factory: Callable[[str, str], object] = existing_motor_factory) -> list[dict]:
    rows: list[dict] = []
    for asset in ASSETS:
        signal = factory(asset, TIMEFRAME)
        if signal is None:
            raise RuntimeError(f"signal_unavailable:{asset}")
        rows.append({
            "asset": signal.asset,
            "direction": signal.direction,
            "timeframe": signal.timeframe,
            "context": signal.context,
            "risk": signal.risk,
            "created_at": signal.created_at,
        })
    return rows


def send_once(*, request_post: Callable = requests.post, marker: Path = Path("runtime/private_test_sent")) -> int:
    token, chat_id = _require_authorization()
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker.exists():
        raise RuntimeError("duplicate_send_blocked")
    signals = prepare_signals()
    text = _message(signals)
    try:
        response = request_post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        )
    except requests.Timeout as exc:
        raise RuntimeError("telegram_timeout_no_retry") from exc
    response.raise_for_status()
    payload = response.json()
    if payload.get("ok") is not True:
        raise RuntimeError("telegram_rejected")
    message_id = int(payload.get("result", {}).get("message_id", 0))
    if message_id <= 0:
        raise RuntimeError("telegram_message_id_missing")
    marker.write_text(json.dumps({"message_id": message_id}), encoding="utf-8")
    return message_id


if __name__ == "__main__":
    print(f"MESSAGE_ID={send_once()}")
