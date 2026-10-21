from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib

from .contracts import FEATURE_HASH
from .feature_pipeline import FEATURE_NAMES
from .inference import ARTIFACT_NAMES, ARTIFACT_ROOT


def _canonical_hash(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def shadow_health_check(artifacts_root: Path = ARTIFACT_ROOT, *, latency_p95_ms: float | None = None, persistence_failures: int = 0, duplicates: int = 0, coverage: float = 1.0) -> dict:
    checks = {}
    try:
        manifest = json.loads((artifacts_root / "feature_manifest.json").read_text(encoding="utf-8"))
        checks["feature_count"] = manifest.get("feature_count") == 54 == len(FEATURE_NAMES)
        checks["feature_hash"] = manifest.get("feature_hash") == FEATURE_HASH == _canonical_hash(manifest.get("features"))
    except Exception:
        checks.update(feature_count=False, feature_hash=False)
    for name in ARTIFACT_NAMES:
        try:
            metadata = json.loads((artifacts_root / name / "metadata.json").read_text(encoding="utf-8"))
            joblib.load(artifacts_root / name / "pipeline.joblib")
            checks[f"model_{name}"] = metadata.get("feature_hash") == FEATURE_HASH
        except Exception:
            checks[f"model_{name}"] = False
    checks["persistence"] = persistence_failures == 0
    checks["duplicates"] = duplicates == 0
    checks["coverage"] = coverage >= 0.999
    checks["latency"] = latency_p95_ms is None or latency_p95_ms <= 250.0
    return {"ok": all(checks.values()), "checks": checks, "customer_visible": False}
