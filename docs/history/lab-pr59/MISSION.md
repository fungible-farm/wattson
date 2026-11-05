⚡ Mission: NEM PowerCoder — Qwen3.8 Distillation and Sovereign Deployment

Implementation specification · 16 October 2026 · NVIDIA DGX Spark GB10

Mission objective: Transform the existing NEM PowerAgent laboratory into a reproducible, locally operated model-distillation and fine-tuning system that produces exceptionally capable Python/power-system engineering agents, validated against real numerical solvers and deployable as quantised models.

The training sequence will be:

Qwen3.8 teacher → llama-swap → PowerMCP validation → Unsloth SFT → Axolotl optimisation → INT4/INT8 deployment.

This is not a request to train the enormous Qwen3.8-Flash-Next checkpoint from scratch. Its role is primarily a high-capability teacher running on the GB10; smaller students inherit its domain-specific capabilities through verified demonstrations.

1. Architecture and repository ownership

I inspected the three supplied fungible-farm repositories and the upstream llama-swap project. Your existing code provides significantly more infrastructure than a greenfield implementation would require.

Repository	Existing capability	Mission responsibility
nem-poweragent-lab	Nine numbered labs, CSIRO Synthetic-NEM, pandapower, N-1 studies, benchmark scorecards, Podman and uv infrastructure	Primary orchestrator for datasets, training, evaluation and release qualification
PowerMCP	Solver interfaces including pandapower, PyPSA, ANDES and additional engineering packages	Authoritative tool execution, typed schemas and solver-result evidence
qwen3.8-Flash-DGX-UltraFast	GB10-optimised vLLM serving recipe, AutoRound quantisation, speculative decoding and documented benchmarks	Teacher inference backend; preserve upstream performance optimisations
mostlygeek/llama-swap	OpenAI-compatible inference routing, process management, model switching and concurrent-model controls	Stable model gateway separating training-data consumers from inference backends
Key architectural decision

Do not merge these repositories into a monolith. Introduce a model-training subsystem within nem-poweragent-lab, consume PowerMCP through an explicit versioned interface, and keep the optimised Qwen serving recipe independently buildable.

The existing laboratory already has docs/DEFINITION_OF_DONE.md, a Justfile, uv package management, a real PowerMCP streamable-HTTP endpoint, and labs/03-advanced-provider-bakeoff.

That third lab is particularly useful: its existing scorecard evaluates deterministic stand-in providers, but the repository explicitly identifies live LLM-provider evaluation as an unfinished integration. Replacing those stand-ins with real models should be the first functional deliverable.

2. Confirmed model strategy

Qwen3.6 and Qwen3.8 are both real released families. Alibaba's official model repositories document Qwen3.6-27B, Qwen3.6-35B-A3B, Qwen3.8-27B and Qwen3.8-Flash-Next. Axolotl's September 2026 release notes identify support for Qwen3.6 and Qwen3.8-Flash-Next.

Model	Proposed role	Decision
Qwen3.8-Flash-Next	Primary locally served teacher	Adopt existing UltraFast recipe
Qwen3.8-27B	Independent teacher / quality challenger	Benchmark for engineering correctness
Qwen3.6-27B	Earlier-generation control	Optional reference
Qwen3.5-4B	Windows student	First Unsloth candidate
Qwen2.5-Coder-1.5B	Qualcomm Q6A student	First edge candidate
IBM Granite 4.2-3B	Independent laboratory challenger	Include in evaluation

Do not select students by version number alone. Qwen3.8 is valuable as a teacher, while Qwen3.5 or Qwen2.5 can remain preferable as students because the final Windows/Qualcomm accelerator must support their computation graphs.

The architecture should allow any future Qwen release to replace a teacher through configuration, without changes to the dataset or solver-validation contracts.

3. Detailed implementation mission

The following specification is intended for implementation by a coding agent. It defines work packages, concrete deliverables, integration contracts, tests, and acceptance gates.

NEM PowerCoder — End-to-End Engineering Mission
NEM PowerCoder — End-to-End Engineering Mission

Status: Proposed implementation specification
Hardware: NVIDIA DGX Spark / GB10, 128 GB unified memory
Primary repository: fungible-farm/nem-poweragent-lab
Inference: Qwen3.8-Flash-Next via UltraFast + llama-swap
Training: Unsloth → Axolotl
Deployment: Windows Foundry Local INT4/INT8; Qualcomm QNN edge model

Mission directive

Build a reproducible, sovereign, solver-verified training and evaluation system that distils advanced Qwen3.8 coding and reasoning behaviour into smaller models specialised for Python programming, Australian electricity-market modelling, power-system engineering and PowerMCP tool use.

Use existing repositories and infrastructure. Minimise duplication, preserve backwards compatibility, favour typed interfaces, deterministic workflows, immutable evidence and independently verifiable acceptance tests.

The mission is complete only when at least one fine-tuned model demonstrably improves on its untuned base model and the full pipeline can be reproduced from documented inputs. Hardware-specific deployment requires additional, independently measured acceptance.

Engineering invariants
The teacher model is an inference service, not a training dependency embedded within either trainer.
llama-swap is the stable model API and routing boundary.
PowerMCP and numerical solvers, not the teacher LLM, establish numerical ground truth.
Unsloth provides initial training experiments; Axolotl provides the reproducible, configurable training pipeline and subsequent optimisation.
Preserve BF16 checkpoints before any deployment quantisation.
Never treat a successfully converged simulation as automatically physically valid.
Never treat CPU/GPU fallback as successful NPU acceleration.
Preserve existing labs and their deterministic test fixtures.
Public repositories and datasets contain no AEMO-internal information.
Every experimental result identifies its source model, revision, dataset, configuration, seed and software environment.
No release may claim superiority without a defined reference, fixed evaluation protocol and reproducible measurements.
Work package M00 — Environment and repository qualification

Priority: P0
Dependencies: None

Tasks
Inspect all three fungible-farm repositories and their AGENTS.md, README and existing tests.
Record exact baseline Git commit SHAs.
Inventory the DGX Spark's OS, NVIDIA driver, CUDA, container runtime, GPU memory, storage and running workloads.
Validate ARM64 support for pinned Unsloth, Axolotl, PyTorch, Triton, Transformers, Flash Attention and related dependencies.
Inspect Qwen3.8-Flash-Next, Qwen3.8-27B and student-model checkpoint formats, tokenisers and model licences.
Record the UltraFast recipe's build hashes and configuration.
Preserve existing services and benchmark results; do not overwrite or terminate unrelated workloads.
Deliverables
docs/powercoder/00-environment.md
docs/powercoder/model-qualification.md
docs/powercoder/dependency-matrix.md
configs/powercoder/models.yaml

The model registry must record Hugging Face model ID, revision, architecture, role, licence, supported training framework and deployment status.

Acceptance
Reproducible read-only environment diagnostic command.
All upstream commits and model revisions pinned.
Existing Lab 1–9 tests retain their baseline status.
Training framework compatibility established through executable smoke tests, not solely documentation.
Unsupported models explicitly marked experimental or blocked.
Work package M01 — Qwen3.8 teacher service and llama-swap

Priority: P0
Dependencies: M00

Tasks
Use the existing qwen3.8-Flash-DGX-UltraFast v16b recipe without initially modifying its measured configuration.
Validate model download hashes, image build, T80 drafter construction and startup.
Confirm /v1/models and /v1/chat/completions on the vLLM server.
Deploy llama-swap as a separately managed service.
Register the UltraFast vLLM endpoint as a peer rather than allowing llama-swap to launch a second copy.
Expose a stable teacher model identifier through llama-swap.
Verify OpenAI-compatible chat completion, tool calling, streaming, failure handling and model switching.
Establish exclusive GPU resource scheduling between teacher inference and student training.
Required interfaces
teacher-inference:
  backend: qwen3.8-ultrafast
  served_model: qwen
  endpoint: http://127.0.0.1:8000/v1
  gateway: llama-swap
  role: teacher

Use llama-swap's documented peers configuration to register the externally managed vLLM server. The client-facing model ID should be stable across backend replacements.

Important: A peer registration does not automatically give llama-swap authority to stop the external vLLM container. GPU resource ownership must be implemented separately.

Deliverables
infra/llama-swap/config.yaml
powercoder/inference/client.py
powercoder/inference/registry.py
powercoder/inference/gpu_lease.py
tests/integration/test_teacher_gateway.py
Acceptance
Successful end-to-end request through llama-swap to the real Qwen3.8 backend.
Actual model revision and backend recorded with every inference result.
Tool calls validated for schema conformity.
Controlled failure on unavailable backend; no silent fallback to an unspecified model.
Concurrent model requests respect configured resource limits.
GPU resource lease prevents overlapping memory-intensive training and teacher inference.
No unrelated containers or processes terminated.
UltraFast performance measured with its own benchmark scripts and compared against the repository's recorded baseline.
Work package M02 — PowerMCP canonical execution contract

Priority: P0
Dependencies: M00

Tasks
Reuse the existing pandapower streamable-HTTP MCP server.
Extend the integration with PyPSA and ANDES where practical.
Discover actual MCP tool definitions dynamically.
Snapshot tool schemas and versions for every training run.
Build a typed Python adapter with clearly defined request, result, provenance and error structures.
Provide deterministic simulation fixtures from the existing Synthetic-NEM test networks.
Separate solver execution from agent-generated conclusions.
Introduce controlled work directories, timeouts, resource limits and read-only reference datasets.
Canonical execution evidence

Each invocation must return or persist:

{
  "case_id": "snemsa-n1-0001",
  "solver": "pandapower",
  "solver_version": "pinned-version",
  "tool_name": "run_contingency_analysis",
  "input_hash": "sha256:...",
  "status": "converged",
  "result_hash": "sha256:...",
  "validity": {
    "schema": true,
    "numerical": true,
    "physical": true
  },
  "diagnostics": []
}

The schema is a proposed canonical representation. Actual PowerMCP responses should be adapted into it rather than assuming the existing server already emits these fields.

Deliverables
powercoder/powermcp/client.py
powercoder/powermcp/contracts.py
powercoder/verification/solvers.py
powercoder/verification/physics.py
tests/integration/test_powermcp_execution.py
Acceptance
Demonstrated MCP discovery and execution against a real network case.
Deterministic fixture re-execution within documented numerical tolerances.
Explicit differentiation among execution failure, non-convergence, constraint violation and successful validation.
No fabricated numerical values substituted for absent tool output.
Existing PowerMCP functionality retained.
Work package M03 — Live model integration with Lab 3

Priority: P0
Dependencies: M01, M02

Objective: Close the repository's existing live-model evaluation gap before building a new training framework.

Tasks
Introduce a real LLMProvider implementation supporting OpenAI-compatible endpoints.
Connect the existing bake-off orchestrator to llama-swap.
Preserve the existing deterministic policies as controls.
Add a genuine model-driven parameter-selection loop.
Compare proposals against the same simulator and scorer.
Record inference latency, tokens, solver calls and failures.
Implement provider-independent result collection.
Deliverables
powercoder/providers/openai_compatible.py
powercoder/providers/deterministic.py
Lab 3 integration updates
tests/integration/test_live_provider.py
Acceptance
At least one actual local LLM completes the existing Lab 3 task matrix.
Scorecard clearly distinguishes real models, deterministic control policies and non-LLM forecasting baselines.
No hard-coded expected model answer.
Existing deterministic scorecard tests continue passing.
Evaluation results include model and backend provenance.
Work package M04 — Training dataset factory

Priority: P1
Dependencies: M02, M03

Build a dataset pipeline, not a collection of manually curated prompts.

Dataset families
Family	Initial target
Python scientific programming	6,000
Power flow and contingency analysis	6,000
PowerMCP agent/tool trajectories	5,000
Code repair and error recovery	4,000
Engineering interpretation	3,000
Safety and uncertainty handling	1,000
Total	25,000

These are development targets subject to quality validation, not mandatory synthetic quotas.

Tasks
Extract authorised examples from existing labs, documentation and solver workflows.
Generate parameterised engineering tasks using deterministic seeds.
Use real solver execution to establish expected results.
Convert verified demonstrations to conversational SFT examples.
Retain failed examples separately for repair training.
Deduplicate near-identical code and tasks.
Partition datasets by network topology and task family to reduce leakage.
Reserve independent benchmark cases from the outset.
Enforce dataset provenance and licence checks.
Exclude confidential, restricted and personally identifiable data from public artefacts.
Dataset lifecycle
proposed
   ↓
generated
   ↓
executed
   ↓
verified
   ↓
accepted / rejected
   ↓
versioned
   ↓
train / validation / test
Deliverables
powercoder/datasets/generate.py
powercoder/datasets/validate.py
powercoder/datasets/deduplicate.py
powercoder/datasets/split.py
schemas/training-example.schema.json
Dataset manifests containing hashes and licence metadata.
Acceptance
Initial 500-example smoke dataset generated.
Full provenance for 100% of accepted records.
All records marked verified have corresponding execution evidence.
Fixed dataset splits and seeds.
No benchmark contamination.
Repeated generation with identical inputs produces the same task definitions and expected solver results.
Work package M05 — Qwen3.8 teacher distillation

Priority: P1
Dependencies: M01, M04

Tasks
Send structured tasks to the teacher through llama-swap.
Generate candidate Python solutions and PowerMCP workflows.
Execute candidate solutions in isolated environments.
Verify correctness using independent numerical checks.
Automatically reject unsupported or invalid solutions.
Permit targeted correction attempts using actual execution diagnostics.
Retain accepted repair trajectories and tool-use demonstrations.
Compare Qwen3.8-Flash-Next and Qwen3.8-27B on the same held-out task set.
Record all generation and validation costs.
Design principle

The teacher supplies proposed behaviours; solvers provide objective observations.

Do not retain unverified teacher reasoning as engineering truth.

Deliverables
powercoder/distillation/teacher.py
powercoder/distillation/generate.py
powercoder/distillation/repair.py
powercoder/distillation/filter.py
powercoder/distillation/manifest.py
Acceptance
Initial batch of 500 examples processed end to end.
Every accepted example contains reference execution evidence.
Invalid examples rejected or explicitly quarantined.
Generator can resume after interruption without duplicating accepted work.
Acceptance rate, solver call counts and rejection reasons reported.
A teacher-comparison report identifies which model performs better on the target engineering tasks.
Work package M06 — Unsloth initial SFT

Priority: P1
Dependencies: M04, M05

Objective: Establish working student fine-tuning before introducing additional optimisation complexity.

Tasks
Set up a pinned Unsloth container for GB10 ARM64.
Smoke-test Qwen3.5-4B and Qwen2.5-Coder-1.5B.
Verify tokenizer, chat template, padding, tool-call representation and target-module selection.
Run BF16 LoRA training on the 500-example dataset.
Start with rank 32 and 64 experiments.
Record train/validation loss, memory usage and training throughput.
Save adapters and merged BF16 checkpoints.
Evaluate against the unchanged base models.
Extend training to larger verified datasets only after positive evidence.
Deliverables
training/unsloth/train.py
training/unsloth/config.yaml
training/unsloth/Containerfile
training/unsloth/export.py
Baseline model scorecards
Signed or hash-verified checkpoint manifests
Acceptance
Successful GPU fine-tuning on the physical GB10.
Checkpoints can be saved, reloaded and used for inference.
Adapter tensors demonstrably changed.
Validation results reproducible within a documented tolerance.
Baseline and tuned checkpoint evaluated under identical conditions.
No evidence of training-data leakage into test results.
Work package M07 — Axolotl reproducible training

Priority: P1
Dependencies: M06

Objective: Transition from rapid experimentation to a durable training pipeline.

Tasks
Build and validate a separate Axolotl ARM64 training environment.
Implement YAML-controlled configurations matching Unsloth's dataset, base model and LoRA configuration.
Reproduce the Unsloth experiment from the same base model.
Measure accuracy and performance differences.
Implement longer SFT runs, checkpoints, restarts and resumable training.
Test loading an Unsloth-produced PEFT adapter into Axolotl where supported.
Validate compatibility before attempting continued training from an adapter.
Introduce full fine-tuning of the 1.5B student as a separate research experiment if resource usage is acceptable.
Export merged BF16 checkpoints.
Important requirement

Do not silently stack Axolotl training on an Unsloth adapter.

Provide two explicit modes:

Reproduction: Axolotl independently trains from the original base model.
Continuation: Axolotl resumes from a compatible, validated adapter/checkpoint.

Both modes must record their complete lineage.

Deliverables
training/axolotl/sft-4b.yaml
training/axolotl/sft-edge.yaml
training/axolotl/Containerfile
training/axolotl/compare.py
docs/powercoder/trainer-parity.md
Acceptance
Axolotl trains a student successfully on GB10.
Checkpoint reload/resume demonstrated.
Comparison against Unsloth completed.
Model, adapter, tokenizer and dataset compatibility verified.
Any training continuation reproducibly attributed to the correct parent checkpoint.
Work package M08 — Solver-reward optimisation

Priority: P2
Dependencies: M07

After conventional SFT establishes an improvement, evaluate GRPO or another supported reinforcement-learning approach.

Reward dimensions
Python execution success.
Appropriate PowerMCP tool selection.
Correct tool parameters.
Solver convergence handling.
Numerical correctness.
Physical validity.
Correct interpretation of violations.
Evidence-backed conclusions.
Appropriate escalation when results are uncertain.
Requirements
Keep the reward evaluator independent of the agent.
Execute candidate code in disposable sandboxes.
Apply execution timeouts and resource limits.
Penalise invented results and unsupported claims.
Record per-component rewards.
Compare RL-tuned models against SFT-only models.
Reject any optimisation that improves the scalar reward while reducing real engineering correctness.
Acceptance
Reward components unit tested against adversarial examples.
No access to hidden test answers from the training process.
A statistically meaningful comparison against the SFT baseline.
Full traceability of reward computation.

GRPO is an optimisation experiment, not a mandatory dependency for the first release.

Work package M09 — Independent benchmark suite

Priority: P1
Dependencies: M03, M04

Extend the existing benchmark architecture instead of building an unrelated evaluator.

Required benchmark families

Python programming: executable function generation, debugging, tests, scientific NumPy/SciPy tasks and an external coding benchmark where licence permits.

Power engineering: AC/DC power flow, contingency analysis, bus-voltage compliance, thermal violations, load-flow convergence, unit consistency, topology modification and engineering interpretation.

Agent behaviour: MCP tool selection, correct schema generation, recovery from failed simulations, multi-step execution, evidence citation and uncertainty handling.

Robustness: invalid input, missing solver, contradictory instructions, fabricated observations, unsafe operational requests and numerical edge cases.

Scorecard

Report at least:

Solver-verified pass@1.
Python execution pass rate.
Power-system correctness rate.
Tool-call schema validity.
False-safe and fabricated-result rates.
Tokens and tool calls per accepted task.
End-to-end latency.
Peak memory usage.
Energy per correctly completed task where measurable.
Acceptance gates

Initial release targets, to be finalised against measured baselines:

Domain pass@1 improves by at least 10 percentage points against the unchanged student base model, with uncertainty intervals reported.
No material regression on general Python benchmarks.
Zero fabricated solver results in the designated critical negative-test suite.
Numerical outputs validated using documented tolerances.
Benchmark held-out data never used in training or teacher correction loops.
Raw individual results retained, not only summary scores.

Use at least 200 independent held-out domain tasks for initial candidate selection, with larger evaluation sets for release qualification.

Work package M10 — Windows Foundry Local deployment

Priority: P2
Dependencies: M07, M09

Tasks
Export the selected merged BF16 student to supported ONNX format.
Apply deployment-specific INT8 and INT4 quantisation.
Validate model loading through ONNX Runtime GenAI.
Integrate with Foundry Local where supported.
Identify the actual execution provider.
Test DirectML acceleration separately from CPU fallback.
Compare quantised outputs against BF16.
Package model metadata, tokenizer, quantisation parameters and runtime requirements.
Acceptance
Model loads and produces valid outputs on a physical supported Windows machine.
Execution provider verified rather than assumed.
INT8 and INT4 benchmark reports include numerical and agentic regressions.
Proposed maximum acceptable accuracy regression: 3 percentage points for INT8 and 5 for INT4, subject to confirmation against baseline variation.
Deployment instructions identify tested Windows and runtime versions.
Unsupported operator or hybrid-architecture limitations are reported explicitly.
Work package M11 — Qualcomm Q6A edge deployment

Priority: P2
Dependencies: M07, M09

Tasks
Qualify Qwen2.5-Coder-1.5B against the Radxa Q6A's supported QAIRT/QNN runtime.
Export a compatible checkpoint for Qualcomm's compiler.
Quantise using an accelerator-supported scheme.
Generate the target-specific compiled model/context binary.
Run the complete prompt → inference → response path.
Validate Hexagon NPU execution.
Test memory requirements, context length and generation throughput.
Compare student model outputs against the original BF16 checkpoint.
Evaluate a newer Qwen student only after the first model compiles successfully.
Acceptance
Actual execution on the physical target.
Verified NPU acceleration, not only successful compilation.
No undisclosed CPU fallback.
Acceptable sustained throughput and peak-memory usage for the intended use.
Solver-verified domain pass rate survives quantisation.
Versioned QNN binary with documented chip, firmware, runtime and compiler compatibility.

If the architecture cannot compile, mark the target unsupported and preserve the failure evidence. Do not substitute a different accelerator or claim success.

Work package M12 — Release, provenance and repeatability

Priority: P2
Dependencies: M09–M11

Tasks
Create a versioned model manifest.
Record source checkpoint, LoRA adapter, dataset hash, trainer configuration and export toolchain.
Produce model cards with intended use and limitations.
Generate SBOMs and dependency inventories.
Record model and dataset licences separately.
Sign or hash-verify released artefacts.
Produce machine-readable benchmark results.
Provide an installation and rollback procedure.
Validate a clean-environment reproduction.
Establish release gates for future teacher/student model upgrades.
Release artefacts
powercoder-release/
├── manifest.json
├── model-card.md
├── provenance.json
├── dataset-manifest.json
├── benchmark-results.json
├── software-sbom.json
├── training/
│   ├── unsloth/
│   └── axolotl/
├── bf16/
│   └── checkpoint/
├── windows/
│   ├── int4/
│   └── int8/
└── qualcomm/
    └── q6a-int4/

The output directories for Windows and Qualcomm are populated only after their separate acceptance gates pass.

Acceptance
Every released artefact traceable to its source weights and dataset.
Model checksum verified before use.
Training and evaluation can be reproduced from published configuration and permitted inputs.
Licensing and redistribution reviewed.
No model represented as operationally certified for electricity-grid control merely because benchmark targets were achieved.
No unresolved critical security or data-provenance failures.
Operational delivery requirements
Repository integration

nem-poweragent-lab

Owns orchestration, dataset generation, student-training recipes, scoring, reports and release manifests.

PowerMCP

Owns solver functionality and MCP interfaces. Add capabilities only when reusable outside PowerCoder. Avoid putting training-specific code into solver implementations.

qwen3.8-Flash-DGX-UltraFast

Owns teacher-serving performance. Keep its measured configuration pinned; new optimisations must be separate reproducible variants with new benchmarks.

llama-swap

Remains an upstream dependency. Configure it rather than forking it unless an independently demonstrated limitation requires a change.

Commands to deliver

Extend the existing Justfile with consistent commands such as:

just powercoder-doctor
just powercoder-teacher-check
just powercoder-mcp-check
just powercoder-benchmark-baseline
just powercoder-dataset-smoke
just powercoder-distill-smoke
just powercoder-train-unsloth
just powercoder-train-axolotl
just powercoder-evaluate
just powercoder-export-windows
just powercoder-export-qnn
just powercoder-release-check

Commands must have predictable exit codes, support --help where appropriate, and never silently pass after a skipped critical test.

Delivery sequencing
Phase	Work packages	Exit gate
G0 — Qualify	M00	Environment, repositories and licences verified
G1 — Connect	M01–M03	Real teacher + PowerMCP + Lab 3 integration
G2 — Produce	M04–M05	Verified 500-example distillation batch
G3 — Train	M06	First Unsloth checkpoint and baseline comparison
G4 — Reproduce	M07	Axolotl training and documented parity
G5 — Improve	M08–M09	Independently demonstrated performance improvement
G6 — Deploy	M10–M11	Device-specific quantisation and runtime validation
G7 — Release	M12	Signed, documented, reproducible model release

Work packages are dependency ordered, not arbitrary calendar commitments.

Final definition of done

The overall mission is accepted when:

The existing PowerAgent labs remain functional.
A real Qwen3.8 teacher is accessible through llama-swap.
Solver-backed training records are generated without fabricated numerical ground truth.
Unsloth has produced and evaluated a fine-tuned student.
Axolotl has independently reproduced or correctly continued a verified training run.
The resulting model demonstrates a measurable improvement in power-engineering task success.
Every accepted training example has complete provenance.
Quantised deployment artefacts pass their respective hardware acceptance tests.
The build, evaluation and release process is reproducible.
All results are accurately labelled, including failed and unsupported deployment targets.

Engineering principle: The objective is not to make the small model sound like a large model. It is to make the small model execute correct engineering workflows reliably, efficiently and with verifiable evidence.

4. Important implementation decisions
A. Preserve the UltraFast performance recipe

Your qwen3.8-Flash-DGX-UltraFast README reports the following measurements for its v16b configuration on one GB10:

Peak single-stream

74.1

Tokens/s, copy-heavy workload

Peak 8-stream aggregate

212.2

Tokens/s, same workload

Model residency

~71 GiB

Reported baseline footprint

Those are workload-specific repository measurements, not guaranteed throughput for novel PowerMCP tasks.

The existing configuration uses AutoRound INT4 experts, FP8 side layers, a memory-mapped embedding table and speculative decoding. Don't merge a student LoRA adapter into this quantised inference recipe. Train the students from their own Hugging Face checkpoints.

Because the teacher occupies much of the GB10 memory, schedule inference-data generation and training as separate GPU-intensive phases.

B. Licence differences matter

Verified Hugging Face model metadata

Teacher	Licence	Release implication
Qwen3.8-27B	Apache 2.0	Relatively straightforward for a redistributable training pipeline
Qwen3.8-Flash-Next	Qwen Community License 1.0	Requires review of its specific terms and any obligations for derived work

Do not conflate the Apache 2.0 licence of the UltraFast serving code with the licence of the underlying teacher weights.

I would maintain a training-lineage report that can identify exactly which teacher generated every retained example. That allows the project to release a clean Apache-licensed training stream independently if necessary, subject to the relevant licence review.

C. llama-swap should be an API boundary, not the trainer

For your externally managed UltraFast vLLM service, the conceptual llama-swap configuration is:

peers:
  dgx-teacher:
    proxy: http://127.0.0.1:8000
    models:
      - qwen

This uses llama-swap's documented peer support. Run the proxy in the same host network context, or change the address to a reachable container-service hostname. Validate the client-facing peer model identifier and request routing before training.

Then the datasets and trainers never need to know whether the teacher is vLLM, llama.cpp, a future Qwen3.9 model, or another OpenAI-compatible server.

5. Suggested initial issue breakdown

The mission should be implemented in small pull requests rather than one enormous PR.

Proposed work items

Select the issues to include in the implementation backlog.

[Copy issue specifications]

These are proposed issues, not issues already opened in GitHub. No repository changes have been made in this conversation.

The highest-value first milestone is G1: a real Qwen3.8-Flash-Next teacher, served by the existing optimised GB10 recipe, routed through llama-swap, executing real PowerMCP-backed evaluations within your existing Lab 3. Once that works, Unsloth and Axolotl become controlled consumers of a proven training-data pipeline rather than independent experiments.

I've also handed the repository-specific implementation task to ChatGPT Work, where it can inspect and work across the repositories as a coordinated project.
