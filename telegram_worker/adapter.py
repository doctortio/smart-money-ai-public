"""Adapter boundary for the existing Smart Money AI signal motor."""
from __future__ import annotations

from .worker import Signal


def existing_motor_factory(asset: str, timeframe: str) -> Signal | None:
    """Call the existing app motor and translate its decision to the worker contract.

    Import is lazy so importing the worker itself never starts Streamlit or consumes
    a client allowance. The app motor remains the single source of signal logic.
    """
    from app import gerar_sinal_scalp_gemini

    decision, _ = gerar_sinal_scalp_gemini(asset, timeframe, "Cripto")
    if not decision:
        return None
    direction = str(decision.get("direcao") or "").strip().upper()
    if direction not in {"COMPRA", "VENDA", "AGUARDANDO"}:
        return None
    return Signal(
        asset=asset,
        direction=direction,
        timeframe=timeframe,
        context=str(decision.get("motivo") or "Motor técnico Smart Money AI"),
        risk=str(decision.get("risco") or "NÃO INFORMADO"),
        created_at=str(decision.get("criado_em_iso") or ""),
    )
