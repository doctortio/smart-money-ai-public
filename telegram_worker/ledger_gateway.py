"""HMAC gateway client; no service_role key is ever used by this client."""
from __future__ import annotations
import hashlib, hmac, time, uuid, requests

class HmacLedgerGateway:
    def __init__(self, url: str, secret: str, *, timeout: float = 15.0):
        if not url or not secret: raise ValueError("ledger_gateway_config_missing")
        self.url, self.secret, self.timeout = url.rstrip("/"), secret, timeout
    def _call(self, body: dict) -> bool:
        body = {**body, "method": "POST", "path": "/functions/v1/sma-telegram-ledger", "timestamp": int(time.time()), "nonce": str(uuid.uuid4())}
        def value(k):
            v = body.get(k, "")
            return "" if v is None else str(v)
        canonical = ".".join(value(k) for k in ("method","path","timestamp","nonce","operation","execution_key","channel","scheduled_at","owner_id","status","message_id")) + "." + __import__("json").dumps(body.get("assets", []), separators=(",", ":")) + "." + value("timeframe")
        body["signature"] = hmac.new(self.secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        r = requests.post(self.url, json=body, timeout=self.timeout); r.raise_for_status(); data = r.json()
        if not data.get("ok"): raise RuntimeError(str(data.get("error", "ledger_rejected")))
        return True
    def reserve(self, execution_key: str, *, channel: str, assets: list[str], timeframe: str, scheduled_at: str, owner_id: str) -> bool:
        return self._call(dict(operation="reserve", execution_key=execution_key, channel=channel, assets=assets, timeframe=timeframe, scheduled_at=scheduled_at, owner_id=owner_id))
    def mark(self, execution_key: str, status: str, message_id: int | None = None, *, channel: str="@smartmoneyaioficial", scheduled_at: str="", owner_id: str="") -> bool:
        return self._call(dict(operation="mark", execution_key=execution_key, channel=channel, scheduled_at=scheduled_at, owner_id=owner_id, status=status, message_id=message_id))

def ledger_from_env():
    import os
    url, secret = os.getenv("TELEGRAM_LEDGER_GATEWAY_URL", ""), os.getenv("TELEGRAM_LEDGER_HMAC_SECRET", "")
    return HmacLedgerGateway(url, secret) if url and secret else None
