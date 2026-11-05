"""Run Axolotl reproduction or explicit checkpoint continuation with lineage.

This wraps the upstream CLI, validates the already prepared data, and records
the exact inputs before training. It never auto-resumes or treats adapter-only
loading as an optimizer/RNG checkpoint resume.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
import time
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import yaml

# These are the full-state files written by the single-GPU Trainer checkpoint.
PARENT_FILES: tuple[str, ...] = (
    "adapter_config.json", "adapter_model.safetensors", "trainer_state.json",
    "optimizer.pt", "scheduler.pt", "rng_state.pth",
)
# Output locations and continuation length may change without changing the
# model/data/optimizer experiment. Every other config value must match.
LINEAGE_ONLY_FIELDS: frozenset[str] = frozenset({
    "output_dir", "resume_from_checkpoint", "auto_resume_from_checkpoints",
    "lora_model_dir", "max_steps",
})
TRAINING_PACKAGES: tuple[str, ...] = (
    "axolotl", "torch", "transformers", "peft", "datasets", "accelerate", "bitsandbytes",
)
HASH_CHUNK_BYTES: int = 1 << 20  # Stream checkpoint hashes without allocating whole files.


def digest(path: Path) -> str:
    """Hash exact source/data/checkpoint bytes for the run manifest."""
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(HASH_CHUNK_BYTES), b""):
            value.update(chunk)
    return value.hexdigest()


def plan_run(
    config_path: Path, mode: str, parent: Path | None = None,
    output_dir: Path | None = None, max_steps: int | None = None,
) -> dict[str, Any]:
    """Validate immutable inputs and parent compatibility before GPU allocation."""
    config = yaml.safe_load(config_path.read_text())
    if not re.fullmatch(r"[0-9a-f]{40}", str(config.get("revision_of_model", ""))):
        raise ValueError("A pinned base model revision is required")
    if config.get("lora_model_dir") or config.get("resume_from_checkpoint") or config.get("auto_resume_from_checkpoints"):
        raise ValueError("Config must not contain an implicit adapter/checkpoint parent")
    if mode not in {"reproduction", "continuation"}:
        raise ValueError("Unknown training mode")
    if (mode == "reproduction" and parent is not None) or (mode == "continuation" and parent is None):
        raise ValueError("Only continuation requires an explicit parent checkpoint")
    if max_steps is not None:
        config["max_steps"] = max_steps
    if isinstance(config["max_steps"], bool) or not isinstance(config["max_steps"], int) or config["max_steps"] <= 0:
        raise ValueError("max_steps must be a positive integer")
    target = (output_dir or Path(config["output_dir"])).resolve()
    if target.exists():
        raise ValueError("Output directory already exists; refuse implicit reuse or resume")
    config["output_dir"] = str(target)
    config["auto_resume_from_checkpoints"] = False
    config["lora_model_dir"] = None
    data_hashes, manifest_hashes = {}, {}
    allowed_root = (config_path.resolve().parent / "out").resolve()
    for group, filename in (("datasets", "tokenized-train.jsonl"), ("test_datasets", "tokenized-validation.jsonl")):
        if not config.get(group):
            raise ValueError(f"Missing {group}")
        for source in config[group]:
            path = Path(source["path"])
            if path.name != filename or not path.resolve().is_relative_to(allowed_root):
                raise ValueError("Only the prepared train/validation token files may be used")
            manifest_path = path.parent / "tokenized-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            value = digest(path)
            if manifest.get("benchmark_access") is not False or manifest["files"][str(path)]["sha256"] != value:
                raise ValueError(f"Prepared data fingerprint does not match: {path}")
            data_hashes[str(path)] = value
            manifest_hashes[str(manifest_path)] = digest(manifest_path)
    compatibility = {key: value for key, value in config.items() if key not in LINEAGE_ONLY_FIELDS}
    signature = hashlib.sha256(json.dumps(compatibility, sort_keys=True).encode()).hexdigest()
    parent_hashes = {}
    if parent is not None:
        parent = parent.resolve()
        previous = json.loads((parent.parent / "run_manifest.json").read_text())
        if previous["state"] != "complete" or previous["compatibility_sha256"] != signature:
            raise ValueError("Parent is incomplete or has a different model/data/optimizer configuration")
        if previous["data_sha256"] != data_hashes:
            raise ValueError("Parent training data fingerprints differ")
        for filename in PARENT_FILES:
            parent_hashes[filename] = digest(parent / filename)
            recorded = previous["checkpoint_files_sha256"].get(f"{parent.name}/{filename}")
            if recorded != parent_hashes[filename]:
                raise ValueError(f"Parent checkpoint fingerprint changed: {filename}")
        adapter = json.loads((parent / "adapter_config.json").read_text())
        if (
            adapter["base_model_name_or_path"] != config["base_model"]
            or adapter["r"] != config["lora_r"] or adapter["lora_alpha"] != config["lora_alpha"]
        ):
            raise ValueError("Parent adapter base/rank/alpha does not match")
        step = json.loads((parent / "trainer_state.json").read_text())["global_step"]
        if step >= config["max_steps"]:
            raise ValueError("Continuation must perform steps beyond the parent checkpoint")
        config["resume_from_checkpoint"] = str(parent)
    else:
        config["resume_from_checkpoint"] = None
    return {
        "state": "validated", "mode": mode, "effective_config": config,
        "config_source": str(config_path), "config_sha256": digest(config_path),
        "runner_sha256": digest(Path(__file__)), "compatibility_sha256": signature,
        "data_sha256": data_hashes, "token_manifest_sha256": manifest_hashes,
        "parent_checkpoint": str(parent) if parent is not None else None,
        "parent_files_sha256": parent_hashes,
    }


def main() -> int:
    """Persist the run plan, invoke upstream Axolotl, and record measured completion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("training/axolotl/sft-4b.yaml"))
    parser.add_argument("--mode", choices=("reproduction", "continuation"), default="reproduction")
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    plan = plan_run(args.config, args.mode, args.parent, args.output_dir, args.max_steps)
    if args.validate_only:
        print(json.dumps(plan, indent=2))
        return 0
    if find_spec("unsloth") is not None:
        raise RuntimeError("Use the separate Axolotl environment without Unsloth")
    import torch

    if not torch.cuda.is_available() or "GB10" not in torch.cuda.get_device_name(0):
        raise RuntimeError("Physical GB10 CUDA training is required; CPU fallback is forbidden")
    packages = {name: importlib.metadata.version(name) for name in TRAINING_PACKAGES}
    if plan["parent_checkpoint"]:
        previous = json.loads((Path(plan["parent_checkpoint"]).parent / "run_manifest.json").read_text())
        if previous["packages"] != packages:
            raise ValueError("Parent and continuation package versions differ")
    target = Path(plan["effective_config"]["output_dir"])
    target.mkdir(parents=True)
    config_snapshot = target / "run.yaml"
    config_snapshot.write_text(yaml.safe_dump(plan["effective_config"], sort_keys=False))
    command = ["axolotl", "train", str(config_snapshot)]
    plan.update({"state": "running", "packages": packages, "command": command,
                 "gpu": torch.cuda.get_device_name(0), "cuda": torch.version.cuda})
    manifest_path = target / "run_manifest.json"
    manifest_path.write_text(json.dumps(plan, indent=2) + "\n")
    started = time.monotonic()
    completed = subprocess.run(command, check=False)
    errors = []
    if any(digest(Path(path)) != value for path, value in plan["data_sha256"].items()):
        errors.append("Prepared training/validation data changed during the run")
    final_checkpoint = target / f"checkpoint-{plan['effective_config']['max_steps']}"
    if completed.returncode == 0:
        missing = [name for name in PARENT_FILES if not (final_checkpoint / name).is_file()]
        if missing:
            errors.append(f"Final full-state checkpoint missing files: {missing}")
    artifacts = {}
    for checkpoint in sorted(target.glob("checkpoint-*")):
        for filename in PARENT_FILES:
            path = checkpoint / filename
            if path.is_file():
                artifacts[str(path.relative_to(target))] = digest(path)
    exit_code = completed.returncode or int(bool(errors))
    plan.update({"state": "complete" if exit_code == 0 else "failed",
                 "exit_code": exit_code, "child_exit_code": completed.returncode,
                 "wall_s": time.monotonic() - started, "validation_errors": errors,
                 "checkpoint_files_sha256": artifacts})
    manifest_path.write_text(json.dumps(plan, indent=2) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
