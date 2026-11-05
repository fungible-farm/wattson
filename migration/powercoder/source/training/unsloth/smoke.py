"""Verify saved-checkpoint reload and actual CUDA inference on a training prompt.

This proves checkpoint usability, not answer correctness or benchmark quality.
No held-out prompt or answer is read by this smoke check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from training.unsloth.audit_checkpoint import digest_file
from training.unsloth.evaluate import generate_answer
from training.unsloth.runtime import load_checkpoint

# Short inference probe; full domain answers use the separate 512-token protocol.
SMOKE_TOKENS: int = 64


def main() -> int:
    """Require CUDA placement, decode a real response, and write runtime evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="Qwen/Qwen3.5-4B")
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--config", type=Path, default=Path("training/unsloth/config.yaml"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    with Path(config["dataset"]["split"]).open() as stream:
        example = json.loads(next(stream))
    if example["lifecycle"]["split"] != "train":
        raise ValueError("Smoke prompt must come from the training split")
    model, processor = load_checkpoint(
        args.model, str(args.adapter) if args.adapter is not None else None
    )
    model.eval()
    tokenizer = processor.tokenizer if hasattr(processor, "tokenizer") else processor
    devices = sorted({str(parameter.device) for parameter in model.parameters()})
    if devices != ["cuda:0"]:
        raise ValueError(f"Checkpoint did not load entirely on CUDA: {devices}")
    response = generate_answer(model, tokenizer, example["task"]["prompt"], SMOKE_TOKENS)
    if not response.strip():
        raise ValueError("Reloaded checkpoint produced an empty response")
    import torch

    report = {
        "reload_inference_pass": True,
        "model": args.model,
        "adapter": str(args.adapter) if args.adapter is not None else None,
        "devices": devices,
        "gpu": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(),
        "prompt_source": config["dataset"]["split"],
        "example_id": example["example_id"],
        "max_new_tokens": SMOKE_TOKENS,
        "response": response,
        "accuracy_claim": False,
    }
    if args.adapter is not None:
        report["adapter_file"] = digest_file(args.adapter / "adapter_model.safetensors")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
