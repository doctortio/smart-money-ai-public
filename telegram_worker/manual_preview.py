"""Confirmed manual-preview lifecycle; transport is injected by the host backend."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import uuid
from .signal_center import manual_preview

@dataclass(frozen=True)
class Preview:
    preview_id: str
    admin_id: str
    assets: tuple
    timeframe: str
    signals: tuple
    expires_at: datetime
    confirmed: bool = False

class PreviewStore:
    def __init__(self): self._items = {}
    def create(self, admin_id, factory, assets, timeframe="5m", ttl_seconds=300):
        p=Preview(str(uuid.uuid4()), str(admin_id), tuple(assets), timeframe, manual_preview(factory, assets, timeframe), datetime.now(timezone.utc)+timedelta(seconds=ttl_seconds))
        self._items[p.preview_id]=p; return p
    def confirm(self, preview_id, admin_id):
        p=self._items.get(preview_id)
        if not p or p.admin_id != str(admin_id) or p.expires_at <= datetime.now(timezone.utc) or p.confirmed: raise RuntimeError("preview_invalid_or_already_confirmed")
        p=Preview(p.preview_id,p.admin_id,p.assets,p.timeframe,p.signals,p.expires_at,True); self._items[preview_id]=p; return p
