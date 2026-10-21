import pytest
from telegram_worker.signal_center import validate_config, manual_preview
from telegram_worker.worker import Signal

def test_modes_are_independent_and_stop_fails_closed():
    c=validate_config(["BTCUSDT","ETHUSDT"],["BNBUSDT","SOLUSDT"],"5m", emergency_stop=True)
    assert c["auto_enabled"] is False and c["auto_assets"] != c["manual_assets"]

def test_preview_uses_real_factory_contract():
    out=manual_preview(lambda a,t: Signal(a,"COMPRA",t,"ctx","MEDIO","now"),["BTCUSDT","ETHUSDT"])
    assert [x.asset for x in out] == ["BTCUSDT","ETHUSDT"]

def test_forex_and_unapproved_timeframe_blocked():
    with pytest.raises(ValueError): validate_config(["EUR/USD","GBP/USD"],["BTCUSDT","ETHUSDT"],"5m")
    with pytest.raises(ValueError): validate_config(["BTCUSDT","ETHUSDT"],["BNBUSDT","SOLUSDT"],"1h")
