# 0030 — Re-run historical NEM dispatch intervals with nempy

- **Status:** open, **blocked on data access**. **Merit: third** of the 2026-10-23 tool review; the
  most AEMO-specific activity on the list.
- **Affects:** Lab 4 (AEMO reconciliation and constraint reasoning), G5, and a new task family.

## Why
AEMO's central operation is dispatch: NEMDE solving each 5-minute interval subject to constraint
equations. nempy (UNSW CEEM, BSD-3-Clause, checked 2026-10-23) re-runs NEM dispatch from public
AEMO data, and the published outcomes are the check. Example tasks:
- why did unit X dispatch to Y MW;
- which constraint bound;
- what happens if this constraint is relaxed.

The answers can be verified by re-solving the interval.

## Blocker
NEMWeb pulls are blocked for lab agents (403, org policy; AGENTS.md capability matrix). Lab 4 has a
local cache (`data/nemosis_cache`); find out how many intervals it covers before scoping. Do not
route around the block.

## Scope (once data is available)
1. **Reproduce intervals:** re-solve N cached intervals with nempy and compare unit dispatch and
   prices with AEMO's published results. Record the agreement, and use nempy's own documented
   simplifications as the named stand-ins.
2. **Task families:**
   - the binding constraint;
   - a unit's marginal-price reason;
   - sensitivity to one constraint's right-hand side.

## Licence
nempy BSD-3-Clause. AEMO data terms to be recorded at adoption, per the PSCADOSSE rule.
