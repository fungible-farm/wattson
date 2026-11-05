# PowerCoder

## Physics-Grounded Autonomous Power-System Engineering

### System Requirements, Milestone Gates and Verification Plan

**Version:** 0.1
**Date:** 23 October 2026
**Classification:** Research architecture; operational integration subject to authorisation
**Implementation approach:** Sovereign, open-source-first, Rust-orchestrated, solver-verified
**Primary objective:** Demonstrate progressively superior performance on independently verifiable power-system engineering tasks.

---

# 1. Mission

Develop an AI agent capable of independently investigating, modelling, forecasting and evaluating electricity-network operating conditions using authoritative network data, validated numerical tools and evidence-grounded reasoning.

The system SHALL progressively demonstrate:

1. Correct execution of a domain-specific command protocol.
2. Reliable selection and composition of electrical engineering tools.
3. Physically valid calculations with independently checkable evidence.
4. Grounded understanding of electrical network topology and equipment.
5. Investigation of historical and near-real-time network conditions.
6. Probabilistic forecasting incorporating electrical and environmental conditions.
7. Improvements in investigative effectiveness through verifier-guided training.
8. Measurable performance against deterministic baselines, established engineering benchmarks and qualified human engineers.

**Success is defined by demonstrated task performance, not by model size, conversational fluency or benchmark claims unsupported by independent evaluation.**

The ultimate research objective is to establish whether specialised models can outperform qualified engineers on clearly defined tasks while remaining auditable, reproducible and safe.

Human authority over operational decisions is retained.

---

# 2. Current Baseline

The following capabilities and constraints are reported as the existing project baseline. They require confirmation by the implementation team's acceptance tests rather than redevelopment.

| Component                      | Reported status                                                        |
| ------------------------------ | ---------------------------------------------------------------------- |
| PowerCoder LoRA                | Trained to execute the tool protocol in approximately three hours      |
| r6 student models              | Supervised protocol-training baseline                                  |
| PowerMCP                       | Existing engineering tool interface                                    |
| Grader and provenance checking | Existing verification infrastructure                                   |
| RestoreBench                   | Available through Lab 10                                               |
| Contingency screening          | Existing Lab 2 capability                                              |
| Dynamic simulation             | DPsim, Lab 5                                                           |
| Sequential control environment | Grid2Op, Lab 9                                                         |
| Sensitivity implementation     | Backlog 0013                                                           |
| Prompted-base evaluation       | Backlog 0008                                                           |
| Teacher distillation           | Existing `powercoder/distillation/teacher.py`                          |
| Model serving                  | llama-swap; larger Qwen planner candidates                             |
| Hardware                       | DGX GB10; sm3lly for specialised ML                                    |
| Weather integration            | windyux / DEC-035 candidates                                           |
| Real NEM data                  | Access-dependent; NEMWeb currently returns 403 in the reported sandbox |

### Baseline interpretation

The three-hour LoRA result establishes that the protocol can be learned efficiently.

It does not yet establish superior engineering judgement.

The next research question is:

> Can the model learn to choose, sequence and interpret physically grounded investigations more effectively than a fixed procedural script, while remaining within an explicit tool and computational budget?

---

# 3. Reference Architecture

```text
                         OPERATOR
                            │
                            ▼
                  NATURAL LANGUAGE TASK
                            │
                            ▼
                    SOVEREIGN PLANNER
                  llama.cpp / llama-swap
                            │
                            ▼
                    CONSTRAINED DSL
                 JSON Schema / CFG
                            │
                            ▼
                     TYPED RUST IR
                            │
                 ┌──────────┴─────────┐
                 │                    │
           POLICY GATE           TASK PLANNER
                 │                    │
                 └──────────┬─────────┘
                            │
                    TOOL ORCHESTRATOR
                            │
       ┌────────────────────┼────────────────────┐
       │                    │                    │
       ▼                    ▼                    ▼
   SEMANTIC DATA       OBSERVATIONS         NUMERICAL
       │                    │                SERVICES
   CIM / OWL            SCADA / PMU              │
   RDF / SHACL          Weather / Lake       NR / GN / OPF
   SPARQL               Time Series          Contingencies
       │                    │                Sensitivities
       │                    │                Dynamics
       └────────────────────┼────────────────────┘
                            │
                            ▼
                  EVIDENCE / VERIFIER
                   Residuals / Limits
                  Provenance / Quality
                            │
                            ▼
                   VERIFIED FINDINGS
                            │
                ┌───────────┴───────────┐
                │                       │
           BENCHMARKING            TRAINING
            Scoreboard              SFT / RL
                │                   Distillation
                └───────────┬───────────┘
                            │
                      EVALUATION
                            │
                      HUMAN REVIEW
```

The architecture separates six responsibilities:

* **Language model:** Selects and plans investigations.
* **Semantic layer:** Resolves electrical equipment and relationships.
* **Evidence layer:** Retrieves observations and forecasts.
* **Numerical layer:** Performs engineering calculations.
* **Verifier:** Checks results against mathematical, procedural and safety constraints.
* **Evaluator:** Measures performance independently of the agent.

The same interfaces SHALL support synthetic networks, historical investigations and authorised read-only enterprise deployments.

---

# 4. Functional Requirements

## 4.1 Command language and execution

### PC-REQ-001 — Typed command protocol

The system SHALL expose a versioned domain-specific command language.

The minimum command vocabulary SHALL include:

| Command    | Required semantics                                              |
| ---------- | --------------------------------------------------------------- |
| `RECALL`   | Retrieve recorded observations, documents or existing forecasts |
| `RESOLVE`  | Resolve CIM assets and electrical relationships                 |
| `ESTIMATE` | Reconstruct electrical states from measurements                 |
| `SOLVE`    | Solve electrical network equations                              |
| `OPTIMIZE` | Solve constrained engineering optimisation problems             |
| `SIMULATE` | Evaluate hypothetical conditions                                |
| `FORECAST` | Predict future quantities                                       |
| `HINDCAST` | Reproduce historical forecasts without future information       |
| `COMPARE`  | Evaluate competing solutions or hypotheses                      |
| `EXPLAIN`  | Produce an evidence-grounded engineering explanation            |

Commands SHALL compile into a typed intermediate representation.

The textual DSL SHALL NOT execute directly against operational systems.

### PC-REQ-002 — Constrained generation

The inference layer SHALL support grammar- or schema-constrained command generation.

Acceptable implementations include llama.cpp grammar support, LLGuidance or an independently validated XGrammar integration.

The grammar SHALL be generated from or validated against the canonical command schemas.

### PC-REQ-003 — Executor isolation

All commands SHALL pass through a policy-enforcing execution gateway.

The gateway SHALL independently validate:

* Command permissions.
* Asset and dataset scope.
* Physical units and argument types.
* Resource consumption limits.
* Temporal constraints.
* Query complexity.
* Operational safety restrictions.

Valid syntax SHALL NOT imply authorisation.

### PC-REQ-004 — Evidence provenance

Every reported numerical engineering result SHALL reference a corresponding tool result, validated dataset or reproducible derivation.

**The LLM SHALL NOT invent numerical engineering results.**

The evidence contract SHALL include:

```text
result_id
command_id
command_version
tool_version
input_snapshot_ids
dataset_versions
model_or_solver_version
result_value
physical_units
numerical_status
validation_evidence
uncertainty
execution_timestamp
```

Unverified, approximate and hypothetical results SHALL be explicitly distinguishable.

### PC-REQ-005 — Reproducibility

The execution environment SHALL record sufficient inputs, configuration, dependency versions and random seeds to reproduce analytical results within declared numerical tolerances.

Recorded experiments SHALL be independently replayable without consulting the originating LLM.

---

## 4.2 Electrical engineering capabilities

### PC-REQ-010 — AC and DC power flow

The system SHALL expose AC and DC power-flow operations.

AC power flow SHALL support Newton–Raphson or another explicitly identified nonlinear solution method.

Results SHALL include convergence status, power mismatches, iteration count and limit violations.

### PC-REQ-011 — Optimal power flow

The system SHALL expose AC and DC optimal-power-flow operations.

Results SHALL identify:

* Objective value.
* Feasibility status.
* Binding constraints.
* Dispatch variables.
* Relevant dual values or shadow prices, when mathematically available.
* Optimality information supported by the solver.

AC OPF SHALL NOT be described as globally optimal unless a valid global-optimality certificate or bound supports that claim.

### PC-REQ-012 — Contingency analysis

The system SHALL support N-1 and bounded N-2 contingency analysis.

It SHALL distinguish:

* Exhaustively enumerated contingencies.
* Heuristically selected contingencies.
* Validated contingencies.
* Unexamined contingencies.

The system SHALL rank investigated contingencies by defined severity measures and identify violations introduced relative to the base case.

A limited search SHALL NOT be represented as complete screening.

### PC-REQ-013 — Sensitivity analysis

The numerical tools SHALL support:

* PTDF.
* LODF.
* AC power-flow Jacobian.
* Voltage/reactive-power sensitivities.

Calculations SHALL report numerical conditioning, applicable assumptions and residual checks.

### PC-REQ-014 — State estimation

The system SHALL support SCADA-based weighted least-squares state estimation using Gauss–Newton or another identified estimator.

State estimation SHALL NOT depend on PMU availability.

The estimator SHALL report:

* Observability status.
* Measurement residuals.
* Convergence status.
* Estimated state.
* Bad-data diagnostics.
* Declared uncertainty or limitations.

Optional robust estimators SHALL be supported.

### PC-REQ-015 — Dynamic and fault analysis

The system SHALL expose dynamic simulation and short-circuit analysis through specialised numerical services.

Steady-state power-flow results SHALL NOT be presented as evidence of transient stability.

### PC-REQ-016 — Numerical proof contract

Each numerical tool SHALL return an independently inspectable verification record.

Depending on the operation, this includes:

```text
converged
equation_residuals
constraint_violations
feasibility_status
optimality_gap_or_bound
solver_iterations
conditioning
validation_warnings
```

A solver's success flag alone SHALL NOT constitute proof of engineering correctness.

---

## 4.3 CIM knowledge graph

### PC-REQ-020 — Semantic network model

The system SHALL ingest and query CIM-encoded electrical network models using RDF.

The semantic layer SHALL support:

* OWL/RDFS semantics where applicable.
* SHACL model validation.
* SPARQL retrieval.
* CIM equipment identifiers.
* Network connectivity.
* Equipment parameters.
* Measurement associations.
* Geographic relationships.

### PC-REQ-021 — Versioned electrical topology

The system SHALL distinguish physical connectivity from electrical connectivity under a particular switching state.

Topology snapshots SHALL preserve:

* Network model version.
* Effective switching state.
* Relevant transformer tap positions.
* Timestamp or validity interval.
* Source and quality status.

### PC-REQ-022 — Controlled graph queries

The command gateway SHALL expose typed, bounded topology operations.

Arbitrary unrestricted SPARQL SHALL NOT be required for normal agent operation.

### PC-REQ-023 — Derived electrical relationships

The architecture SHOULD support dynamically calculated relationships derived from PTDF, LODF and Jacobian sensitivities.

These relationships SHALL remain distinct from authoritative CIM topology.

---

## 4.4 Observations and environmental evidence

### PC-REQ-030 — SCADA integration

The system SHALL support read-only retrieval of historical and authorised near-real-time SCADA observations.

Measurements SHALL resolve to appropriate CIM equipment or measurement identifiers.

### PC-REQ-031 — Sparse PMU integration

Where PMU observations are available, the system SHALL support retrieval and synchronised analytical processing.

PMU availability SHALL NOT be assumed across the network.

Electrical proximity SHALL be distinguished from geographical proximity.

### PC-REQ-032 — Enterprise data lake access

The system SHALL support authorised read-only access to the enterprise data lake.

Initial datasets SHOULD include:

* Historical weather observations.
* Historical weather forecasts.
* Renewable-generation observations.
* Demand observations.
* Existing authoritative forecasts.

The data interface SHALL enforce dataset permissions and prohibit unbounded queries.

### PC-REQ-033 — Temporal consistency

All retrieved observations and forecasts SHALL preserve:

* Observation or valid time.
* Data availability time.
* Forecast issue time, where applicable.
* Source and quality metadata.

The system SHALL support reconstruction of what information was available at a specified historical instant.

### PC-REQ-034 — Environmental reasoning

The agent SHOULD investigate weather-dependent electrical conditions, including:

* Renewable-generation variability.
* Demand variability.
* Transmission thermal conditions.
* Weather-related forecasting errors.

Environmental correlation SHALL NOT independently establish electrical causation.

---

## 4.5 Forecasting dialect

### PC-REQ-040 — Forecast command language

The DSL SHALL support explicit forecast targets, horizons, resolutions, input datasets and output formats.

Example:

```text
FORECAST demand
    FOR REGION VIC1
    HORIZON 24 HOURS
    RESOLUTION 30 MINUTES

    USING
        HISTORICAL_SCADA,
        WEATHER_FORECAST

    METHOD BEST_VALIDATED

    RETURN
        P10, P50, P90,
        MODEL_ID,
        UNCERTAINTY;
```

### PC-REQ-041 — Interchangeable forecasting engines

The forecast interface SHALL be independent of the forecasting model.

Candidate engines include:

* StatsForecast.
* LightGBM.
* Chronos.
* TimesFM.
* Domain-specific physical models.

Each engine SHALL have a recorded version, licence, validation status and supported target types.

### PC-REQ-042 — Leakage-free hindcasting

Historical backtests SHALL use only observations, model versions and forecast inputs available at the declared historical issue time.

Revised or later-published data SHALL NOT silently enter an historical forecast.

### PC-REQ-043 — Probabilistic forecasting

The system SHALL support quantile forecasts and SHOULD support correlated scenario ensembles.

Forecast quality SHALL be evaluated using appropriate metrics, including:

* Pinball loss.
* MAE.
* WAPE or other suitable scale-normalised error.
* Prediction-interval calibration.
* Forecast skill relative to a defined baseline.

MAPE SHALL NOT be used without safeguards where actual values approach zero.

### PC-REQ-044 — Forecast-driven physical simulation

Forecast outputs SHOULD feed deterministic electrical network simulations.

The system SHALL distinguish:

1. Forecast inputs.
2. Assumed future topology and controls.
3. Calculated future network states.
4. Derived probabilities of constraint violations.

Probabilities SHALL be obtained from validated statistical or scenario methods, not generated directly as unsupported LLM estimates.

---

## 4.6 Agent judgement and training

### PC-REQ-050 — Study selection

The agent SHALL select relevant engineering investigations from natural-language objectives.

It SHALL distinguish tasks requiring power flow, state estimation, OPF, contingency analysis, dynamic simulation or forecasting.

### PC-REQ-051 — Multi-tool planning

The agent SHALL support multi-step investigations with dependency management and bounded computational budgets.

### PC-REQ-052 — Verifier-guided training

The training environment SHALL support reinforcement learning with verifiable rewards.

Candidate methods include GRPO and other reinforcement-learning approaches compatible with tool-execution trajectories.

Reward functions SHOULD consider:

* Validated task success.
* Physical feasibility.
* Accuracy.
* Evidence completeness.
* Tool-call efficiency.
* Computational cost.

Unsafe or unsupported conclusions SHALL receive explicit penalties.

### PC-REQ-053 — Procedural task generation

The system SHALL generate diverse tasks across multiple networks, operating conditions and contingencies.

Training SHALL include network and scenario diversity sufficient to discourage memorisation of particular topology instances.

### PC-REQ-054 — Teacher distillation

A larger sovereign teacher model MAY generate candidate investigation trajectories.

Only independently validated outcomes and defensible intermediate steps SHALL enter the verified training corpus.

Teacher confidence alone SHALL NOT constitute verification.

### PC-REQ-055 — Inference-time search

The system SHOULD support multiple candidate investigations and selection based on verifier-confirmed results.

The computational budget and evidence of each candidate SHALL be recorded.

---

# 5. Verification Scoreboard

The scoreboard SHALL be established before further model optimisation.

| Dimension              | Primary metrics                                | Comparison                          |
| ---------------------- | ---------------------------------------------- | ----------------------------------- |
| Protocol competence    | Valid calls, schema repairs, tool errors       | Prompted base vs r6                 |
| Electrical correctness | Residuals, feasible solutions, violations      | Independent numerical reference     |
| Contingency discovery  | Top-k recall, severity regret, false-safe rate | Exhaustive or hidden reference      |
| OPF                    | Objective regret, feasibility, time            | Reference optimiser                 |
| Recovery               | Success rate, action count, validation budget  | RestoreBench baselines              |
| Forecasting            | Pinball loss, MAE, calibration, skill          | Same-horizon authoritative forecast |
| Investigation          | Correct study selection, evidence coverage     | Scripted and expert baselines       |
| Efficiency             | Wall time, tokens, tool calls, compute cost    | Equivalent-budget baselines         |
| Engineering judgement  | Blind expert assessment                        | Qualified human engineers           |
| Safety                 | Unauthorised actions, unsupported claims       | Zero-tolerance controls             |

Results SHALL be reported by task family.

A single aggregate score SHALL NOT conceal a deterioration in safety or critical engineering accuracy.

**Outperformance claims must identify the dimension of performance.**

For example:

* Faster than human experts on specified tasks.
* More accurate than a defined numerical or procedural baseline.
* Better contingency discovery within equal computational budgets.
* Superior forecast skill on unseen periods.
* Higher-quality engineering reasoning under blind expert assessment.

These are different claims and require different evidence.

---

# 6. Progressive Milestone Gates

Every milestone SHALL have an independently executable acceptance test and an immutable evidence package.

A gate is complete only when its defined acceptance criteria are satisfied.

## G0 — Establish the baseline

**Priority:** A
**Dependency:** Existing r6 implementation
**Related backlog:** 0008

### Deliverables

* Prompted-base baseline.
* r6 LoRA baseline.
* Existing procedural/scripted baseline.
* Frozen benchmark definitions.
* Versioned evaluation harness.
* Machine-readable scoreboard.

### Required tests

Run identical tasks against the prompted base, r6 student and scripted baseline.

Include free-text study-selection tasks and at least one network absent from fine-tuning.

### Exit criteria

* All baseline runs have reproducible configurations.
* Numerical scores are generated by the evaluator, not self-reported by the agent.
* Benchmark datasets and train/test boundaries are recorded.
* Protocol accuracy, task accuracy, cost and failure rates are reported separately.
* Evaluation infrastructure passes repeatability checks.

### Evidence

`G0-baseline-report.json`
`G0-benchmark-manifest.json`
`G0-reproduction-report.md`

**Gate proves:** What has already been learned, and whether further fine-tuning is currently the limiting factor.

---

## G1 — Trusted command execution

**Priority:** A
**Dependencies:** G0
**Requirements:** PC-REQ-001–005

### Deliverables

* Typed Rust command IR.
* Versioned schema/grammar generation.
* llama.cpp integration.
* Policy-enforcing execution gateway.
* Provenance and replay implementation.

### Required tests

Evaluate valid, malformed, ambiguous and deliberately unauthorised commands.

Inject adversarial instructions into tool responses and retrieved documents.

### Exit criteria

* At least 99% valid command generation on the declared protocol test suite.
* 100% of unauthorised test commands rejected by the executor.
* Every accepted numerical result has a valid evidence reference.
* Every replay test reproduces its validated result within declared tolerances.
* No test permits retrieved content to bypass command authorisation.

**Gate proves:** The model can use the tools without becoming the authority for executing them.

---

## G2 — Verified engineering mathematics

**Priority:** A
**Dependencies:** G1
**Requirements:** PC-REQ-010–016
**Related backlog:** 0013

### Deliverables

* AC/DC power flow.
* AC/DC OPF.
* N-1 screening.
* Bounded N-2 screening.
* PTDF, LODF and Jacobian calculations.
* WLS state estimation.
* Numerical proof contract.

Dynamic simulation and short-circuit capabilities may initially remain separate services.

### Required tests

Use synthetic, IEEE and available CSIRO network cases.

Cross-check selected results against independent implementations or reference solutions.

Test non-convergence, numerical ill-conditioning, infeasible OPF and incorrect topology.

### Exit criteria

* Every numerical tool returns the proof contract.
* Reference cases meet predeclared, solver-specific tolerances.
* Non-converged solutions are never reported as solved.
* Constraint violations are independently recomputed.
* Failed, infeasible and indeterminate states are distinguishable.
* Contingency coverage is explicitly reported.

**Gate proves:** Numerical capability is physically grounded and independently checkable.

---

## G3 — Verified investigative learning

**Priority:** A
**Dependencies:** G0, G2
**Requirements:** PC-REQ-050–055

### Deliverables

* Procedural task generator.
* Solver-grounded reward functions.
* GRPO/RL training experiment.
* Teacher-distillation experiment.
* Inference-time candidate search.
* Evaluation against r6 and scripted baselines.

### Required tests

Train on diverse generated networks and operating conditions.

Evaluate on frozen, previously unseen networks and scenario families.

Run ablations:

1. Prompted base with tools.
2. r6 with tools.
3. RL-trained student with tools.
4. Student with verifier-guided inference-time search.
5. Larger planner with student workers.

### Exit criteria

* Demonstrated improvement against the strongest eligible baseline on a predeclared primary benchmark.
* Improvement supported by a confidence interval excluding zero.
* No regression on mandatory safety and evidence checks.
* Tool-call cost and elapsed time recorded.
* Reward hacking and verifier disagreement tests completed.
* Training and hidden evaluation datasets remain isolated.

**Gate proves:** Learning improves investigative decisions rather than merely reproducing scripted procedures.

**Important:** A solver is a powerful verifier but not an infallible oracle. Numerical convergence, physical validity, operational feasibility and global optimality are separate properties.

---

## G4 — CIM semantic network intelligence

**Priority:** B
**Dependencies:** G1
**Requirements:** PC-REQ-020–023

### Deliverables

* CIM/RDF ingestion.
* OWL/RDFS support.
* SHACL validation.
* Typed graph-query tools.
* Versioned topology snapshots.
* CIM-to-numerical-model mapping.

### Required tests

Use synthetic topology cases containing substations, lines, transformers, switches and measurements.

Modify switching states and verify that derived electrical connectivity changes appropriately.

### Exit criteria

* All reference equipment identifiers resolve correctly.
* Required SHACL constraints are checked.
* Known-invalid reference graphs are rejected or quarantined.
* Graph-derived connectivity matches the reference electrical model.
* Historical topology snapshots remain reproducible.
* Graph retrieval respects declared query budgets.

**Gate proves:** The agent understands network structure through explicit semantics rather than memorised descriptions.

---

## G5 — Enterprise observations and weather

**Priority:** B
**Dependencies:** G4
**Requirements:** PC-REQ-030–034

### Deliverables

* SCADA observation interface.
* Optional sparse-PMU interface.
* Read-only enterprise data lake connector.
* Weather observation/forecast interface.
* CIM measurement and geographic mapping.
* Time-consistent snapshot assembler.

### Required tests

Begin with synthetic and approved historical datasets.

Introduce stale SCADA values, missing observations, inconsistent timestamps and unavailable PMUs.

Test historical weather queries using both observations and forecasts.

### Exit criteria

* Read-only access restrictions independently verified.
* Observation timestamps and availability times preserved.
* PMU absence does not prevent SCADA-based investigations.
* Weather forecasts are distinguished from actual observations.
* State-estimation observability failures are explicitly identified.
* Historical replay cannot access future-unavailable data.

**Gate proves:** The agent can reconstruct a defensible evidence set from multiple operational and environmental sources.

**External dependency:** Enterprise data access approval. Synthetic-data testing SHALL proceed independently of that approval.

---

## G6 — Autonomous engineering investigations

**Priority:** B
**Dependencies:** G2, G4, G5
**Requirements:** PC-REQ-004, 050–051

### Reference investigation

> Investigate a historical voltage disturbance, identify affected assets, examine SCADA and available PMU evidence, retrieve relevant weather conditions, evaluate electrical sensitivities and produce a reproducible engineering explanation.

### Deliverables

* Multi-tool investigation planner.
* Hypothesis generation and testing.
* Evidence-linked engineering report.
* Uncertainty and data-quality reporting.
* Investigation replay capability.

### Exit criteria

* Every quantitative claim resolves to source data or a tool execution.
* Unsupported causal claims are identified and rejected by evaluation.
* The agent distinguishes observation, calculation and hypothesis.
* Results reproduce from recorded evidence.
* Blind expert review confirms technical defensibility against a predefined rubric.

**Gate proves:** The agent can investigate problems requiring contextual judgement across tools, rather than simply executing prescribed workflows.

---

## G7 — Probabilistic forecasting

**Priority:** B
**Dependencies:** G2, G5
**Requirements:** PC-REQ-040–044

### Deliverables

* Forecasting DSL.
* Forecast-model registry.
* Weather-conditioned forecasting.
* Leakage-free hindcasting.
* Probabilistic forecast evaluation.
* Forecast-driven AC power-flow scenarios.

### Initial targets

1. Regional demand.
2. Wind and solar generation.
3. Network voltage and constraint risk.

### Required tests

Compare forecasting engines across identical historical issue times and target horizons.

Benchmark against suitable persistence, statistical and authoritative forecast baselines.

Inject future-data leakage to confirm rejection.

### Exit criteria

* Forecast outputs carry model identity, issue time and valid time.
* Historical evaluation prevents future-information leakage.
* Probabilistic outputs pass predefined calibration assessments.
* Forecast skill is evaluated on unseen periods.
* Dependent renewable forecasts preserve appropriate spatial correlation when aggregated.
* Forecast-driven AC simulations independently validate physical feasibility.
* Infeasible future scenarios are reported rather than silently discarded.

**Gate proves:** The agent can produce and evaluate probabilistic forecasts grounded in evidence and physical models.

**External dependency:** Access to sufficiently complete historical demand, generation and authoritative forecast data.

---

## G8 — Independent engineering superiority assessment

**Priority:** C
**Dependencies:** G3, G6, G7

### Deliverables

* Full external benchmark campaign.
* Held-out network evaluations.
* Human-engineer comparison study.
* Statistical analysis.
* Operational assurance report.

### Benchmark scope

* RestoreBench.
* PowerAgentBench steady-state Levels 1–2.
* Appropriate dynamic benchmarks where tooling and licences permit.
* Forecasting benchmarks against matched authoritative baselines.
* Blind engineering investigation tasks.

### Human evaluation protocol

Qualified engineers and agents SHALL receive comparable tasks, evidence access and time constraints.

Assessments SHALL be blind to author identity.

Scoring criteria SHALL be established before results are collected.

The evaluation SHALL distinguish unaided engineers, tool-assisted engineers and AI-agent performance where those comparisons are relevant.

### Exit criteria

* Primary superiority hypotheses and thresholds preregistered.
* Results independently reproducible.
* Statistical uncertainty reported.
* Safety and engineering-validity requirements satisfied.
* Human comparison conducted using a consistent rubric.
* Unsupported claims excluded from final reporting.

**Gate proves:** Whether the system outperforms specified human and computational baselines on defined task families.

No gate authorises autonomous operational control.

---

# 7. Milestone Dependencies

```text
                    G0
                 BASELINE
                    │
                    ▼
                    G1
             COMMAND PROTOCOL
                    │
          ┌─────────┴──────────┐
          │                    │
          ▼                    ▼
          G2                   G4
     MATHEMATICS           CIM GRAPH
          │                    │
          ▼                    ▼
          G3                   G5
    VERIFIER TRAINING     SCADA / WEATHER
          │                    │
          │             ┌──────┴──────┐
          │             │             │
          │             ▼             ▼
          │             G6            G7
          │         INVESTIGATION   FORECAST
          │             │             │
          └─────────────┼─────────────┘
                        │
                        ▼
                        G8
                 HUMAN COMPARISON
```

G3 and G4–G7 may progress in parallel once their prerequisites are met.

Numerical intelligence is not dependent on enterprise data access.

The graph and forecasting workstreams SHALL therefore not block the early solver-verification and training experiments.

---

# 8. Mandatory Evidence Package

Every milestone SHALL produce a machine-readable evidence package.

```text
milestone/
    requirements.json
    requirements-traceability.json
    acceptance-tests.json
    benchmark-manifest.json
    execution-results.jsonl
    solver-proofs/
    provenance/
    reproducibility/
    scorecard.json
    gate-decision.json
```

Requirements SHALL have stable identifiers and explicit verification methods.

The implementation SHOULD support export or mapping to ReqIF and SysML v2 representations.

Each requirement SHALL be traceable to:

```text
Requirement
    ├── Design component
    ├── Implementation artifact
    ├── Verification test
    ├── Execution evidence
    ├── Acceptance result
    └── Milestone gate
```

A gate SHALL report one of:

* `PASS`
* `FAIL`
* `BLOCKED`
* `WAIVED`

Waivers require explicit authorisation and SHALL remain visible in downstream assurance reports.

A waived engineering-safety requirement cannot silently become a passing requirement.

---

# 9. Architectural and Safety Constraints

### PC-CON-001 — Sovereign execution

Sensitive operational data and derived information SHALL remain within approved execution and storage boundaries.

The architecture SHALL permit local inference and locally executed numerical tools.

### PC-CON-002 — Read-only operational integration

The initial research system SHALL NOT issue operational switching, dispatch or SCADA control commands.

Simulation commands SHALL operate only on isolated model state.

### PC-CON-003 — Human authority

Operational recommendations SHALL require authorised human review before any execution through operational systems.

### PC-CON-004 — Separation of verification

The training agent SHALL NOT modify hidden benchmark answers, reward functions or independent validation results.

### PC-CON-005 — Source integrity

Untrusted retrieved text SHALL NOT override execution policies or tool authorisation.

### PC-CON-006 — Model independence

The tool protocol SHALL remain usable across multiple model families, sizes and inference engines.

### PC-CON-007 — OSS and licensing

Dependencies, models, datasets and benchmarks SHALL have recorded licences and redistribution restrictions.

Use of commercial power-system software or licensed benchmark datasets SHALL require explicit approval.

### PC-CON-008 — Safety-weighted evaluation

A faster answer SHALL NOT be considered an improvement if it increases unacceptable engineering or operational risk.

---

# 10. Required End-to-End Demonstrations

Three demonstrations define the system's progressive maturity.

### Demonstration A — Verified mathematical reasoning

**Question:**

> Determine the largest permissible transfer under a defined set of network contingencies.

**Required behaviour:**

The agent selects appropriate studies, invokes numerical tools, tests candidate transfers, evaluates constraints and reports the maximum verified feasible result within the defined search assumptions.

**Success:** Improved accuracy or efficiency against a scripted reference, with no loss of physical validity.

**Primary gates:** G2–G3.

### Demonstration B — Evidence-grounded network investigation

**Question:**

> Explain the cause of an observed voltage disturbance using network topology, SCADA observations, available PMUs and weather history.

**Required behaviour:**

The agent identifies relevant electrical assets, retrieves measurements, evaluates state consistency, invokes appropriate calculations and tests competing explanations.

**Success:** Reproducible evidence, defensible engineering conclusions and explicit treatment of unresolved uncertainty.

**Primary gates:** G4–G6.

### Demonstration C — Forecasting and physical risk

**Question:**

> Forecast the probability of voltage-limit violations over the next six hours under expected weather, demand and renewable-generation conditions.

**Required behaviour:**

The agent retrieves approved forecasts, constructs correlated future scenarios, executes AC power-flow studies, evaluates constraint violations and quantifies uncertainty.

**Success:** Calibrated forecasts and independently validated physical calculations, with full temporal provenance.

**Primary gates:** G7–G8.

---

# 11. Delivery Priorities

| Priority | Capability                                      | Justification                                             |
| -------- | ----------------------------------------------- | --------------------------------------------------------- |
| **A1**   | Scoreboard and prompted-base baseline           | Establishes whether training improves anything measurable |
| **A2**   | Typed DSL, executor and provenance              | Foundation for safe, reusable tools                       |
| **A3**   | OPF, contingencies and sensitivities            | Highest immediate increase in engineering capability      |
| **A4**   | Verifier-guided training                        | Tests whether agents improve beyond scripts               |
| **B1**   | CIM/RDF/SHACL graph                             | Adds semantic knowledge of the actual network             |
| **B2**   | SCADA and weather-data interfaces               | Adds operational and environmental evidence               |
| **B3**   | Multi-source investigations                     | Demonstrates contextual engineering judgement             |
| **B4**   | Forecasting DSL and probabilistic models        | Adds prediction and forward-looking risk analysis         |
| **C1**   | Full-scale benchmarks and human comparison      | Validates superiority claims                              |
| **C2**   | Operational assurance and deployment assessment | Determines suitability for controlled organisational use  |

---

# 12. Definition of Programme Success

The programme succeeds technically when an agent can:

1. Select an appropriate engineering investigation without being told which specific solver to run.
2. Execute the investigation through authorised typed interfaces.
3. Produce physically validated, reproducible results.
4. Recognise insufficient or contradictory evidence.
5. Retrieve authoritative operational, environmental and semantic context.
6. Forecast future conditions with measured uncertainty.
7. Improve task performance through verified learning and search.
8. Demonstrate superiority over defined baselines without relaxing safety constraints.

The final measure of success is not an LLM that can explain electrical engineering.

It is an independently evaluated agent that can **perform useful engineering investigations, discover better solutions, challenge its own conclusions and prove the numerical basis of its answers.**

---

# 13. Immediate Implementation Directive

**Execute G0 → G1 → G2 → G3 as the critical path.**

Do not defer verified engineering capability while awaiting enterprise integration.

In parallel, begin G4 using synthetic CIM/RDF network models.

Enterprise SCADA, PMU and weather-data integration SHALL follow the approved data-access process.

Forecasting SHALL reuse the command protocol, evidence contract, topology model and evaluation framework.

The first major research finding should establish whether solver-verified training produces a statistically meaningful improvement over the existing three-hour LoRA, prompted-base and deterministic-script baselines.

Subsequent milestones extend that demonstrated competence from mathematical tasks to contextual engineering investigations and probabilistic forecasting.

**Acceptance principle: No claimed capability without a test; no numerical conclusion without evidence; no operational authority without independent assurance.**

