"""Rewording keeps every task and changes only fit prompts, and only when its inputs reproduce them."""

from __future__ import annotations

import pytest

from wattson.datasets.generate import fit_prompt
from wattson.datasets.reword import reword

INPUTS: dict = {"case": "snem-sa-2000", "element": "bus 1791", "target": 1.027219, "scale_lo": 0.9, "scale_hi": 1.1,
                "tolerance": 0.0011595451595186647, "metric_unit": "pu"}


def row(prompt: str, subfamily: str = "pf-load-scale-fit") -> dict:
    return {"example_id": "ex_1", "subfamily": subfamily, "task": {"prompt": prompt, "inputs": INPUTS}}


def test_v1_reproduces_the_frozen_wording() -> None:
    assert fit_prompt("pf-load-scale-fit", INPUTS, "v1") == (
        "Using the power flow solver, find the scale factor s in [0.9, 1.1] applied to bus 1791 on case "
        "'snem-sa-2000' such that the bus voltage equals 1.027219 pu (within 0.0011595451595186647 pu). "
        "Return only the scale factor.")


def test_a_load_scale_fit_says_every_load_is_scaled_and_one_bus_observed() -> None:
    new = reword(row(fit_prompt("pf-load-scale-fit", INPUTS, "v1")))
    assert "applied to every load's active and reactive demand" in new["task"]["prompt"]
    assert "voltage at bus 1791" in new["task"]["prompt"] and new["task"]["inputs"] == INPUTS


def test_the_open_interval_variant_stays_open() -> None:
    new = reword(row(fit_prompt("pf-load-scale-fit", INPUTS, "v1", interval=False)))
    assert " in [" not in new["task"]["prompt"]


def test_a_prompt_its_inputs_do_not_reproduce_is_refused_and_reports_are_untouched() -> None:
    with pytest.raises(ValueError, match="do not reproduce"):
        reword(row("Find s for bus 1791, please."))
    report = row("Run an AC power flow on case 'snem-sa-2000' and report the result.", "pf-report")
    assert reword(report) is report
    current = row(fit_prompt("pf-load-scale-fit", INPUTS))
    assert reword(current) is current, "rewording is idempotent"
