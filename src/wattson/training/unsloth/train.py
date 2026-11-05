#!/usr/bin/env python
"""M06 Unsloth SFT: BF16 LoRA fine-tune of Qwen3.5-4B on the 500-example
solver-verified dataset (train split only).

Every training target is objective: `expected.answer` was produced by a real
pandapower / PowerMCP execution (M04/M05), so the SFT loss is grounded in
solver truth, not a teacher's guess.

The dataset is pre-tokenized with a loss mask so the gradient only flows on the
assistant turn (the solver-verified answer), never on the system/user prompt.

GPU idiom (verified on this node):  podman run --rm --device nvidia.com/gpu=all
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Unsloth must be imported before transformers for its optimized ops.
from unsloth import FastLanguageModel, is_bfloat16_supported

# isort: split

import torch
import yaml
from datasets import Dataset
from transformers import Trainer, TrainingArguments


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_train_examples(split_path: str, system_prompt: str) -> list[dict]:
    """Load the train split and shape each row into {user, assistant}.

    `task.prompt` is self-contained (case/element/target/tolerance inline) and
    `expected.answer` is the solver-verified natural-language answer.
    """
    rows: list[dict] = []
    with open(split_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ex = json.loads(line)
            if ex["lifecycle"].get("split") != "train":
                raise ValueError(f"Refusing non-training row: {ex['example_id']}")
            if ex["lifecycle"].get("status") != "verified":
                raise ValueError(f"Refusing unverified row: {ex['example_id']}")
            rows.append(
                {
                    "user": ex["task"]["prompt"],
                    "assistant": ex["expected"]["answer"],
                }
            )
    return rows


def build_dataset(rows: list[dict], processor, text_tokenizer, system_prompt: str, seq_len: int) -> Dataset:
    """Pre-tokenize with a loss mask on the assistant turn only.

    `processor` supplies `apply_chat_template` (works for both plain tokenizers
    and VL processors); `text_tokenizer` does the actual tokenization (for a VL
    processor this is the underlying text tokenizer, avoiding the image-decode
    path that a raw `processor(text)` call would hit).
    """

    def tokenize(row: dict) -> dict:
        prompt_msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": row["user"]},
        ]
        full_msgs = prompt_msgs + [{"role": "assistant", "content": row["assistant"]}]
        prompt_text = processor.apply_chat_template(
            prompt_msgs, tokenize=False, add_generation_prompt=True
        )
        full_text = processor.apply_chat_template(
            full_msgs, tokenize=False, add_generation_prompt=False
        )
        full_enc = text_tokenizer(full_text, truncation=True, max_length=seq_len, padding=False)
        prompt_len = len(text_tokenizer(prompt_text, add_special_tokens=False)["input_ids"])
        labels = full_enc["input_ids"].copy()
        for i in range(min(prompt_len, len(labels))):
            labels[i] = -100
        # A truncated assistant must fail rather than train on an unrelated
        # final prompt token and report a misleading successful SFT run.
        if all(l == -100 for l in labels):
            raise ValueError("Assistant target fully truncated or masked; increase seq_len")
        return {
            "input_ids": full_enc["input_ids"],
            "attention_mask": full_enc["attention_mask"],
            "labels": labels,
        }

    return Dataset.from_list([tokenize(r) for r in rows])


def make_data_collator(pad_token_id: int):
    """Data collator for pre-tokenized sequences.

    The dataset is pre-tokenized (input_ids / attention_mask / labels are lists
    of ints of varying length). The default Trainer collator tries to re-pad and
    fails on the nested labels. This collator right-pads to the batch max length:
    input_ids with pad_token_id, attention_mask with 0, labels with -100 (so the
    pad tokens never contribute to the loss).
    """

    def collate(features: list[dict]) -> dict:
        max_len = max(len(f["input_ids"]) for f in features)
        input_ids, attention_mask, labels = [], [], []
        for f in features:
            pad_len = max_len - len(f["input_ids"])
            input_ids.append(f["input_ids"] + [pad_token_id] * pad_len)
            attention_mask.append(f["attention_mask"] + [0] * pad_len)
            labels.append(f["labels"] + [-100] * pad_len)
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "attention_mask": torch.tensor(attention_mask, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
        }

    return collate


def main() -> None:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = load_config(cfg_path)

    model_cfg = cfg["model"]
    lora_cfg = cfg["lora"]
    ds_cfg = cfg["dataset"]
    tr_cfg = cfg["training"]
    out_cfg = cfg["output"]

    if not torch.cuda.is_available():
        print("FATAL: CUDA not available. Run with: podman run --device nvidia.com/gpu=all", file=sys.stderr)
        sys.exit(1)
    print(f"GPU: {torch.cuda.get_device_name(0)} "
          f"({torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GiB)")

    # --- Load model + tokenizer (Unsloth) ---
    model, processor = FastLanguageModel.from_pretrained(
        model_name=model_cfg["name"],
        max_seq_length=tr_cfg["seq_len"],
        dtype=model_cfg["dtype"],
        load_in_4bit=model_cfg.get("use_4bit", False),
        revision=model_cfg.get("revision"),
    )
    # Qwen3.5-4B is a VL model: the returned object is a Qwen3VLProcessor. Use the
    # underlying text tokenizer for tokenization (a raw processor(text) call hits
    # the image-decode path); the processor still supplies apply_chat_template.
    text_tokenizer = processor.tokenizer if hasattr(processor, "tokenizer") else processor

    # --- LoRA ---
    model = FastLanguageModel.get_peft_model(
        model,
        r=lora_cfg["r"],
        target_modules=lora_cfg["target_modules"],
        lora_alpha=lora_cfg["alpha"],
        lora_dropout=lora_cfg["dropout"],
        bias=lora_cfg["bias"],
        use_gradient_checkpointing="unsloth",
    )

    # --- Dataset ---
    rows = load_train_examples(ds_cfg["split"], ds_cfg["system_prompt"])
    print(f"Loaded {len(rows)} train examples from {ds_cfg['split']}")
    dataset = build_dataset(rows, processor, text_tokenizer, ds_cfg["system_prompt"], tr_cfg["seq_len"])

    # --- Training args ---
    out_dir = Path(out_cfg["dir"]) / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    training_args = TrainingArguments(
        output_dir=str(out_dir),
        max_steps=tr_cfg["max_steps"],
        per_device_train_batch_size=tr_cfg["per_device_train_batch_size"],
        gradient_accumulation_steps=tr_cfg["gradient_accumulation_steps"],
        warmup_steps=tr_cfg["warmup_steps"],
        learning_rate=tr_cfg["learning_rate"],
        logging_steps=tr_cfg["logging_steps"],
        save_steps=tr_cfg["save_steps"],
        save_total_limit=2,
        bf16=is_bfloat16_supported(),
        optim=tr_cfg["optim"],
        weight_decay=tr_cfg["weight_decay"],
        lr_scheduler_type=tr_cfg["lr_scheduler_type"],
        seed=tr_cfg["seed"],
        report_to="none",
        remove_unused_columns=False,
    )

    pad_token_id = text_tokenizer.pad_token_id
    if pad_token_id is None:
        pad_token_id = text_tokenizer.eos_token_id
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        processing_class=text_tokenizer,
        data_collator=make_data_collator(pad_token_id),
    )

    # --- Train ---
    trainer.train()

    # --- Save adapter + log ---
    adapter_dir = Path(out_cfg["dir"]) / out_cfg["adapter_name"]
    model.save_pretrained(str(adapter_dir))
    text_tokenizer.save_pretrained(str(adapter_dir))

    # Training log: final loss + config + provenance.
    # The last log_history entry is the train_runtime summary (no "loss" key);
    # find the most recent entry that actually carries a loss.
    final_loss = None
    for entry in reversed(trainer.state.log_history):
        if "loss" in entry:
            final_loss = entry["loss"]
            break
    log = {
        "model": model_cfg["name"],
        "lora": lora_cfg,
        "dtype": model_cfg["dtype"],
        "train_examples": len(rows),
        "max_steps": tr_cfg["max_steps"],
        "final_loss": final_loss,
        "log_history": trainer.state.log_history,
        "gpu": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
    }
    with open(Path(out_cfg["dir"]) / "train_log.json", "w") as f:
        json.dump(log, f, indent=2)
    print(f"Saved adapter to {adapter_dir}")
    print(f"Final loss: {log['final_loss']}")
    print("TRAIN PASS")


if __name__ == "__main__":
    main()
