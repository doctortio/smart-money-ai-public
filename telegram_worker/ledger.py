"""Supabase ledger client for the Telegram worker (server-side only)."""
from __future__ import annotations

import requests


class SupabaseLedger:
    def __init__(self, url: str, service_role_key: str, *, timeout: float = 15.0):
        self.url = url.rstrip("/")
        self.key = service_role_key
        self.timeout = timeout

    def _rpc(self, name: str, payload: dict):
        response = requests.post(
            f"{self.url}/rest/v1/rpc/{name}",
            headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def reserve(self, execution_key: str, *, channel: str, assets: list[str], timeframe: str, scheduled_at: str, owner_id: str) -> bool:
        return bool(self._rpc("sma_telegram_reserve_publication", {
            "p_execution_key": execution_key, "p_channel": channel, "p_assets": assets,
            "p_timeframe": timeframe, "p_scheduled_at": scheduled_at, "p_owner_id": owner_id,
        }))

    def mark(self, execution_key: str, status: str, message_id: int | None = None) -> bool:
        return bool(self._rpc("sma_telegram_mark_publication", {
            "p_execution_key": execution_key, "p_status": status, "p_message_id": message_id,
        }))
