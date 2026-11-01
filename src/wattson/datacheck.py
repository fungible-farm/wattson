"""Verify data/ against data/MANIFEST.json (SHA-256 of every file) and the row counts the dataset manifests record.

The published results were measured on exactly these tasks and trajectories; a silent edit would make them unreproducible.
``scripts/verify_data.py`` and ``wattson doctor`` both call this module, so the script and the container report the same thing.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path

DATA_ENV: str = "WATTSON_DATA"
MANIFEST_NAME: str = "MANIFEST.json"
# The CSIRO case files are downloaded by scripts/fetch_csiro_nem_data.py and checksum-verified there, not shipped.
FETCHED_SUFFIX: str = ".m"
_BLOCK: int = 1 << 20


def data_dir() -> Path:
    """``$WATTSON_DATA`` if set, else the repository's ``data/`` (two levels above this package's ``src``)."""
    override = os.environ.get(DATA_ENV)
    return Path(override) if override else Path(__file__).resolve().parents[2] / "data"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_BLOCK), b""):
            h.update(block)
    return h.hexdigest()


def files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name != MANIFEST_NAME and p.suffix != FETCHED_SUFFIX)


def build(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): digest(p) for p in files(root)}


def row_count_problems(root: Path) -> list[str]:
    """Trajectory manifests record how many rows they kept; the gzip file must hold that many."""
    problems: list[str] = []
    for manifest in sorted((root / "trajectories").glob("*/manifest.json")):
        kept = json.loads(manifest.read_text()).get("kept")
        with gzip.open(manifest.parent / "trajectories.jsonl.gz", "rt") as stream:
            rows = sum(1 for line in stream if line.strip())
        if kept is not None and rows != kept:
            problems.append(f"{manifest.parent.name}: manifest says {kept} rows, file has {rows}")
    return problems


def verify(root: Path) -> tuple[int, list[str]]:
    """Return (files checked, problems). An empty problem list means the data is exactly what the manifest records."""
    manifest = root / MANIFEST_NAME
    if not manifest.exists():
        return 0, [f"no {MANIFEST_NAME} in {root}"]
    recorded = json.loads(manifest.read_text())
    actual = build(root)
    problems = [f"changed: {k}" for k in actual if k in recorded and actual[k] != recorded[k]]
    problems += [f"unlisted: {k}" for k in actual if k not in recorded]
    problems += [f"missing: {k}" for k in recorded if k not in actual]
    problems += row_count_problems(root)
    return len(actual), problems


def write(root: Path) -> int:
    """Regenerate the manifest after an intended change; return how many files it lists."""
    actual = build(root)
    (root / MANIFEST_NAME).write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    return len(actual)
