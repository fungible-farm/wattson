"""Train a student on tool-call trajectories rendered exactly as it is evaluated.

The r4 students learned ``{prompt -> answer}`` text with no tools, another
system prompt and 1024-token sequences, then were evaluated as multi-turn tool
agents (AGENTS.md FM-19). This trains both Qwen3.5-4B and Granite 4.2 8B with
one recipe on ``wattson/datasets/trajectories.py`` output: the evaluation's
system prompt, the evaluation's tool list (rendered by each model's own chat
template with ``tools=``), and real MCP tool responses.

Each assistant turn is supervised exactly once per epoch, on the text the
model must produce after the prompt vLLM sends it:

- the prompt for turn k is ``apply_chat_template(messages[:k], tools,
  add_generation_prompt=True)``, which is what the evaluation serves;
- the target is that turn as the template renders it after ``messages[:k]``,
  minus the part the generation prompt already supplied. Granite's generation
  prompt ends ``<think>\\n`` while its rendered turn starts ``<think></think>``;
  the only permitted difference is whitespace, so the target starts ``</think>``.

Every turn is its own sequence: the generation prompt tokenised on its own,
then the target tokenised on its own, with labels on the target tokens only.
Tokenising prompt and target as one string is wrong: Qwen's prompt ends
``<think>\\n`` and its target starts ``\\n</think>``, and the two newlines merge
into one ``\\n\\n`` token, so training never shows the model the prompt's own
final token, the state vLLM actually serves. The first r6 Qwen student learned
exactly that: teacher-forced loss ~1e-5, yet from the served prompt it answered
without a tool call (docs/POWERCODER_HANDOFF_2026-10-21.md, r6). The prompt
tokens are verified against vLLM's ``/tokenize`` in that record. Batches are
sized in conversations (``conversations_per_step``), so both students take the
same optimizer steps over the same supervised tokens.

Teacher-forced loss alone cannot catch that class of defect, so after merging
the trainer greedily generates the first turn of 2 training trajectories per
subfamily from their served prompts and refuses to finish unless each
reproduces its target's first ``GENERATION_CHECK_TOKENS`` tokens.

LoRA targets every linear layer inside the decoder layers (regex in the
config), never Qwen's vision tower or either ``lm_head``, so the merged
checkpoint keeps the base architecture and vLLM serves it like the base.
Dependencies are the pinned training image's Transformers, PEFT, Datasets and
PyTorch (Apache-2.0 / BSD-3-Clause) and bitsandbytes (MIT).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from wattson.training.granite.train import IGNORE_LABEL, SAVED_CHECKPOINT_LIMIT, Features, digest, verify_base

Render = Callable[[list[dict[str, Any]], bool], str]
# Model weights and their index are written by save_pretrained; every other base
# file (processor, chat template, generation config) is copied beside them.
WEIGHT_SUFFIXES: tuple[str, ...] = (".safetensors", ".safetensors.index.json", ".bin")
GENERATION_CONFIG: str = "generation_config.json"
# Post-merge greedy check: 2 first turns per benchmark subfamily (8), each
# compared on its target's first 32 tokens (the tool call's opening, with its
# function name and first arguments). The trajectories are deterministic, so a
# trained student must reproduce them exactly.
GENERATION_CHECKS_PER_SUBFAMILY: int = 2
GENERATION_CHECK_TOKENS: int = 32



def read_trajectory_bytes(path: Path) -> bytes:
    """The trajectory file's content, decompressed if it is ``.gz``.

    The manifest records the SHA-256 of the uncompressed ``trajectories.jsonl``; ``data/trajectories/*`` ships it gzipped (about a tenth of the
    size), so the hash is taken over the decompressed bytes and both forms verify against the same manifest.
    """
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as stream:
            return stream.read()
    return path.read_bytes()

def normalise(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decode tool-call arguments: chat templates iterate them as a mapping."""
    out = []
    for message in messages:
        message = dict(message)
        if message.get("tool_calls"):
            message["tool_calls"] = [{**call, "function": {**call["function"],
                                     "arguments": json.loads(call["function"]["arguments"])}}
                                     for call in message["tool_calls"]]
        out.append(message)
    return out


def turn_segments(messages: list[dict[str, Any]], render: Render) -> list[tuple[str, str]]:
    """Return (generation prompt, target text) for every assistant turn.

    Raises:
        ValueError: if the template rewrites history or the turn differs from
            the generation prompt by more than whitespace.
    """
    segments = []
    for k, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        # The harness re-sends only role, content and tool_calls (tool_probe), so the served history carries no
        # reasoning from earlier assistant turns. Train on that prompt; the target turn keeps its own reasoning.
        # The first scripted-thinking student trained on histories WITH reasoning and, served without it, wrote its
        # reasoning into the answer on the second turn (backlog 0041, 21 of 90 held-out fits).
        served = [{key: value for key, value in earlier.items() if key != "reasoning_content"}
                  for earlier in messages[:k]]
        history = render(served, False)
        prompt = render(served, True)
        rendered = render([*served, message], False)
        if not (prompt.startswith(history) and rendered.startswith(history)):
            raise ValueError(f"Template rewrote the history before assistant turn {k}")
        opening, turn = prompt[len(history):], rendered[len(history):]
        shared = 0
        while shared < min(len(opening), len(turn)) and opening[shared] == turn[shared]:
            shared += 1
        if opening[shared:].strip():
            raise ValueError(f"Generation prompt and rendered turn {k} differ beyond whitespace")
        target = turn[shared:]
        if not target.strip():
            raise ValueError(f"Assistant turn {k} renders empty")
        segments.append((prompt, target))
    return segments


def schedule(conversations: int, sequences: int, per_step: int, epochs: int) -> tuple[int, int]:
    """Return (gradient accumulation, optimizer steps) for ``per_step`` conversations a step.

    One sequence per device step; accumulation covers the average number of
    turn sequences in ``per_step`` conversations.
    """
    accumulation = max(round(per_step * sequences / conversations), 1)
    return accumulation, math.ceil(sequences * epochs / accumulation)


def encode(prompt: str, target: str, tokenizer: Any, seq_len: int) -> Features:
    """Concatenate the separately tokenised prompt and target; label the target only.

    Raises:
        ValueError: if the sequence exceeds ``seq_len`` or the target has no tokens.
    """
    prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    target_ids = tokenizer(target, add_special_tokens=False)["input_ids"]
    ids = prompt_ids + target_ids
    if not target_ids:
        raise ValueError("Target tokenises to nothing")
    if len(ids) > seq_len:
        raise ValueError(f"{len(ids)} tokens exceed seq_len {seq_len}; truncation would drop targets")
    return {"input_ids": ids, "attention_mask": [1] * len(ids),
            "labels": [IGNORE_LABEL] * len(prompt_ids) + target_ids}


def supervised_segments(row: dict[str, Any], render: Render) -> list[tuple[str, str]]:
    """The (prompt, target) turns to train on: every assistant turn except an injected mistake.

    A recovery variant (``row["fault"]`` set, wattson/datasets/trajectories.py)
    opens with a deliberately wrong call. Supervising it would train the student to
    make that mistake on 119 first-turn prompts; it stays in the history of every
    later turn, so the student still learns to read the real error and correct it.
    """
    segments = turn_segments(normalise(row["messages"]), render)
    return segments[1:] if row.get("fault") else segments


def generation_check(model: Any, tokenizer: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Greedily generate trained first turns from their served prompts (see module docstring)."""
    import torch

    chosen: list[dict[str, Any]] = []
    for subfamily in sorted({row["subfamily"] for row in rows}):
        # Plain trajectories only: a recovery variant's first turn is the mistake.
        chosen += [row for row in rows if row["subfamily"] == subfamily
                   and row.get("fault") is None][:GENERATION_CHECKS_PER_SUBFAMILY]
    results = []
    for row in chosen:
        def render(messages: list[dict[str, Any]], generation: bool, tools: list = row["tools"]) -> str:
            return tokenizer.apply_chat_template(messages, tools=tools, tokenize=False,
                                                 add_generation_prompt=generation)
        prompt, target = turn_segments(normalise(row["messages"]), render)[0]
        prompt_ids = tokenizer(prompt, add_special_tokens=False, return_tensors="pt")["input_ids"].to(model.device)
        expected = tokenizer(target, add_special_tokens=False)["input_ids"][:GENERATION_CHECK_TOKENS]
        with torch.no_grad():
            generated = model.generate(input_ids=prompt_ids, attention_mask=torch.ones_like(prompt_ids),
                                       max_new_tokens=len(expected), do_sample=False)[0, prompt_ids.shape[1]:].tolist()
        results.append({"example_id": row["example_id"], "subfamily": row["subfamily"],
                        "matches": generated == expected, "expected": tokenizer.decode(expected),
                        "generated": tokenizer.decode(generated)})
    return results


def verify_hub_snapshot(model: dict[str, Any]) -> dict[str, str]:
    """Verify a Hugging Face hub snapshot: its revision, and each shard's LFS sha256.

    Hub snapshots link each LFS file to a blob named by its sha256.

    Raises:
        ValueError: on another revision, a missing or unlinked shard, or a mismatch.
    """
    snapshot = Path(model["name"])
    if snapshot.name != model["source_revision"]:
        raise ValueError("Snapshot directory is not the pinned revision")
    hashes = {}
    for shard in sorted(snapshot.glob("*.safetensors")):
        actual = digest(shard)
        # The snapshot's own link names the blob by sha256; that blob may itself be
        # a link into deduplicated storage, so read one hop rather than resolve().
        if not shard.is_symlink() or Path(os.readlink(shard)).name != actual:
            raise ValueError(f"Pinned checkpoint integrity mismatch: {shard.name}")
        hashes[shard.name] = actual
    if not hashes:
        raise ValueError("No safetensors shards in the snapshot")
    return hashes


def tail_loss(model: Any, inputs: dict[str, Any], num_items_in_batch: int | None = None) -> Any:
    """Cross-entropy over the supervised tail only, computing logits for just those positions (backlog 0041).

    A turn is ignored prompt labels followed by supervised target labels, so the loss needs the logits of the target
    positions and the one before them, not of the whole sequence. The default path upcasts every position's
    full-vocabulary logits to float32, which for a 5,500-token sequence on Qwen3.5's 248k vocabulary is the allocation
    that ran the 24 GB card out of memory. This is the same loss: summed over supervised tokens, divided by
    ``num_items_in_batch`` when the trainer passes it (as the default path does) and by the supervised count otherwise.
    Batch size is 1 (no padding), which is what the trainer uses.
    """
    import torch.nn.functional as functional

    labels = inputs.pop("labels")
    supervised = (labels[0] != IGNORE_LABEL).nonzero()
    first = int(supervised[0])
    keep = labels.shape[1] - first + 1  # the position before the first target predicts it
    logits = model(**inputs, logits_to_keep=keep).logits[:, :-1, :]
    targets = labels[:, first:]
    total = functional.cross_entropy(logits.float().reshape(-1, logits.shape[-1]), targets.reshape(-1),
                                     ignore_index=IGNORE_LABEL, reduction="sum")
    count = num_items_in_batch if num_items_in_batch is not None else int((targets != IGNORE_LABEL).sum())
    return total / count


def train(config_path: Path, preflight: bool = False) -> None:
    """Verify inputs, train, reload the saved adapter, merge and hash everything.

    With ``preflight`` it stops after rendering, tokenising and writing
    ``preflight.json``, which needs no GPU, so template or length defects show
    before a GPU stage is admitted.
    """
    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import (AutoConfig, AutoModelForCausalLM, AutoModelForImageTextToText, AutoTokenizer,
                              DataCollatorForSeq2Seq, Trainer, TrainingArguments, set_seed)

    config = yaml.safe_load(config_path.read_text())
    model_cfg, lora, data, settings, output = (config[k] for k in ("model", "lora", "dataset", "training", "output"))
    out = Path(f"{output['dir']}-preflight") if preflight else Path(output["dir"])
    if out.exists():
        raise FileExistsError(f"Refusing to reuse {out}")
    if not preflight and not torch.cuda.is_available():
        raise RuntimeError("Physical CUDA required for training")
    base_hashes = verify_hub_snapshot(model_cfg) if model_cfg["verify"] == "hub_snapshot" else verify_base(model_cfg)
    trajectories = Path(data["trajectories"])
    manifest = json.loads((trajectories.parent / "manifest.json").read_text())
    raw = read_trajectory_bytes(trajectories)
    if manifest["trajectories_sha256"] != hashlib.sha256(raw).hexdigest() or manifest["benchmark_access"]:
        raise ValueError("Trajectories are not the frozen, benchmark-free set their manifest describes")
    set_seed(settings["seed"])
    tokenizer = AutoTokenizer.from_pretrained(model_cfg["name"], local_files_only=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    tokenizer.padding_side = "right"
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    features: list[Features] = []
    for row in rows:
        def render(messages: list[dict[str, Any]], generation: bool, tools: list = row["tools"]) -> str:
            return tokenizer.apply_chat_template(messages, tools=tools, tokenize=False,
                                                 add_generation_prompt=generation)
        features += [encode(prompt, target, tokenizer, settings["seq_len"])
                     for prompt, target in supervised_segments(row, render)]
    loader = AutoModelForImageTextToText if model_cfg["loader"] == "image-text-to-text" else AutoModelForCausalLM
    with torch.device("meta"):
        skeleton = loader.from_config(AutoConfig.from_pretrained(model_cfg["name"], local_files_only=True))
    targets = {name: type(module).__name__ for name, module in skeleton.named_modules()
               if re.fullmatch(lora["target_modules"], name)}
    if not targets or set(targets.values()) != {"Linear"}:
        raise ValueError(f"LoRA regex must match only Linear layers: {sorted(set(targets.values()))}")
    del skeleton
    accumulation, max_steps = schedule(len(rows), len(features), settings["conversations_per_step"],
                                       settings["epochs"])
    provenance = {
        "source_hf_id": model_cfg["source_hf_id"], "source_revision": model_cfg["source_revision"],
        "base_sha256": base_hashes, "config_sha256": digest(config_path),
        "trajectories_sha256": manifest["trajectories_sha256"], "trajectories": len(rows),
        "sequences": len(features), "gradient_accumulation_steps": accumulation, "max_steps": max_steps,
        "supervised_tokens": sum(sum(t != IGNORE_LABEL for t in f["labels"]) for f in features),
        "tokens": sum(len(f["input_ids"]) for f in features),
        "max_sequence_length": max(len(f["input_ids"]) for f in features),
        "gpu": None if preflight else torch.cuda.get_device_name(0), "cuda": torch.version.cuda,
        "versions": {n: importlib.metadata.version(n) for n in ("torch", "transformers", "peft", "datasets", "accelerate", "bitsandbytes")},
        "lora_targets": len(targets),
        "benchmark_access": False,
    }
    out.mkdir(parents=True)
    (out / "preflight.json").write_text(json.dumps(provenance, indent=2) + "\n")
    if preflight:
        print(json.dumps(provenance, indent=2), flush=True)
        return

    def load_base() -> Any:
        return loader.from_pretrained(model_cfg["name"], local_files_only=True, dtype=torch.bfloat16,
                                      device_map={"": "cuda:0"}, attn_implementation="sdpa")

    model = get_peft_model(load_base(), LoraConfig(r=lora["r"], lora_alpha=lora["alpha"],
        lora_dropout=lora["dropout"], target_modules=lora["target_modules"], bias=lora["bias"], task_type="CAUSAL_LM"))
    model.config.use_cache = False
    model.enable_input_require_grads()
    warmup = math.ceil(max_steps * settings["warmup_fraction"])
    class TailLossTrainer(Trainer):
        """The trainer with ``tail_loss`` (config ``training.tail_loss``): the same loss, far less logits memory."""

        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):  # noqa: ANN001
            loss = tail_loss(model, dict(inputs), num_items_in_batch)
            return (loss, None) if return_outputs else loss

    trainer_class = TailLossTrainer if settings.get("tail_loss") else Trainer
    trainer = trainer_class(model=model, processing_class=tokenizer,
        train_dataset=Dataset.from_list(features),
        data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True, label_pad_token_id=IGNORE_LABEL),
        args=TrainingArguments(output_dir=str(out / "runs"), max_steps=max_steps, warmup_steps=warmup,
            per_device_train_batch_size=1, gradient_accumulation_steps=accumulation,
            **{k: settings[k] for k in ("learning_rate", "logging_steps", "save_steps", "optim", "weight_decay",
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
    if trainer.state.global_step != max_steps:
        raise RuntimeError("Training ended before the computed step count")
    # Merge the saved adapter after an independent reload, never the live LoRA.
    del trainer, model
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    merged = PeftModel.from_pretrained(load_base(), adapter, local_files_only=True).merge_and_unload(safe_merge=True)
    merged.config.use_cache = True
    checks = generation_check(merged, tokenizer, rows)
    destination = out / "merged-bf16"
    merged.save_pretrained(destination, safe_serialization=True)
    tokenizer.save_pretrained(destination)
    for source in Path(model_cfg["name"]).iterdir():
        if source.is_file() and not source.name.endswith(WEIGHT_SUFFIXES) and not (destination / source.name).exists():
            shutil.copy(source, destination / source.name)
    # Loading aligns the model's generation config with the tokenizer's special
    # tokens; vLLM (--generation-config vllm) reads stop tokens from this file,
    # so the student keeps the base's, exactly as served for the base.
    if (Path(model_cfg["name"]) / GENERATION_CONFIG).is_file():
        shutil.copy(Path(model_cfg["name"]) / GENERATION_CONFIG, destination / GENERATION_CONFIG)
    hashes = {str(p.relative_to(out)): digest(p) for directory in (adapter, destination)
              for p in sorted(directory.rglob("*")) if p.is_file()}
    (out / "hash_manifest.json").write_text(json.dumps(hashes, indent=2) + "\n")
    (out / "generation_check.json").write_text(json.dumps(checks, indent=2) + "\n")
    if not all(check["matches"] for check in checks):
        raise RuntimeError(f"Merged student does not reproduce its trained first turns; see {out / 'generation_check.json'}")
    print("TRAIN AND SAVED-ADAPTER MERGE COMPLETE", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true", help="Render and tokenise only; no GPU")
    arguments = parser.parse_args()
    train(arguments.config, arguments.preflight)
