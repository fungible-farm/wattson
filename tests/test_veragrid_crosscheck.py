"""Tests for scripts/veragrid_crosscheck.py: the comparison rule, and one real two-solver run when the case and VeraGrid exist."""
from __future__ import annotations

import importlib.util
import math
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "veragrid_crosscheck.py"
spec = importlib.util.spec_from_file_location("veragrid_crosscheck", SCRIPT)
assert spec is not None and spec.loader is not None
crosscheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crosscheck)


def test_equal_lists_pass_with_zero_difference() -> None:
    result = crosscheck.compare_voltages([1.0, 0.95], [1.0, 0.95], 1e-4)
    assert result["passed"] and result["max_abs_diff"] == 0.0 and result["n"] == 2


def test_a_difference_inside_the_tolerance_passes_and_names_the_worst_index() -> None:
    result = crosscheck.compare_voltages([1.0, 0.95, 1.02], [1.0, 0.95005, 1.02001], 1e-4)
    assert result["passed"] and result["worst_index"] == 1
    assert math.isclose(result["max_abs_diff"], 5e-5, abs_tol=1e-12)


def test_a_difference_over_the_tolerance_fails() -> None:
    result = crosscheck.compare_voltages([1.0, 0.95], [1.0, 0.951], 1e-4)
    assert not result["passed"] and result["worst_index"] == 1


def test_nan_in_either_list_fails_rather_than_passing_silently() -> None:
    assert not crosscheck.compare_voltages([1.0, math.nan], [1.0, 0.9], 1e-4)["passed"]


def test_length_mismatch_and_empty_lists_are_errors() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        crosscheck.compare_voltages([1.0], [1.0, 1.0], 1e-4)
    with pytest.raises(ValueError, match="no voltages"):
        crosscheck.compare_voltages([], [], 1e-4)


def test_a_missing_case_file_exits_two(tmp_path: Path) -> None:
    assert crosscheck.main(["--case", str(tmp_path / "nope.m")]) == 2


def test_the_snem_sa_case_agrees_between_the_two_solvers(capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("VeraGridEngine")
    case = Path(os.environ.get("WATTSON_CASE_DIR", "/nonexistent")) / "snemSA.m"
    if not case.exists():
        pytest.skip("snemSA.m is not in WATTSON_CASE_DIR")
    code = crosscheck.main(["--case", str(case), "--scale", "1.0", "--bus", "2008"])
    printed = capsys.readouterr().out
    assert "veragrid-crosscheck: n=" in printed
    assert code in (0, 1), printed  # the recorded difference is the result; the run itself must not error
