"""Prepare identical SFT messages and a clean validation set for Axolotl.

Retains all 273 original training rows to reproduce the Unsloth sampling
distribution. Validation excludes every training prompt/input and internal
duplicate. The separate benchmark is never read by this preparation step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from powercoder.datasets.audit import audit_rows, load_rows, prompt_key, task_key


def convert(row: dict[str, Any], system_prompt: str) -> dict[str, Any]:
    """Keep the same system, user, and supervised answer as Unsloth SFT."""
    return {
        "example_id": row["example_id"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": row["task"]["prompt"]},
            {"role": "assistant", "content": row["expected"]["answer"]},
        ],
    }


def main() -> int:
    """Export train/validation messages and hashes; refuse accidental overwrite."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unsloth-config", type=Path, default=Path("training/unsloth/config.yaml"))
    parser.add_argument("--validation", type=Path, default=Path("powercoder/datasets/smoke/splits/validation.jsonl"))
    parser.add_argument("--out-dir", type=Path, default=Path("training/axolotl/out/data"))
    args = parser.parse_args()
    if args.out_dir.exists():
        raise ValueError("Refusing to overwrite prepared training data")
    config = yaml.safe_load(args.unsloth_config.read_text())
    train_path = Path(config["dataset"]["split"])
    train = load_rows(train_path)
    if not train or any(
        row["lifecycle"]["split"] != "train" or row["lifecycle"]["status"] != "verified"
        for row in train
    ):
        raise ValueError("Training data must contain verified train rows only")
    seen_prompts = {prompt_key(row) for row in train}
    seen_tasks = {task_key(row) for row in train}
    validation = []
    for row in load_rows(args.validation):
        if row["lifecycle"]["split"] != "validation":
            raise ValueError("Validation source contains another split")
        prompt, task = prompt_key(row), task_key(row)
        if prompt not in seen_prompts and task not in seen_tasks:
            seen_prompts.add(prompt)
            seen_tasks.add(task)
            validation.append(row)
    audit = audit_rows(validation, train)
    if not audit["clean"]:
        raise ValueError(f"Validation independence failed: {audit}")
    args.out_dir.mkdir(parents=True)
    for label, rows in (("train", train), ("validation", validation)):
        path = args.out_dir / f"{label}.jsonl"
        path.write_text("".join(
            json.dumps(convert(row, config["dataset"]["system_prompt"])) + "\n"
            for row in rows
        ))
    manifest = {
        "train_rows": len(train), "validation_rows": len(validation),
        "validation_audit": audit,
        "source_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (args.unsloth_config, train_path, args.validation)
        },
        "prepared_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(args.out_dir.glob("*.jsonl"))
        },
        "benchmark_access": False,
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
