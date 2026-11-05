# Backlog

Deferred/gap items that are real (grounded in the actual code, not speculative), not yet worked,
and not part of any lab's current Definition of Done. This directory exists because
`docs/DEFINITION_OF_DONE.md` already has an "Out of scope for 'done'" convention for
*deliberately* deferred scope — this is the equivalent for gaps that were **not** deliberate,
found by auditing the repo after the fact. Each item is one file, numbered in discovery order,
never renumbered once merged (dropped items are marked `[status: dropped]` in place, not deleted).

`docs/VISION.md` §13 "Known gaps / backlog" is the canonical index a reader hits first; this
directory is where the detail lives so `VISION.md` doesn't bloat with implementation-level
research notes.

| # | Title | Status |
|---|-------|--------|
| [0001](0001-topology-and-results-visualization-gap.md) | No lab renders a topology diagram or a results chart — matplotlib is used in 1 of ~15 scripts | done (charts landed for Labs 1–5; item 2's `pandapower.plotting` gap closed via Lab 1's `sample_network_chart.png`) |
| [0002](0002-pandapower-diagram-and-symbolic-representation-options.md) | Research: pandapower's own advanced diagram options, and open-source symbolic/single-line-diagram packages | done (research consumed by 0003/0004; free tier implemented, stretch tier gated on CIM/CGMES export) |
| [0003](0003-lab3-scorecard-visualization.md) | Lab 3: bake-off scorecard has zero charts despite being the one dataset built to be compared | done |
| [0004](0004-lab4-lab5-visualization-options.md) | Lab 4 & Lab 5: visualization options (reconciliation chart; chaos-net topology + symbolic single-line) | done (incl. powsybl-diagram symbolic diagrams via the CGMES export, 2026-10-23) |
| [0005](0005-unified-notebook-playbook.md) | Suggestion: a jupytext/Jupyter playbook binding all 5 labs' visual outputs into one narrative, without becoming a second source of truth | done (`notebooks/lab_playbook.py` + `just playbook`) |
| [0006](0006-lab5-advanced-transient-visualization-techniques.md) | Research: advanced Lab 5 transient-visualization techniques (symmetrical components, R-X impedance trajectory, spectrogram, network-wide sag propagation) beyond the existing six views | done (all four options implemented: symmetrical components, R-X impedance trajectory, spectrogram, network-wide sag propagation) |
| [0007](0007-clif-x-grammar-constrained-generation.md) | Idea: a CLIF (Common Logic Interchange Format) x-grammar for LLM constrained generation, demoed against the self-hosted Phi-4-mini pod | demo done (`labs/_shared/clif_demo/`), idea not adopted -- see doc for the "grammar-constrained can be semantically evasive" finding |
| [0008](0008-powercoder-benchmark-cannot-measure-agent-value.md) | PowerCoder benchmark cannot measure agent value (one network, fixed interval, 36/69 keys shared with training, scripted policy); add baselines and judgement tasks — PRD-0010 **G0** | open — priority 1; baselines (a)(b) and the 40-task judgement suite built (template ceiling 30/40); model runs need GPU |
| [0009](0009-recovery-training-supervised-the-injected-mistake.md) | Recovery training supervised the injected mistake | done (`8314e14`; verified on r6b journals: students 0 of 1,310 calls) |
| [0010](0010-provenance-check-hardening.md) | Provenance check (PC-REQ-1) is weaker than its requirement | done (v3 field-level score alongside v2; r6b students unchanged) |
| [0011](0011-strict-tool-calling-arm-redesign.md) | Strict-tool-calling arm: redesign as a 2×2 before running it | implemented; queued after r6 |
| [0012](0012-answer-by-reference.md) | Idea: answer by reference (numbers rendered from cited tool output) | idea |
| [0013](0013-sensitivity-newton-tool.md) | Idea: sensitivity/Newton tool with certificates; PCA and the one-interface generator dropped | absorbed into 0016 |
| [0014](0014-grammar-engines-xgrammar-llguidance-gbnf.md) | Grammar engines: xgrammar kept in sync for vLLM; GBNF/llguidance for llama.cpp | decided |
| [0015](0015-g1-typed-command-protocol-and-gateway.md) | PRD-0010 G1: typed command protocol (Rust IR, DSL) and policy gateway | first step done (SOLVE/SIMULATE IR, byte-identical schema, DSL); 8 commands + gateway left |
| [0016](0016-g2-verified-engineering-mathematics.md) | PRD-0010 G2: verified engineering mathematics (OPF, contingencies, sensitivities, state estimation, proof contract) | mostly done (DC/AC OPF, N-1/N-2, SE, V/Q); dynamics and short circuit left |
| [0017](0017-g3-verifier-guided-investigative-learning.md) | PRD-0010 G3: verifier-guided learning (task generator, GRPO, distillation, search) | groundwork done (two-network pool, reward, bisection baseline); GRPO needs GPU |
| [0018](0018-g4-cim-semantic-network-intelligence.md) | PRD-0010 G4: CIM/RDF/SHACL semantic network and versioned topology | mostly done (export, SHACL, CGMES vs pypowsybl, snapshots, bounded queries); switches and OWL left |
| [0019](0019-g5-observations-and-weather.md) | PRD-0010 G5: SCADA, sparse PMU, data lake and weather | blocked (data access); synthetic path open |
| [0020](0020-g6-autonomous-engineering-investigations.md) | PRD-0010 G6: autonomous engineering investigations | open |
| [0021](0021-g7-probabilistic-forecasting.md) | PRD-0010 G7: probabilistic forecasting and leakage-free hindcasting | first step done (leakage-free harness, baselines on cached SA demand); real data blocked by G5 |
| [0022](0022-g8-independent-superiority-assessment.md) | PRD-0010 G8: independent superiority assessment (benchmarks, blind expert comparison) | open |
| [0023](0023-snemsa-case-lacks-ratings-costs-and-distributed-slack.md) | snemSA has no real branch ratings, one generator cost, and a single slack: OPF and thermal tasks carry no signal | proposals 1, 3 done (IEEE 39 OPF/thermal signal; QP check; both slack conventions); 2 not needed |
| [0024](0024-element-discovery-and-graph-search.md) | Element discovery: valid ids in errors (done), then a fixed-query CIM graph tool; vector search not on synthetic names | step 1 done; open |
| [0025](0025-podman-desktop-packaging-of-on-demand-services.md) | Podman Desktop packaging of optional, on-demand, memory-heavy services | awaiting requirements |
| [0026](0026-walkable-path-namespace-ls-get.md) | Walkable path namespace: one grammar, `ls`/`get`, answers that cite paths (80/20 of 0027) | code + trajectories done; retrain/eval need GPU |
| [0027](0027-functional-data-virtualization-layer.md) | Functional data-virtualization layer: CIM-named, memoised, Arrow-queryable `/proc` for the grid | deferred |
| [0028](0028-short-circuit-and-system-strength-tasks.md) | Short-circuit and system-strength (SCR proxy) tasks on snemSA, IEC 60909 via pandapower | blocked (no transmission case with short-circuit data; measured) |
| [0029](0029-cgmes-round-trip-with-pypowsybl.md) | Check the G4 CIM export against pypowsybl's CGMES | done (not importable; AC agrees to 8.6e-4 pu; gaps listed) |
| [0030](0030-nem-dispatch-reruns-with-nempy.md) | Re-run historical NEM dispatch with nempy | blocked (NEMWeb access) |
| [0031](0031-openstef-short-term-forecasting.md) | OpenSTEF as G7's first forecasting engine | blocked (G5 data) |
| [0032](0032-energnn-screening-surrogate.md) | EnerGNN as a screening ranker, never a source of reported numbers | deferred |
| [0033](0033-study-workbook-audit.md) | Audit an engineer's study workbook: every number traced, re-derived or flagged (prototype 6/6 on seeded errors) | deferred (user: skip Excel, don't reinvent) |
| [0034](0034-cgmes-conformant-g4-export.md) | Make the G4 export importable as CGMES: EQ/TP/SSH/SV profiles with headers | done (pypowsybl imports all and agrees to 1.38e-5 pu) |

Independent reviews behind items: [`reviews/2026-10-23-r6-merit-review.md`](reviews/2026-10-23-r6-merit-review.md)
(a separate AI reviewer, read-only; kept as evidence).
