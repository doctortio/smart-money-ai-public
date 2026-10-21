from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd
import requests


FINAL = {"P1", "P2", "P3", "LOSS"}
ASSETS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")


def sample_status(n: int) -> str:
    if n < 30:
        return "AMOSTRA_INSUFICIENTE"
    if n < 100:
        return "AMOSTRA_INICIAL"
    if n < 300:
        return "AMOSTRA_INTERMEDIARIA"
    return "AMOSTRA_RELEVANTE"


def _rate(count: int, total: int) -> float:
    return round(100.0 * count / total, 6) if total else 0.0


def metrics(frame: pd.DataFrame, result_column: str) -> dict:
    values = frame.get(result_column, pd.Series(dtype=str)).fillna("PENDENTE").astype(str).str.upper()
    finalized = values.isin(FINAL)
    total = int(finalized.sum())
    counts = {name: int((values[finalized] == name).sum()) for name in FINAL}
    fast = counts["P1"] + counts["P2"]
    gain = fast + counts["P3"]
    return {
        "TOTAL_FINALIZADOS": total,
        "P1_COUNT": counts["P1"], "P1_RATE": _rate(counts["P1"], total),
        "P2_COUNT": counts["P2"], "P2_RATE": _rate(counts["P2"], total),
        "P3_COUNT": counts["P3"], "P3_RATE": _rate(counts["P3"], total),
        "LOSS_COUNT": counts["LOSS"], "LOSS_RATE": _rate(counts["LOSS"], total),
        "FAST_GAIN_COUNT": fast, "FAST_GAIN_RATE": _rate(fast, total),
        "TOTAL_GAIN_COUNT": gain, "TOTAL_GAIN_RATE": _rate(gain, total),
        "PENDING_COUNT": int((values == "PENDENTE").sum()),
        "NO_DATA_COUNT": int((values == "SEM DADOS").sum()),
        "ERROR_COUNT": int((frame.get("shadow_status", pd.Series(index=frame.index, dtype=str)).fillna("") == "shadow_error").sum()),
        "SAMPLE_STATUS": sample_status(total),
    }


def _confidence_bucket(value) -> str:
    if pd.isna(value):
        return "NULL"
    number = float(value)
    if number <= 1:
        number *= 100
    for low, high in ((0, 49), (50, 59), (60, 69), (70, 79), (80, 89), (90, 100)):
        if low <= number <= high + 0.999999:
            return f"{low}-{high}"
    return "OUT_OF_RANGE"


def build_report(records: list[dict]) -> tuple[pd.DataFrame, dict]:
    all_rows = pd.DataFrame(records)
    if all_rows.empty:
        all_rows = pd.DataFrame(columns=["shadow_status", "resultado_official", "resultado_shadow", "v1_direction", "v2_direction", "symbol", "v2_risk", "confidence_raw_shadow", "confidence_calibrated_shadow"])
    success = all_rows[all_rows["shadow_status"].fillna("") == "shadow_success"].copy()
    report = []

    def add(scope, group, side, data):
        result = "resultado_official" if side == "V1" else "resultado_shadow"
        report.append({"SCOPE": scope, "GROUP": str(group), "SIDE": side, **metrics(data, result)})

    for side in ("V1", "R03"):
        add("GLOBAL", "GLOBAL", side, success)
    for side, direction_col in (("V1", "v1_direction"), ("R03", "v2_direction")):
        for direction in ("COMPRA", "VENDA"):
            add("DIRECTION", direction, side, success[success[direction_col] == direction])
    for v1 in ("COMPRA", "VENDA"):
        for r03 in ("COMPRA", "VENDA"):
            subset = success[(success["v1_direction"] == v1) & (success["v2_direction"] == r03)]
            for side in ("V1", "R03"):
                add("DIRECTION_MATRIX", f"V1_{v1}__R03_{r03}", side, subset)
    for asset in ASSETS:
        subset = success[success["symbol"] == asset]
        for side in ("V1", "R03"):
            add("ASSET", asset, side, subset)
    for risk in ("BAIXO", "MEDIO", "MÉDIO", "ALTO"):
        subset = success[success["v2_risk"] == risk]
        add("R03_RISK", risk, "R03", subset)
    for column in ("confidence_raw_shadow", "confidence_calibrated_shadow"):
        buckets = success[column].apply(_confidence_bucket)
        for bucket in ("0-49", "50-59", "60-69", "70-79", "80-89", "90-100", "NULL"):
            add(f"R03_{column.upper()}", bucket, "R03", success[buckets == bucket])

    report_df = pd.DataFrame(report)
    v1 = metrics(success, "resultado_official")
    r03 = metrics(success, "resultado_shadow")
    same = success["v1_direction"].eq(success["v2_direction"])
    summary = {
        "REAL_ROWS_FOUND": len(all_rows), "FINALIZED_ROWS": r03["TOTAL_FINALIZADOS"],
        "PENDING_ROWS": r03["PENDING_COUNT"], "NO_DATA_ROWS": r03["NO_DATA_COUNT"],
        "ERROR_ROWS": int((all_rows["shadow_status"] == "shadow_error").sum()),
        "SAME_DIRECTION_COUNT": int(same.sum()), "OPPOSITE_DIRECTION_COUNT": int((~same).sum()),
        "V1": v1, "R03": r03,
        "DELTA_P1_PP": round(r03["P1_RATE"] - v1["P1_RATE"], 6),
        "DELTA_FAST_GAIN_PP": round(r03["FAST_GAIN_RATE"] - v1["FAST_GAIN_RATE"], 6),
        "DELTA_TOTAL_GAIN_PP": round(r03["TOTAL_GAIN_RATE"] - v1["TOTAL_GAIN_RATE"], 6),
        "DELTA_LOSS_PP": round(r03["LOSS_RATE"] - v1["LOSS_RATE"], 6),
        "SAMPLE_STATUS": sample_status(r03["TOTAL_FINALIZADOS"]),
    }
    return report_df, summary


def fetch_production_rows(base_url: str, service_key: str) -> list[dict]:
    response = requests.get(
        f"{base_url.rstrip('/')}/rest/v1/sma_signal_shadow",
        headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
        params={"select": "*", "order": "created_at.asc"}, timeout=30,
    )
    response.raise_for_status()
    return response.json()


def write_outputs(records: list[dict], output_dir: Path) -> dict:
    report, summary = build_report(records)
    output_dir.mkdir(parents=True, exist_ok=True)
    report.to_csv(output_dir / "r03_shadow_production_report.csv", index=False)
    lines = ["# R03 Shadow — comparação V1 × R03", "", f"Amostra: **{summary['SAMPLE_STATUS']}**", ""]
    for side in ("V1", "R03"):
        item = summary[side]
        lines.append(f"- {side}: N={item['TOTAL_FINALIZADOS']}; P1={item['P1_RATE']:.2f}%; FAST={item['FAST_GAIN_RATE']:.2f}%; GANHO={item['TOTAL_GAIN_RATE']:.2f}%; LOSS={item['LOSS_RATE']:.2f}%")
    lines.append("")
    lines.append("Diferenças são descritivas em pontos percentuais; não implicam melhoria nem promoção automática.")
    (output_dir / "r03_shadow_production_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="JSON exportado de sma_signal_shadow")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    if args.input:
        records = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            raise SystemExit("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY ausentes")
        records = fetch_production_rows(url, key)
    summary = write_outputs(records, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
