"""Pure validation and mode separation for the admin signal center."""
from .admin_center import CRYPTO_ASSETS, validate_two_assets

def validate_config(auto_assets, manual_assets, timeframe, *, emergency_stop=True):
    a = validate_two_assets(auto_assets, CRYPTO_ASSETS); m = validate_two_assets(manual_assets, CRYPTO_ASSETS)
    if timeframe != "5m": raise ValueError("timeframe_not_homologated")
    return {"auto_assets": a, "manual_assets": m, "timeframe": timeframe, "auto_enabled": False if emergency_stop else True, "emergency_stop": emergency_stop}

def manual_preview(factory, assets, timeframe="5m"):
    chosen = validate_two_assets(assets, CRYPTO_ASSETS); signals = [factory(asset, timeframe) for asset in chosen]
    if any(x is None for x in signals): raise RuntimeError("signal_generation_failed")
    return tuple(signals)
