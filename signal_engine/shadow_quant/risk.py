from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ARTIFACT_ROOT = Path(__file__).resolve().parent / "artifacts"


def classify_risk(features: pd.Series, direction: str, config_path: Path | None = None) -> str:
    config = json.loads((config_path or ARTIFACT_ROOT / "risk_v1.json").read_text(encoding="utf-8"))
    sign = 1 if direction == "COMPRA" else -1
    score = int(float(features.atr_pct) > config["atr_high_threshold"])
    score += int(float(features.rel_volume) < config["relative_volume_low_threshold"])
    score += int(np.sign(float(features.mtf5_trend)) != sign)
    score += int(np.sign(float(features.mtf15_trend)) != sign)
    score += int((float(features.taker_ratio) - .5) * sign < 0)
    score += int(float(features.macd_hist) * sign < 0)
    score += int(float(features.bb_pos) < 0 or float(features.bb_pos) > 1)
    bin_index = int(np.digitize([score], config["cuts"][1:-1], right=True)[0])
    return config["bin_names"][str(bin_index)]
