import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from signal_engine.shadow_quant.contracts import FEATURE_HASH
from signal_engine.shadow_quant.feature_pipeline import FEATURE_NAMES
from signal_engine.shadow_quant.inference import ARTIFACT_ROOT, infer_feature_row


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def test_01_artifact_transfer_hash():
    manifest = json.loads((ARTIFACT_ROOT / "feature_manifest.json").read_text(encoding="utf-8"))
    assert canonical_hash(manifest["features"]) == FEATURE_HASH
    assert (ARTIFACT_ROOT / "feature_hash").read_text().strip() == FEATURE_HASH


def test_02_golden_120_of_120():
    golden = pd.read_csv(ARTIFACT_ROOT / "golden_parity.csv")
    assert len(golden) == 120
    matched = 0
    for artifact, group in golden.groupby("artifact", sort=False):
        metadata = json.loads((ARTIFACT_ROOT / artifact / "metadata.json").read_text(encoding="utf-8"))
        model = joblib.load(ARTIFACT_ROOT / artifact / "pipeline.joblib")
        probability = model.predict_proba(group[FEATURE_NAMES])[:, 1]
        assert np.allclose(probability, group.expected_raw_probability, rtol=0, atol=1e-15)
        matched += int((probability >= metadata["threshold"]).sum())
    assert matched == 120


def _direction_case(winner):
    golden = pd.read_csv(ARTIFACT_ROOT / "golden_parity.csv")
    for _, row in golden.iterrows():
        result = infer_feature_row(row[FEATURE_NAMES])
        if result["shadow_direction"] == winner:
            return result
    raise AssertionError(f"no {winner} case")


def test_09_compra_output(): assert _direction_case("COMPRA")["shadow_direction"] == "COMPRA"
def test_10_venda_output(): assert _direction_case("VENDA")["shadow_direction"] == "VENDA"


def test_11_no_aguardar():
    golden = pd.read_csv(ARTIFACT_ROOT / "golden_parity.csv")
    assert all(infer_feature_row(row[FEATURE_NAMES])["shadow_direction"] in {"COMPRA", "VENDA"} for _, row in golden.iterrows())


def test_12_coverage_100_percent():
    golden = pd.read_csv(ARTIFACT_ROOT / "golden_parity.csv")
    directions = [infer_feature_row(row[FEATURE_NAMES])["shadow_direction"] for _, row in golden.iterrows()]
    assert len(directions) == len(golden) and not set(directions) - {"COMPRA", "VENDA"}
