from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .contracts import FEATURE_HASH, MODEL_VERSION, ShadowContext, fail_result, validate_context
from .feature_pipeline import FEATURE_NAMES, build_features
from .risk import classify_risk

ARTIFACT_ROOT = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_NAMES = ("buy_primary", "buy_challenger", "sell_research")


@lru_cache(maxsize=3)
def _load_artifact(root_text: str, name: str):
    root = Path(root_text)
    metadata = json.loads((root / name / "metadata.json").read_text(encoding="utf-8"))
    if metadata["feature_hash"] != FEATURE_HASH:
        raise ValueError("SHADOW_FEATURE_HASH_MISMATCH")
    if (root / name / "feature_hash").read_text().strip() != FEATURE_HASH:
        raise ValueError("SHADOW_TRANSFER_HASH_MISMATCH")
    return joblib.load(root / name / "pipeline.joblib"), metadata


def infer_feature_row(feature_row: pd.Series | pd.DataFrame, artifacts_root: Path = ARTIFACT_ROOT) -> dict:
    row = feature_row.to_frame().T if isinstance(feature_row, pd.Series) else feature_row.copy()
    if list(row.columns) != FEATURE_NAMES or len(row) != 1:
        raise ValueError("SHADOW_FEATURE_CONTRACT_INVALID")
    if np.isinf(row.to_numpy(dtype=float)).any():
        raise ValueError("SHADOW_FEATURE_INFINITY")
    probabilities = {}
    metadata = {}
    for name in ARTIFACT_NAMES:
        model, meta = _load_artifact(str(artifacts_root.resolve()), name)
        probabilities[name] = float(model.predict_proba(row[FEATURE_NAMES])[:, 1][0])
        metadata[name] = meta
    primary_direction = "COMPRA" if probabilities["buy_primary"] >= metadata["buy_primary"]["threshold"] else "VENDA"
    challenger_direction = "COMPRA" if probabilities["buy_challenger"] >= metadata["buy_challenger"]["threshold"] else "VENDA"
    sell_direction = "VENDA" if probabilities["sell_research"] >= metadata["sell_research"]["threshold"] else "COMPRA"
    shadow_direction = "COMPRA" if probabilities["buy_primary"] >= probabilities["sell_research"] else "VENDA"
    risk = classify_risk(row.iloc[0], shadow_direction, artifacts_root / "risk_v1.json")
    return {
        "shadow_direction": shadow_direction,
        "buy_primary_direction": primary_direction, "buy_primary_probability": probabilities["buy_primary"],
        "buy_challenger_direction": challenger_direction, "buy_challenger_probability": probabilities["buy_challenger"],
        "sell_research_direction": sell_direction, "sell_research_probability": probabilities["sell_research"],
        "risk_class_shadow": risk, "calibrated_probability": None,
        "model_version": MODEL_VERSION,
        "config_hash": {name: metadata[name]["config_hash"] for name in ARTIFACT_NAMES},
        "feature_hash": FEATURE_HASH, "status": "shadow_success", "error_code": None,
    }


def run_quant_shadow(context: ShadowContext, artifacts_root: Path = ARTIFACT_ROOT) -> dict:
    """Fail-open quantitative observation. It has no persistence or side effects."""
    try:
        frame = validate_context(context)
        feature_row = build_features(frame).iloc[-1][FEATURE_NAMES]
        result = infer_feature_row(feature_row, artifacts_root)
        result["idempotency_key"] = context.idempotency_key
        return result
    except Exception as exc:
        return {**fail_result(exc), "idempotency_key": context.idempotency_key}
