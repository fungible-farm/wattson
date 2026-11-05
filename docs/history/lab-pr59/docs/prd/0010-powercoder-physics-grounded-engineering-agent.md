# 0010 — PowerCoder: physics-grounded autonomous power-system engineering

- **Status:** proposed 2026-10-23. Gates G0–G8 are not started. The r6b evaluation (§2) is the
  input to G0.
- **Depends on:** the r6 study (`docs/POWERCODER_HANDOFF_2026-10-23.md`), backlog 0008–0014, Lab 10
  (RestoreBench), Lab 2 (contingency screening), Lab 5 (DPsim), Labs 6, 8 and 9 (CIM traceability,
  Grid2Op, `rust/mission-engine`).
- **Backlog items:** one per gate. G0 is [0008](../backlog/0008-powercoder-benchmark-cannot-measure-agent-value.md);
  G1–G8 are [0015](../backlog/0015-g1-typed-command-protocol-and-gateway.md)–[0022](../backlog/0022-g8-independent-superiority-assessment.md).
- **Version:** 0.1. Research architecture; any operational integration needs separate
  authorisation.
- **Approach:** sovereign, open-source first, Rust-orchestrated, solver-verified.

**Primary objective:** demonstrate progressively better performance on independently verifiable
power-system engineering tasks. Every milestone produces an independently testable capability.
Improvements from training are measured separately from those due to tools, data access or extra
inference compute.

## 0. Reconciled with the repository (measured 2026-10-23)

The specification below was drafted outside the repo. These facts are measured here, and they take
precedence wherever the text differs.

| Topic | Measured state |
|---|---|
| Students | Qwen3.5-4B and Granite 4.2 8B LoRA (r32, decoder linear layers, 1 epoch, 46 steps). Trained on 357 scripted, graded tool-call trajectories: 238 plain, plus 119 recovery variants whose injected mistake is context only. Training took 2.7 h (Qwen) and 3.4 h (Granite) on the GB10. Both passed `generation_check` 8/8 |
| Smoke check (20 validation tasks, ≥10 to pass) | r6b: Qwen student 17/20, Granite student 11/20. The first r6 attempt was stopped after FM-25 made 2 of its 3 report tasks unsolvable |
| Four-model 100-task evaluation | **In progress (r6b).** No benchmark result exists yet |
| Latency (idle server, concurrency 1, 512 forced tokens) | Qwen base: TTFT 0.16 s, 19.2 tok/s, 26.7 s per request, peak 22.8 GB host memory. The other three models are pending |
| The caveat that frames G0 | One network (`snem-sa-2000`), one fit interval, 36 of 69 benchmark task keys shared with training, and every task solvable by the ~30-line policy the students imitate (backlog 0008) |
| PowerMCP / grader / provenance | `powercoder/inference/{powermcp,studies,mcp_server,evaluate_agent,provenance}.py`. Provenance is weaker than its requirement (backlog 0010) |
| Serving | r6 models are served by **vLLM 0.31** (xgrammar 0.2.7, llguidance 1.7.6). llama-swap/llama.cpp serves GGUF models on shinyDGX, and the r00t register flags that as DEC-013/OPEN-001: GGUF serving belongs on sm3lly. A llama.cpp planner therefore goes on sm3lly, or the open decision is closed first |
| Constrained generation | vLLM builds xgrammar's model-native tool grammar per request. The Qwen-XML tag is xgrammar-only in vLLM; llama.cpp offers GBNF, plus LLGuidance as an optional build that llama-swap's binary lacks ([backlog 0014](../backlog/0014-grammar-engines-xgrammar-llguidance-gbnf.md)) |
| Rust | `rust/` already has `mission-engine` (Lab 9: a Grid2Op bridge and FSM), `systhread-core` and `phase-model`. The G1 IR extends this workspace |
| CIM | Lab 6 (LinkML → SysML, CIM class-URI traceability, PRD-0007) and Labs 8/9 (cim-gridy spikes, Grid2Op dataset from `data/snemSA.m`). There is no RDF/SHACL store yet |
| SCADA, sparse PMU, enterprise data lake | **Not present in this repo.** External systems that need approved access. G5's synthetic path doesn't need them |
| NEM data | AEMO NEMWeb returns 403 by org policy in this sandbox (AGENTS.md capability matrix). The CSIRO synthetic cases are available |
| External benchmarks | RestoreBench (Lab 10, MIT + CC BY 4.0): IEEE 118 witnesses verify 45/46 on aarch64 ([PowerAgentBench#13](https://github.com/Power-Agent/PowerAgentBench/issues/13)), PEGASE 89 46/46. PowerAgentBench Levels 1–2 have **no licence** ([#11](https://github.com/Power-Agent/PowerAgentBench/issues/11)), so G8 can't use them until it's resolved |
| Upstream scoreboard | PowerAgentBench's metric families (discovery, evidence, mitigation, efficiency, reliability) are reused where their licence allows, rather than duplicated |

**Requirement IDs.** This PRD's `PC-REQ-0xx` numbering supersedes the two interim IDs in AGENTS.md:

| Interim ID | Superseded by |
|---|---|
| PC-REQ-1 (answer numbers trace to tool output or prompt) | PC-REQ-004, evidence provenance |
| PC-REQ-2 (strict tool calling as its own 2×2) | PC-REQ-002 and PC-REQ-055 |

Until G1, the tests named in AGENTS.md remain the verification for both.

## 1. Mission

Develop an AI agent that can independently investigate, model, forecast and evaluate
electricity-network operating conditions. It uses authoritative network data, validated numerical
tools and evidence-grounded reasoning. The system SHALL progressively demonstrate:

1. Correct execution of a domain-specific command protocol.
2. Reliable selection and composition of electrical engineering tools.
3. Physically valid calculations with independently checkable evidence.
4. Grounded understanding of electrical network topology and equipment.
5. Investigation of historical and near-real-time network conditions.
6. Probabilistic forecasting that incorporates electrical and environmental conditions.
7. Improvements in investigative effectiveness through verifier-guided training.
8. Measurable performance against deterministic baselines, established engineering benchmarks and
   qualified human engineers.

**Success means demonstrated task performance**, not model size, conversational fluency, or
benchmark claims without independent evaluation. The research question is whether specialised
models can outperform qualified engineers on clearly defined tasks while staying auditable,
reproducible and safe. Human authority over operational decisions is retained.

## 2. Baseline interpretation

The LoRA result shows that the tool protocol can be learned efficiently. It doesn't yet show
superior engineering judgement. The next research question:

> Can the model learn to choose, sequence and interpret physically grounded investigations more
> effectively than a fixed procedural script, within an explicit tool and compute budget?

## 3. Reference architecture

```text
OPERATOR → NATURAL-LANGUAGE TASK → SOVEREIGN PLANNER (local; see §0 Serving)
        → CONSTRAINED DSL (JSON Schema / CFG) → TYPED RUST IR
        → POLICY GATE + TASK PLANNER → TOOL ORCHESTRATOR
            ├─ SEMANTIC DATA:  CIM / OWL / RDF / SHACL / SPARQL
            ├─ OBSERVATIONS:   SCADA / PMU / weather / data lake / time series
            └─ NUMERICAL:      NR / Gauss–Newton / OPF / contingencies / sensitivities / dynamics
        → EVIDENCE / VERIFIER (residuals, limits, provenance, quality) → VERIFIED FINDINGS
        → BENCHMARKING (scoreboard) + TRAINING (SFT / RL / distillation) → EVALUATION → HUMAN REVIEW
```

Six separated responsibilities:

| Responsibility | Does |
|---|---|
| Language model | Selects and plans investigations |
| Semantic layer | Resolves equipment and relationships |
| Evidence layer | Retrieves observations and forecasts |
| Numerical layer | Performs calculations |
| Verifier | Checks mathematical, procedural and safety constraints |
| Evaluator | Measures performance independently of the agent |

The same interfaces SHALL serve synthetic networks, historical investigations and authorised
read-only enterprise deployments.

## 4. Functional requirements

### 4.1 Command language and execution

- **PC-REQ-001 Typed command protocol.** A versioned DSL with at least these commands, compiled to a
  typed IR. The textual DSL SHALL NOT execute directly against operational systems.

  | Command | Purpose |
  |---|---|
  | `RECALL` | Retrieve observations, documents or existing forecasts |
  | `RESOLVE` | Resolve CIM assets and relationships |
  | `ESTIMATE` | Reconstruct states from measurements |
  | `SOLVE` | Solve network equations |
  | `OPTIMIZE` | Constrained optimisation |
  | `SIMULATE` | Evaluate hypothetical conditions |
  | `FORECAST` | Predict future quantities |
  | `HINDCAST` | Reproduce historical forecasts without future information |
  | `COMPARE` | Evaluate competing solutions or hypotheses |
  | `EXPLAIN` | Evidence-grounded explanation |

- **PC-REQ-002 Constrained generation.** Grammar- or schema-constrained command generation, through
  llama.cpp grammars, LLGuidance, or an independently validated XGrammar integration (vLLM's, per
  0014). The grammar SHALL be generated from, or validated against, the canonical command schemas.
- **PC-REQ-003 Executor isolation.** A policy-enforcing gateway independently validates:
  - permissions;
  - asset and dataset scope;
  - units and types;
  - resource limits;
  - temporal constraints;
  - query complexity;
  - safety restrictions.

  Valid syntax SHALL NOT imply authorisation.
- **PC-REQ-004 Evidence provenance.** Every reported numerical engineering result references a tool
  result, a validated dataset or a reproducible derivation. **The LLM SHALL NOT invent numerical
  engineering results.**
  - **The evidence contract:** `result_id`, `command_id`, `command_version`, `tool_version`,
    `input_snapshot_ids`, `dataset_versions`, `model_or_solver_version`, `result_value`,
    `physical_units`, `numerical_status`, `validation_evidence`, `uncertainty`,
    `execution_timestamp`.
  - **Result kinds:** unverified, approximate and hypothetical results are explicitly
    distinguishable.
- **PC-REQ-005 Reproducibility.** Inputs, configuration, dependency versions and seeds are recorded
  so results reproduce within declared tolerances. Recorded experiments replay without the
  originating LLM.

### 4.2 Electrical engineering capabilities

- **PC-REQ-010 AC and DC power flow.** Newton–Raphson, or another named method. Reports convergence,
  mismatches, iterations and limit violations.
- **PC-REQ-011 Optimal power flow, AC and DC.** Reports objective, feasibility, binding constraints,
  dispatch, duals or shadow prices where they exist, and optimality information. AC OPF SHALL NOT
  be called globally optimal without a certificate or bound.
- **PC-REQ-012 Contingency analysis, N-1 and bounded N-2.** Distinguishes exhaustively enumerated,
  heuristically selected, validated and unexamined contingencies. Ranks by defined severity and
  identifies violations introduced relative to the base case. A limited search SHALL NOT be
  represented as complete screening.
- **PC-REQ-013 Sensitivities.** PTDF, LODF, the AC power-flow Jacobian and V/Q sensitivities, with
  their conditioning, assumptions and residual checks.
- **PC-REQ-014 State estimation.** SCADA-based weighted least squares (Gauss–Newton or another named
  method), not dependent on PMUs. Reports observability, residuals, convergence, the state,
  bad-data diagnostics and uncertainty. Robust estimators are optional.
- **PC-REQ-015 Dynamic and fault analysis** through specialised services. Steady-state results SHALL
  NOT be presented as evidence of transient stability.
- **PC-REQ-016 Numerical proof contract.** Each tool returns `converged`, `equation_residuals`,
  `constraint_violations`, `feasibility_status`, `optimality_gap_or_bound`, `solver_iterations`,
  `conditioning` and `validation_warnings`, as applicable. A solver success flag alone SHALL NOT
  constitute proof. FM-17 and FM-25 are examples of why.

### 4.3 CIM knowledge graph

- **PC-REQ-020 Semantic network model.** CIM over RDF:
  - OWL/RDFS semantics where they apply;
  - SHACL validation and SPARQL retrieval;
  - CIM identifiers, connectivity, parameters and measurement associations;
  - geography.

  CGMES (ENTSO-E) RDF/RDFS/SHACL profiles are the interoperability target.
- **PC-REQ-021 Versioned electrical topology.** Physical connectivity is distinct from electrical
  connectivity under a switching state. Snapshots keep the model version, switching state, tap
  positions, validity interval, source and quality.
- **PC-REQ-022 Controlled graph queries.** Typed, bounded topology operations. Normal operation needs
  no unrestricted SPARQL.
- **PC-REQ-023 Derived electrical relationships** (PTDF, LODF and Jacobian relationships) stay
  distinct from authoritative CIM topology.

### 4.4 Observations and environmental evidence

- **PC-REQ-030 SCADA.** Read-only historical and authorised near-real-time observations, resolved to
  CIM equipment and measurements.
- **PC-REQ-031 Sparse PMU.** Synchronised processing where PMUs exist. PMU coverage is never assumed,
  and electrical proximity is distinct from geographic proximity.
- **PC-REQ-032 Enterprise data lake.** Authorised, read-only, permission-enforced and bounded.
  Initial datasets:
  - weather observations and weather forecasts;
  - renewable-generation and demand observations;
  - existing authoritative forecasts.
- **PC-REQ-033 Temporal consistency.** Valid time, availability time, issue time, source and
  quality are kept, so the information available at any historical instant can be reconstructed.
- **PC-REQ-034 Environmental reasoning.** Weather-dependent variability, thermal conditions and
  forecast errors. Correlation SHALL NOT establish electrical causation.

### 4.5 Forecasting dialect

- **PC-REQ-040 Forecast commands.** Explicit target, horizon, resolution, inputs and outputs. For
  example:

  ```text
  FORECAST demand FOR REGION VIC1 HORIZON 24 HOURS RESOLUTION 30 MINUTES
      USING HISTORICAL_SCADA, WEATHER_FORECAST  METHOD BEST_VALIDATED
      RETURN P10, P50, P90, MODEL_ID, UNCERTAINTY;
  ```

- **PC-REQ-041 Interchangeable engines:** StatsForecast, LightGBM, Chronos, TimesFM and physical
  models. Each records its version, licence, validation status and targets. Licences are recorded at
  adoption, per PSCADOSSE.
- **PC-REQ-042 Leakage-free hindcasting.** Only data, models and inputs available at the declared
  issue time. Revised data SHALL NOT silently enter a historical forecast.
- **PC-REQ-043 Probabilistic forecasting.** Quantiles; correlated scenario ensembles SHOULD be
  supported. Metrics: pinball loss, MAE, WAPE, interval calibration, and skill against a baseline.
  MAPE is not used near zero without safeguards.
- **PC-REQ-044 Forecast-driven physical simulation.** Forecast inputs, assumed topology and controls,
  calculated states and violation probabilities are kept distinct. Probabilities come from validated
  statistical or scenario methods, never unsupported LLM estimates.

### 4.6 Agent judgement and training

- **PC-REQ-050 Study selection** from natural-language objectives: power flow, state estimation,
  OPF, contingency analysis, dynamics or forecasting.
- **PC-REQ-051 Multi-tool planning** with dependencies and bounded compute budgets.
- **PC-REQ-052 Verifier-guided training.** Reinforcement learning with verifiable rewards (GRPO or a
  compatible method).
  - **Rewards:** validated success, physical feasibility, accuracy, evidence completeness, tool-call
    efficiency and compute cost.
  - **Penalties:** explicit penalties for unsafe or unsupported conclusions.
- **PC-REQ-053 Procedural task generation** across networks, operating conditions and contingencies,
  diverse enough to discourage memorising a topology. The r6 set is one network and one interval
  (0008).
- **PC-REQ-054 Teacher distillation.** A larger sovereign teacher (`powercoder/distillation/teacher.py`,
  llama-swap Qwen3.8) MAY propose trajectories. Only independently validated outcomes enter the
  corpus; teacher confidence is never verification.
- **PC-REQ-055 Inference-time search.** Several candidate investigations, the result chosen by the
  verifier, with each candidate's budget and evidence recorded. The r6b strict-tool-calling 2×2 is
  the first measurement of constrained generation's effect.

## 5. Verification scoreboard

Establish this before further model optimisation, reusing PowerAgentBench/RestoreBench metric
families where licensed.

| Dimension | Primary metrics | Comparison |
|---|---|---|
| Protocol competence | Valid calls, schema repairs, tool errors | Prompted base vs r6 |
| Electrical correctness | Residuals, feasibility, violations | Independent numerical reference |
| Contingency discovery | Top-k recall, severity regret, false-safe rate | Exhaustive or hidden reference |
| OPF | Objective regret, feasibility, time | Reference optimiser |
| Recovery | Success, action count, validation budget | RestoreBench baselines |
| Forecasting | Pinball loss, MAE, calibration, skill | Same-horizon authoritative forecast |
| Investigation | Study selection, evidence coverage | Scripted and expert baselines |
| Efficiency | Wall time, tokens, tool calls, compute | Equal-budget baselines |
| Engineering judgement | Blind expert assessment | Qualified engineers |
| Safety | Unauthorised actions, unsupported claims | Zero tolerance |

- **No aggregate hiding:** results are reported by task family, so a single aggregate can't hide a
  safety or critical-accuracy regression.
- **Outperformance claims name their dimension.** "Faster on specified tasks", "more accurate than a
  defined baseline", "better contingency discovery at equal budget", "better forecast skill on
  unseen periods" and "better reasoning under blind expert review" are different claims, each needing
  its own evidence.

## 6. Milestone gates

Every gate has an independently executable acceptance test and an immutable evidence package
(§8). A gate is complete only when its criteria are met. Each gate's detail and status live in its
backlog item.

| Gate | Item | Priority | Depends on | Proves |
|---|---|---|---|---|
| **G0** Baseline | [0008](../backlog/0008-powercoder-benchmark-cannot-measure-agent-value.md) | A | r6b | What is already learned, and whether fine-tuning is the bottleneck |
| **G1** Trusted command execution | [0015](../backlog/0015-g1-typed-command-protocol-and-gateway.md) | A | G0 | The model uses tools without becoming the authority that executes them |
| **G2** Verified engineering mathematics | [0016](../backlog/0016-g2-verified-engineering-mathematics.md) | A | G1 | Numerical capability is physically grounded and checkable |
| **G3** Verified investigative learning | [0017](../backlog/0017-g3-verifier-guided-investigative-learning.md) | A | G0, G2 | Learning improves decisions beyond the scripted procedure |
| **G4** CIM semantic network intelligence | [0018](../backlog/0018-g4-cim-semantic-network-intelligence.md) | B | G1 | Structure comes from explicit semantics, not memorised descriptions |
| **G5** Enterprise observations and weather | [0019](../backlog/0019-g5-observations-and-weather.md) | B | G4 | A defensible, time-consistent evidence set from several sources |
| **G6** Autonomous engineering investigations | [0020](../backlog/0020-g6-autonomous-engineering-investigations.md) | B | G2, G4, G5 | Contextual judgement across tools |
| **G7** Probabilistic forecasting | [0021](../backlog/0021-g7-probabilistic-forecasting.md) | B | G2, G5 | Calibrated, leakage-free forecasts tied to physical models |
| **G8** Independent superiority assessment | [0022](../backlog/0022-g8-independent-superiority-assessment.md) | C | G3, G6, G7 | Whether the system beats specified human and computational baselines |

```text
G0 → G1 ─┬─ G2 → G3 ──────────────┐
         └─ G4 → G5 ─┬─ G6 ───────┼─→ G8
                     └─ G7 ───────┘
```

- **Parallel work:** G3 and G4–G7 may proceed in parallel once their prerequisites are met.
- **No data-access blocker:** numerical intelligence doesn't depend on enterprise data access, so
  the graph and forecasting work SHALL NOT block G1–G3.
- **No operational control:** no gate authorises autonomous operational control.

## 7. Required end-to-end demonstrations

| Demonstration | Task | Gates | Succeeds when |
|---|---|---|---|
| A. Verified mathematical reasoning | "Determine the largest permissible transfer under a defined set of contingencies" | G2–G3 | Better accuracy or efficiency than a scripted reference, with no loss of physical validity |
| B. Evidence-grounded investigation | "Explain an observed voltage disturbance from topology, SCADA, available PMUs and weather history" | G4–G6 | Reproducible evidence, defensible conclusions, explicit unresolved uncertainty |
| C. Forecasting and physical risk | "Probability of voltage-limit violations over the next six hours" | G7–G8 | Calibrated forecasts, independently validated AC calculations, full temporal provenance |

## 8. Mandatory evidence package

Each gate writes a `milestone/` directory containing `requirements.json`,
`requirements-traceability.json`, `acceptance-tests.json`, `benchmark-manifest.json`,
`execution-results.jsonl`, `solver-proofs/`, `provenance/`, `reproducibility/`, `scorecard.json`
and `gate-decision.json`.

- **Traceability:** requirements have stable IDs and verification methods, traced as requirement →
  design component → implementation → test → execution evidence → acceptance → gate.
- **Exchange formats:** export to ReqIF/SysML v2 SHOULD go through the kr0ki provider (r00t
  DEC-031/032) and Lab 6's pipeline.
- **Gate decisions:** each gate decides `PASS`, `FAIL`, `BLOCKED` or `WAIVED`. Waivers need explicit
  authorisation and stay visible; a waived safety requirement never becomes a pass.

## 9. Architectural and safety constraints

| ID | Constraint |
|---|---|
| PC-CON-001 | Sovereign execution: sensitive data and derived information stay inside approved boundaries; inference and numerics run locally |
| PC-CON-002 | Read-only operational integration: no switching, dispatch or SCADA control; simulations act only on isolated model state |
| PC-CON-003 | Human authority: recommendations need authorised human review before any operational execution |
| PC-CON-004 | Separation of verification: the training agent can't modify hidden answers, rewards or validation results |
| PC-CON-005 | Source integrity: untrusted retrieved text can't override policies or tool authorisation |
| PC-CON-006 | Model independence: the protocol works across model families, sizes and inference engines |
| PC-CON-007 | OSS and licensing: every dependency, model, dataset and benchmark has a recorded licence; commercial power-system software and licensed datasets need explicit approval (AGENTS.md golden-path rules) |
| PC-CON-008 | Safety-weighted evaluation: a faster answer that adds unacceptable risk is not an improvement |

## 10. Delivery priorities

| Priority | Capability | Why |
|---|---|---|
| A1 | Scoreboard and prompted-base baseline | Establishes whether training improves anything measurable |
| A2 | Typed DSL, executor and provenance | Foundation for safe, reusable tools |
| A3 | OPF, contingencies and sensitivities | The largest immediate gain in engineering capability |
| A4 | Verifier-guided training | Tests whether agents improve beyond scripts |
| B1 | CIM/RDF/SHACL graph | Semantic knowledge of the actual network |
| B2 | SCADA and weather interfaces | Operational and environmental evidence |
| B3 | Multi-source investigations | Contextual engineering judgement |
| B4 | Forecasting DSL and probabilistic models | Forward-looking risk |
| C1 | Full benchmarks and human comparison | Validates superiority claims |
| C2 | Operational assurance and deployment assessment | Suitability for controlled organisational use |

## 11. Definition of programme success

An agent that, without being told which solver to run:
1. selects the right investigation;
2. executes it through authorised typed interfaces;
3. produces physically validated, reproducible results;
4. recognises insufficient or contradictory evidence;
5. retrieves operational, environmental and semantic context;
6. forecasts with measured uncertainty;
7. improves through verified learning and search;
8. beats defined baselines without relaxing safety.

The measure is not an LLM that can explain electrical engineering. It is an independently evaluated
agent that performs useful investigations, finds better solutions, challenges its own conclusions,
and proves the numerical basis of its answers.

## 12. Immediate directive

- **Critical path:** G0 → G1 → G2 → G3. Verified engineering capability isn't deferred while
  waiting for enterprise integration.
- **In parallel:** begin G4 on synthetic CIM/RDF models built from the CSIRO cases and Lab 6's CIM
  annotations.
- **Enterprise data:** SCADA, PMU and weather integration follow the approved data-access process.
- **Forecasting** reuses the command protocol, the evidence contract, the topology model and the
  evaluation framework.
- **First major finding:** whether solver-verified training gives a statistically meaningful
  improvement over the r6 LoRA, prompted-base and scripted baselines.

**Acceptance principle:** no claimed capability without a test; no numerical conclusion without
evidence; no operational authority without independent assurance.

## References

- PowerAgentBench: <https://github.com/Power-Agent/PowerAgentBench>. Only Level 3 (RestoreBench) is
  licensed today.
- ENTSO-E CGMES library (CIM for grid model exchange):
  <https://www.entsoe.eu/data/cim/cim-for-grid-models-exchange/>
- pandapower state estimation (WLS and robust estimators): pandapower `estimation` module
  (BSD-3-Clause).
