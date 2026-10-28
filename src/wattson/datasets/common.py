"""Shared primitives for the NEM PowerCoder M04 dataset factory.

Used by generate.py / validate.py / deduplicate.py / split.py. Keeps the
determinism and provenance rules in ONE place so all four stages agree:

- Determinism: a per-example seed is derived from (base_seed, family,
  subfamily, topology, example_index) via sha256, so repeated generation with
  the same base seed and counts yields byte-identical task definitions and
  (on the same pinned solver stack) identical expected solver results.
- Provenance: every example carries generator, generator_version, base seed,
  git commit, UTC timestamp, licence, and source references.
- example_id: content-addressed ('ex_' + 16 hex) over the canonical task
  definition, so identical inputs always produce the same ID.

This module performs NO solver execution -- it is pure bookkeeping. The real
pandapower execution lives in generate.py (reusing wattson.inference.powermcp).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo root = two levels up from this file (wattson/datasets/ -> repo root).
REPO_ROOT: Path = Path(__file__).resolve().parents[3]

# The M04 dataset factory generator identity (recorded in every example's
# provenance). Bump the minor version when the task templates or parameter
# derivation change (which changes the generated task definitions).
GENERATOR_NAME: str = "wattson/datasets/generate.py"
GENERATOR_VERSION: str = "0.1.0"

# Underlying data licence: the CSIRO Synthetic-NEM-2000-Bus case files are
# public data (fetched + checksum-verified by scripts/fetch_csiro_nem_data.py).
# Generated examples derive from them; the examples themselves are original
# works of this repo. Recorded per example so public artefacts are licence-clean.
DATA_LICENCE: str = (
    "CSIRO Synthetic-NEM-2000-Bus case data (public); generated examples "
    "(c) PromptExecution, MIT"
)

# MISSION.md M04 dataset families and the concrete subfamilies (task templates)
# the smoke dataset generates. The smoke dataset covers two of the six mission
# families (power-flow-contingency and powermcp-trajectories); the remaining
# families (python-scientific, code-repair, engineering-interpretation,
# safety-uncertainty) are follow-up templates added to this table.
FAMILIES: dict[str, list[str]] = {
    "power-flow-contingency": [
        "pf-report",
        "pf-load-scale-fit",
        "pf-line-rating-fit",
        "pf-gen-dispatch-fit",
    ],
    "powermcp-trajectories": [
        "mcp-trajectory",
    ],
}

# The case files the smoke dataset generates against. snem-sa-2000 converges
# (CONSTRAINT_VIOLATION base case -- a property of the synthetic data); the
# 2-bus fixtures exercise the SUCCESS branch. snem-1803 is deliberately
# excluded from the smoke set because it is an EXECUTION_FAILURE in
# pandapower 3.5.4 (divide-by-zero) -- it is reserved for the code-repair
# family, not the power-flow family.
CASE_IDS: list[str] = ["snem-sa-2000", "fixture-2bus-ok"]


def sha256_hex(b: bytes) -> str:
    """Plain hex sha256 (no prefix) -- for content addressing."""
    return hashlib.sha256(b).hexdigest()


def sha256_prefixed(b: bytes) -> str:
    """Prefixed sha256 ('sha256:<hex>') -- for evidence records."""
    return "sha256:" + sha256_hex(b)


def canonical_json(obj: Any) -> str:
    """Canonical (sorted-keys, no whitespace variance) JSON serialization.

    This is the single serialization used for BOTH the example_id hash and the
    execution result_hash, so the two hash domains are consistent.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def derive_example_seed(
    base_seed: int,
    family: str,
    subfamily: str,
    topology: str,
    example_index: int,
) -> int:
    """Derive a deterministic per-example seed.

    sha256 over the stable string (base_seed, family, subfamily, topology,
    example_index) -> first 8 bytes -> unsigned 64-bit int. Fully deterministic
    across processes and platforms (unlike Python's salted hash()).
    """
    key = f"{base_seed}|{family}|{subfamily}|{topology}|{example_index}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def compute_example_id(
    family: str,
    subfamily: str,
    topology: str,
    seed: int,
    task_inputs: dict[str, Any],
) -> str:
    """Content-addressed example ID over the canonical task definition.

    Identical (family, subfamily, topology, seed, task.inputs) -> identical ID,
    which is what makes 'repeated generation with identical inputs produces the
    same task definitions' checkable.
    """
    definition = {
        "family": family,
        "subfamily": subfamily,
        "topology": topology,
        "seed": seed,
        "inputs": task_inputs,
    }
    return "ex_" + sha256_hex(canonical_json(definition).encode("utf-8"))[:16]


def git_commit(cwd: Path | None = None) -> str:
    """Full 40-char HEAD SHA, or 'unknown' if not a git repo / git missing."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd or REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_provenance(
    base_seed: int,
    source_refs: list[str],
    commit: str | None = None,
    created_utc: str | None = None,
) -> dict[str, Any]:
    """Build the provenance block for one example (MISSION.md M04: full
    provenance for 100% of accepted records)."""
    return {
        "generator": GENERATOR_NAME,
        "generator_version": GENERATOR_VERSION,
        "seed": base_seed,
        "git_commit": commit if commit is not None else git_commit(),
        "created_utc": created_utc if created_utc is not None else utc_now_iso(),
        "licence": DATA_LICENCE,
        "source_refs": source_refs,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dicts (one per line)."""
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    """Write a list of dicts to a JSONL file (one compact JSON object per line).

    Parent dirs are created. Each row is serialized with sorted keys so the
    file is stable across runs for identical input rows.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(canonical_json(row) + "\n")
    return path


def write_json(path: Path, obj: Any) -> Path:
    """Write an object to a pretty-printed, sorted-keys JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
