from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app.py"
EXPECTED_PRICES = {
    "pacote_5": 14.90,
    "pacote_10": 24.90,
    "pacote_20": 42.90,
    "pacote_40": 74.90,
    "ilimitado_mensal": 99.90,
    "ilimitado_anual": 1119.90,
}


def _plans_from_app() -> dict:
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CARTEIRA_FREEMIUM_PLANOS":
                    return ast.literal_eval(node.value)
    raise AssertionError("CARTEIRA_FREEMIUM_PLANOS_NOT_FOUND")


class FakeCheckoutGateway:
    def __init__(self, *, fail=None):
        self.fail = fail
        self.calls = []

    def create(self, payload, *, idempotency_key):
        self.calls.append((payload, idempotency_key))
        if self.fail == "http":
            return {"ok": False, "status": 500}
        if self.fail == "timeout":
            raise TimeoutError("TIMEOUT")
        if self.fail == "invalid_response":
            return {"ok": True, "checkout_url": ""}
        return {"ok": True, "checkout_url": "https://www.mercadopago.com.br/checkout/v1/redirect/mock"}


def create_checkout_contract(produto, usuario, client_price, gateway, idempotency_key):
    plans = _plans_from_app()
    if not usuario:
        return {"ok": False, "code": "USER_REQUIRED"}
    if produto not in plans:
        return {"ok": False, "code": "INVALID_PRODUCT"}
    payload = {"produto": produto, "valor": plans[produto]["preco"], "client_price_ignored": client_price}
    try:
        response = gateway.create(payload, idempotency_key=idempotency_key)
    except TimeoutError:
        return {"ok": False, "code": "TIMEOUT"}
    if not response.get("ok"):
        return {"ok": False, "code": "EXTERNAL_HTTP_ERROR"}
    if not str(response.get("checkout_url", "")).startswith("https://"):
        return {"ok": False, "code": "INVALID_RESPONSE"}
    return {"ok": True, "payload": payload, "checkout_url": response["checkout_url"]}


def test_planos_precos_server_side():
    plans = _plans_from_app()
    assert {k: plans[k]["preco"] for k in EXPECTED_PRICES} == EXPECTED_PRICES


def test_produto_invalido_e_usuario_obrigatorio():
    gateway = FakeCheckoutGateway()
    assert create_checkout_contract("bad", "user@example.com", 0.01, gateway, "k1")["code"] == "INVALID_PRODUCT"
    assert create_checkout_contract("pacote_5", "", 14.90, gateway, "k1")["code"] == "USER_REQUIRED"
    assert gateway.calls == []


def test_preco_nao_vem_do_cliente_e_idempotency_key_enviada():
    gateway = FakeCheckoutGateway()
    result = create_checkout_contract("pacote_5", "u@example.com", 0.01, gateway, "idem-1")
    assert result["ok"] is True
    payload, key = gateway.calls[0]
    assert payload["valor"] == 14.90
    assert payload["client_price_ignored"] == 0.01
    assert key == "idem-1"


def test_erros_http_timeout_resposta_invalida():
    assert create_checkout_contract("pacote_5", "u", 14.90, FakeCheckoutGateway(fail="http"), "k")["code"] == "EXTERNAL_HTTP_ERROR"
    assert create_checkout_contract("pacote_5", "u", 14.90, FakeCheckoutGateway(fail="timeout"), "k")["code"] == "TIMEOUT"
    assert create_checkout_contract("pacote_5", "u", 14.90, FakeCheckoutGateway(fail="invalid_response"), "k")["code"] == "INVALID_RESPONSE"
