"""RDF namespaces for the CGMES-shaped CIM graph.

``CIM`` is the CIM16 namespace used by CGMES 2.4.15 and already cited by Lab 6's PRD-0007 class-URI
traceability (``labs/06-sysml-digital-thread/build_grid_instances.py`` ``CIM_NAMESPACE``), so the
class URIs here (``cim:TopologicalNode``, ``cim:ACLineSegment``, ``cim:PowerTransformer``,
``cim:SynchronousMachine`` ...) are the same strings that pipeline emits.

``PCX`` is this repo's extension namespace for pandapower-only quantities CGMES has no property for
(pandapower row index, ``parallel``, derating factor ``df``, load/generator ``scaling``, slack
weight, the external grid's voltage angle, bus voltage-band limits). It is a URN on purpose: it
claims no web domain, and anything under it is by definition *not* CIM.
"""
from __future__ import annotations

from typing import Final

from rdflib import Namespace

CIM_URI: Final[str] = "http://iec.ch/TC57/2013/CIM-schema-cim16#"
"""CIM16 namespace (CGMES 2.4.15); identical to Lab 6's ``CIM_NAMESPACE``."""

MD_URI: Final[str] = "http://iec.ch/TC57/61970-552/ModelDescription/1#"
"""IEC 61970-552 model-description header namespace used by CGMES ``md:FullModel``."""

PCX_URI: Final[str] = "urn:powercoder:semantic:pandapower-ext#"
"""Extension namespace for pandapower-only fields (named stand-in, not CIM)."""

CIM: Final[Namespace] = Namespace(CIM_URI)
MD: Final[Namespace] = Namespace(MD_URI)
PCX: Final[Namespace] = Namespace(PCX_URI)


def bind_prefixes(graph) -> None:  # noqa: ANN001 -- rdflib.Graph; kept import-light
    """Bind the ``cim``, ``md`` and ``pcx`` prefixes on ``graph`` for readable Turtle."""
    graph.bind("cim", CIM)
    graph.bind("md", MD)
    graph.bind("pcx", PCX)
