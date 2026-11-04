"""Acquire pinned build artifacts in the builder, never inside task execution.

Release archive hashes below are the sha256 digests returned by GitHub's release
API on 2026-11-04. A mismatch fails immediately; no alternative mirror is tried.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import tarfile
import urllib.request

# Immutable NEMO source snapshot inspected through the GitHub connector.
NEMO_COMMIT: str = "0251ee41cf806915905ad43934520d8d258d8810"
# v0.2.0; local source fixes are recorded separately in the image provenance.
JUST_MCP_COMMIT: str = "11a2a7c47a0598ee21769ecf515db563bba96f0b"
# Operational limit for build-time downloads; execution remains offline.
DOWNLOAD_TIMEOUT_SECONDS: int = 180
RELEASES: dict[str, dict[str, tuple[str, str]]] = {
    "just-mcp": {
        "aarch64": ("https://github.com/toolprint/just-mcp/releases/download/v0.2.0/just-mcp-v0.2.0-aarch64-unknown-linux-gnu.tar.gz", "ef5bd3d5999da088ee87ce9a39d4c881dac216236271564d9d536c69edf54488"),
        "x86_64": ("https://github.com/toolprint/just-mcp/releases/download/v0.2.0/just-mcp-v0.2.0-x86_64-unknown-linux-gnu.tar.gz", "4c90cac9ddd8490678f78e7469eef1ca06f4de3edeb094c3b48f22f339d08b23"),
    },
    "mistralrs": {
        "aarch64": ("https://github.com/EricLBuehler/mistral.rs/releases/download/v0.9.5/mistralrs-cpu-aarch64-unknown-linux-gnu.tar.gz", "760f10bf201a1b26ce6b1caa6cd9f09b091f7ba3252af40493975ab50312ec1a"),
        "x86_64": ("https://github.com/EricLBuehler/mistral.rs/releases/download/v0.9.5/mistralrs-cpu-x86_64-unknown-linux-gnu.tar.gz", "9b542da90b6541abd599ff275e3246fa6e144885bdcd67d1f0aefdba1729f7b5"),
    },
}


def download(url: str) -> bytes:
    """Fetch a public build artifact once and propagate network/HTTP failures."""
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        return response.read()


def acquire(artifact: str, destination: Path) -> None:
    """Unpack a pinned upstream source or checksum-verified release executable."""
    destination.mkdir(parents=True, exist_ok=True)
    if artifact in {"nemo", "just-mcp-source"}:
        repository, commit = ("bje-/NEMO", NEMO_COMMIT) if artifact == "nemo" else ("toolprint/just-mcp", JUST_MCP_COMMIT)
        url = f"https://codeload.github.com/{repository}/tar.gz/{commit}"
        archive = download(url)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(destination, filter="data")
        unpacked = next(path for path in destination.iterdir() if path.is_dir())
        for item in unpacked.iterdir():
            item.rename(destination / item.name)
        unpacked.rmdir()
        provenance = {"commit": commit, "source_url": url, "archive_sha256": hashlib.sha256(archive).hexdigest()}
    else:
        url, expected = RELEASES[artifact][platform.machine()]
        archive = download(url)
        actual = hashlib.sha256(archive).hexdigest()
        if actual != expected:
            raise ValueError(f"{artifact} release checksum mismatch")
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            candidates = [member for member in source.getmembers() if member.isfile() and Path(member.name).name == artifact]
            if len(candidates) != 1:
                raise ValueError(f"expected one executable named {artifact}")
            stream = source.extractfile(candidates[0])
            assert stream is not None
            executable = destination / artifact
            executable.write_bytes(stream.read())
            executable.chmod(0o755)
        provenance = {"url": url, "archive_sha256": actual, "architecture": platform.machine()}
    (destination / f"{artifact}-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", choices=["nemo", "just-mcp-source", *RELEASES])
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    acquire(args.artifact, args.destination)
