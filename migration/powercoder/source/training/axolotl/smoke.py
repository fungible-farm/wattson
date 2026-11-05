"""Reload a merged Axolotl model using native Transformers on physical CUDA.

The probe reads only the first training prompt. It checks checkpoint usability;
it does not supply tools or establish task accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

# Match the existing Unsloth reload probe's generation budget.
SMOKE_TOKENS: int = 64


def main() -> int:
    """Require full CUDA placement and retain the actual generated response."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("Refusing to overwrite inference evidence")
    training = Path("training/axolotl/out/data/train.jsonl")
    with training.open() as stream:
        example = json.loads(next(stream))
    messages = example["messages"][:-1]
    if messages[-1]["role"] != "user" or any(m["role"] == "assistant" for m in messages):
        raise ValueError("Probe must contain only the training prompt, without the answer")

    import torch
    from transformers import AutoModelForImageTextToText, AutoTokenizer

    if not torch.cuda.is_available():
        raise RuntimeError("Physical CUDA required; no CPU fallback")
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForImageTextToText.from_pretrained(
        args.model, dtype=torch.bfloat16, device_map={"": "cuda:0"},
        attn_implementation="sdpa",
    ).eval()
    devices = sorted({str(p.device) for p in model.parameters()})
    if devices != ["cuda:0"]:
        raise ValueError(f"Incomplete CUDA placement: {devices}")
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt").to("cuda:0")
    with torch.inference_mode():
        output = model.generate(**inputs, max_new_tokens=SMOKE_TOKENS, do_sample=False)
    response = tokenizer.decode(output[0, inputs.input_ids.shape[1]:], skip_special_tokens=True)
    if not response.strip():
        raise ValueError("Empty generated response")
    report = {
        "reload_inference_pass": True, "accuracy_claim": False,
        "model": str(args.model), "devices": devices,
        "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__,
        "cuda": torch.version.cuda, "example_id": example["example_id"],
        "prompt_source": str(training),
        "training_file_sha256": hashlib.sha256(training.read_bytes()).hexdigest(),
        "max_new_tokens": SMOKE_TOKENS, "do_sample": False, "response": response,
        "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
