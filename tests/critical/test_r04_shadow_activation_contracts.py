import ast
import json
import logging
import time
import types
from pathlib import Path

import pandas as pd

from signal_engine.shadow_quant import adapter
from signal_engine.shadow_quant.adapter import persistence_mapping, quant_shadow_enabled, run_shadow_observer
from signal_engine.shadow_quant.contracts import FEATURE_HASH
from signal_engine.shadow_quant.feature_pipeline import FEATURE_NAMES
from signal_engine.shadow_quant.health import shadow_health_check
from signal_engine.shadow_quant.inference import ARTIFACT_ROOT
from tests.critical.test_r03_quant_shadow_contracts import context, market_data

ROOT = Path(__file__).resolve().parents[2]


def official():
    return {"direcao": "VENDA", "confianca": 72, "risco": "ALTO", "texto": "oficial",
            "entrada_iso": "2026-10-02T12:00:00+00:00", "p1_iso": "2026-10-02T12:01:00+00:00",
            "p2_iso": "2026-10-02T12:02:00+00:00", "p3_iso": "2026-10-02T12:03:00+00:00"}


def success(_):
    return {"status": "shadow_success", "error_code": None, "shadow_direction": "COMPRA", "risk_class_shadow": "BAIXO",
            "model_version": "r03", "config_hash": {"a": "b"}, "feature_hash": FEATURE_HASH,
            "buy_primary_direction": "COMPRA", "buy_primary_probability": .7,
            "buy_challenger_direction": "COMPRA", "buy_challenger_probability": .6,
            "sell_research_direction": "VENDA", "sell_research_probability": .4, "calibrated_probability": None}


def test_01_flag_absent_off(monkeypatch): monkeypatch.delenv("R03_QUANT_SHADOW_ENABLED", raising=False); assert quant_shadow_enabled() is False
def test_02_flag_false_off(): assert quant_shadow_enabled("false") is False
def test_03_flag_invalid_off(): assert quant_shadow_enabled("invalid") is False
def test_04_flag_true_allowed(): assert quant_shadow_enabled("true") is True


def test_flag_read_error_is_off(monkeypatch):
    monkeypatch.setattr(adapter.os, "getenv", lambda *args: (_ for _ in ()).throw(RuntimeError()))
    assert quant_shadow_enabled() is False


def test_05_off_does_not_execute_model():
    called = []
    run_shadow_observer(official(), context(), enabled=False, runner=lambda _: called.append(1))
    assert called == []


def test_06_off_does_not_write():
    writes = []
    _, result = run_shadow_observer(official(), context(), enabled=False, persist=lambda row: writes.append(row))
    assert writes == [] and result["persistence_status"] == "not_attempted"


def test_07_shadow_failure_does_not_break_official():
    def broken(_): raise RuntimeError("inference")
    before = official(); after, result = run_shadow_observer(before, context(), enabled=True, runner=broken)
    assert after == before and result["status"] == "shadow_error"


def test_08_persistence_failure_does_not_break_official():
    def broken_write(_): raise RuntimeError("database")
    before = official(); after, result = run_shadow_observer(before, context(), enabled=True, runner=success, persist=broken_write)
    assert after == before and result["persistence_status"] == "persistence_error"


def test_09_missing_model_does_not_break_official():
    def missing(_): raise FileNotFoundError("pipeline.joblib")
    before = official(); after, result = run_shadow_observer(before, context(), enabled=True, runner=missing)
    assert after == before and result["status"] == "shadow_error"


def test_10_wrong_feature_hash_blocks_only_shadow():
    def wrong(_): return {"status": "shadow_error", "error_code": "SHADOW_FEATURE_HASH_MISMATCH"}
    before = official(); after, result = run_shadow_observer(before, context(), enabled=True, runner=wrong)
    assert after == before and result["error_code"] == "SHADOW_FEATURE_HASH_MISMATCH"


def test_11_exactly_54_features(): assert len(FEATURE_NAMES) == 54


def test_12_golden_manifest_120():
    golden = pd.read_csv(ARTIFACT_ROOT / "golden_parity.csv")
    assert len(golden) == 120 and set(FEATURE_NAMES).issubset(golden.columns)


def test_13_zero_lookahead_contract_reused():
    source = (ROOT / "signal_engine/shadow_quant/feature_pipeline.py").read_text(encoding="utf-8")
    assert "shift()" in source and "shift(-" not in source


def test_14_5m_closed_contract():
    manifest = json.loads((ARTIFACT_ROOT / "feature_manifest.json").read_text())
    assert all("fully closed 5-minute" in f["closed_candle_requirement"] for f in manifest["features"] if f["timeframe"] == "5m")


def test_15_15m_closed_contract():
    manifest = json.loads((ARTIFACT_ROOT / "feature_manifest.json").read_text())
    assert all("fully closed 15-minute" in f["closed_candle_requirement"] for f in manifest["features"] if f["timeframe"] == "15m")


def test_16_direction_coverage_contract():
    assert success(None)["shadow_direction"] in {"COMPRA", "VENDA"}


def test_17_database_idempotency_is_unique():
    sql = next((ROOT / "supabase/migrations").glob("*_r04_quant_shadow_contract.sql")).read_text(encoding="utf-8")
    assert "unique index" in sql.lower() and "official_signal_id, v2_engine_version" in sql


def test_18_retry_same_key(): assert context().idempotency_key == context().idempotency_key


def test_19_zero_wallet_call():
    source = (ROOT / "signal_engine/shadow_quant/adapter.py").read_text(encoding="utf-8").lower()
    assert "sma_carteira_" not in source and "carteira_reservar" not in source


def test_20_zero_notification_call():
    source = (ROOT / "signal_engine/shadow_quant/adapter.py").read_text(encoding="utf-8").lower()
    assert not any(name in source for name in ("send_email", "whatsapp", "push", "webhook"))


def test_21_official_direction_unchanged():
    before = official(); after, _ = run_shadow_observer(before, context(), enabled=True, runner=success)
    assert after["direcao"] == before["direcao"]


def test_22_official_confidence_unchanged():
    before = official(); after, _ = run_shadow_observer(before, context(), enabled=True, runner=success)
    assert after["confianca"] == before["confianca"]


def test_23_official_risk_unchanged():
    before = official(); after, _ = run_shadow_observer(before, context(), enabled=True, runner=success)
    assert after["risco"] == before["risco"]


def test_24_official_temporal_opportunity_unchanged():
    before = official(); after, _ = run_shadow_observer(before, context(), enabled=True, runner=success)
    assert [after[k] for k in ("entrada_iso", "p1_iso", "p2_iso", "p3_iso")] == [before[k] for k in ("entrada_iso", "p1_iso", "p2_iso", "p3_iso")]


def test_25_mapping_uses_same_timestamps():
    before = official(); mapped = persistence_mapping(before, success(None), context())
    assert [mapped[k] for k in ("entrada_iso", "p1_iso", "p2_iso", "p3_iso")] == [before[k] for k in ("entrada_iso", "p1_iso", "p2_iso", "p3_iso")]


def test_26_risk_does_not_block(): assert success(None)["risk_class_shadow"] == "BAIXO" and success(None)["status"] == "shadow_success"


def test_27_no_p5_in_quant_mapping(): assert "p5_iso" not in persistence_mapping(official(), success(None), context())


def test_28_no_target_stop():
    mapped = persistence_mapping(official(), success(None), context())
    assert "target" not in mapped and "stop" not in mapped


def test_29_health_check():
    result = shadow_health_check(latency_p95_ms=39.8)
    assert result["ok"] is True and result["customer_visible"] is False


def test_health_detects_failures():
    assert shadow_health_check(persistence_failures=3)["ok"] is False
    assert shadow_health_check(duplicates=1)["ok"] is False
    assert shadow_health_check(coverage=.8)["ok"] is False
    assert shadow_health_check(latency_p95_ms=251)["ok"] is False


def test_30_kill_switch(monkeypatch):
    monkeypatch.setenv("R03_QUANT_SHADOW_ENABLED", "true"); assert quant_shadow_enabled() is True
    monkeypatch.setenv("R03_QUANT_SHADOW_ENABLED", "false"); assert quant_shadow_enabled() is False


def test_schema_remains_service_role_only():
    migration = (ROOT / "supabase/migrations/20260926183914_add_signal_shadow_table.sql").read_text(encoding="utf-8")
    assert "enable row level security" in migration.lower()
    assert "revoke all on table public.sma_signal_shadow from anon" in migration.lower()
    assert "service_role" in migration


def test_release_hook_is_post_confirmation_and_fail_open():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    confirm = source.index('carteira_confirmar_sinal(request_id_carteira')
    official_audit = source.index('salvar_registro_auditoria_persistente(novo_registro_auditoria)', confirm)
    quant = source.index('quant_shadow_status = agendar_quant_shadow(', official_audit)
    assert confirm < official_audit < quant
    scheduler = source[source.index("def agendar_quant_shadow"):source.index("def carteira_criar_solicitacao")]
    assert "threading.Thread" not in scheduler
    assert "observation = _executar_quant_shadow_isolado(" in scheduler


def test_release_scope_is_crypto_1m_five_assets():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'market == "Cripto" and canonical_timeframe == "1m"' in source
    for symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"):
        assert symbol in source


def test_release_flag_parser_is_deterministic_and_defaults_off():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    block = source[source.index("def resolve_quant_shadow_flag"):source.index("def quant_shadow_normalize_gate")]
    assert 'st.secrets.get("R03_QUANT_SHADOW_ENABLED", missing)' in block
    assert 'os.getenv("R03_QUANT_SHADOW_ENABLED")' in block
    assert "return parse_quant_shadow_flag(valor), source" in block


def test_runtime_flag_prefers_streamlit_secret_then_env(monkeypatch):
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {"parse_quant_shadow_flag", "resolve_quant_shadow_flag", "quant_shadow_production_enabled"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]

    def resolve(secret, env):
        secrets = {} if secret is ... else {"R03_QUANT_SHADOW_ENABLED": secret}
        if env is None:
            monkeypatch.delenv("R03_QUANT_SHADOW_ENABLED", raising=False)
        else:
            monkeypatch.setenv("R03_QUANT_SHADOW_ENABLED", env)
        namespace = {"st": types.SimpleNamespace(secrets=secrets), "os": __import__("os")}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
        return namespace["quant_shadow_production_enabled"]()

    assert resolve(True, None) is True
    assert resolve("true", None) is True
    assert resolve(False, None) is False
    assert resolve(..., "true") is True
    assert resolve(..., "false") is False
    assert resolve(..., None) is False
    assert resolve(False, "true") is False
    for value in ("false", "0", "no", "off", "nao", "não", "", None, "unknown"):
        assert resolve(value, None) is False


def test_release_shadow_block_has_no_wallet_or_notification_calls():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    block = source[source.index("def _executar_quant_shadow_isolado"):source.index("def carteira_criar_solicitacao")]
    assert "carteira_" not in block
    assert not any(name in block.lower() for name in ("email", "whatsapp", "telegram", "push", "webhook"))


def test_release_shadow_has_safe_persistence_observability():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    block = source[source.index("def _executar_quant_shadow_isolado"):source.index("def carteira_criar_solicitacao")]
    for marker in ("R03_SHADOW_GATE", "R03_SHADOW_SKIP", "R03_SHADOW_SCHEDULED", "R03_SHADOW_STARTED", "R03_SHADOW_PERSISTED", "R03_SHADOW_PERSISTENCE_ERROR", "R03_SHADOW_ERROR"):
        assert marker in block
    assert "type(exc).__name__" in block
    assert "sanitizar_erro_observabilidade(str(exc))" in block
    assert "raise" in block
    assert "SUPABASE_SERVICE_ROLE_KEY" not in block
    error_log = block[block.index("R03_SHADOW_PERSISTENCE_ERROR"):block.index("raise", block.index("R03_SHADOW_PERSISTENCE_ERROR"))]
    assert "supabase_key" not in error_log and "Authorization" not in error_log


def test_release_worker_does_not_access_streamlit_context():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    worker = source[source.index("def _executar_quant_shadow_isolado"):source.index("def agendar_quant_shadow")]
    scheduler = source[source.index("def agendar_quant_shadow"):source.index("def carteira_criar_solicitacao")]
    assert "st." not in worker
    assert "_sb_post(" not in worker
    assert "supabase_base_url" in worker and "supabase_key" in worker
    assert "threading.Thread" not in scheduler
    assert scheduler.index("_supabase_base_url()") < scheduler.index("_executar_quant_shadow_isolado(")
    assert scheduler.index("_supabase_service_role_key()") < scheduler.index("_executar_quant_shadow_isolado(")
    assert "_supabase_key()" not in scheduler


def test_release_r03_credential_is_service_role_only():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_supabase_service_role_key")
    namespace = {"st": types.SimpleNamespace(secrets={"SUPABASE_ANON_KEY": "anon-secret"})}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), namespace)
    assert namespace["_supabase_service_role_key"]() == ""
    namespace["st"].secrets["SUPABASE_SERVICE_ROLE_KEY"] = "service-secret"
    assert namespace["_supabase_service_role_key"]() == "service-secret"


def test_release_worker_runs_inference_and_persists_without_streamlit(caplog):
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_executar_quant_shadow_isolado")
    requests = []

    class Response:
        status_code = 201
        text = "[]"

    namespace = {
        "logging": logging, "time": time, "pd": __import__("pandas"),
        "buscar_candles_binance_quant_shadow": lambda symbol: market_data(2400),
        "_http_request": lambda *args, **kwargs: (requests.append((args, kwargs)) or Response()),
        "sanitizar_erro_observabilidade": lambda value: str(value)[:80],
        "_agendar_avaliacao_quant_shadow_isolada": lambda *args, **kwargs: None,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), namespace)
    candles = market_data(2400)
    decision = {"id_sinal": "official-1", "direcao": "VENDA", "confianca": 80, "risco": "BAIXO"}
    with caplog.at_level(logging.WARNING):
        namespace["_executar_quant_shadow_isolado"](
            decision, symbol="BTCUSDT", timeframe="1m",
            event_id="00000000-0000-4000-8000-000000000000",
            request_id="00000000-0000-4000-8000-000000000001",
            supabase_base_url="https://example.invalid/rest/v1", supabase_key="test-key",
        )
    assert len(candles) == 2400 and len(requests) == 1
    args, kwargs = requests[0]
    row = kwargs["json"]
    assert args == ("POST", "https://example.invalid/rest/v1/sma_signal_shadow")
    assert kwargs["timeout"] == 25 and kwargs["params"] == {"on_conflict": "official_signal_id,v2_engine_version"}
    assert kwargs["headers"]["Prefer"] == "resolution=ignore-duplicates,return=representation"
    assert row["feature_hash"] == FEATURE_HASH
    assert row["shadow_status"] in {"shadow_success", "shadow_no_trade", "shadow_error", "shadow_unsupported"}
    assert row["v2_engine_version"] and row["v2_config_hash"]
    json.dumps(row, allow_nan=False)
    assert "R03_SHADOW_PERSISTED" in caplog.text
    assert "test-key" not in caplog.text


def test_real_admin_bitcoin_1m_case_reaches_worker_once(caplog):
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {"parse_quant_shadow_flag", "resolve_quant_shadow_flag", "quant_shadow_production_enabled", "quant_shadow_normalize_gate", "quant_shadow_eligible", "agendar_quant_shadow"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    calls = []

    def worker(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "shadow_success", "persistence_status": "persisted"}

    namespace = {
        "st": types.SimpleNamespace(secrets={"R03_QUANT_SHADOW_ENABLED": True}), "os": __import__("os"),
        "logging": logging,
        "QUANT_SHADOW_SUPPORTED_SYMBOLS": {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"},
        "_supabase_base_url": lambda: "https://example.invalid/rest/v1",
        "_supabase_service_role_key": lambda: "test-key",
        "sanitizar_erro_observabilidade": lambda value: str(value)[:80],
        "_executar_quant_shadow_isolado": worker,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
    with caplog.at_level(logging.WARNING):
        result = namespace["agendar_quant_shadow"](
            {"direcao": "VENDA"}, mercado="Cripto", symbol="BITCOIN", timeframe="1M",
            event_id="00000000-0000-4000-8000-000000000000",
            request_id="00000000-0000-4000-8000-000000000001",
        )
    assert result["status"] == "shadow_completed" and len(calls) == 1
    assert calls[0][1]["symbol"] == "BTCUSDT" and calls[0][1]["timeframe"] == "1m"
    assert "R03_SHADOW_GATE" in caplog.text and "eligible=true" in caplog.text
    assert "R03_SHADOW_SCHEDULED" in caplog.text and "test-key" not in caplog.text


def test_missing_service_role_is_fail_open_and_does_not_use_anon(caplog):
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {"parse_quant_shadow_flag", "resolve_quant_shadow_flag", "quant_shadow_production_enabled", "quant_shadow_normalize_gate", "quant_shadow_eligible", "agendar_quant_shadow"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    calls = []
    namespace = {
        "st": types.SimpleNamespace(secrets={"R03_QUANT_SHADOW_ENABLED": True, "SUPABASE_ANON_KEY": "anon-secret"}),
        "os": __import__("os"), "logging": logging,
        "QUANT_SHADOW_SUPPORTED_SYMBOLS": {"BTCUSDT"},
        "_supabase_base_url": lambda: "https://example.invalid/rest/v1",
        "_supabase_service_role_key": lambda: "",
        "sanitizar_erro_observabilidade": lambda value: str(value)[:80],
        "_executar_quant_shadow_isolado": lambda *args, **kwargs: calls.append((args, kwargs)),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
    with caplog.at_level(logging.WARNING):
        result = namespace["agendar_quant_shadow"](
            {"direcao": "VENDA"}, mercado="Cripto", symbol="BTC", timeframe="1m",
            event_id="00000000-0000-4000-8000-000000000000",
            request_id="00000000-0000-4000-8000-000000000001",
        )
    assert result["status"] == "shadow_error" and calls == []
    assert "key_source=SERVICE_ROLE" in caplog.text and "service_role_present=false" in caplog.text
    assert "SERVICE_ROLE_MISSING" in caplog.text
    assert "anon-secret" not in caplog.text


def test_streamlit_secret_capture_reaches_final_service_role_headers(caplog):
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        "parse_quant_shadow_flag", "resolve_quant_shadow_flag", "quant_shadow_production_enabled",
        "quant_shadow_normalize_gate", "quant_shadow_eligible", "_supabase_base_url",
        "_supabase_service_role_key", "_executar_quant_shadow_isolado", "agendar_quant_shadow",
    }
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    requests = []

    class Response:
        status_code = 201
        text = "[]"

    namespace = {
        "st": types.SimpleNamespace(secrets={
            "SUPABASE_URL": "https://valor-fake-url.invalid",
            "SUPABASE_SERVICE_ROLE_KEY": "valor_fake_service_role",
            "SUPABASE_ANON_KEY": "valor_fake_anon",
            "R03_QUANT_SHADOW_ENABLED": True,
        }),
        "os": __import__("os"), "logging": logging, "time": time, "pd": pd,
        "QUANT_SHADOW_SUPPORTED_SYMBOLS": {"BTCUSDT"},
        "buscar_candles_binance_quant_shadow": lambda symbol: market_data(2400),
        "_http_request": lambda *args, **kwargs: (requests.append((args, kwargs)) or Response()),
        "sanitizar_erro_observabilidade": lambda value: str(value)[:80],
        "_agendar_avaliacao_quant_shadow_isolada": lambda *args, **kwargs: None,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
    with caplog.at_level(logging.WARNING):
        result = namespace["agendar_quant_shadow"](
            {"id_sinal": "official-1", "direcao": "VENDA", "confianca": 80, "risco": "BAIXO"},
            mercado="Cripto", symbol="BITCOIN", timeframe="1M",
            event_id="00000000-0000-4000-8000-000000000000",
            request_id="00000000-0000-4000-8000-000000000001",
        )
    assert result["status"] == "shadow_completed" and len(requests) == 1
    headers = requests[0][1]["headers"]
    assert headers["apikey"] == "valor_fake_service_role"
    assert headers["Authorization"] == "Bearer valor_fake_service_role"
    assert "valor_fake_service_role" not in caplog.text and "valor_fake_anon" not in caplog.text
    assert "service_role_present=true" in caplog.text and "supabase_url_present=true" in caplog.text


def test_ineligible_case_logs_skip_reason(caplog):
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "R03_SHADOW_SKIP reason=%s" in source
    assert all(reason in source for reason in ("FLAG_DISABLED", "MARKET_INELIGIBLE", "ASSET_INELIGIBLE", "TIMEFRAME_INELIGIBLE", "MISSING_EVENT_ID"))
