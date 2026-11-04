"""Hash an existing PEFT checkpoint and inspect its saved F32 adapter tensors.

Runs without CUDA or pickle deserialization. The safetensors JSON header and
raw F32 ranges are read with NumPy; unsupported tensor dtypes fail explicitly.
This is a saved-weight audit, not proof of successful reload or GPU execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any

import numpy as np

# Safetensors prefixes its JSON header with one little-endian uint64.
HEADER_PREFIX_BYTES: int = 8
# Prevent malformed headers from causing unbounded allocations.
MAX_HEADER_BYTES: int = 100_000_000
# Stream hashing instead of allocating entire checkpoint files.
HASH_CHUNK_BYTES: int = 1 << 20


def digest_file(path: Path) -> dict[str, Any]:
    """Return exact byte size and SHA-256 for a checkpoint or source artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size_bytes": path.stat().st_size}


def inspect_weights(path: Path) -> dict[str, Any]:
    """Check finite F32 tensors and nonzero B matrices from default LoRA init.

    PEFT's default init_lora_weights=True initializes B to zero. Nonzero saved
    B values contradict an unchanged default adapter. Initial A tensors were
    not retained by the crashed run, so exact A deltas cannot be recovered.
    """
    with path.open("rb") as stream:
        header_size = struct.unpack("<Q", stream.read(HEADER_PREFIX_BYTES))[0]
        if not 0 < header_size <= min(MAX_HEADER_BYTES, path.stat().st_size):
            raise ValueError("Invalid safetensors header length")
        header = json.loads(stream.read(header_size))
    data_start = HEADER_PREFIX_BYTES + header_size
    tensors = {}
    for name, descriptor in header.items():
        if name == "__metadata__":
            continue
        if descriptor["dtype"] != "F32":
            raise ValueError(f"Unsupported saved adapter dtype: {descriptor['dtype']}")
        start, end = descriptor["data_offsets"]
        count = math.prod(descriptor["shape"])
        if not (0 <= start < end <= path.stat().st_size - data_start):
            raise ValueError(f"Invalid tensor bounds: {name}")
        if end - start != count * np.dtype("<f4").itemsize:
            raise ValueError(f"Invalid tensor shape: {name}")
        values = np.memmap(path, dtype="<f4", mode="r", offset=data_start + start, shape=(count,))
        tensors[name] = {
            "shape": descriptor["shape"],
            "finite": bool(np.isfinite(values).all()),
            "nonzero_values": int(np.count_nonzero(values)),
        }
        del values
    b_matrices = {name: item for name, item in tensors.items() if ".lora_B." in name}
    language_b = {name: item for name, item in b_matrices.items() if ".language_model." in name}
    return {
        "tensor_count": len(tensors),
        "all_finite": bool(tensors) and all(item["finite"] for item in tensors.values()),
        "lora_b_count": len(b_matrices),
        "nonzero_lora_b_count": sum(item["nonzero_values"] > 0 for item in b_matrices.values()),
        "language_lora_b_count": len(language_b),
        "nonzero_language_lora_b_count": sum(item["nonzero_values"] > 0 for item in language_b.values()),
        "tensors": tensors,
    }


def main() -> int:
    """Write checkpoint hashes and measured tensor evidence to a local report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--source", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.adapter / "adapter_config.json").read_text())
    report = inspect_weights(args.adapter / "adapter_model.safetensors")
    report["default_zero_b_initialization"] = config.get("init_lora_weights") is True
    report["saved_tensor_gate"] = (
        report["all_finite"] and report["default_zero_b_initialization"]
        and report["language_lora_b_count"] > 0
        and report["nonzero_language_lora_b_count"] == report["language_lora_b_count"]
    )
    sources = sorted(path for path in args.adapter.rglob("*") if path.is_file())
    report["files"] = {str(path): digest_file(path) for path in sources + args.source}
    report["limitations"] = [
        "Initial adapter tensors were not retained; exact before/after deltas are unavailable.",
        "Audit hashes describe current files; consult the run manifest for launch-time fingerprints when available.",
        "Reload, inference, physical GPU execution, and model accuracy require separate evidence.",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key not in {"tensors", "files"}}, indent=2))
    return 0 if report["saved_tensor_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
