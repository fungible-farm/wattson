"""Train the pinned Granite student with real CUDA Transformers/PEFT LoRA.

This uses the same rows and hyperparameters as the Qwen experiment. Granite
uses its own native chat template; assistant masks are derived from the actual
rendered answer boundary, because its generation prefix includes thinking
markers absent from ordinary supervised assistant turns. No benchmark data is
read. Dependencies are existing pinned training-image packages: Transformers,
PEFT, Datasets and Accelerate (Apache-2.0), PyTorch (BSD-3-Clause),
bitsandbytes and PyYAML (MIT). No simulated training or solver is substituted.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import time
from pathlib import Path
from typing import Any, TypedDict

import yaml

# Stream checkpoints in 1 MiB blocks to avoid allocating a second model in RAM.
HASH_BLOCK_BYTES: int = 1 << 20
# The original Qwen trainer retained its final two checkpoints.
SAVED_CHECKPOINT_LIMIT: int = 2
# Transformers excludes these labels from the language-model loss.
IGNORE_LABEL: int = -100


class Features(TypedDict):
    """A single native-template assistant-only training sequence."""

    input_ids: list[int]
    attention_mask: list[int]
    labels: list[int]


def digest(path: Path) -> str:
    """Hash an artifact without loading its complete contents into memory."""
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(HASH_BLOCK_BYTES), b""):
            value.update(block)
    return value.hexdigest()


def encode_example(row: dict[str, Any], tokenizer: Any, system: str, seq_len: int) -> Features:
    """Mask the exact rendered assistant prefix and reject target truncation.

    Args:
        row: A verified training row with its original prompt and answer.
        tokenizer: The pinned Granite tokenizer and native template.
        system: The original Qwen training system prompt.
        seq_len: The configured sequence-length limit.
    Returns:
        Tokens with labels only for the assistant target and turn terminator.
    Raises:
        ValueError: For an incompatible template or any truncated answer.
    """
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": row["task"]["prompt"]}]
    before = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    answer = row["expected"]["answer"]
    full = tokenizer.apply_chat_template(messages + [{"role": "assistant", "content": answer}],
                                         tokenize=False, add_generation_prompt=False)
    if not answer or not full.startswith(before):
        raise ValueError("Template did not preserve the supervised conversation prefix")
    boundary = full.index(answer, len(before))
    prefix = tokenizer(full[:boundary], add_special_tokens=False)["input_ids"]
    encoded = tokenizer(full, add_special_tokens=False)
    ids = encoded["input_ids"]
    if len(ids) > seq_len or ids[:len(prefix)] != prefix or len(prefix) >= len(ids):
        raise ValueError("Truncated target or incompatible token boundary")
    return {"input_ids": ids, "attention_mask": encoded["attention_mask"],
            "labels": [IGNORE_LABEL] * len(prefix) + ids[len(prefix):]}


def verify_base(config: dict[str, Any]) -> dict[str, str]:
    """Verify each downloaded weight shard against its pinned Hub metadata.

    Hugging Face local-directory metadata records commit and content etag.
    Safetensors LFS etags are SHA-256 digests. Missing/incomplete shards or a
    different commit fail before GPU memory is allocated.
    """
    base = Path(config["name"])
    index = json.loads((base / "model.safetensors.index.json").read_text())
    shards = sorted(set(index["weight_map"].values()))
    hashes = {}
    for name in shards:
        metadata = (base / ".cache" / "huggingface" / "download" / f"{name}.metadata").read_text().splitlines()
        actual = digest(base / name)
        if metadata[0] != config["source_revision"] or metadata[1] != actual:
            raise ValueError(f"Pinned checkpoint integrity mismatch: {name}")
        hashes[name] = actual
    return hashes


def train(config_path: Path) -> None:
    """Execute real assistant-only LoRA training, saved-adapter reload and merge."""
    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import (AutoModelForCausalLM, AutoTokenizer, DataCollatorForSeq2Seq,
                              Trainer, TrainingArguments, set_seed)

    config = yaml.safe_load(config_path.read_text())
    model_cfg, lora, data, settings, output = (config[k] for k in ("model", "lora", "dataset", "training", "output"))
    out = Path(output["dir"])
    out.mkdir(parents=True, exist_ok=False)
    if not torch.cuda.is_available():
        raise RuntimeError("Physical CUDA required for Granite training")
    base_hashes = verify_base(model_cfg)
    set_seed(settings["seed"])
    tokenizer = AutoTokenizer.from_pretrained(model_cfg["name"], local_files_only=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    tokenizer.padding_side = "right"
    source = Path(data["split"])
    rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    if not rows or any(r["lifecycle"]["split"] != "train" or r["lifecycle"]["status"] != "verified" for r in rows):
        raise ValueError("Only verified rows in the original training split are permitted")
    features = [encode_example(r, tokenizer, data["system_prompt"], settings["seq_len"]) for r in rows]
    provenance = {
        "source_hf_id": model_cfg["source_hf_id"], "source_revision": model_cfg["source_revision"],
        "base_sha256": base_hashes, "config_sha256": digest(config_path),
        "training_sha256": digest(source), "train_examples": len(rows),
        "supervised_tokens": sum(sum(t != IGNORE_LABEL for t in f["labels"]) for f in features),
        "max_sequence_length": max(len(f["input_ids"]) for f in features),
        "gpu": torch.cuda.get_device_name(0), "cuda": torch.version.cuda,
        "versions": {n: importlib.metadata.version(n) for n in ("torch", "transformers", "peft", "datasets", "accelerate", "bitsandbytes")},
        "benchmark_access": False,
    }
    (out / "preflight.json").write_text(json.dumps(provenance, indent=2) + "\n")
    model = AutoModelForCausalLM.from_pretrained(model_cfg["name"], local_files_only=True,
        dtype=torch.bfloat16, device_map={"": "cuda:0"}, attn_implementation="sdpa")
    model = get_peft_model(model, LoraConfig(r=lora["r"], lora_alpha=lora["alpha"],
        lora_dropout=lora["dropout"], target_modules=lora["target_modules"], bias=lora["bias"], task_type="CAUSAL_LM"))
    model.config.use_cache = False
    model.enable_input_require_grads()
    trainer = Trainer(model=model, processing_class=tokenizer,
        train_dataset=Dataset.from_list(features),
        data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True, label_pad_token_id=IGNORE_LABEL),
        args=TrainingArguments(output_dir=str(out / "runs"),
            **{k: settings[k] for k in ("max_steps", "per_device_train_batch_size", "gradient_accumulation_steps",
                "warmup_steps", "learning_rate", "logging_steps", "save_steps", "optim", "weight_decay",
                "lr_scheduler_type", "seed", "gradient_checkpointing")},
            bf16=True, report_to="none", remove_unused_columns=False, save_total_limit=SAVED_CHECKPOINT_LIMIT))
    start = time.monotonic()
    result = trainer.train()
    adapter = out / output["adapter_name"]
    model.save_pretrained(adapter)
    tokenizer.save_pretrained(adapter)
    report = {**provenance, "config": config, "wall_s": time.monotonic() - start,
              "completed_steps": trainer.state.global_step, "metrics": result.metrics,
              "log_history": trainer.state.log_history, "adapter_sha256": digest(adapter / "adapter_model.safetensors")}
    (out / "train_log.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    if trainer.state.global_step != settings["max_steps"]:
        raise RuntimeError("Training ended before the configured step count")
    # Merge the saved adapter after an independent reload, never a fresh LoRA.
    del trainer, model
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    reloaded = AutoModelForCausalLM.from_pretrained(model_cfg["name"], local_files_only=True,
        dtype=torch.bfloat16, device_map={"": "cuda:0"}, attn_implementation="sdpa")
    saved = PeftModel.from_pretrained(reloaded, adapter, local_files_only=True)
    merged = saved.merge_and_unload(safe_merge=True)
    merged.config.use_cache = True
    destination = out / "merged-bf16"
    merged.save_pretrained(destination, safe_serialization=True)
    tokenizer.save_pretrained(destination)
    manifest = {str(p.relative_to(out)): digest(p) for directory in (adapter, destination)
                for p in sorted(directory.rglob("*")) if p.is_file()}
    (out / "hash_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("TRAIN AND SAVED-ADAPTER MERGE COMPLETE", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("training/unsloth/config.granite42.yaml"))
    train(parser.parse_args().config)
