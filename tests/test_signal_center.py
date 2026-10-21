import pytest
from telegram_worker.admin_center import build_signal_center_config, validate_two_assets

def test_exactly_two_assets_and_emergency_stop():
    cfg = build_signal_center_config(["BTCUSDT", "ETHUSDT"], ["BNBUSDT", "SOLUSDT"], "5m", True, True)
    assert cfg["auto_enabled"] is False and cfg["timezone"] == "America/Sao_Paulo"

@pytest.mark.parametrize("items", [["BTCUSDT"], ["BTCUSDT", "BTCUSDT"], ["EUR/USD", "BTCUSDT"]])
def test_invalid_asset_selection(items):
    with pytest.raises(ValueError): validate_two_assets(items)
