"""Use the official hf CLI to stage pinned repositories and verify content identity.

The model OCI images carry data only. This helper never imports downloaded Python,
uses remote model code, or includes the caller's HF credentials in its output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from huggingface_hub import HfApi

LOCK_NAME: str = "wattson-model-lock.json"
MODEL_ROOT: Path = Path("/stage")
REGISTRY_PATH: Path = Path("/opt/hf/models.json")


def digest(path: Path) -> str:
    """Hash a model file with bounded memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(destination: Path, spec: dict[str, str]) -> dict[str, Any]:
    """Verify every staged byte against the locked identity, without network access."""
    lock = json.loads((destination / LOCK_NAME).read_text())
    for key in ("repo_id", "revision"):
        if lock[key] != spec[key]:
            raise ValueError(f"model identity differs: {key}")
    for row in lock["files"]:
        if digest(destination / row["path"]) != row["sha256"]:
            raise ValueError(f"model checksum mismatch: {row['path']}")
    print(json.dumps({"verified": spec, "bytes": lock["bytes"], "files": len(lock["files"])}, indent=2), flush=True)
    return lock


def download(spec: dict[str, str], destination: Path) -> None:
    """Download via hf at an immutable commit and record hashes and publisher LFS checksums."""
    if (destination / LOCK_NAME).exists():
        verify(destination, spec)
        return
    info = HfApi().model_info(spec["repo_id"], revision=spec["revision"], files_metadata=True)
    if info.sha != spec["revision"]:
        raise ValueError("Hub returned an unexpected revision")
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(["hf", "download", spec["repo_id"], "--revision", spec["revision"],
                    "--local-dir", str(destination)], check=True)
    rows: list[dict[str, Any]] = []
    for sibling in sorted(info.siblings, key=lambda row: row.rfilename):
        path = destination / sibling.rfilename
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"expected a regular model file: {sibling.rfilename}")
        actual = digest(path)
        expected = sibling.lfs.sha256 if sibling.lfs else None
        if expected and actual != expected:
            raise ValueError(f"Hub LFS checksum mismatch: {sibling.rfilename}")
        rows.append({"path": sibling.rfilename, "bytes": path.stat().st_size,
                     "sha256": actual, "publisher_lfs_sha256": expected})
    lock = {**spec, "download_tool": "hf / huggingface_hub 2.4.0", "files": rows,
            "bytes": sum(row["bytes"] for row in rows)}
    (destination / LOCK_NAME).write_text(json.dumps(lock, indent=2) + "\n")
    verify(destination, spec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["download", "verify", "spec"])
    parser.add_argument("model", choices=["qwen", "granite"])
    parser.add_argument("field", nargs="?")
    args = parser.parse_args()
    spec = json.loads(REGISTRY_PATH.read_text())[args.model]
    if args.action == "spec":
        print(spec[args.field] if args.field else json.dumps(spec))
    elif args.action == "verify":
        verify(Path("/artifact/models"), spec)
    else:
        download(spec, MODEL_ROOT)
