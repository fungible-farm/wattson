"""data/ is exactly what data/MANIFEST.json records, and each trajectory file holds the rows its manifest says."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT: Path = Path(__file__).resolve().parents[1] / "scripts" / "verify_data.py"


def test_data_matches_its_manifest(capsys) -> None:
    spec = importlib.util.spec_from_file_location("verify_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import sys

    argv, sys.argv = sys.argv, ["verify_data"]
    try:
        code = module.main()
    finally:
        sys.argv = argv
    assert code == 0, capsys.readouterr().out
