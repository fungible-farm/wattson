# Trainer parity — M07 qualification in progress

Axolotl GPU training, full checkpoint resume and merged-model inference have
succeeded on physical GB10. Measured evidence appears below. Task accuracy
comparison and performance parity remain unproven; M07 remains incomplete.

## Environments

| Component | Unsloth evaluation image | Separate Axolotl image |
| --- | --- | --- |
| Image ID | `74feb3e2e84cfde5d1d2d21c34213d60114d7eb7a80bb1f9352ff2dda15c00ff` | `3bbe9128db0886d5a89bbaf5de7050c7877e0804b19e6e56b4dbc97e4840b2b2` |
| Framework | Unsloth 2026.10.7 | Axolotl 0.21.1.dev0 |
| PyTorch | 2.13.0+cu130 | 2.13.0+cu130 |
| Transformers | 5.17.0 | 5.20.1 |
| PEFT | 0.21.2 | 0.21.2 |
| Datasets | 4.8.5 | 5.0.1 |
| Accelerate | 1.15.0 | 1.15.0 |
| bitsandbytes | 0.50.2 | 0.50.2 |

The Axolotl image contains no Unsloth package. It starts from the existing
ARM64 PyTorch GPU-test image
`bbee48c815a1210eed3ccbd63035ccb7b8008fa2db6e62e3c41b94bcbd6f08c8`.
That substrate was created by the prior pi session; rebuilding it from a fully
committed, locked recipe remains part of environment qualification. Full
resolved inventories are in `training/unsloth/out/eval-environment.lock.txt`
and `training/axolotl/out/environment.lock.txt`.

Axolotl source is pinned to
[`cc83a1c12e2d2010345806702d8ad3f794fad7f6`](https://github.com/axolotl-ai-cloud/axolotl/tree/cc83a1c12e2d2010345806702d8ad3f794fad7f6).
The configuration adapts its Qwen3.5 example, with BF16 LoRA, SDPA, and sample
packing disabled for the ARM64 baseline. Upstream requires additional FLA
kernels for packed Qwen3.5 sequences.

Upstream pins `zstandard==0.22.0`, whose isolated build pulled cffi 1.16.0 and
failed on this Python 3.13 ARM64 image. The recipe explicitly overrides it to
`zstandard==0.25.0`, which installed successfully. This exception is local to
the trainer environment. Axolotl is Apache-2.0 and also installs the explicitly
named `axolotl-contribs-lgpl==0.0.7` training dependency; retain that dependency
in the license inventory rather than describing the entire stack as permissive.

## Measured data and token parity

The reproduction uses the original 273 train records, including their original
multiplicity. Validation contains 45 distinct tasks after excluding training
overlap and repeated validation inputs. No benchmark or test data is read by
`training/axolotl/prepare.py` or the token comparison.

Initial chat-template preprocessing yielded identical input tokens but different
loss masks: 22,982 Unsloth supervised tokens versus 22,163 Axolotl tokens, a
difference of three tokens per training row. The initial failure report is
`training/axolotl/out/token-parity-before-alignment.json`.

The configuration now uses Axolotl's documented
[pre-tokenized dataset format](https://github.com/axolotl-ai-cloud/axolotl/blob/cc83a1c12e2d2010345806702d8ad3f794fad7f6/docs/dataset-formats/tokenized.qmd).
Tokenization and masks are exported with the original Unsloth preprocessing
function, while training remains Axolotl's independent trainer and starts from
the original base weights. Actual Axolotl preprocessing then produced:

| Observation | Measured result |
| --- | --- |
| Training records | 273 |
| Input sequences identical, including multiplicity | true |
| Supervised labels identical, including multiplicity | true |
| Supervised tokens in each trainer | 22,982 |

Evidence: `training/axolotl/out/token-parity-aligned.json`. The comparison loads
the actual prepared Arrow dataset. A matching YAML declaration alone is not
accepted as token-parity evidence.

## Reproduction and continuation

`sft-4b.yaml` and `sft-edge.yaml` define reproduction mode: the original pinned
base, no adapter parent, no checkpoint resume, and automatic resume disabled.
The prepared training directory can be mounted alone; the benchmark directory
does not need to be exposed to the training container.

`training/axolotl/run.py` validates the sealed tokenized files and records the
command, frozen configuration, source/data hashes, package versions, physical
GPU, elapsed time, and checkpoint hashes in a run manifest. A reproduction has
a null parent. Existing output directories are refused, including the directory
previously created by preprocessing; the first actual run uses a separate
`reproduction-4b-r1` directory.

Inside the separate Axolotl image, with this training directory and model cache
mounted:

```sh
python training/axolotl/run.py --mode reproduction \
  --output-dir training/axolotl/out/reproduction-4b-r1
```

The guards pass five CPU tests using explicitly synthetic token/checkpoint
markers. These tests do not establish actual GPU training or checkpoint resume.

Continuation must explicitly name a validated parent checkpoint and use a new
output directory. Match its model revision, tokenizer, target-module names,
LoRA rank/alpha, and dataset hashes; retain hashes of its adapter, optimizer,
scheduler, RNG state, and trainer state. The launcher checks saved hashes,
model/data/optimizer compatibility and package versions before setting
Axolotl's explicit `resume_from_checkpoint`. Resuming checkpoint-50 to step 100 is a
concrete restart test; merely loading a finished checkpoint-100 with a
100-step budget does not demonstrate further training.

Loading an Unsloth adapter into Axolotl remains unverified. Do not silently
substitute adapter-only loading for a full optimizer/RNG resume or describe a
continuation as an independent reproduction.

## Verified GB10 execution — 2026-10-20

This section supersedes the earlier statement that GPU training and resume are
unverified. Both containers exited 0, their manifests report `state=complete`,
and both final trainer states report global step 100.

| Measurement | Independent reproduction | Resume checkpoint-50 to 100 |
| --- | --- | --- |
| Container | `powercoder-m07-reproduction-4b-r1` | `powercoder-m07-resume50-100-r1` |
| Directory under `training/axolotl/out/` | `reproduction-4b-r1` | `resumed-4b-r1` |
| Parent | none; pinned original base | reproduction checkpoint-50, full optimizer/RNG resume |
| Actual updates in this launch | 100 | 50 |
| Trainer runtime, seconds | 218.7 | 113.6 |
| Initial validation loss | 1.3415668011 | 0.1845375896 at resumed step 50 |
| Final validation loss | 0.1763202846 | 0.1758020669 |
| Final adapter SHA-256 | `1574dd45d80b451c95500be4010a5863c39b676ac4253fa33862ffe014ed1df2` | `5b1c386b4f53b24dac22631fabe9c138564179e2f9790eea1703667a83c6efde` |

Evidence: each directory's `run_manifest.json` and
`checkpoint-100/trainer_state.json`. The manifests retain frozen configurations,
source/data hashes, checkpoint hashes, software versions and explicit ancestry.
Both final adapters contain 694 finite F32 tensors; all 248 language LoRA B
matrices are nonzero. Reports: `out/checkpoint100-audit.json` and
`out/resumed100-audit.json`.

Continuation changes all 496 language adapter tensors relative to checkpoint-50,
with maximum absolute change 0.00495513; no vision tensor changes. Compared with
uninterrupted step 100, it differs in those same 496 tensors: maximum absolute
difference 0.00252026 and RMS difference 0.00000721419. Reports:
`out/resume-update-tensor-comparison.json` and
`out/reproduction-resume-tensor-comparison.json`. Resume is not bitwise identical;
no numerical repeatability tolerance was preregistered.

All 688 original Unsloth tensor names and shapes match Axolotl. Axolotl adds six
tensors in three vision modules (merger linear_fc1/linear_fc2 and patch_embed.proj);
their B matrices remain zero in this text-only run. Active language targets match,
but the complete target sets differ. Report: `out/module-parity-checkpoint50.json`.

The original Unsloth log reports 390.2732 seconds for 100 updates. This is not a
controlled speed comparison: package versions differ, initial Unsloth adapter
weights were not retained, and resource/warmup conditions were not matched.
Validation loss does not establish domain pass@1 or the required 10 pp improvement.

Native `axolotl merge-lora` exited 0 in `powercoder-m07-merge-4b-r1`, writing
`out/export-4b-r1/merged/`. The native Transformers probe
`training/axolotl/smoke.py` then exited 0 in `powercoder-m07-merged-smoke-r1`:
all parameters on cuda:0, GB10 generation, peak allocated 9,229,814,272 bytes.
It reads only a training prompt without the answer. Its 64-token response
restated the task without solving it. Report: `out/merged-reload-smoke-r1.json`.
This proves usability, not accuracy. M07 remains incomplete pending controlled
trainer/task comparisons and environment qualification; edge and rank-64 trials
also remain required by the broader training scope.
