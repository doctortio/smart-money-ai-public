from __future__ import annotations

import ast
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace

APP = Path(__file__).resolve().parents[2] / "app.py"
HELPERS = {
    "gerar_event_id",
    "event_id_valido",
    "obter_event_id_fluxo",
    "sanitizar_erro_observabilidade",
    "log_evento_operacional",
    "mensagem_usuario_com_event_id",
}
SENSITIVE = [
    "user@example.com",
    "SUPABASE_SERVICE_ROLE_KEY",
    "MERCADOPAGO_ACCESS_TOKEN",
    "MERCADOPAGO_WEBHOOK_SECRET",
    "Authorization",
    "Bearer abc",
    "service_role",
    "senha",
    "token",
]


class FakeSessionState(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def load_helpers():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in HELPERS]
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {
        "uuid": uuid,
        "logging": logging,
        "st": SimpleNamespace(session_state=FakeSessionState()),
        "Exception": Exception,
        "str": str,
        "bool": bool,
        "any": any,
    }
    exec(compile(module, "app_event_helpers", "exec"), ns)
    return ns


def test_event_id_formato_valido_e_uuid4():
    ns = load_helpers()
    event_id = ns["gerar_event_id"]()
    parsed = uuid.UUID(event_id)
    assert parsed.version == 4
    assert ns["event_id_valido"](event_id) is True


def test_event_ids_diferentes_para_operacoes_independentes():
    ns = load_helpers()
    ids = {ns["gerar_event_id"]() for _ in range(8)}
    assert len(ids) == 8


def test_mesmo_id_reutilizado_no_mesmo_fluxo():
    ns = load_helpers()
    first = ns["obter_event_id_fluxo"]("checkout")
    second = ns["obter_event_id_fluxo"]("checkout")
    third = ns["obter_event_id_fluxo"]("carteira")
    assert first == second
    assert first != third


def test_event_id_nao_contem_dado_sensivel():
    ns = load_helpers()
    event_id = ns["gerar_event_id"]()
    assert not any(item.lower() in event_id.lower() for item in SENSITIVE)


def test_sanitizacao_de_erro_e_mensagem_usuario_sem_secrets():
    ns = load_helpers()
    event_id = ns["gerar_event_id"]()
    assert ns["sanitizar_erro_observabilidade"]("SUPABASE_SERVICE_ROLE_KEY=real") == "ERRO_SANITIZADO"
    msg = ns["mensagem_usuario_com_event_id"]("Não foi possível concluir a operação.", event_id)
    assert event_id in msg
    assert not any(item in msg for item in SENSITIVE)


def test_app_fluxos_criticos_instrumentados_estaticamente():
    source = APP.read_text(encoding="utf-8")
    for marker in [
        'log_evento_operacional(event_id, "auth.login"',
        'log_evento_operacional(event_id, "auth.session"',
        'log_evento_operacional(event_id, "carteira.reservar"',
        'log_evento_operacional(event_id, "carteira.confirmar"',
        'log_evento_operacional(event_id, "carteira.estornar"',
        'log_evento_operacional(event_id, "checkout"',
        'log_evento_operacional(event_id_sinal, "sinal.oficial"',
        'log_evento_operacional(event_id, "live_shadow"',
    ]:
        assert marker in source


def test_live_shadow_continua_sem_consumo_credito_e_falha_isolada():
    source = APP.read_text(encoding="utf-8")
    assert 'if V2_CONFIG_HASH != "792460b7"' in source
    assert 'return {"ok": False, "status": "shadow_error", "error_code": type(exc).__name__}' in source
    assert 'st.session_state["event_id_live_shadow"] = event_id_sinal' in source
    shadow_block = source[source.index('def registrar_shadow_v2_sinal'):source.index('def carteira_criar_solicitacao')]
    assert 'carteira_' not in shadow_block
    assert 'credito' not in shadow_block.lower()


def test_comportamento_funcional_existente_preservado_estaticamente():
    source = APP.read_text(encoding="utf-8")
    checkout_start = source.index("def mercado_pago_checkout_criar")
    checkout_block = source[checkout_start:source.index("# =============================", checkout_start)]
    assert 'json={"produto": produto, "nova_tentativa": bool(nova_tentativa)}' in checkout_block
    assert 'return False, "Não foi possível iniciar o checkout Mercado Pago. Tente novamente em instantes.", {}' in checkout_block
    assert 'return False, "Checkout Mercado Pago não retornou link válido.", {}' in checkout_block
    assert "mensagem_usuario_com_event_id" not in checkout_block
    assert "Código de atendimento" not in checkout_block
    assert 'return True, "Checkout Mercado Pago criado. Abra o link para concluir o pagamento.", {**dados, "checkout_url": checkout_url}' in checkout_block
    assert 'resultado_shadow = run_live_shadow(' in source
    assert 'carteira_confirmar_sinal(request_id_carteira, decisao.get("id_sinal", ""))' in source
