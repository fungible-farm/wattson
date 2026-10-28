"""Shared checkpoint loading for evaluation and merged export.

Load a saved adapter directly using Unsloth's documented inference path:
https://unsloth.ai/docs/basics/inference-and-deployment/unsloth-inference
This avoids constructing a second, randomly initialized adapter around PEFT.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_checkpoint(
    model_name: str, adapter_path: str | None = None, dtype: str = "bfloat16"
) -> tuple[Any, Any]:
    """Load the unchanged base or exactly one saved adapter; require CUDA.

    Check the adapter's declared base before allocating GPU memory. The
    tokenizer/processor is loaded from the same checkpoint as the weights.
    """
    source = model_name
    if adapter_path is not None:
        adapter = Path(adapter_path)
        config = json.loads((adapter / "adapter_config.json").read_text())
        if config["base_model_name_or_path"] != model_name:
            raise ValueError("Adapter base model does not match requested base")
        if not (adapter / "adapter_model.safetensors").is_file():
            raise ValueError("Missing saved adapter weights")
        source = str(adapter)

    # Unsloth must patch transformers before importing the rest of the stack.
    import torch
    from unsloth import FastLanguageModel

    if not torch.cuda.is_available():
        raise RuntimeError("Physical CUDA GPU required; CPU fallback is forbidden")
    model, processor = FastLanguageModel.from_pretrained(
        model_name=source, dtype=getattr(torch, dtype), load_in_4bit=False,
        # GB10's free-memory heuristic may offload MTP tensors that Unsloth
        # removes, breaking PEFT's offload index. Require explicit CUDA loading.
        device_map={"": "cuda:0"},
    )
    return model, processor
