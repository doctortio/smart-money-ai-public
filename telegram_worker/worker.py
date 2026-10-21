"""Telegram worker V1 for Smart Money AI.

DRY_RUN is deliberately enabled by default. This module has no Streamlit runtime
and does not reserve or consume user wallet credits.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

import requests

LOG = logging.getLogger("smart_money.telegram_worker")
TZ = ZoneInfo("America/Sao_Paulo")
PLATFORM_URL = "https://smart-money.streamlit.app/"
RISK_NOTICE = "Aviso: sinais são apoio à análise; o mercado financeiro envolve riscos."
ASSETS = ("BTCUSDT", "ETHUSDT")
TIMEFRAME = "5m"
CHANNEL = "@smartmoneyaioficial"
SCHEDULE_TIMES = (dt_time(9, 0), dt_time(14, 0), dt_time(20, 0))


@dataclass(frozen=True)
class Signal:
    asset: str
    direction: str
    timeframe: str
    context: str
    risk: str
    created_at: str


@dataclass(frozen=True)
class RunResult:
    run_id: str
    scheduled_at: str
    dry_run: bool
    messages: tuple[str, ...]
    sent: int
    skipped: int


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def dry_run() -> bool:
    return _truthy(os.getenv("TELEGRAM_DRY_RUN"), True)


def load_secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required secret: {name}")
    return value


def execution_key(scheduled_at: str, asset: str) -> str:
    raw = f"smart-money-ai|{CHANNEL}|{scheduled_at}|{asset}|{TIMEFRAME}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("sent", []))
    except (OSError, ValueError) as exc:
        LOG.warning("state_read_failed type=%s", type(exc).__name__)
        return set()


def save_state(path: Path, sent: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"sent": sorted(sent)}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def format_message(signal: Signal) -> str:
    return (
        "<b>Smart Money AI · sinal demonstrativo</b>\n\n"
        f"Ativo: <b>{signal.asset}</b>\n"
        f"Direção: <b>{signal.direction}</b>\n"
        f"Timeframe: <b>{signal.timeframe}</b>\n"
        f"Contexto: {signal.context}\n"
        f"Risco: {signal.risk}\n\n"
        f"{PLATFORM_URL}\n{RISK_NOTICE}"
    )


def send_telegram(text: str, *, token: str, chat_id: str, timeout: float = 15.0) -> int:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram API rejected message")
    return int(payload.get("result", {}).get("message_id", 0))


def run_publication(
    scheduled_at: datetime,
    signal_factory: Callable[[str, str], Signal | None],
    *,
    state_path: Path,
    send: Callable[[str], int] | None = None,
    ledger=None,
) -> RunResult:
    scheduled_at = scheduled_at.astimezone(TZ)
    stamp = scheduled_at.strftime("%Y-%m-%dT%H:%M:%S%z")
    run_id = uuid.uuid4().hex[:12]
    sent_keys = load_state(state_path)
    messages: list[str] = []
    sent = skipped = 0
    for asset in ASSETS:
        key = execution_key(stamp, asset)
        if key in sent_keys:
            skipped += 1
            LOG.info("telegram_skip_duplicate run_id=%s asset=%s", run_id, asset)
            continue
        signal = signal_factory(asset, TIMEFRAME)
        if signal is None:
            LOG.info("telegram_skip_no_signal run_id=%s asset=%s", run_id, asset)
            skipped += 1
            continue
        text = format_message(signal)
        messages.append(text)
        if dry_run():
            LOG.info("telegram_dry_run run_id=%s asset=%s status=prepared", run_id, asset)
            continue
        if ledger is not None:
            if not ledger.reserve(key, channel=CHANNEL, assets=list(ASSETS), timeframe=TIMEFRAME, scheduled_at=stamp, owner_id=run_id):
                skipped += 1
                continue
            ledger.mark(key, "sending")
        if send is None:
            raise RuntimeError("send callback required when TELEGRAM_DRY_RUN is false")
        try:
            message_id = send(text)
        except Exception:
            if ledger is not None:
                ledger.mark(key, "unknown")
            raise
        if ledger is not None:
            ledger.mark(key, "sent", message_id)
        sent_keys.add(key)
        save_state(state_path, sent_keys)
        sent += 1
        LOG.info("telegram_sent run_id=%s asset=%s message_id=%s", run_id, asset, message_id)
    return RunResult(run_id, stamp, dry_run(), tuple(messages), sent, skipped)


def current_schedule(now: datetime | None = None) -> tuple[datetime, ...]:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    return tuple(datetime.combine(now.date(), t, TZ) for t in SCHEDULE_TIMES)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    raise SystemExit("Configure a signal_factory adapter before running the worker; TELEGRAM_DRY_RUN defaults to true.")


if __name__ == "__main__":
    main()
