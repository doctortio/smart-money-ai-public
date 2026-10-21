"""Atomic public-channel publisher; activation is intentionally fail-closed."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Callable

import requests

from .adapter import existing_motor_factory
from .ledger import SupabaseLedger
from .ledger_gateway import ledger_from_env
from .worker import ASSETS, CHANNEL, TIMEFRAME, TZ, Signal, format_message

PUBLIC_SLOTS = (dt_time(9, 0), dt_time(14, 0), dt_time(20, 0))
WINDOW_MINUTES = 15


def validate_scheduled_slot(scheduled_at: datetime, *, now: datetime | None = None) -> str:
    """Return the canonical slot or fail closed outside today's BRT window."""
    local = scheduled_at.astimezone(TZ)
    current = (now or datetime.now(TZ)).astimezone(TZ)
    if local.date() != current.date():
        raise RuntimeError("scheduled_slot_not_current_day")
    slot = min(PUBLIC_SLOTS, key=lambda t: abs((local.hour * 60 + local.minute) - (t.hour * 60 + t.minute)))
    slot_dt = datetime.combine(local.date(), slot, TZ)
    if abs((local - slot_dt).total_seconds()) > WINDOW_MINUTES * 60:
        raise RuntimeError("scheduled_slot_invalid")
    if abs((current - slot_dt).total_seconds()) > WINDOW_MINUTES * 60:
        raise RuntimeError("execution_outside_window")
    if current < slot_dt - timedelta(minutes=WINDOW_MINUTES):
        raise RuntimeError("scheduled_slot_in_future")
    return slot_dt.strftime("%Y-%m-%dT%H:%M:%S%z")


def bundle_message(signals: list[Signal]) -> str:
    if len(signals) != 2 or {s.asset for s in signals} != set(ASSETS):
        raise RuntimeError("both_assets_required")
    blocks = [format_message(signal) for signal in signals]
    return "<b>Smart Money AI · sinais oficiais</b>\n\n" + "\n\n".join(blocks)


def prepare_public_signals(factory: Callable[[str, str], Signal | None] = existing_motor_factory) -> list[Signal]:
    signals = [factory(asset, TIMEFRAME) for asset in ASSETS]
    if any(signal is None for signal in signals):
        raise RuntimeError("signal_generation_failed")
    return [signal for signal in signals if signal is not None]


def publish_public(
    scheduled_at: datetime,
    *,
    now: datetime | None = None,
    factory: Callable[[str, str], Signal | None] = existing_motor_factory,
    ledger: SupabaseLedger | None = None,
    send: Callable[[str], int] | None = None,
) -> int | None:
    if os.getenv("TELEGRAM_DRY_RUN", "true").strip().lower() == "true":
        prepare_public_signals(factory)
        return None
    if os.getenv("PUBLIC_PUBLISH_AUTHORIZED", "") != "PUBLIC_PUBLISH_AUTHORIZED":
        raise RuntimeError("public_publish_not_authorized")
    if os.getenv("TELEGRAM_CHAT_ID", "").strip() != CHANNEL:
        raise RuntimeError("public_destination_invalid")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("bot_token_missing")
    if ledger is None:
        ledger = ledger_from_env()
    if ledger is None or send is None:
        raise RuntimeError("public_dependencies_missing")
    stamp = validate_scheduled_slot(scheduled_at, now=now)
    execution_key = f"public|{CHANNEL}|{stamp}|{TIMEFRAME}"
    owner_id = os.getenv("GITHUB_RUN_ID", "local-public")
    if not ledger.reserve(execution_key, channel=CHANNEL, assets=list(ASSETS), timeframe=TIMEFRAME, scheduled_at=stamp, owner_id=owner_id):
        raise RuntimeError("duplicate_public_publication")
    ledger.mark(execution_key, "sending", owner_id=owner_id)
    try:
        message_id = send(bundle_message(prepare_public_signals(factory)))
    except Exception:
        ledger.mark(execution_key, "unknown", owner_id=owner_id)
        raise
    ledger.mark(execution_key, "sent", message_id, owner_id=owner_id)
    return message_id
