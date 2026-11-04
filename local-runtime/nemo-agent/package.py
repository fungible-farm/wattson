"""Assemble a self-contained source/image/model/SBOM package, excluding its own output.

Called inside the local agent image after the host adapter saves/scans each image.
No container engine socket or external service is needed by this packaging step.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tarfile

SOURCE_ROOT: Path = Path("/source")
OUTPUT_ROOT: Path = Path("/package")
# Build products and generated/private state never become source-package inputs.
EXCLUDED: frozenset[str] = frozenset({".wattson", "target", "__pycache__", ".git", ".venv", ".pytest_cache"})


def package() -> None:
    """Archive this environment's source and verified staged runtime artifacts."""
    source_archive = OUTPUT_ROOT / "source.tar.gz"
    with tarfile.open(source_archive, "w:gz") as archive:
        for root, directories, filenames in os.walk(SOURCE_ROOT):
            directories[:] = sorted(name for name in directories if name not in EXCLUDED)
            for name in sorted(filenames):
                path = Path(root) / name
                if path.is_symlink():
                    raise ValueError(f"source symlink needs explicit review: {path}")
                archive.add(path, arcname=Path("source") / path.relative_to(SOURCE_ROOT), recursive=False)
    inventory = json.loads((SOURCE_ROOT / "nemo-agent/components.json").read_text())
    inventory["model_artifacts"] = json.loads((SOURCE_ROOT / "huggingface/models.json").read_text())
    inventory["package_files"] = []
    for path in sorted(OUTPUT_ROOT.rglob("*")):
        if not path.is_file() or path.name in {"manifest.json", "SHA256SUMS", "wattson-local.tar"}:
            continue
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        inventory["package_files"].append({"path": str(path.relative_to(OUTPUT_ROOT)), "sha256": digest})
    inventory["sbom_scope"] = "Syft scans of actual OCI archives, plus declared component inventory. Compiled Rust transitive coverage depends on release binary metadata; not a vulnerability certification."
    (OUTPUT_ROOT / "manifest.json").write_text(json.dumps(inventory, indent=2) + "\n")
    checksums: list[str] = []
    for path in sorted(OUTPUT_ROOT.rglob("*")):
        if path.is_file() and path.name not in {"SHA256SUMS", "wattson-local.tar"}:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            checksums.append(f"{digest}  {path.relative_to(OUTPUT_ROOT)}")
    (OUTPUT_ROOT / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
    with tarfile.open(OUTPUT_ROOT / "wattson-local.tar", "w") as archive:
        for path in sorted(OUTPUT_ROOT.iterdir()):
            if path.name != "wattson-local.tar":
                archive.add(path, arcname=path.name)
    print("PASS: self package includes source, software/model OCI images, SBOMs and checksums")


if __name__ == "__main__":
    package()
