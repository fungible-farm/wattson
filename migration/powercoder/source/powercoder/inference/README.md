# Canonical PowerMCP transport

Run `uv run --no-sync python -m powercoder.inference.mcp_server` as an MCP stdio
child process. It uses the installed official MCP Python SDK and exposes the
exact two schemas from `PowerMCP.get_tool_schemas()`: `load_network` and
`run_power_flow`. Responses preserve canonical status, solver data, error,
execution time and evidence. Calls execute serially because the iteration
observer temporarily patches the NR function.

This endpoint serves this repository's four-state contract. The older
`kube/powermcp_serve_http.py` wraps upstream Power-Agent's eight-tool server;
that separate endpoint does not establish canonical contract parity.

`uv run --no-sync python -m pytest tests/integration/test_canonical_mcp_transport.py -q`
starts the real stdio process, discovers schemas and makes actual solver calls.
It checks success and non-convergence against saved real-solver fixtures;
unsupported IDs are rejected by MCP schema validation before backend execution.
The small networks are named test fixtures, not NEM benchmark performance.

This is transport qualification. Model-controlled tool use, controller-owned
durable traces, scalar modification tools for fitting tasks and full benchmark
comparisons still need implementation and verification. A model's self-reported
trajectory alone cannot prove it called a tool.

## Model tool-use probe

With a student OpenAI-compatible server ready, run:

```sh
uv run --no-sync python -m powercoder.inference.tool_probe \
  --base-url http://127.0.0.1:8101 --model powercoder-axolotl-4b \
  --prompt training/axolotl/out/tool-probe-r1-prompt.txt \
  --journal training/axolotl/out/tool-probe-r1.jsonl
```

Supply a training-only prompt as plain text. The probe exposes tools discovered
from the actual MCP child, lets the real model select calls, and supplies actual
RPC responses in subsequent model turns. The controller flushes and fsyncs each
observation; existing journals are refused. It limits model turns and tool
calls, uses greedy generation, and never executes generated code. Raw model
responses, tool requests, actual tool responses and terminal states are retained.

A terminal `answered` state records protocol completion, not answer correctness;
inspect the independent tool responses and final answer. This probe is not the
release benchmark or evidence of the required improvement.

Observed qualification on 2026-10-20: `powercoder-axolotl-4b` served by vLLM
0.31.0 on GB10 made both requested calls for training example
`ex_981a9cd2b75678ce`. Real CSIRO results were 503 buses, 698 branches, 57
generators, 186 loads; the power flow converged in five iterations with 39
voltage violations. The final response retained `constraint_violation`, reported
physical validity false, and rounded the actual voltage range to 0.907–1.094 pu.
The controller exited 0 after two actual tool calls and a final `stop` response.
Raw evidence: `training/axolotl/out/tool-probe-r1.jsonl`. This training-prompt
qualification does not establish held-out accuracy, zero fabrication across a
suite, or an improvement over base.

## Complete held-out domain comparison

`evaluate_agent` runs all 306 audited tasks once, with greedy decoding and the
probe's same turn, token, and tool budgets for each model. Only the task prompt
is given to the model. `run_study_trial` permits the model to select scalar
trials and returns real AC solver measurements; it does not fit a target for it.
Keep each model's serving image, launch arguments, checkpoint revision and
weight hashes beside the run. Use the original Unsloth merged student for the
primary Qwen comparison; the independent Axolotl reproduction is a separate
candidate.

1. Start the pinned local model server with automatic tool calling enabled.
   Run the following for each model, using a fresh output directory:

   ```sh
   uv run --offline --no-sync python -m powercoder.inference.evaluate_agent \
     --base-url http://127.0.0.1:8101 --model powercoder-qwen-base \
     --tasks benchmarks/power-agent-bench-lite/results/domain-held-out-20261020/examples.jsonl \
     --exclude powercoder/datasets/smoke/examples.jsonl \
     --out training/unsloth/out/agent-domain-r1/base
   ```

   You should see progress through `306/306`, followed by a complete summary.
   Each model response and actual MCP response is fsynced to an exclusive
   per-task journal. A terminal model answer alone is insufficient: the
   independent grader must accept its numerical result and the controller must
   have observed a matching successful study. A fit's final scale must itself
   have been tried. Model protocol errors fail the attempt; transport errors
   stop visibly. Interrupted output is preserved and never silently reused.

2. Serve the student with matching settings and repeat into
   `training/unsloth/out/agent-domain-r1/student`. Then compare:

   ```sh
   uv run --offline --no-sync python -m powercoder.inference.compare_agents \
     --base-dir training/unsloth/out/agent-domain-r1/base \
     --student-dir training/unsloth/out/agent-domain-r1/student \
     --out training/unsloth/out/agent-domain-r1/comparison.json
   ```

   You should see the measured paired delta, confidence intervals and per-family
   counts. Missing tasks, unequal budgets, changed inputs or changed raw journals
   prevent a comparison. `domain_threshold_met` reports the measured ten-point
   criterion; it is never presumed. General Python and fabrication gates remain
   separate. This comparison supports an AEMO modeller assessing whether local
   fine-tuning improves independently checked study results under equal tool
   access, rather than whether a model can repeat saved answers.

3. Check the scoring boundaries before changing the harness:

   ```sh
   uv run --offline --no-sync python -m pytest \
     powercoder/inference/test_evaluate_agent.py \
     powercoder/inference/test_compare_agents.py \
     training/unsloth/test_evaluation.py \
     tests/integration/test_canonical_mcp_transport.py -q
   ```

   The evidence tests reject calls for another scale, case, element or
   contingency, MCP errors, nonconvergence and truncated final generations.
   Their small response envelopes are adversarial test fixtures, not physics
   results or model performance evidence.

Granite is a separate experiment configured in
`training/unsloth/config.granite42.yaml`, using the same training split and
hyperparameters. Its upstream model is Apache-2.0, pinned to revision
`b3791992239dce12d0038f1f54a8adb7b02d59ab` of
[`ibm-granite/granite-4.2-8b`](https://huggingface.co/ibm-granite/granite-4.2-8b).
The config alone does not establish a completed download, training run or gain.
The sequential driver and Granite training procedure are documented in
[`training/granite/README.md`](../../training/granite/README.md).

## Solver-verified numerical scorecard

MISSION.md M09 lists solver-verified domain pass@1 and agent behaviour as
separate metrics. The original `compare_agents` output is the stricter agent
metric: it also requires a successful matching model-selected study, including
a trial at the final fit scale. Retain that output and its original scores.

For the numerical metric, run the same fixed post-processing for every model:

```sh
uv run --offline --no-sync python -m powercoder.inference.domain_scorecard \
  --base-dir training/unsloth/out/agent-domain-r2/base \
  --student-dir training/unsloth/out/agent-domain-r2/student \
  --out training/unsloth/out/agent-domain-r2/qwen-numerical-comparison.json
```

You should see all 306 paired tasks, the numerical pass-rate delta and its
uncertainty, plus the retained stricter agent comparison. The command refuses
partial runs, altered raw journals, mismatched source data or unequal budgets.
It derives final answers from the actual model responses and checks them
against their original scored records. Every fit is independently re-solved at
the proposed scale. Every report is re-solved for its actual contingency with
the canonical loader and real pandapower engine; labelled plain text, Markdown
and JSON fields use the same documented tolerances (voltages 0.0001 pu, line
loading 0.01 percentage points, exact iteration/violation counts). Missing or
incorrect values, nonfinite JSON numbers, contradictory convergence declarations
and incomplete model attempts fail. The parser consistently takes the first
labelled occurrence of a repeated field, as the original evaluator does.

The `solver-verified-domain-v1` grader was fixed before the first full paired
comparison. Post-processing gives the models no feedback, new attempt or extra
tool call. It preserves original scores and hashes, records its own source hash,
and applies to Qwen and Granite equally. A numerically correct answer can have
failed tool evidence; the scorecard retains both facts. Numerical correctness
alone does not establish zero fabrication, the general Python gate or G5.

`powercoder/inference/test_domain_scorecard.py` checks presentation invariance,
failure precedence, missing/nonfinite fields and an actual AC solve on a named
small solver fixture. Add it to the test command above when changing this
post-processing. These fixture tests are not model performance results.
