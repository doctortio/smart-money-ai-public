"""Runtime policy shared by automatic and manual execution paths."""
from __future__ import annotations
from datetime import datetime
from .admin_center import AUTO_SLOTS, CRYPTO_ASSETS, validate_two_assets

def validate_persisted_config(config: dict) -> dict:
    auto = validate_two_assets(config.get("auto_assets", []), CRYPTO_ASSETS)
    manual = validate_two_assets(config.get("manual_assets", []), CRYPTO_ASSETS)
    if config.get("timeframe") != "5m": raise ValueError("timeframe_not_homologated")
    slots = tuple(config.get("slots", AUTO_SLOTS))
    if any(s not in AUTO_SLOTS for s in slots): raise ValueError("slot_not_homologated")
    return {"auto_assets": auto, "manual_assets": manual, "timeframe": "5m", "slots": slots,
            "auto_enabled": bool(config.get("auto_enabled", False)), "emergency_stop": bool(config.get("emergency_stop", True))}

def automatic_allowed(config: dict, scheduled_slot: str) -> bool:
    c = validate_persisted_config(config)
    return c["auto_enabled"] and not c["emergency_stop"] and scheduled_slot in c["slots"]

def manual_allowed(config: dict) -> bool:
    return not validate_persisted_config(config)["emergency_stop"]
