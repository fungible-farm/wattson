"""Shared pytest fixtures for wattson.semantic: the snem-sa-2000 network and its CIM graph.

rdflib is required for every test here; pyshacl only for the SHACL tests. Both are project
dependencies; each test module still calls ``pytest.importorskip`` so a partial environment
reports a skip rather than an import error.
"""
from __future__ import annotations

import pandapower as pp
import pytest

from wattson.semantic._cases import SA_CASE, SA_NETWORK_ID


@pytest.fixture(scope="session")
def sa_net() -> pp.pandapowerNet:
    """snem-sa-2000 loaded through PowerMCP's own loader (imported, not modified)."""
    if not SA_CASE.exists():
        pytest.skip(f"{SA_CASE} absent -- run scripts/fetch_csiro_nem_data.py")
    pytest.importorskip("powerio")
    from wattson.inference.powermcp import _load_network_object

    net, _hash = _load_network_object(SA_NETWORK_ID)
    return net


@pytest.fixture(scope="session")
def sa_graph(sa_net):  # noqa: ANN001, ANN201 -- pytest fixture
    """The exported snem-sa-2000 graph (read-only for tests; copy before mutating)."""
    pytest.importorskip("rdflib")
    from wattson.semantic.export import export_network

    return export_network(sa_net, model_id=SA_NETWORK_ID)
