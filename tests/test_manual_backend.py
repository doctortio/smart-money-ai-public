import pytest
from telegram_worker.manual_backend import create_manual_preview, confirm_manual_preview
from telegram_worker.worker import Signal

ADMIN = {"authenticated": True, "is_admin": True, "admin_id": 1, "email": "admin@test.invalid"}
def factory(asset, timeframe):
    return Signal(asset, "COMPRA", timeframe, "test", "MEDIO", "now")
class Rpc:
    def __init__(self):
        self.calls = []
    def __call__(self, name, args):
        self.calls.append((name, args))
        return args["p_preview_id"] if name.endswith("create_preview") else args["p_request_id"]

def test_preview_persists_only_two_supported_assets():
    rpc = Rpc()
    p = create_manual_preview(ADMIN, rpc, factory, ["BTCUSDT", "ETHUSDT"])
    assert len(p["signals"]) == 2
    assert rpc.calls[0][0] == "sma_signal_manual_create_preview"
    assert len(rpc.calls[0][1]["p_signals_sha256"]) == 64

def test_confirm_never_publishes():
    rpc = Rpc()
    p = create_manual_preview(ADMIN, rpc, factory, ["BTCUSDT", "ETHUSDT"])
    result = confirm_manual_preview(ADMIN, rpc, p["preview_id"])
    assert result["status"] == "queued" and result["published"] is False
    assert rpc.calls[-1][0] == "sma_signal_manual_confirm_preview"

def test_no_admin_session_fails_closed():
    rpc = Rpc()
    with pytest.raises(PermissionError):
        create_manual_preview({"authenticated": False, "is_admin": True, "admin_id": 1, "email": "admin@test.invalid"}, rpc, factory, ["BTCUSDT", "ETHUSDT"])
    assert not rpc.calls

def test_unsupported_assets_fail_before_rpc():
    rpc = Rpc()
    with pytest.raises(ValueError):
        create_manual_preview(ADMIN, rpc, factory, ["BTCUSDT", "SOLUSDT"])
    assert not rpc.calls

def test_bad_confirmation_id_fails_before_rpc():
    rpc = Rpc()
    with pytest.raises(ValueError):
        confirm_manual_preview(ADMIN, rpc, "not-a-uuid")
    assert not rpc.calls

def test_rpc_failure_never_claims_success():
    def rpc(name, args):
        raise RuntimeError("backend_unavailable")
    with pytest.raises(RuntimeError, match="backend_unavailable"):
        create_manual_preview(ADMIN, rpc, factory, ["BTCUSDT", "ETHUSDT"])
