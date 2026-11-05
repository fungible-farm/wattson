"""The recorded cases (0023): the IEEE 39 case keeps the OPF and thermal signal it was chosen for."""

from __future__ import annotations

import pytest

from wattson.engineering.cases import CASES, REPO_ROOT, load_case

HAS_PAB: bool = (REPO_ROOT / CASES["ieee39-pab-stressed"]["path"]).exists()


def test_every_case_records_source_licence_and_measurement() -> None:
    for record in CASES.values():
        assert record["source"] and record["licence"] and record["measured"]


@pytest.mark.skipif(not HAS_PAB, reason="run `just lab10-fetch` first")
def test_ieee39_has_a_binding_flow_limit_and_an_lmp_spread() -> None:
    from wattson.engineering.dc_opf import run_dc_opf

    result = run_dc_opf(load_case("ieee39-pab-stressed"))
    assert result["solved"] and result["cross_check"]["status"] == "agree"
    assert any(b["constraint"] == "flow_limit" for b in result["binding_constraints"])
    low, high = result["lmp_range_eur_per_mwh"]
    assert high - low > 1.0
