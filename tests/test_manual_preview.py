import pytest
from telegram_worker.manual_preview import PreviewStore
from telegram_worker.worker import Signal

def factory(a,t): return Signal(a,"COMPRA",t,"ctx","MEDIO","now")
def test_preview_confirmed_once_without_recalculation():
    s=PreviewStore(); p=s.create("admin-1",factory,["BTCUSDT","ETHUSDT"]); q=s.confirm(p.preview_id,"admin-1")
    assert q.signals == p.signals
    with pytest.raises(RuntimeError): s.confirm(p.preview_id,"admin-1")
def test_preview_cannot_be_confirmed_by_other_admin():
    s=PreviewStore(); p=s.create("admin-1",factory,["BTCUSDT","ETHUSDT"])
    with pytest.raises(RuntimeError): s.confirm(p.preview_id,"admin-2")
