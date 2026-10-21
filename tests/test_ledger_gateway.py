import hashlib, hmac, json
from telegram_worker.ledger_gateway import HmacLedgerGateway

def test_gateway_signs_and_uses_single_request(monkeypatch):
    seen = {}
    class R:
        def raise_for_status(self): pass
        def json(self): return {"ok": True}
    def post(url, json, timeout): seen.update(json); return R()
    monkeypatch.setattr("telegram_worker.ledger_gateway.requests.post", post)
    g = HmacLedgerGateway("https://example.invalid/ledger", "unit-test-secret")
    assert g.reserve("e", channel="@smartmoneyaioficial", assets=["BTCUSDT","ETHUSDT"], timeframe="5m", scheduled_at="s", owner_id="o")
    assert seen["channel"] == "@smartmoneyaioficial" and len(seen["signature"]) == 64
