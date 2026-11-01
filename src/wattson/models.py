"""Pull a released student from Hugging Face into a directory and verify every file against its ``hash_manifest.json``.

Hugging Face does not serve models through an OCI registry, so a container cannot ``podman pull`` weights. The supported route
is to download into a named volume once (this module, run by the quick start's init container) and keep that volume mounted
into the model server. Pulling is idempotent: a directory whose marker file and hashes already match is not downloaded again.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from wattson import log

MANIFEST: str = "hash_manifest.json"
MARKER: str = ".wattson-pulled.json"
# The model card is replaced when a model is published, so it is not part of the trained artifact the manifest hashes.
UNHASHED: frozenset[str] = frozenset({"README.md", MANIFEST, MARKER, ".gitattributes"})
# The training run hashed files under these directory names; the published repo puts the merged weights at its root.
STRIPPED_PREFIX: str = "merged-bf16/"
_BLOCK: int = 1 << 24
_LOG = log.get("models")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_BLOCK), b""):
            h.update(block)
    return h.hexdigest()


def verify(root: Path) -> tuple[int, list[str]]:
    """Return (files checked, problems) for ``root`` against its ``hash_manifest.json``."""
    manifest = root / MANIFEST
    if not manifest.exists():
        return 0, [f"no {MANIFEST} in {root}"]
    problems: list[str] = []
    checked = 0
    for name, expected in sorted(json.loads(manifest.read_text()).items()):
        relative = name.removeprefix(STRIPPED_PREFIX)
        if Path(relative).name in UNHASHED:
            continue
        path = root / relative
        if not path.exists():
            problems.append(f"missing: {relative}")
            continue
        started = time.monotonic()
        actual = _sha256(path)
        checked += 1
        _LOG.debug("model.hash", extra={"file": relative, "ok": actual == expected, "seconds": round(time.monotonic() - started, 2)})
        if actual != expected:
            problems.append(f"changed: {relative}")
    return checked, problems


def is_pulled(root: Path) -> bool:
    return (root / MARKER).exists()


def pull(repo_id: str, dest: Path, revision: str | None = None, force: bool = False) -> dict[str, object]:
    """Download ``repo_id`` into ``dest`` (resumable) and verify it; raise ``RuntimeError`` if any hash differs."""
    if is_pulled(dest) and not force:
        checked, problems = verify(dest)
        if not problems:
            _LOG.info("model.present", extra={"repo": repo_id, "dest": str(dest), "files_verified": checked})
            return {"repo": repo_id, "dest": str(dest), "downloaded": False, "files_verified": checked}
        _LOG.warning("model.stale", extra={"repo": repo_id, "problems": problems[:5]})
    try:
        from huggingface_hub import snapshot_download
    except ImportError as error:  # pragma: no cover - exercised only in a bare install
        raise RuntimeError("huggingface_hub is not installed; use the container, or `uv sync --extra hub`") from error
    dest.mkdir(parents=True, exist_ok=True)
    _LOG.info("model.download", extra={"repo": repo_id, "dest": str(dest), "revision": revision or "main"})
    started = time.monotonic()
    snapshot_download(repo_id=repo_id, revision=revision, local_dir=str(dest))
    checked, problems = verify(dest)
    if problems:
        raise RuntimeError(f"{repo_id}: {len(problems)} file(s) failed verification: {problems[:5]}")
    record = {"repo": repo_id, "revision": revision or "main", "files_verified": checked, "seconds": round(time.monotonic() - started, 1)}
    (dest / MARKER).write_text(json.dumps(record, indent=2) + "\n")
    _LOG.info("model.ready", extra=record)
    return {**record, "dest": str(dest), "downloaded": True}


def main(argv: list[str] | None = None) -> int:
    """``python -m wattson.models pull REPO --dest DIR`` / ``verify DIR``: the entry point of the ``wattson-hf`` image (and of ``wattson models``)."""
    import argparse

    parser = argparse.ArgumentParser(prog="wattson-hf", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    pull_parser = sub.add_parser("pull", help="download a released student and verify every file against its hash_manifest.json")
    pull_parser.add_argument("repo")
    pull_parser.add_argument("--dest", required=True, type=Path)
    pull_parser.add_argument("--revision")
    pull_parser.add_argument("--force", action="store_true")
    verify_parser = sub.add_parser("verify", help="verify a directory against its hash_manifest.json")
    verify_parser.add_argument("directory", type=Path)
    arguments = parser.parse_args(argv)
    log.configure()
    if arguments.action == "pull":
        print(json.dumps(pull(arguments.repo, arguments.dest, arguments.revision, arguments.force)))
        return 0
    checked, problems = verify(arguments.directory)
    print(f"{'FAIL' if problems else 'PASS'}: {checked} files, {len(problems)} problems {problems[:5] if problems else ''}")
    return 1 if problems else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
