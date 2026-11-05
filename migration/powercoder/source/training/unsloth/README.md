# M06 recovery and verification

The crashed pi session left a Qwen3.5-4B rank-32 adapter and checkpoint-100.
The training log reports 273 training rows, 100 steps, 390.2732 seconds,
NVIDIA GB10, and torch 2.13.0+cu130. These are recorded observations from
that run. Recovery has since verified adapter and merged-checkpoint CUDA
reload/inference; answer quality and benchmark improvement remain unproven.

The saved checkpoint audit finds 688 finite F32 adapter tensors. All 248
language LoRA B matrices differ from their default zero initialization. The
96 vision B matrices are zero. Exact initial A tensors were not saved.
Checkpoint hashes describe the files at recovery, not a launch-time snapshot.

## CPU checks

From the repository root:

```sh
uv run --offline --no-sync python -m pytest training/unsloth/test_evaluation.py powercoder/distillation/test_distillation.py -q
uv run --offline --no-sync python training/unsloth/audit_checkpoint.py \
  --adapter training/unsloth/out/qwen3.5-4b-lora \
  --source training/unsloth/config.yaml training/unsloth/out/train_log.json \
    training/unsloth/out/train_full.log powercoder/datasets/smoke/splits/train.jsonl \
  --out training/unsloth/out/checkpoint_audit.json
```

The checkpoint audit should exit 0 with `saved_tensor_gate: true`. It does
not establish checkpoint reload, GPU execution, or model quality.

The old 227 held-out rows contain only 108 unique task inputs and 23 rows
overlapping training. Their combined independence audit must fail. The old
110-row topology-reserved benchmark contains only seven unique prompts.
Preserve these files as evidence; do not report them as 227 independent tasks.

Generate a fresh domain set in a new output directory:

```sh
uv run --offline --no-sync python -m powercoder.datasets.build_benchmark \
  --exclude powercoder/datasets/smoke/examples.jsonl \
  --out-dir benchmarks/power-agent-bench-lite/results/domain-held-out-20261020
```

This run generated 306 verified tasks: 26 power-flow reports, 120 load fits,
120 line-rating fits, and 40 dispatch fits. All 306 prompts and input sets are
distinct; none overlaps any task in the original 500-example dataset used for
teacher correction and training. The rows and manifest are ignored artifacts.
Generation refuses to overwrite an existing directory. Recheck independence:

```sh
uv run --offline --no-sync python -m powercoder.datasets.audit \
  --candidates benchmarks/power-agent-bench-lite/results/domain-held-out-20261020/examples.jsonl \
  --exclude powercoder/datasets/smoke/examples.jsonl
```

This is separation by prompt and concrete inputs, not separation by topology
or template. It covers four power-flow task families on the existing synthetic
NEM topology. General Python, agent behaviour, robustness, and the critical
fabrication suite remain required for M09.

## Closed-book diagnostic and export

The evaluation script below is a direct-response diagnostic. The grader uses
the solver, but the model itself has no tool interface. This does not establish
the mission's agent correctness or evidence-backed tool execution. The release
comparison needs an actual solver/tool interface for both base and candidate.

Inside the existing qualified GPU environment, with the repository mounted
as the working directory and the base model cached. Independent numerical
scoring also requires pandapower 3.5.4, powerio 0.7.2, and SciPy in that
environment; the updated container recipe includes them but still needs a
real build and GPU qualification:

```sh
python training/unsloth/evaluate.py \
  --adapter training/unsloth/out/qwen3.5-4b-lora \
  --splits benchmarks/power-agent-bench-lite/results/domain-held-out-20261020/examples.jsonl \
  --exclude powercoder/datasets/smoke/examples.jsonl \
  --out training/unsloth/out/eval_results.json
python training/unsloth/export.py training/unsloth/config.yaml
```

Evaluation loads the base and saved adapter separately, uses greedy decoding
and the same system prompt, retains full individual responses, and computes
Wilson pass-rate intervals and a paired percentile bootstrap interval for the
delta. Fit answers are checked by a fresh solver run with the task's actual
metric tolerance; report and trajectory answers use reference matching.
Tool execution and evidence use are not established by reference matching.
The overall G5 gate stays pending the separate regression and fabrication
suites even if domain improvement exceeds ten percentage points.

Export now loads the saved adapter before calling PEFT's
`merge_and_unload(safe_merge=True)`. The previous implementation loaded only
the base and could not produce a valid tuned export. The corrected GPU export
exited 0, and all 15 manifest files (9,429,343,523 bytes) were independently
rehashed. `out/merged-reload-smoke.json` also confirms full CUDA placement and
actual GB10 inference from the merged checkpoint. These probes establish
checkpoint usability, not numerical answer correctness.

The initial recovery was restricted by the sandbox. GPU, Podman, and network
access were restored on 2026-10-20. Ruff now runs successfully. The evaluation
image built as `74feb3e2e84cfde5d1d2d21c34213d60114d7eb7a80bb1f9352ff2dda15c00ff`
using `Containerfile.eval`; its resolved packages are recorded in
`out/eval-environment.lock.txt`.

`powercoder-m06-eval306-r1` was intentionally stopped after preserving 106
base responses in `out/eval306-r1-raw/`. It is an incomplete closed-book
diagnostic, not a completed base-versus-candidate or M09 agent comparison.
The stop freed GPU capacity for independent Axolotl reproduction. Keep the raw
responses as diagnostic evidence. The incremental-output mechanism preserves
finished answers on interruption and refuses to mix new runs with old files.

The first actual adapter reload exposed a PEFT offload-index failure for
`base_model.model.mtp.fc` under automatic device placement. The shared loader
now requests `device_map={"": "cuda:0"}` explicitly. The follow-up probe is
`powercoder-m06-adapter-smoke-r2`, writing `out/adapter-reload-smoke-r2.json`.
That follow-up exited 0: all parameters loaded on `cuda:0`, and the GB10
generated a nonempty response from a training prompt. The saved adapter hash
is `d749dd82e787625e5a217c8092eec06603a58c69236ff3cbf5a9385f64cde319`.
The generated answer is poor. This verifies reload and inference, and does
not establish answer correctness or improvement over the base model.

The edge base has been downloaded at revision
`df3ce67c0e24480f20468b6ef2894622d69eb73b`; `config.edge.yaml` records the
separate rank-32 run. Training now honours an explicit model revision. Edge
training and rank-64 experiments remain unexecuted.

M06 still needs the valid baseline comparison, validation loss, rank-64 and
edge-student experiments, and a fully pinned
environment. M07 needs independent Axolotl training and verified resume/lineage.
M08 must follow evidence of SFT improvement. M09 needs the remaining suites
and actual model scorecards. M10 needs selected-model ONNX quantization and
physical Windows execution with a measured execution provider.
