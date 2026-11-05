# 0024 — Element discovery: let the agent find valid ids, and when a graph search is worth it

- **Status:** step 1 done on `chore/bus-discovery` (`6b78405`, `e5e8f93`); steps 2–4 open. Found
  2026-10-23 reading r6b's Qwen student failures (AGENTS.md FM-26).
- **Affects:** every tool argument that names an element (`observation_id`, `element_id`,
  `contingency_line`), [0018](0018-g4-cim-semantic-network-intelligence.md) (G4), and DEC-028's
  hint-returning proxy in the r00t register.

## Evidence (measured)
- **The failure.** On 3 of the r6b student's 25 report tasks, the first call observed bus 999. The
  tool answered "Unknown observation element", and `load_network` returns counts only. The student
  then guessed bus 1, 2, 3, … (or 0 nine times) until its 12 turns ran out. The other 22 report
  tasks used bus 985 and passed.
- **The ids are sparse.** snem-sa-2000's 503 buses run from 985 to 10106, so stating a range would
  name ids that don't exist.
- **What the case carries about a bus.** These are pandapower's `net.bus` columns, as loaded by
  `pm._load_network_object`:
  - names are synthetic (`bus_2355` …), all 503 distinct;
  - 1 zone and no `geo`;
  - 20 voltage levels from 0.4 to 275 kV.
- **Data the load doesn't use.** `data/snemSA.m` also has these tables, which nothing in the repo
  reads:
  - `mpc.bus_data` holds `x y` per bus. The file doesn't say what the units are or whether the
    values are geographic. Lab 1's comment "snemSA.m has none" predates this finding.
  - `gen_data`, `branch_data` and `load_data` hold names that encode endpoints
    (`line_4002_to_4109_1_cp`).
- **Real-world names are only a heuristic mapping.** The only ones in the repo are Lab 4's
  `duid_mapping.csv`: 70 AEMO DUIDs with station, participant and region. That file is a
  capacity-proximity mapping onto synthetic generators, not an identity. For example, OSB-AG and
  GSWF1A both map to bus 1684.
- **The graph already exists.** `powercoder.semantic.export_network` builds snem-sa-2000's CIM graph:
  - 57,597 triples, built in 1.43 s;
  - Turtle in 1.28 s, 3.5 MB;
  - a five-row SPARQL `TopologicalNode` name query takes 0.043 s;
  - 0 SHACL violations (G4).

  Measured once on GB10, 2026-10-23, with rdflib from the `uv run --with` overlay. Loading from a
  cached Turtle file wasn't measured.

## Options

| Option | Answers | Cost | Verdict |
|---|---|---|---|
| 1. Unknown-id errors name the id, the element count and the lowest 10 ids | "what is a valid id?" | none. Successful responses are unchanged, and the 357 v2 trajectories rebuild byte-identically | **done** (`studies.unknown_element`); the v3 trajectories add 18 `unknown_bus` recoveries |
| 2. A `find_elements` tool that runs fixed, named SPARQL queries over the CIM graph: by kind, voltage level, connectivity ("buses at 275 kV", "the ends of line 94", "generators at bus 1684") | structural questions, with ids taken from a SHACL-valid graph | adds a tool, which changes every prompt's tool block, so students must be retrained and the run is not comparable with r6 (new run root, a `tool_set` condition in `compare_agents.CONDITIONS`). Each attempt starts a fresh MCP server, so graph build time (1.43 s, or less from cached Turtle, which is unmeasured) is paid per attempt unless the server caches it | **recommended next.** No free-form SPARQL from the model: fixed queries keep the answers checkable and the cost bounded |
| 3. SHACL as hints (DEC-028): a refused argument returns the constraint it broke and the shape's allowed values | "why was this refused?" | small; the same message channel as option 1 | fold into option 2. SHACL validates the graph the tool answers from; it is not a search method |
| 4. Vector embeddings or BM25 over element names | natural-language lookup ("the Torrens Island units") | an embedding model, an index, and every answer's provenance | **not on this case.** Names are synthetic, so similarity has nothing to match. The only real names (Lab 4) are a heuristic mapping, and an index would present them as fact unless every hit carries the mapping's rationale. Revisit for a case with real names and coordinates |

## Next steps
1. ~~Name valid ids in errors; add the `unknown_bus` recovery~~ (done; merge after r6b's strict and
   prompted arms finish, because every attempt starts its MCP server from the working tree).
2. `uv add rdflib pyshacl` (also after the GPU queue). Measure graph load from cached Turtle against a
   fresh build in an MCP server process.
3. **Superseded by [0026](0026-walkable-path-namespace-ls-get.md)** (walkable `ls`/`get` namespace, 2026-10-23); the
   graph stays the source of truth behind it. Original plan: `find_elements` with 3–5 named queries, deterministic tests against the graph and against
   pandapower's tables, and trajectories that use it on a bad id. Evaluate as its own arm.
4. ~~Find out what `bus_data` `x y` are~~ **(checked 2026-10-23).** They come from CSIRO's
   `Data/original/XY Position.csv` (`BUS#,x,y`; the dataset is CC BY 4.0). The repository documents
   neither their units nor their meaning. Measured over its 2,340 buses: all integers,
   x −4058…3065, y −6670…7565. They are therefore **not latitude/longitude** (they fall outside
   ±180/±90) and not a projected easting/northing for South Australia (those run to 10^5–10^6 m),
   so they are most plausibly single-line-diagram layout positions. Use them for drawing (backlog
   0004's symbolic single-line), never for distance or geography.

## Acceptance
- **Smoke run:** a smoke run with the new tool has no attempt that guesses an id after a refusal
  (journal check: no `run_study_trial` with an id the previous response didn't name).
- **Ids:** every id `find_elements` returns exists in `net[table].index`, checked by a test.
- **Comparisons:** the comparison names the tool-set difference (`compare_agents` refuses otherwise).
