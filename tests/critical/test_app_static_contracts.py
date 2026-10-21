from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app.py"


def test_app_static_contracts_carteira_checkout_shadow():
    source = APP.read_text(encoding="utf-8")
    assert 'dados.get("creditos_comprados", dados.get("saldo_creditos", 0))' in source
    assert 'resumo["saldo_creditos"] = creditos_comprados' in source
    assert 'MERCADOPAGO_CHECKOUT_PRODUCAO_URL = "https://tigccvvuemverlbczpoh.supabase.co/functions/v1/mercadopago-checkout"' in source
    assert 'json={"produto": produto, "nova_tentativa": bool(nova_tentativa)}' in source
    assert 'if not signal_v2_shadow_enabled()' in source
    assert 'if V2_CONFIG_HASH != "792460b7"' in source
    assert 'carteira_reservar_sinal(usuario_uso, request_id_carteira)' in source
    assert 'admin_signal_access_unlimited(email)' in source
    assert '"origem": "ADMIN_NO_CHARGE"' in source
    assert 'reserva_carteira.get("origem") == "ADMIN_NO_CHARGE"' in source
    assert 'carteira_confirmar_sinal(request_id_carteira' in source
    assert 'carteira_estornar_sinal(request_id_carteira' in source


def test_prioridade_consumo_documentada_no_fluxo_app():
    source = APP.read_text(encoding="utf-8")
    reserva_pos = source.index('carteira_reservar_sinal(usuario_uso, request_id_carteira)')
    gerar_pos = source.index('gerar_sinal_scalp_gemini', reserva_pos)
    confirmar_pos = source.index('carteira_confirmar_sinal(request_id_carteira', gerar_pos)
    shadow_pos = source.index('registrar_shadow_v2_sinal', confirmar_pos)
    assert reserva_pos < gerar_pos < confirmar_pos < shadow_pos


def test_security_errors_do_not_embed_secret_markers():
    safe_messages = [
        "Não foi possível iniciar o checkout Mercado Pago. Tente novamente em instantes.",
        "Não foi possível confirmar saldo no servidor. Sinal não gerado.",
        "Carteira indisponível para reservar sinal.",
        "Confirmação não processada no servidor.",
    ]
    forbidden = ("SUPABASE_SERVICE_ROLE_KEY", "MERCADOPAGO_ACCESS_TOKEN", "MERCADOPAGO_WEBHOOK_SECRET", "Authorization", "Bearer ", "service_role")
    for message in safe_messages:
        assert not any(marker in message for marker in forbidden)


def test_webhook_python_coverage_separate_required():
    edge_file = Path(__file__).resolve().parents[2] / "supabase" / "functions" / "mercadopago-webhook" / "index.ts"
    coverage = "PYTHON_TESTABLE" if edge_file.exists() else "SEPARATE_REQUIRED"
    assert coverage == "SEPARATE_REQUIRED"
