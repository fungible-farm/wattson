#!/usr/bin/env python3
"""Download, verify, and install the kit CLI binary.

Usage:
    python scripts/install_kit.py [--version VERSION] [--dest DIR] [--arch ARCH] [--base-url URL]

Integrity: the tarball is checked against the upstream checksums file published next to it. That detects a corrupted or
swapped download; it is NOT a signature check (both files come from the same host).

Defaults:
    version: 1.16.0
    dest:    <repo root>/.tools/kit (the directory that contains scripts/)
    arch:    auto-detected from platform.machine()
    base-url: https://github.com/kitops-ml/kitops/releases/download
"""

import argparse
import hashlib
import os
import platform
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import urllib.error


def detect_arch():
    """Auto-detect architecture from platform.machine()."""
    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    print(f"ERROR: unsupported architecture: {machine}", file=sys.stderr)
    sys.exit(2)


def default_dest():
    """<repo root>/.tools/kit, where the repo root is the directory that contains scripts/."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(script_dir), ".tools", "kit")


def sha256_file(path):
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def download(url, dest_path):
    """Download a URL to a local path."""
    print(f"downloading {url}")
    try:
        urllib.request.urlretrieve(url, dest_path)
    except urllib.error.HTTPError as e:
        print(f"ERROR: failed to download {url}: HTTP {e.code}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: failed to download {url}: {e.reason}", file=sys.stderr)
        sys.exit(1)


def parse_checksums(checksums_path):
    """Parse a checksums file into a dict of {filename: sha256}."""
    checksums = {}
    with open(checksums_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                checksums[parts[1]] = parts[0]
    return checksums


def main():
    parser = argparse.ArgumentParser(description="Install the kit CLI binary")
    parser.add_argument("--version", default="1.16.0", help="kit version (default: 1.16.0)")
    parser.add_argument("--dest", default=None, help="install destination directory")
    parser.add_argument("--arch", default="auto", help="architecture: auto, arm64, x86_64")
    parser.add_argument("--base-url", default="https://github.com/kitops-ml/kitops/releases/download",
                        help="base URL for downloads")
    args = parser.parse_args()

    # Determine architecture
    if args.arch == "auto":
        arch = detect_arch()
    else:
        arch = args.arch
        if arch not in ("arm64", "x86_64"):
            print(f"ERROR: unsupported architecture: {arch}", file=sys.stderr)
            sys.exit(2)

    # Determine destination
    if args.dest is None:
        args.dest = default_dest()

    version = args.version
    base_url = args.base_url.rstrip("/")

    # Check if already installed with correct version
    kit_path = os.path.join(args.dest, "kit")
    if os.path.exists(kit_path):
        try:
            result = subprocess.run([kit_path, "version"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and version in result.stdout:
                print(f"already installed {kit_path} version={version}")
                return 0
        except (subprocess.TimeoutExpired, OSError):
            pass  # fall through to re-install

    # Prepare download paths
    tarball_name = f"kitops-linux-{arch}.tar.gz"
    checksums_name = f"kitops_v{version}_checksums.txt"
    tarball_url = f"{base_url}/v{version}/{tarball_name}"
    checksums_url = f"{base_url}/v{version}/{checksums_name}"

    with tempfile.TemporaryDirectory() as tmpdir:
        tarball_path = os.path.join(tmpdir, tarball_name)
        checksums_path = os.path.join(tmpdir, checksums_name)

        # Download checksums file
        download(checksums_url, checksums_path)

        # Download tarball
        download(tarball_url, tarball_path)

        # Verify checksum
        checksums = parse_checksums(checksums_path)
        if tarball_name not in checksums:
            print(f"ERROR checksum: {tarball_name} not found in checksums file", file=sys.stderr)
            sys.exit(1)

        expected = checksums[tarball_name].lower()
        actual = sha256_file(tarball_path)
        if actual != expected:
            print(f"ERROR checksum: {tarball_name} expected {expected} got {actual}", file=sys.stderr)
            sys.exit(1)

        # Check all tar members for path traversal before extracting
        with tarfile.open(tarball_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    print(f"ERROR: unsafe tar member path: {member.name}", file=sys.stderr)
                    sys.exit(1)

            # Find the kit binary member: exactly 'kit' or './kit', and a regular file (never a symlink or hard link)
            kit_member = None
            for member in tar.getmembers():
                if member.name in ("kit", "./kit"):
                    kit_member = member
                    break

            if kit_member is None:
                print("ERROR: 'kit' binary not found in tarball", file=sys.stderr)
                sys.exit(1)
            if not kit_member.isreg():
                print(f"ERROR: tar member {kit_member.name!r} is not a regular file (link or special file refused)", file=sys.stderr)
                sys.exit(1)

            # Create destination directory
            os.makedirs(args.dest, exist_ok=True)

            # Extract only the kit binary
            tar.extract(kit_member, path=args.dest)

        # Make executable
        os.chmod(kit_path, 0o755)

    # Verify installation
    try:
        result = subprocess.run([kit_path, "version"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            print(f"ERROR: installed kit binary failed to run: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        version_output = result.stdout.strip().split("\n")[0]
        print(f"installed {kit_path} version={version_output}")
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"ERROR: failed to verify installed kit: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
