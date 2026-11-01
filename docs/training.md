# Training

## What is reproducible from this repository

| Piece | Where | State |
|---|---|---|
| training data | `data/trajectories/*` | shipped, hash-verified |
| training recipe | `configs/agent/config.*.yaml` | shipped: the Granite student and the v9 to v11 Qwen students |
| trainer | `src/wattson/training/agent_sft.py` | shipped; reads the gzipped data directly |
| training image | `containers/train/Containerfile` | shipped, every package pinned |
| parity check | `src/wattson/training/parity_check.py` | shipped |
| base models | Hugging Face: `ibm-granite/granite-4.2-8b`, `Qwen/Qwen3.5-4B` | pinned by revision in each config |

**Verified:** the training image builds on aarch64 and the 18 torch-dependent tests pass inside it (including the supervised-tail loss
tests, which are skipped on any machine without torch). The trainer's `--preflight` mode, which renders and tokenizes the whole training set
without a GPU, passes on the Qwen thinking config: 826 trajectories from the shipped `.jsonl.gz`, 438,362 supervised tokens, longest
sequence 5,588 tokens, the data hash equal to its manifest, and the pinned library versions reported.

**Not yet verified:** that a full training run from this repository reproduces the released weights. We have not run one. The released
weights come from the same code and data in the lab, with the manifest hashes shown in each model repository. Expect a reproduction to
match in behaviour and not in bytes (GPU kernels are not bit-deterministic).

## Steps

```bash
# 1. the training image (pip installs torch for CUDA 13; several GB)
podman build -f containers/train/Containerfile -t localhost/wattson-train:local .

# 2. the base model, at the pinned revision (the revision is in the config)
hf download ibm-granite/granite-4.2-8b --revision b3791992239dce12d0038f1f54a8adb7b02d59ab --local-dir models/granite-4.2-8b-base

# 3. train (the Granite student; one GPU)
podman run --rm --device nvidia.com/gpu=all --shm-size 1g -v "$PWD":/workspace \
  localhost/wattson-train:local python -m wattson.training.agent_sft --config configs/agent/config.granite42-8b.g11py.yaml
```

To check a config and the data without a GPU, add `--preflight` to the last command.

The trainer refuses to start if the trajectories do not match their manifest, if the run directory already exists, or if no CUDA device
is visible. After training it generates the first turn of two training trajectories and refuses to save a model that does not reproduce
the tool call, which has caught four data bugs that each would have cost a multi-hour run.

Check the trainer's own `--help` for the remaining options. The Qwen configs (`config.qwen35-4b.v11*.yaml`) name the model as a path inside
the container (`/hub/models--Qwen--...`): mount your Hugging Face cache there.

## Hardware seen

Qwen3.5-4B trained on a 24 GB RTX 3090 with the supervised-tail loss (`tail_loss: true`) and on an NVIDIA GB10. Plain-tools trajectories
reach 7.7k tokens and did not fit the 24 GB card. Granite 4.2 8B was trained on the GB10 (about 17 GB of weights plus training state).

## Variants

| Recipe | Trajectories | Config |
|---|---|---|
| Python shell, non-thinking | `20261029-v11-python` | `config.qwen35-4b.v11py*.yaml`, `config.granite42-8b.g11py.yaml` |
| Python shell, scripted thinking | `20261029-v11-python-thinking` | `config.qwen35-4b.v11pyt*.yaml` |
| plain tools (control) | `20261029-v11-plain` | `config.qwen35-4b.v11plain.yaml` |

`-s43` and `-s44` are the same recipe with other seeds.
