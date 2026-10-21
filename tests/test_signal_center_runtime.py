import pytest
from telegram_worker.signal_center_runtime import validate_persisted_config, automatic_allowed, manual_allowed

def config(**kw):
    return {"auto_assets":["BTCUSDT","ETHUSDT"],"manual_assets":["BNBUSDT","SOLUSDT"],"timeframe":"5m","slots":["09:00","14:00","20:00"],"auto_enabled":True,"emergency_stop":False,**kw}

def test_automatic_and_manual_are_independent():
    assert automatic_allowed(config(), "09:00") and manual_allowed(config(auto_enabled=False))

def test_emergency_stop_blocks_both():
    assert not automatic_allowed(config(emergency_stop=True), "09:00") and not manual_allowed(config(emergency_stop=True))

def test_automatic_rejects_unauthorized_slot():
    assert not automatic_allowed(config(), "11:00")

def test_unapproved_assets_and_timeframes_fail():
    with pytest.raises(ValueError): validate_persisted_config(config(auto_assets=["EUR/USD","GBP/USD"]))
    with pytest.raises(ValueError): validate_persisted_config(config(timeframe="1h"))
