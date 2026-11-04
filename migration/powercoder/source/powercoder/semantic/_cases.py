"""Test networks for powercoder.semantic: small pandapower cases and the snem-sa-2000 location.

A plain module (not conftest) so test modules can import the constants and builders directly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Final

import pandapower as pp
import pandapower.networks as pn

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SA_CASE: Final[Path] = REPO_ROOT / "data" / "snemSA.m"
"""The real CSIRO South Australia case (fetched by scripts/fetch_csiro_nem_data.py)."""

SA_NETWORK_ID: Final[str] = "snem-sa-2000"


def case9_with_sgen() -> pp.pandapowerNet:
    """IEEE 9-bus plus one static generator, so ``cim:EnergySource`` is exercised too.

    The sgen (10 MW, 2 Mvar at bus 4) is a fixture value with no physical source; it only needs
    to be non-zero so a sign error in the CGMES load-sign convention would show in the results.
    """
    net = pn.case9()
    pp.create_sgen(net, bus=4, p_mw=10.0, q_mvar=2.0, name="test_sgen")
    return net


SMALL_CASES: Final[dict[str, Callable[[], pp.pandapowerNet]]] = {
    "case9+sgen": case9_with_sgen,  # lines, gens, ext_grid, loads, sgen
    "case14": pn.case14,  # adds two-winding transformers (with and without taps) and a shunt
    "case30": pn.case30,  # 41 lines, 2 shunts
}
"""Built-in cases that together cover every exported element table."""
