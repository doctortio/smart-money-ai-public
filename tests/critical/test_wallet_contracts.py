from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from tests.critical.helpers_wallet import FakeWallet, map_rpc_summary_to_ui


def test_mapping_creditos_comprados_para_saldo_creditos():
    assert map_rpc_summary_to_ui({"creditos_comprados": 5, "saldo_creditos": 0})["saldo_creditos"] == 5


def test_ilimitado_ativo_nao_consume_gratis_nem_comprado():
    wallet = FakeWallet(creditos_comprados=3, ilimitado_fim=datetime.now(timezone.utc) + timedelta(days=1))
    hoje = date(2026, 9, 26)
    reserva = wallet.reservar("r1", hoje)
    assert reserva["origem"] == "ILIMITADO"
    assert wallet.confirmar("r1")["ok"] is True
    assert wallet.creditos_comprados == 3
    assert hoje not in wallet.gratuito_usado_em


def test_gratis_tem_prioridade_sobre_credito_comprado_e_segundo_sinal_usa_comprado():
    wallet = FakeWallet(creditos_comprados=2)
    hoje = date(2026, 9, 26)
    assert wallet.reservar("r1", hoje)["origem"] == "GRATIS"
    assert wallet.confirmar("r1")["status"] == "CONFIRMADO"
    assert wallet.creditos_comprados == 2
    assert wallet.reservar("r2", hoje)["origem"] == "COMPRADO"
    assert wallet.confirmar("r2")["status"] == "CONFIRMADO"
    assert wallet.creditos_comprados == 1


def test_credito_comprado_acumula_e_nao_expira_no_modelo_de_regressao():
    wallet = FakeWallet(creditos_comprados=1)
    wallet.comprar_creditos(5)
    wallet.comprar_creditos(10)
    assert wallet.creditos_comprados == 16
    assert wallet.resumo(date(2027, 9, 26))["creditos_comprados"] == 16


def test_saldo_zero_bloqueia_reserva():
    wallet = FakeWallet(creditos_comprados=0, gratuito_usado_em={date(2026, 9, 26)})
    assert wallet.reservar("r1", date(2026, 9, 26)) == {"ok": False, "status": "SEM_CREDITOS"}


def test_reserva_confirmacao_estorno_e_idempotencia_request_id():
    wallet = FakeWallet(creditos_comprados=1, gratuito_usado_em={date(2026, 9, 26)})
    reserva1 = wallet.reservar("rid", date(2026, 9, 26))
    reserva2 = wallet.reservar("rid", date(2026, 9, 26))
    assert reserva1 is reserva2
    assert wallet.confirmar("rid")["delta_credito"] == -1
    assert wallet.creditos_comprados == 0
    assert wallet.confirmar("rid")["delta_credito"] == 0
    assert wallet.creditos_comprados == 0
    assert wallet.estornar("rid")["status"] == "ESTORNADO"
    assert wallet.creditos_comprados == 1


def test_admin_autenticado_gera_sem_consumir_gratis_ou_credito():
    hoje = date(2026, 9, 26)
    wallet = FakeWallet(creditos_comprados=0, gratuito_usado_em={hoje})
    reserva = wallet.reservar_admin_no_charge("admin-1", hoje, admin_autenticado=True)
    assert reserva["origem"] == "ADMIN_NO_CHARGE"
    assert wallet.confirmar("admin-1") == {"ok": True, "status": "CONFIRMADO", "origem": "ADMIN_NO_CHARGE", "delta_credito": 0}
    assert wallet.creditos_comprados == 0
    assert hoje in wallet.gratuito_usado_em
    assert wallet.total_consumido == 0


def test_admin_multiplos_sinais_sem_alterar_saldo():
    hoje = date(2026, 9, 26)
    wallet = FakeWallet(creditos_comprados=0, gratuito_usado_em={hoje})
    for idx in range(3):
        request_id = f"admin-{idx}"
        assert wallet.reservar_admin_no_charge(request_id, hoje, admin_autenticado=True)["ok"] is True
        assert wallet.confirmar(request_id)["ok"] is True
    assert wallet.creditos_comprados == 0
    assert wallet.total_consumido == 0


def test_cliente_simulando_admin_sem_autenticacao_nao_obtem_no_charge():
    hoje = date(2026, 9, 26)
    wallet = FakeWallet(creditos_comprados=0, gratuito_usado_em={hoje})
    assert wallet.reservar_admin_no_charge("fake-admin", hoje, admin_autenticado=False) == {"ok": False, "status": "SEM_CREDITOS"}
