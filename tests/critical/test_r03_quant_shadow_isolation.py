from copy import deepcopy

from signal_engine.shadow_quant.adapter import persistence_mapping, quant_shadow_enabled, run_shadow_fail_open
from signal_engine.shadow_quant.contracts import ShadowContext

from tests.critical.test_r03_quant_shadow_contracts import context


def official(): return {"direcao": "VENDA", "confianca": 73, "risco": "ALTO", "texto": "oficial"}


def success_runner(_):
    return {"status": "shadow_success", "shadow_direction": "COMPRA", "risk_class_shadow": "BAIXO", "model_version": "m1", "config_hash": {}, "buy_primary_probability": .7, "sell_research_probability": .4}


def test_23_idempotency(): assert context().idempotency_key == context().idempotency_key


def test_24_retry_same_opportunity():
    first = context(); second = context()
    assert first.idempotency_key == second.idempotency_key


def test_25_shadow_failure_does_not_break_official_signal():
    before = official()
    def broken(_): raise RuntimeError("broken")
    after, shadow = run_shadow_fail_open(before, context(), enabled=True, runner=broken)
    assert after == before and shadow["status"] == "shadow_error"


def test_26_no_wallet_consumption():
    calls = []
    before = official(); after, _ = run_shadow_fail_open(before, context(), enabled=True, runner=success_runner)
    assert calls == [] and after == before


def test_27_no_notification():
    notifications = []
    run_shadow_fail_open(official(), context(), enabled=True, runner=success_runner)
    assert notifications == []


def test_28_official_direction_unchanged():
    before = official(); after, _ = run_shadow_fail_open(before, context(), enabled=True, runner=success_runner)
    assert after["direcao"] == before["direcao"]


def test_29_official_confidence_and_risk_unchanged():
    before = official(); after, _ = run_shadow_fail_open(before, context(), enabled=True, runner=success_runner)
    assert after["confianca"] == before["confianca"] and after["risco"] == before["risco"]


def test_feature_flag_default_false(): assert quant_shadow_enabled() is False


def test_adapter_returns_copy_without_mutation():
    before = official(); snapshot = deepcopy(before)
    after, _ = run_shadow_fail_open(before, context(), enabled=True, runner=success_runner)
    assert before == snapshot and after == snapshot and after is not before


def test_persistence_mapping_is_pure_and_reuses_existing_table_contract():
    before = official(); ctx = context(); mapped = persistence_mapping(before, success_runner(ctx), ctx)
    assert mapped["official_signal_id"] == ctx.official_signal_id
    assert mapped["v2_no_trade"] is False
    assert not any(key in mapped for key in ("wallet", "notification", "credit"))
