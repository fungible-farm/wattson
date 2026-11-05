# Granite 4.2 8B comparison experiment

This experiment uses IBM's Apache-2.0
[`ibm-granite/granite-4.2-8b`](https://huggingface.co/ibm-granite/granite-4.2-8b),
revision `b3791992239dce12d0038f1f54a8adb7b02d59ab`, as a separate starting
checkpoint. The four downloaded weight shards were verified against the pinned
Hub metadata on 2026-10-21. Evidence is local, ignored output at
`training/unsloth/out/granite42-download-verified.json`. A completed download is
not a trained model or an accuracy result.

1. Read `training/unsloth/config.granite42.yaml`. Its training rows and
   hyperparameters match the original Qwen experiment: the same 273 verified
   training rows, rank 32, alpha 64, seed 42 and 100 optimizer steps. The
   Granite checkpoint and output paths are separate from the Qwen artifacts.

   Granite training uses real Transformers/PEFT on the physical GB10, in the
   existing pinned training image. It does not use Unsloth model patches.
   These are two different training implementations, with their package
   versions recorded in the Granite preflight. No new package is installed.
   Dependency licenses are recorded in `train.py`.

2. The native-template tokenization check was run on all 273 original training
   rows. You should see a nonempty assistant target on every row and a maximum
   length of 281 tokens, within the configured 1,024-token limit. `encode_example`
   derives its mask from the actual rendered answer boundary, verifies token
   prefix equality, and refuses any truncated target. The benchmark is never
   opened by the trainer.

3. Run training on a free GPU, with the repository mounted at `/workspace`:

   ```sh
   podman run --name powercoder-granite42-training --device nvidia.com/gpu=all \
     --network none --security-opt label=disable \
     -v "$PWD:/workspace" -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
     -e PYTHONUNBUFFERED=1 \
     74feb3e2e84cfde5d1d2d21c34213d60114d7eb7a80bb1f9352ff2dda15c00ff \
     python -m training.granite.train --config training/unsloth/config.granite42.yaml
   ```

   The trainer refuses an existing output directory, rechecks each base shard
   against its pinned revision and SHA-256, and requires CUDA. You should see
   100 completed steps, a saved adapter, an independently reloaded saved-adapter
   merge and a hash manifest. `train_log.json` records the source hashes,
   completed steps, loss history, runtime versions and physical GPU. The
   configuration alone does not prove any of these steps completed.

4. Evaluate Granite base and student on the complete 306-task suite using
   `powercoder.inference.evaluate_agent`; see `powercoder/inference/README.md`.
   IBM documents `qwen3_coder` tool parsing and permits the built-in
   `nemotron_v3` reasoning parser. Preserve the same greedy decoding, token,
   turn, tool and HTTP budgets as the Qwen runs.

   Compare Granite student with Granite base, Qwen base and Qwen student.
   You should see paired task counts, pass rates, deltas and uncertainty.
   The ten-percentage-point domain gain is a measured gate, not a guaranteed
   consequence of training. The original agent score uses the reference's field
   labels; its parser can reject a numerically correct differently formatted
   report. The numerical scorecard below re-solves reports and accepts labelled
   plain text, Markdown and JSON. Both scores retain their raw responses.

## Sequential driver

`uv run --offline --no-sync python -m training.run_comparison --help` describes
the driver. It serves and evaluates the pinned Qwen base, then the original Qwen
student, trains Granite, and evaluates Granite base and student, followed by all
four strict and four numerical comparisons. It requires all 306 scored tasks
before advancing, releases each server only after its full evaluation, never
retries a failed stage, and refuses to reuse an existing run root.

Before each vLLM server and before Granite training it waits up to an hour for
`MemAvailable` to cover the stage's floor plus `MEMORY_MARGIN_BYTES`
(`memory_wait` / `memory_admitted` / `memory_refused` journal events). The floor
is the vLLM memory fraction of `MemTotal`, or Granite's 16.76 GB of weights for
training. GB10 memory is unified, so llama-swap models compete for it: idle
models unload after the gateway's `globalTTL`, and the on-demand
`granite42-vllm.service` must be stopped. Admission is checked once per stage;
a gateway model loaded mid-stage can still trigger the OOM-killer.

Launch it supervised, never as a detached shell:

```sh
./scripts/launch_powercoder_comparison.sh training/unsloth/out/agent-domain-r3
systemctl --user status powercoder-agent-domain-r3
systemctl --user stop powercoder-agent-domain-r3   # stops driver, evaluator and containers
```

`pipeline.jsonl` in the run root records actual child PIDs, commands, memory
admission, stage exits, errors and a `driver_stopped` event on SIGTERM;
per-model logs and fsynced per-task journals retain raw evidence.
`pipeline_complete` means the stages completed, even if the measured gain misses
the threshold. It writes `qwen-numerical-comparison.json`,
`granite-numerical-comparison.json`, `granite-vs-qwen-base-numerical.json` and
`granite-vs-qwen-student-numerical.json` with the frozen
`powercoder.inference.domain_scorecard` grader alongside the stricter agent
scores.

History: `agent-domain-r2/` was an earlier driver that adopted a separately
started base evaluation by PID (journals `pipeline.jsonl`,
`pipeline-granite-memory.jsonl`, `pipeline-scorecards.jsonl`). It was stopped
unsupervised at 31/306 base results on 2026-10-21 and is kept as partial
evidence only; see `docs/POWERCODER_HANDOFF_2026-10-21.md`.

For an AEMO modeller, the comparison tests whether changing and fine-tuning a
local model improves independently checked AC study answers under equal tool
access. It makes no claim of a real-network digital twin, a release-wide G5
pass, or Windows deployment qualification.
