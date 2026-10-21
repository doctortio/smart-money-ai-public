import ast
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]


def _formatter():
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "formatar_data_hora_br")
    namespace = {"pd": pd}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), namespace)
    return namespace["formatar_data_hora_br"]


def test_utc_timestamp_is_displayed_in_brasilia():
    assert _formatter()("2026-10-03T19:48:00Z") == "03/10/2026 16:48:00"


def test_brasilia_and_legacy_local_timestamps_are_not_shifted_twice():
    formatar = _formatter()
    assert formatar("2026-10-03T16:48:00-03:00") == "03/10/2026 16:48:00"
    assert formatar("03/10/2026 16:48:00") == "03/10/2026 16:48:00"
    assert formatar("2026-10-03 16:48:00") == "03/10/2026 16:48:00"
