"""Generate the two private-test signals without sending anything."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telegram_worker.adapter import existing_motor_factory


def main() -> None:
    rows = []
    for asset in ("BTCUSDT", "ETHUSDT"):
        signal = existing_motor_factory(asset, "5m")
        if signal is None:
            raise RuntimeError(f"Sinal indisponível para {asset}")
        rows.append({
            "asset": signal.asset,
            "direction": signal.direction,
            "timeframe": signal.timeframe,
            "context": signal.context,
            "risk": signal.risk,
            "created_at": signal.created_at,
        })
    print(json.dumps(rows, ensure_ascii=False))


if __name__ == "__main__":
    main()
