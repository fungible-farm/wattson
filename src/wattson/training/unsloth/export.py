#!/usr/bin/env python
"""M06 export: merge the LoRA adapter into the base model and write a
BF16 checkpoint + a SHA-256 hash manifest for provenance.

Outputs (under out/):
  - <adapter_name>/            the LoRA adapter (from train.py)
  - merged-bf16/               the merged base+adapter BF16 checkpoint
  - hash_manifest.json         SHA-256 of every file in merged-bf16/ + adapter/
                               + the config + train_log, with counts/sizes.

The hash manifest is the provenance artifact: anyone can re-hash the checkpoint
files and confirm they match the recorded digests.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from wattson.training.unsloth.runtime import load_checkpoint


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def main() -> None:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    out_dir = Path(cfg["output"]["dir"])
    adapter_dir = out_dir / cfg["output"]["adapter_name"]
    merged_dir = out_dir / "merged-bf16"
    if not adapter_dir.exists():
        print(f"FATAL: adapter not found at {adapter_dir}. Run train.py first.", file=sys.stderr)
        sys.exit(1)

    # --- Load base + merge adapter ---
    print(f"Loading base {cfg['model']['name']} + adapter {adapter_dir.name} ...")
    model, tokenizer = load_checkpoint(
        model_name=cfg["model"]["name"], adapter_path=str(adapter_dir),
        dtype=cfg["model"]["dtype"],
    )
    # PEFT returns the full base model with trained weights merged in place.
    model = model.merge_and_unload(safe_merge=True)

    merged_dir.mkdir(parents=True, exist_ok=True)
    print(f"Saving merged BF16 checkpoint to {merged_dir} ...")
    model.save_pretrained(str(merged_dir), safe_serialization=True)
    tokenizer.save_pretrained(str(merged_dir))

    # --- Hash manifest ---
    manifest: dict = {
        "base_model": cfg["model"]["name"],
        "adapter": cfg["output"]["adapter_name"],
        "dtype": cfg["model"]["dtype"],
        "merge": "saved PEFT adapter, merge_and_unload(safe_merge=True)",
        "files": {},
    }
    total_size = 0
    for d in (merged_dir, adapter_dir):
        for p in sorted(d.rglob("*")):
            if p.is_file():
                digest, size = sha256_file(p)
                rel = str(p.relative_to(out_dir))
                manifest["files"][rel] = {"sha256": digest, "size_bytes": size}
                total_size += size
    # Include config + train_log for full provenance.
    for p in (Path(cfg_path), out_dir / "train_log.json"):
        if p.exists():
            digest, size = sha256_file(p)
            manifest["files"][os.path.relpath(p, out_dir)] = {
                "sha256": digest, "size_bytes": size
            }
            total_size += size
    manifest["total_files"] = len(manifest["files"])
    manifest["total_size_bytes"] = total_size

    manifest_path = out_dir / "hash_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote hash manifest: {manifest_path} "
          f"({manifest['total_files']} files, {total_size / 1e9:.2f} GiB)")
    print("EXPORT PASS")


if __name__ == "__main__":
    main()
