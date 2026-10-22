"""Fail-closed admin adapter for persisted manual previews (no Telegram transport).

The caller must provide an authenticated server-side admin session and a
server-side service-role RPC callable. Never expose the RPC callable to a browser.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from .admin_center import validate_two_assets

def _identity(session):
    if not isinstance(session, dict) or not session.get("authenticated") or not session.get("is_admin"):
        raise PermissionError("authenticated_admin_required")
    admin_id = session.get("admin_id")
    email = session.get("email")
    if type(admin_id) is not int or admin_id <= 0 or not isinstance(email, str) or "@" not in email:
        raise PermissionError("invalid_admin_identity")
    return admin_id, email

def _rpc_result(rpc, name, args):
    result = rpc(name, args)
    if result is None:
        raise RuntimeError("rpc_result_missing")
    return result

def create_manual_preview(session, rpc, signal_factory, assets):
    admin_id, email = _identity(session)
    chosen = validate_two_assets(assets, ("BTCUSDT", "ETHUSDT"))
    signals = [signal_factory(asset, "5m") for asset in chosen]
    if any(s is None for s in signals):
        raise RuntimeError("signal_generation_failed")
    def payload(s):
        return {"asset": s.asset, "direction": s.direction, "timeframe": s.timeframe,
                "context": s.context, "risk": s.risk, "created_at": s.created_at}
    data = [payload(s) for s in signals]
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    preview_id = str(uuid.uuid4())
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    saved = _rpc_result(rpc, "sma_signal_manual_create_preview", {
        "p_preview_id": preview_id, "p_admin_id": admin_id, "p_admin_email": email,
        "p_assets": list(chosen), "p_timeframe": "5m", "p_signals": data,
        "p_signals_sha256": digest, "p_expires_at": expiry})
    if str(saved) != preview_id:
        raise RuntimeError("preview_persistence_mismatch")
    return {"preview_id": preview_id, "assets": list(chosen), "signals": data, "expires_at": expiry}

def confirm_manual_preview(session, rpc, preview_id):
    admin_id, email = _identity(session)
    # No client-provided signals, digest, owner, or execution key accepted.
    preview = str(uuid.UUID(str(preview_id)))
    request_id = str(uuid.uuid4())
    execution_key = "manual:" + preview
    saved = _rpc_result(rpc, "sma_signal_manual_confirm_preview", {
        "p_preview_id": preview, "p_admin_id": admin_id, "p_admin_email": email,
        "p_request_id": request_id, "p_execution_key": execution_key})
    if str(saved) != request_id:
        raise RuntimeError("confirmation_persistence_mismatch")
    return {"request_id": request_id, "status": "queued", "published": False}
