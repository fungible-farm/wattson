"""Verify staged migration inputs without access to the old lab or any network.

This checks preservation, not runtime compatibility. A pending source snapshot must
be ported and tested before it replaces a canonical Wattson module.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[1]
MANIFEST: Path = ROOT / "migration/powercoder/manifest.json"


def check() -> None:
    """Require every staged source file to match its recorded, unmodified bytes."""
    manifest = json.loads(MANIFEST.read_text())
    captured = 0
    for entry in manifest["files"]:
        snapshot = entry.get("snapshot")
        if snapshot is None:
            continue
        path = ROOT / snapshot
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry["source_sha256"]:
            raise ValueError(f"migration snapshot changed: {snapshot}")
        captured += 1
    print(f"PASS: {captured} exact migration inputs preserved; runtime cutover is not certified")


if __name__ == "__main__":
    check()
