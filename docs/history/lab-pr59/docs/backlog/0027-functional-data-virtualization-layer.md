# 0027 — A functional data-virtualization layer: `/proc` for the grid, queryable with Arrow

- **Status:** deferred. The concept is the user's (2026-10-23). [0026](0026-walkable-path-namespace-ls-get.md)
  delivers its interface first; this is the engine and the data behind that interface.
- **Supersedes:** README / PRD-0010 §4.4–4.5, which treat weather, forecasts and a forecast DSL
  as separate sources and a separate dialect.

## Concept
One namespace in which observations, forecasts and derived results are all addressable nodes:
- **Walkable:** listing a node lists its children, which is how discovery works.
- **Compositional:** a derived node is a named function of named inputs.
- **Defined by one inspectable grammar:** the 0026 grammar, extended.

## Design
- **Naming.**
  - CIM (IEC 61970/61968) supplies the containment chain (GeographicalRegion → Substation →
    VoltageLevel → Bay → Equipment) and the stable `mRID`.
  - CIM does not define a path syntax. IEC 81346 reference designations (structure) and IEC 61850
    object references (`LD/LN.DO.DA`, measurements) are the standard candidates.
  - Proposed: CIM containment for the segments, `mRID` as the canonical key, `name` as an alias.
  - Confirm CIM's environmental package class names against the schema before naming `/env/…`
    segments after them.
- **Stored or derived nodes.** A derived node's key is the hash of (function, version, its inputs'
  keys). Values are memoised in RocksDB, with the inputs' keys stored alongside, so every value can
  be traced back to its inputs. Listing a node is a prefix scan, because keys are sorted.
- **Time as path segments.** `issued=` and `valid=` are path segments. A query run "as of" a past
  time cannot resolve any later `issued=`. That makes leakage-free hindcasting (PC-REQ-042)
  structural, so it doesn't rely on the agent.
- **Query surface.**
  - DataFusion: a catalog maps branches to tables, plus a table function over path globs.
  - Polars reads the same Arrow batches.
  - Optional: a FUSE or 9P mount.
- **The forecast DSL becomes derived nodes.** `FORECAST` is `/fn/forecast/<engine>/<version>/…`
  with declared inputs, which also gives interchangeable engines (PC-REQ-041).

## Preconditions
- **0026's interface holds up in a smoke run.** Otherwise the namespace design is wrong, and an
  engine for it is wasted.
- **G5 data access:** weather observations and forecasts for SA, with issue times.
- **A network with real containment and names.** snemSA has none: its names are synthetic, it has 1
  zone, and the load doesn't expose coordinates. `mpc.bus_data` holds `x y` values whose units are
  undocumented (0024).
- **Expensive solves, before memoisation pays:** dynamics or large ensembles. Today's study solve
  takes about a second.

## Grammar caveat
PEG's ordered choice and lookahead have no direct context-free equivalent, while llama.cpp GBNF and
llguidance Lark grammars are context-free. Keep the namespace grammar in the subset where they
agree, and keep the grammar-consistency test from 0026.
