"""Tests for the pure parts of scripts/veragrid_studies.py: the comparison rules, not the solvers (#162)."""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "veragrid_studies.py"
spec = importlib.util.spec_from_file_location("veragrid_studies", SCRIPT)
assert spec is not None and spec.loader is not None
vs = importlib.util.module_from_spec(spec)
sys.modules["veragrid_studies"] = vs
spec.loader.exec_module(vs)


def side(vm, flows, converged=True):
    return {"converged": converged, "vm": vm, "flows": flows}


def test_max_abs_diff_and_errors() -> None:
    assert vs.max_abs_diff([1.0, 2.0], [1.0, 2.5]) == 0.5
    assert math.isnan(vs.max_abs_diff([1.0, math.nan], [1.0, 1.0]))
    with pytest.raises(ValueError):
        vs.max_abs_diff([1.0], [1.0, 2.0])
    with pytest.raises(ValueError):
        vs.max_abs_diff([], [])


def test_unique_pairs_drops_parallel_circuits_and_ignores_direction() -> None:
    assert vs.unique_pairs([(1, 2), (2, 1), (3, 4)]) == {(3, 4)}


def test_derived_limit_is_a_fraction_of_the_90th_percentile() -> None:
    assert vs.derived_limit(list(map(float, range(101)))) == pytest.approx(0.9 * 90)


def test_flat_sorts_parallel_circuits_so_their_order_cannot_matter() -> None:
    assert vs.flat({(1, 2): [5.0, 3.0]}) == ([(1, 2), (1, 2)], [3.0, 5.0])


def test_agreement_passes_and_a_flow_difference_fails() -> None:
    a = side([1.0, 0.99], {(0, 1): [10.0]})
    ok, _ = vs.compare("x", a, side([1.0, 0.99], {(0, 1): [10.0001]}), 50.0, 1e-4, 1e-3)
    assert ok
    ok, text = vs.compare("x", a, side([1.0, 0.99], {(0, 1): [10.5]}), 50.0, 1e-4, 1e-3)
    assert not ok and "max_abs_ds_mva=5.000e-01" in text


def test_a_different_overload_set_fails_even_when_numbers_are_close() -> None:
    a = side([1.0, 1.0], {(0, 1): [50.0004]})
    b = side([1.0, 1.0], {(0, 1): [49.9996]})
    ok, text = vs.compare("x", a, b, 50.0, 1e-4, 1e-3)
    assert not ok and "same=False" in text


def test_islanded_buses_are_excluded_and_counted() -> None:
    pp = side([1.0, math.nan, 1.0], {(0, 2): [10.0], (1, 2): [0.0]})
    vg = side([1.0, 0.97, 1.0], {(0, 2): [10.0], (1, 2): [99.0]})
    ok, text = vs.compare("x", pp, vg, 50.0, 1e-4, 1e-3)
    assert ok and "islanded_buses_excluded=1" in text


def test_convergence_disagreement_fails_and_joint_failure_agrees() -> None:
    ok, text = vs.compare("x", side([1.0], {}, True), side([1.0], {}, False), 1.0, 1e-4, 1e-3)
    assert not ok and "convergence differs" in text
    ok, _ = vs.compare("x", side([], {}, False), side([], {}, False), 1.0, 1e-4, 1e-3)
    assert ok
