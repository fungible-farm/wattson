"""Verified engineering mathematics for PowerCoder (PRD-0010 gate G2, backlog 0016).

Pure-Python study functions that each return a numerical proof contract
(PC-REQ-016) alongside their engineering result, and that are cross-checked
against an independent formulation in their tests:

- ``proof``        -- the numerical proof contract (PC-REQ-016).
- ``dc_opf``       -- DC optimal power flow, pandapower ``rundcopp`` (PIPS) cross-checked
                      against an independent LP solved by HiGHS through scipy (PC-REQ-011).
- ``sensitivities``-- PTDF and LODF with conditioning, cross-checked by finite-difference
                      injections and actual DC outage re-solves (PC-REQ-013).
- ``contingency``  -- N-1 branch-outage screening with coverage labels and base-relative
                      violations (PC-REQ-012).

Not yet wired into the MCP server (``powercoder/inference/mcp_server.py``); that is G1's
command surface. Licences of the numerical dependencies, recorded at adoption per
``docs/PSCADOSSE.md``: pandapower BSD-3-Clause, scipy BSD-3-Clause (HiGHS, bundled in
scipy, MIT), numpy BSD-3-Clause.
"""
