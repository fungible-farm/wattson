"""CIM semantic network model for PowerCoder (PRD-0010 gate G4, PC-REQ-020/021/022).

First step of G4 (docs/backlog/0018-g4-cim-semantic-network-intelligence.md, "Cheapest first
step"): export a pandapower network -- in particular the real CSIRO South Australia case
``data/snemSA.m`` (network id ``snem-sa-2000``) -- to a CGMES-shaped CIM RDF graph, validate it
with SHACL, convert it back to pandapower, and prove the round trip by identical AC power flow.

Modules:

- :mod:`powercoder.semantic.namespaces` -- the CIM16 / CGMES namespace (the same one Lab 6's
  PRD-0007 traceability uses) plus a named extension namespace for pandapower-only fields.
- :mod:`powercoder.semantic.export` -- pandapower net -> rdflib ``Graph`` (Turtle).
- :mod:`powercoder.semantic.import_` -- rdflib ``Graph`` -> pandapower net.
- :mod:`powercoder.semantic.validate` -- pyshacl validation against ``shapes.ttl``.
- :mod:`powercoder.semantic.topology` -- physical vs. electrical connectivity under a switching
  state (PC-REQ-021), derived from the graph alone.

Sandbox stand-in (named, per AGENTS.md): this is a *pragmatic CGMES-shaped subset*, not a
CGMES-conformant export. It uses CGMES 2.4.15 / CIM16 class and property URIs, but it writes one
merged graph rather than separate EQ/TP/SSH/SV profile instances, carries no OWL/RDFS CIM schema,
and stores pandapower-only fields under an extension namespace. See ``export.py``'s module
docstring for exactly what is and is not represented.

Licences (recorded at adoption, docs/PSCADOSSE.md policy):

- ``rdflib`` -- BSD-3-Clause (7.6.0), a direct project dependency since 2026-10-23.
- ``pyshacl`` -- Apache-2.0 (0.40.1), a direct project dependency since 2026-10-23.
"""
