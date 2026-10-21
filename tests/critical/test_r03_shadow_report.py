from scripts.r03_shadow_report import ASSETS, build_report, metrics, sample_status


def rows():
    return [
        {"shadow_status": "shadow_success", "resultado_official": "P1", "resultado_shadow": "P3", "v1_direction": "VENDA", "v2_direction": "COMPRA", "symbol": "BTCUSDT", "v2_risk": "ALTO", "confidence_raw_shadow": .91, "confidence_calibrated_shadow": None},
        {"shadow_status": "shadow_success", "resultado_official": "P2", "resultado_shadow": "P1", "v1_direction": "COMPRA", "v2_direction": "COMPRA", "symbol": "ETHUSDT", "v2_risk": "BAIXO", "confidence_raw_shadow": .75, "confidence_calibrated_shadow": .65},
        {"shadow_status": "shadow_success", "resultado_official": "P3", "resultado_shadow": "P2", "v1_direction": "VENDA", "v2_direction": "VENDA", "symbol": "BNBUSDT", "v2_risk": "MEDIO", "confidence_raw_shadow": .55, "confidence_calibrated_shadow": .52},
        {"shadow_status": "shadow_success", "resultado_official": "LOSS", "resultado_shadow": "LOSS", "v1_direction": "COMPRA", "v2_direction": "VENDA", "symbol": "SOLUSDT", "v2_risk": "ALTO", "confidence_raw_shadow": .45, "confidence_calibrated_shadow": .40},
        {"shadow_status": "shadow_success", "resultado_official": "PENDENTE", "resultado_shadow": "PENDENTE", "v1_direction": "COMPRA", "v2_direction": "COMPRA", "symbol": "XRPUSDT", "v2_risk": "BAIXO", "confidence_raw_shadow": None, "confidence_calibrated_shadow": None},
        {"shadow_status": "shadow_success", "resultado_official": "SEM DADOS", "resultado_shadow": "SEM DADOS", "v1_direction": "VENDA", "v2_direction": "VENDA", "symbol": "BTCUSDT", "v2_risk": "ALTO", "confidence_raw_shadow": None, "confidence_calibrated_shadow": None},
        {"shadow_status": "shadow_error", "resultado_official": "PENDENTE", "resultado_shadow": "PENDENTE", "v1_direction": "VENDA", "v2_direction": None, "symbol": "BTCUSDT", "v2_risk": None, "confidence_raw_shadow": None, "confidence_calibrated_shadow": None},
    ]


def test_rates_exclude_pending_no_data_and_errors():
    _, summary = build_report(rows())
    assert summary["V1"]["TOTAL_FINALIZADOS"] == 4
    assert summary["R03"]["TOTAL_FINALIZADOS"] == 4
    assert summary["V1"]["P1_RATE"] == 25 and summary["R03"]["P1_RATE"] == 25
    assert summary["V1"]["FAST_GAIN_RATE"] == 50 and summary["R03"]["FAST_GAIN_RATE"] == 50
    assert summary["V1"]["TOTAL_GAIN_RATE"] == 75 and summary["R03"]["TOTAL_GAIN_RATE"] == 75
    assert summary["V1"]["LOSS_RATE"] == 25 and summary["R03"]["LOSS_RATE"] == 25
    assert summary["PENDING_ROWS"] == 1 and summary["NO_DATA_ROWS"] == 1 and summary["ERROR_ROWS"] == 1


def test_deltas_direction_asset_risk_confidence_and_disagreement():
    report, summary = build_report(rows())
    assert all(summary[key] == 0 for key in ("DELTA_P1_PP", "DELTA_FAST_GAIN_PP", "DELTA_TOTAL_GAIN_PP", "DELTA_LOSS_PP"))
    assert summary["SAME_DIRECTION_COUNT"] == 4 and summary["OPPOSITE_DIRECTION_COUNT"] == 2
    assert set(ASSETS).issubset(set(report[report.SCOPE == "ASSET"].GROUP))
    assert {"BAIXO", "MEDIO", "ALTO"}.issubset(set(report[report.SCOPE == "R03_RISK"].GROUP))
    assert "NULL" in set(report[report.SCOPE == "R03_CONFIDENCE_CALIBRATED_SHADOW"].GROUP)
    assert set(report.SIDE) == {"V1", "R03"}


def test_sample_status_boundaries_zero_division_and_empty_dataset():
    assert [sample_status(n) for n in (0, 29, 30, 99, 100, 299, 300)] == [
        "AMOSTRA_INSUFICIENTE", "AMOSTRA_INSUFICIENTE", "AMOSTRA_INICIAL", "AMOSTRA_INICIAL",
        "AMOSTRA_INTERMEDIARIA", "AMOSTRA_INTERMEDIARIA", "AMOSTRA_RELEVANTE",
    ]
    assert metrics(__import__("pandas").DataFrame(), "resultado_shadow")["LOSS_RATE"] == 0
    report, summary = build_report([])
    assert not report.empty and summary["FINALIZED_ROWS"] == 0


def test_report_code_is_read_only():
    import inspect
    from scripts import r03_shadow_report
    source = inspect.getsource(r03_shadow_report.fetch_production_rows)
    assert "requests.get(" in source
    assert not any(token in source for token in ("requests.post(", "requests.patch(", "requests.delete("))
