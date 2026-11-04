"""Shared fixtures for the G2 engineering tests: a fast built-in case and the CSIRO case.

The fast cases are pandapower's bundled MATPOWER IEEE 9- and 30-bus networks (no
download). snem-sa-2000 is loaded exactly as the MCP server does
(``powermcp._load_network_object``); its tests skip when ``data/`` was not fetched.
"""

from __future__ import annotations

import pandapower as pp
import pandapower.networks as pn
import pytest

from powercoder.inference import powermcp as pm

SNEM_CASE: str = "snem-sa-2000"
SNEM_PRESENT: bool = (pm.REPO_ROOT / pm.CASE_FILES[SNEM_CASE]).exists()
requires_snem = pytest.mark.skipif(not SNEM_PRESENT, reason="data/snemSA.m absent: run scripts/fetch_csiro_nem_data.py")


@pytest.fixture
def case9() -> pp.pandapowerNet:
    """IEEE 9-bus case (fresh copy per test)."""
    return pn.case9()


@pytest.fixture
def case30() -> pp.pandapowerNet:
    """IEEE 30-bus case (fresh copy per test)."""
    return pn.case30()


@pytest.fixture(scope="session")
def snem_net() -> pp.pandapowerNet:
    """The CSIRO snem-sa-2000 network, via the MCP server's own loader."""
    if not SNEM_PRESENT:
        pytest.skip("data/snemSA.m absent")
    net, _ = pm._load_network_object(SNEM_CASE)
    return net
