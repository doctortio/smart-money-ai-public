from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone


@dataclass
class FakeWallet:
    creditos_comprados: int = 0
    gratuito_usado_em: set[date] = field(default_factory=set)
    ilimitado_fim: datetime | None = None
    reservas: dict[str, dict] = field(default_factory=dict)
    confirmados: set[str] = field(default_factory=set)
    total_consumido: int = 0

    def resumo(self, hoje: date) -> dict:
        return {
            "creditos_comprados": self.creditos_comprados,
            "saldo_creditos": self.creditos_comprados,
            "gratuito_disponivel": 0 if hoje in self.gratuito_usado_em else 1,
            "ilimitado_ativo": bool(self.ilimitado_fim and self.ilimitado_fim > datetime.now(timezone.utc)),
            "total_consumido": self.total_consumido,
        }

    def comprar_creditos(self, quantidade: int) -> None:
        self.creditos_comprados += int(quantidade)

    def reservar(self, request_id: str, hoje: date) -> dict:
        if request_id in self.reservas:
            return self.reservas[request_id]
        if self.ilimitado_fim and self.ilimitado_fim > datetime.now(timezone.utc):
            origem = "ILIMITADO"
        elif hoje not in self.gratuito_usado_em:
            origem = "GRATIS"
        elif self.creditos_comprados > 0:
            origem = "COMPRADO"
        else:
            return {"ok": False, "status": "SEM_CREDITOS"}
        self.reservas[request_id] = {"ok": True, "status": "RESERVADO", "origem": origem, "request_id": request_id, "hoje": hoje}
        return self.reservas[request_id]

    def reservar_admin_no_charge(self, request_id: str, hoje: date, *, admin_autenticado: bool) -> dict:
        if not admin_autenticado:
            return self.reservar(request_id, hoje)
        self.reservas[request_id] = {
            "ok": True,
            "status": "RESERVADO",
            "origem": "ADMIN_NO_CHARGE",
            "request_id": request_id,
            "hoje": hoje,
            "delta_credito": 0,
        }
        return self.reservas[request_id]

    def confirmar(self, request_id: str) -> dict:
        reserva = self.reservas.get(request_id)
        if not reserva or not reserva.get("ok"):
            return {"ok": False, "status": "RESERVA_INEXISTENTE"}
        if request_id in self.confirmados:
            return {"ok": True, "status": "JA_CONFIRMADO", "delta_credito": 0}
        origem = reserva["origem"]
        if origem == "GRATIS":
            self.gratuito_usado_em.add(reserva["hoje"])
        elif origem == "COMPRADO":
            if self.creditos_comprados <= 0:
                return {"ok": False, "status": "SEM_CREDITOS"}
            self.creditos_comprados -= 1
        elif origem == "ILIMITADO":
            pass
        elif origem == "ADMIN_NO_CHARGE":
            pass
        else:
            return {"ok": False, "status": "ORIGEM_INVALIDA"}
        if origem != "ADMIN_NO_CHARGE":
            self.total_consumido += 1
        self.confirmados.add(request_id)
        return {"ok": True, "status": "CONFIRMADO", "origem": origem, "delta_credito": 0 if origem != "COMPRADO" else -1}

    def estornar(self, request_id: str) -> dict:
        reserva = self.reservas.get(request_id)
        if not reserva:
            return {"ok": True, "status": "NADA_A_ESTORNAR"}
        if request_id in self.confirmados:
            origem = reserva["origem"]
            if origem == "GRATIS":
                self.gratuito_usado_em.discard(reserva["hoje"])
            elif origem == "COMPRADO":
                self.creditos_comprados += 1
            self.total_consumido -= 1
            self.confirmados.remove(request_id)
        return {"ok": True, "status": "ESTORNADO"}


def map_rpc_summary_to_ui(dados: dict) -> dict:
    creditos_comprados = int(dados.get("creditos_comprados", dados.get("saldo_creditos", 0)) or 0)
    return {**dados, "saldo_creditos": creditos_comprados}
