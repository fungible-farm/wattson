"""G3's GRPO pilot: sampled tool-use rollouts, group-relative advantages, one policy-gradient pass (backlog 0017).

Two steps, run alternately by ``training/run_grpo_pilot.py`` so the policy is served and trained
in turn, never side by side (FM-10):

``rollout`` (host, against a served policy). Each task gets ``k`` attempts through the
evaluation's own harness: ``tool_probe.probe``, the MCP server and the study tool, at
``--temperature``. They are graded by ``evaluate_agent.grade`` and rewarded by
``training.rl_reward.reward``: +1 verified, -1 fabricated, -0.02 per call. vLLM is asked for each
turn's exact ``prompt_token_ids`` and completion ``token_ids`` (``return_token_ids``), so the
update trains on what the policy saw and produced, never a re-rendered conversation. Re-rendering
is where FM-22's seam token and FM-23's key order came from. Each attempt's advantage is
GRPO's group-relative one: ``(r - mean_group) / (std_group + ADVANTAGE_EPS)``. A group whose
rewards are all equal carries no signal and is dropped.

``update`` (training image, GPU). From the previous policy's LoRA adapter, every turn of every
attempt with a non-zero advantage is one sequence: prompt tokens unlabelled, completion tokens
labelled. Its loss is ``-advantage x mean completion-token log-probability``, averaged over the
sequences of ``groups_per_step`` task groups per optimizer step. The adapter is saved, reloaded
independently and merged, then everything is hashed, as ``agent_sft`` does, so
``run_agent_study.verify_student`` accepts the result.

Named stand-in (AGENTS.md "Sandbox stand-ins must be named"): this is REINFORCE with GRPO's
group-relative baseline. It has no importance ratio, clipping or KL term to a reference policy.
Each iteration makes one pass over its own fresh rollouts, so the data stays close to
on-policy; a full GRPO adds ``clip(pi/pi_old)`` and a KL penalty in ``policy_loss``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import random
import shutil
import statistics
import time
from pathlib import Path
from typing import Any, TypedDict

# Advantage = (r - mean) / (std + eps); groups with std below MIN_GROUP_STD carry no signal.
ADVANTAGE_EPS: float = 1e-6
MIN_GROUP_STD: float = 1e-9
# How an attempt's turns are weighted in the update. "grpo": the group-relative advantage (groups with equal
# rewards drop out). "rft": rejection-sampling fine-tuning, the Phase 1 control (backlog 0039): +1 for each
# attempt the grader passed and nothing else, so the negative and relative signal is the only difference.
WEIGHTINGS: tuple[str, ...] = ("grpo", "rft")
CONCURRENCY: int = 8  # the evaluation's own (run_comparison.CONCURRENCY)
# One attempt's model turns may not exceed the evaluation's own per-request ceiling (tool_probe.INFERENCE_TIMEOUT_S =
# 1800 s), and a timed-out attempt is journaled as the terminal ``attempt_timeout`` and scored as unanswered.
# Grading is local and fast; its bound only exists so a stuck grader fails the stage instead of holding it forever.
ATTEMPT_TIMEOUT_S: float = 1800.0
GRADE_TIMEOUT_S: float = 600.0
WEIGHT_SUFFIXES: tuple[str, ...] = (".safetensors", ".safetensors.index.json", ".bin")  # agent_sft's
GENERATION_CONFIG: str = "generation_config.json"


class Rollout(TypedDict):
    """One graded, rewarded attempt."""

    example_id: str
    attempt: int
    journal: str
    reward: float
    passed: bool
    fabricated: bool
    tool_calls: int
    advantage: float


def advantages(rewards: list[float]) -> list[float]:
    """GRPO's group-relative advantages; all zero when the group's rewards do not differ."""
    if len(rewards) < 2:
        return [0.0] * len(rewards)
    mean, std = statistics.fmean(rewards), statistics.pstdev(rewards)
    if std < MIN_GROUP_STD:
        return [0.0] * len(rewards)
    return [(r - mean) / (std + ADVANTAGE_EPS) for r in rewards]


def select_tasks(tasks: list[dict[str, Any]], count: int, iteration: int, seed: int) -> list[dict[str, Any]]:
    """``count`` tasks for one iteration: a seeded shuffle, sliced so iterations see different tasks."""
    order = sorted(tasks, key=lambda t: t["example_id"])
    random.Random(seed).shuffle(order)
    start = (iteration * count) % max(len(order), 1)
    return (order[start:] + order[:start])[:count]


async def rollout(args: argparse.Namespace) -> list[Rollout]:
    """Run, grade and reward ``k`` sampled attempts per task; write rollouts.jsonl and summary.json."""
    from wattson.datasets.audit import load_rows
    from wattson.inference.evaluate_agent import frozen_task_count, grade
    from wattson.inference.tool_probe import probe, record
    from wattson.training.rl_reward import reward

    tasks = load_rows(args.tasks)
    if len(tasks) != frozen_task_count(args.tasks):
        raise ValueError("Rollout tasks must be a complete frozen pool")
    chosen = select_tasks(tasks, args.tasks_per_iteration, args.iteration, args.seed)
    args.out.mkdir(parents=True)
    (args.out / "prompts").mkdir()
    gate, grading = asyncio.Semaphore(CONCURRENCY), asyncio.Lock()

    async def one(task: dict[str, Any], k: int) -> dict[str, Any]:
        async with gate:
            stem = f"{task['example_id']}-{k}"
            prompt = args.out / "prompts" / f"{stem}.txt"
            prompt.write_text(task["task"]["prompt"])
            journal = args.out / f"ex_{stem}.jsonl"
            options = argparse.Namespace(base_url=args.base_url, model=args.model, prompt=prompt, study_tools=True,
                                         first_turn_tool_choice="auto", namespace_tools=False,
                                         temperature=args.temperature, return_token_ids=True)
            with journal.open("x") as stream:
                try:
                    await asyncio.wait_for(probe(options, stream), ATTEMPT_TIMEOUT_S)
                except (ValueError, KeyError, TypeError) as error:
                    record(stream, "model_protocol_error", {"type": type(error).__name__, "message": str(error)})
                except asyncio.TimeoutError:
                    record(stream, "terminal", {"state": "attempt_timeout", "timeout_s": ATTEMPT_TIMEOUT_S})
            async with grading:
                graded = await asyncio.wait_for(asyncio.to_thread(grade, task, journal), GRADE_TIMEOUT_S)
            events = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
            completed = events[-1]["kind"] == "terminal" and events[-1]["payload"]["state"] == "answered"
            scored = reward(task, events, graded["answer"], completed)
            return {"example_id": task["example_id"], "attempt": k, "journal": journal.name, **scored,
                    "graded_pass": graded["passed"]}

    results = await asyncio.gather(*(one(task, k) for task in chosen for k in range(args.k)))
    rollouts: list[Rollout] = []
    for task in chosen:
        group = [r for r in results if r["example_id"] == task["example_id"]]
        for row, advantage in zip(group, advantages([r["reward"] for r in group])):
            rollouts.append(Rollout(example_id=row["example_id"], attempt=row["attempt"], journal=row["journal"],
                                    reward=row["reward"], passed=row["passed"], fabricated=row["fabricated"],
                                    tool_calls=row["tool_calls"], advantage=advantage))
    path = args.out / "rollouts.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rollouts))
    groups = len(chosen)
    signal = sum(any(r["advantage"] for r in rollouts if r["example_id"] == t["example_id"]) for t in chosen)
    summary = {"iteration": args.iteration, "model": args.model, "tasks": str(args.tasks), "tasks_chosen": groups,
               "k": args.k, "temperature": args.temperature, "attempts": len(rollouts),
               "pass_rate": statistics.fmean(r["passed"] for r in rollouts),
               "fabricated": sum(r["fabricated"] for r in rollouts),
               "mean_reward": statistics.fmean(r["reward"] for r in rollouts),
               "mean_tool_calls": statistics.fmean(r["tool_calls"] for r in rollouts),
               "groups_with_signal": signal, "rollouts_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    return rollouts


class Sequence(TypedDict):
    """One sampled turn to reinforce."""

    input_ids: list[int]
    completion_start: int
    advantage: float
    group: str


def sequences(rollout_dir: Path, seq_len: int, weighting: str = "grpo") -> tuple[list[Sequence], dict[str, int]]:
    """Every turn of every attempt with a non-zero weight, from the journal's exact token ids.

    The weight is the attempt's advantage (``weighting="grpo"``) or +1 for a graded pass and 0 otherwise
    (``"rft"``).

    Raises:
        ValueError: if a turn lacks token ids (the rollout was not run with return_token_ids).
    """
    rows = [json.loads(line) for line in (rollout_dir / "rollouts.jsonl").read_text().splitlines() if line.strip()]
    out: list[Sequence] = []
    counts = {"attempts_used": 0, "turns": 0, "skipped_too_long": 0}
    for row in rows:
        weight = row["advantage"] if weighting == "grpo" else float(bool(row["passed"]))
        if not weight:
            continue
        counts["attempts_used"] += 1
        for line in (rollout_dir / row["journal"]).read_text().splitlines():
            event = json.loads(line)
            if event["kind"] != "model_response":
                continue
            prompt = event["payload"].get("prompt_token_ids")
            completion = event["payload"]["choices"][0].get("token_ids")
            if not prompt or not completion:
                raise ValueError(f"{row['journal']}: a turn has no token ids; rerun the rollout with return_token_ids")
            if len(prompt) + len(completion) > seq_len:
                counts["skipped_too_long"] += 1
                continue
            out.append(Sequence(input_ids=prompt + completion, completion_start=len(prompt),
                                advantage=weight, group=row["example_id"]))
            counts["turns"] += 1
    return out, counts


def policy_loss(tail_logits: Any, completion: Any, advantage: float) -> Any:
    """``-advantage x mean log-probability of the completion tokens`` for one sequence (batch of 1).

    ``tail_logits`` are the logits at the last ``len(completion) + 1`` positions (the forward pass
    is asked for those only, ``logits_to_keep``). Position i predicts completion token i, and the
    final position predicts past the end, so it is dropped. Full-sequence logits for an 8k-token
    turn over a ~248k vocabulary would not fit beside the model on a 24 GB card.
    """
    import torch

    logp = torch.log_softmax(tail_logits[0, :-1].float(), dim=-1)
    taken = logp.gather(1, completion[0].unsqueeze(1)).squeeze(1)
    return -advantage * taken.mean()


def update(args: argparse.Namespace) -> None:
    """One policy-gradient pass from ``--init-adapter`` over a rollout directory; merge and hash."""
    import torch
    import yaml
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoModelForImageTextToText, AutoTokenizer, set_seed

    from wattson.training.agent_sft import verify_hub_snapshot
    from wattson.training.granite.train import digest, verify_base

    config = yaml.safe_load(args.config.read_text())
    model_cfg, settings = config["model"], config["training"]
    if args.out.exists():
        raise FileExistsError(f"Refusing to reuse {args.out}")
    device = args.device
    if device != "cpu" and not torch.cuda.is_available():
        raise RuntimeError("Physical CUDA required for the update (--device cpu is for a smoke test only)")
    base_hashes = verify_hub_snapshot(model_cfg) if model_cfg["verify"] == "hub_snapshot" else verify_base(model_cfg)
    seed = settings["seed"] if args.seed is None else args.seed
    set_seed(seed)
    data, counts = sequences(args.rollouts, settings["seq_len"], args.weighting)
    if not data:
        raise ValueError("No rollout carries a non-zero advantage; nothing to learn from")
    loader = AutoModelForImageTextToText if model_cfg["loader"] == "image-text-to-text" else AutoModelForCausalLM

    def load_base() -> Any:
        return loader.from_pretrained(model_cfg["name"], local_files_only=True, dtype=torch.bfloat16,
                                      device_map={"": device}, attn_implementation="sdpa")

    tokenizer = AutoTokenizer.from_pretrained(model_cfg["name"], local_files_only=True)
    model = PeftModel.from_pretrained(load_base(), args.init_adapter, is_trainable=True)
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    # from_pretrained returns eval mode, and checkpointing applies only in training mode (the Trainer
    # calls train() for agent_sft). Without it the 3090's update ran out of memory at 23.5 GiB;
    # with it, probe_update_memory measured 13.06 GiB peak at the full 8192 tokens.
    model.train()
    model.enable_input_require_grads()
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=settings["learning_rate"],
                                  weight_decay=settings["weight_decay"])
    groups = sorted({s["group"] for s in data})
    random.Random(seed).shuffle(groups)
    per_step = settings["groups_per_step"]
    log, start = [], time.monotonic()
    for step, first in enumerate(range(0, len(groups), per_step)):
        batch = [s for s in data if s["group"] in set(groups[first:first + per_step])]
        optimizer.zero_grad(set_to_none=True)
        total = 0.0
        for item in batch:
            ids = torch.tensor([item["input_ids"]], device=device)
            completion = ids[:, item["completion_start"]:]
            tail = model(input_ids=ids, logits_to_keep=completion.shape[1] + 1).logits
            loss = policy_loss(tail, completion, item["advantage"]) / len(batch)
            loss.backward()
            total += float(loss)
        norm = float(torch.nn.utils.clip_grad_norm_(model.parameters(), settings["max_grad_norm"]))
        optimizer.step()
        log.append({"step": step, "sequences": len(batch), "loss": total, "grad_norm": norm})
        print(json.dumps(log[-1]), flush=True)
    adapter = args.out / "adapter"
    args.out.mkdir(parents=True)
    model.save_pretrained(adapter)
    tokenizer.save_pretrained(adapter)
    rollouts_sha = hashlib.sha256((args.rollouts / "rollouts.jsonl").read_bytes()).hexdigest()
    report = {"config": config, "config_sha256": digest(args.config), "base_sha256": base_hashes,
              "init_adapter": str(args.init_adapter), "rollouts": str(args.rollouts), "seed": seed, "weighting": args.weighting,
              "trajectories_sha256": rollouts_sha, "completed_steps": len(log), "sequences": len(data), **counts,
              "log_history": log, "wall_s": time.monotonic() - start, "device": device,
              "stand_in": "REINFORCE with GRPO group baseline; no ratio, clip or KL"}
    (args.out / "train_log.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    # Merge the saved adapter after an independent reload, never the live one (as agent_sft does).
    del model, optimizer
    import gc

    gc.collect()
    if device != "cpu":
        torch.cuda.empty_cache()
    merged = PeftModel.from_pretrained(load_base(), adapter, local_files_only=True).merge_and_unload(safe_merge=True)
    merged.config.use_cache = True
    destination = args.out / "merged-bf16"
    merged.save_pretrained(destination, safe_serialization=True)
    tokenizer.save_pretrained(destination)
    for source in Path(model_cfg["name"]).iterdir():
        if source.is_file() and not source.name.endswith(WEIGHT_SUFFIXES) and not (destination / source.name).exists():
            shutil.copy(source, destination / source.name)
    if (Path(model_cfg["name"]) / GENERATION_CONFIG).is_file():
        shutil.copy(Path(model_cfg["name"]) / GENERATION_CONFIG, destination / GENERATION_CONFIG)
    hashes = {str(p.relative_to(args.out)): digest(p) for directory in (adapter, destination)
              for p in sorted(directory.rglob("*")) if p.is_file()}
    (args.out / "hash_manifest.json").write_text(json.dumps(hashes, indent=2) + "\n")
    if not all(math.isfinite(entry["loss"]) for entry in log):
        raise RuntimeError("A non-finite loss was recorded")
    print("GRPO UPDATE AND MERGE COMPLETE", flush=True)


def main() -> int:
    """``rollout`` or ``update``."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="step", required=True)
    roll = sub.add_parser("rollout")
    roll.add_argument("--base-url", required=True)
    roll.add_argument("--model", required=True)
    roll.add_argument("--tasks", type=Path, required=True)
    roll.add_argument("--out", type=Path, required=True)
    roll.add_argument("--iteration", type=int, required=True)
    roll.add_argument("--tasks-per-iteration", type=int, required=True)
    roll.add_argument("--k", type=int, required=True)
    roll.add_argument("--temperature", type=float, required=True)
    roll.add_argument("--seed", type=int, required=True)
    upd = sub.add_parser("update")
    upd.add_argument("--config", type=Path, required=True)
    upd.add_argument("--rollouts", type=Path, required=True)
    upd.add_argument("--init-adapter", type=Path, required=True)
    upd.add_argument("--out", type=Path, required=True)
    upd.add_argument("--seed", type=int, default=None, help="Overrides the config's training seed (replication runs)")
    upd.add_argument("--weighting", choices=WEIGHTINGS, default="grpo")
    upd.add_argument("--device", default="cuda:0", help="cpu only for a smoke test of the update path")
    args = parser.parse_args()
    if args.step == "rollout":
        asyncio.run(rollout(args))
    else:
        update(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
