"""CI-safe tests for scripts/install_kit.py.

Uses a local HTTP server thread to serve a fake release tarball and checksums file.
No network, no real kit binary required.
"""

import hashlib
import http.server
import os
import platform
import socket
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest

PY = sys.executable
SCRIPT = Path(__file__).parent.parent / "scripts" / "install_kit.py"

# Skip on Windows
if platform.system() == "Windows":
    pytest.skip("install_kit.py tests are not supported on Windows", allow_module_level=True)


def make_kit_tarball(tmp_path, version="1.16.0"):
    """Create a fake kit tarball with a shell script that prints a version string."""
    release_dir = tmp_path / "release"
    release_dir.mkdir(exist_ok=True)
    kit_script = release_dir / "kit"
    kit_script.write_text(f"#!/bin/sh\necho \"Version: {version}\"\n")
    kit_script.chmod(0o755)

    tarball_path = release_dir / f"kitops-linux-arm64.tar.gz"
    with tarfile.open(tarball_path, "w:gz") as tar:
        tar.add(kit_script, arcname="kit")

    return tarball_path


def make_checksums_file(tmp_path, tarball_path, version="1.16.0"):
    """Create a fake checksums file matching the tarball."""
    tarball_name = tarball_path.name
    sha = hashlib.sha256()
    with open(tarball_path, "rb") as f:
        sha.update(f.read())
    sha_hex = sha.hexdigest()

    release_dir = tarball_path.parent
    checksums_path = release_dir / f"kitops_v{version}_checksums.txt"
    checksums_path.write_text(f"{sha_hex}  {tarball_name}\n")
    return checksums_path


def find_free_port():
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


class FakeReleaseHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that serves fake release files."""

    files = {}
    request_count = 0

    def log_message(self, format, *args):
        pass  # Suppress log output

    def do_GET(self):
        FakeReleaseHandler.request_count += 1
        path = self.path.lstrip("/")
        if path in FakeReleaseHandler.files:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(FakeReleaseHandler.files[path])
        else:
            self.send_response(404)
            self.end_headers()


def start_fake_server(files_dict):
    """Start a fake HTTP server and return (server, port)."""
    port = find_free_port()
    FakeReleaseHandler.files = files_dict
    FakeReleaseHandler.request_count = 0

    server = http.server.HTTPServer(("127.0.0.1", port), FakeReleaseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def run_install(args, dest):
    """Run install_kit.py with the given args."""
    cmd = [PY, str(SCRIPT), "--dest", str(dest)] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result


@pytest.fixture
def fake_release(tmp_path):
    """Set up a fake release with tarball and checksums."""
    version = "1.16.0"
    tarball = make_kit_tarball(tmp_path, version)
    checksums = make_checksums_file(tmp_path, tarball, version)

    files = {
        f"v{version}/kitops-linux-arm64.tar.gz": tarball.read_bytes(),
        f"v{version}/kitops_v{version}_checksums.txt": checksums.read_bytes(),
    }
    server, port = start_fake_server(files)
    return server, port, version


def test_install_success(fake_release, tmp_path):
    """Test successful installation."""
    server, port, version = fake_release
    dest = tmp_path / "kit"

    result = run_install(
        ["--version", version, "--base-url", f"http://127.0.0.1:{port}", "--arch", "arm64"],
        dest
    )

    assert result.returncode == 0, f"install failed: {result.stderr}"
    assert "installed" in result.stdout
    assert "Version: 1.16.0" in result.stdout
    assert (dest / "kit").exists()
    assert FakeReleaseHandler.request_count == 2  # checksums + tarball

    server.shutdown()


def test_install_already_installed(fake_release, tmp_path):
    """Test that second run detects already installed."""
    server, port, version = fake_release
    dest = tmp_path / "kit"

    # First install
    result1 = run_install(
        ["--version", version, "--base-url", f"http://127.0.0.1:{port}", "--arch", "arm64"],
        dest
    )
    assert result1.returncode == 0

    # Second run should detect already installed
    result2 = run_install(
        ["--version", version, "--base-url", f"http://127.0.0.1:{port}", "--arch", "arm64"],
        dest
    )
    assert result2.returncode == 0
    assert "already installed" in result2.stdout
    # No additional downloads on second run
    assert FakeReleaseHandler.request_count == 2

    server.shutdown()


def test_install_checksum_mismatch(tmp_path):
    """Test that a wrong checksum causes failure."""
    version = "1.16.0"
    tarball = make_kit_tarball(tmp_path, version)

    # Create checksums file with wrong hash
    checksums_path = tmp_path / f"kitops_v{version}_checksums.txt"
    checksums_path.write_text(f"{'a' * 64}  kitops-linux-arm64.tar.gz\n")

    files = {
        f"v{version}/kitops-linux-arm64.tar.gz": tarball.read_bytes(),
        f"v{version}/kitops_v{version}_checksums.txt": checksums_path.read_bytes(),
    }
    server, port = start_fake_server(files)
    dest = tmp_path / "kit"

    result = run_install(
        ["--version", version, "--base-url", f"http://127.0.0.1:{port}", "--arch", "arm64"],
        dest
    )

    assert result.returncode == 1
    assert "ERROR checksum" in result.stderr
    assert not (dest / "kit").exists()

    server.shutdown()


def test_install_tar_traversal(tmp_path):
    """Test that tar members with '..' are rejected."""
    version = "1.16.0"

    # Create tarball with a '..' member
    kit_script = tmp_path / "kit"
    kit_script.write_text(f"#!/bin/sh\necho \"Version: {version}\"\n")
    kit_script.chmod(0o755)

    tarball_path = tmp_path / f"kitops-linux-arm64.tar.gz"
    with tarfile.open(tarball_path, "w:gz") as tar:
        tar.add(kit_script, arcname="../evil")

    checksums = make_checksums_file(tmp_path, tarball_path, version)

    files = {
        f"v{version}/kitops-linux-arm64.tar.gz": tarball_path.read_bytes(),
        f"v{version}/kitops_v{version}_checksums.txt": checksums.read_bytes(),
    }
    server, port = start_fake_server(files)
    dest = tmp_path / "kit"

    result = run_install(
        ["--version", version, "--base-url", f"http://127.0.0.1:{port}", "--arch", "arm64"],
        dest
    )

    assert result.returncode == 1
    assert "unsafe tar member" in result.stderr
    assert not (dest / "kit").exists()

    server.shutdown()


def test_install_unsupported_arch(fake_release, tmp_path):
    """Test that unsupported architecture causes exit 2."""
    server, port, version = fake_release
    dest = tmp_path / "kit"

    result = run_install(
        ["--version", version, "--base-url", f"http://127.0.0.1:{port}", "--arch", "mips"],
        dest
    )

    assert result.returncode == 2
    assert "unsupported architecture" in result.stderr

    server.shutdown()


def test_default_destination_is_inside_the_repo():
    """Regression: the default used to resolve to the repo's PARENT directory."""
    import importlib.util
    script = Path(__file__).resolve().parents[1] / "scripts" / "install_kit.py"
    spec = importlib.util.spec_from_file_location("install_kit", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    repo = Path(__file__).resolve().parents[1]
    assert Path(mod.default_dest()).resolve() == (repo / ".tools" / "kit").resolve()


def test_install_refuses_a_symlink_named_kit(tmp_path):
    """A tar member called 'kit' that is a symlink must be refused (only a regular file is installed)."""
    version = "1.16.0"
    tarball_path = tmp_path / "kitops-linux-arm64.tar.gz"
    with tarfile.open(tarball_path, "w:gz") as tar:
        info = tarfile.TarInfo("kit")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/hostname"
        tar.addfile(info)

    checksums = make_checksums_file(tmp_path, tarball_path, version)
    files = {
        f"v{version}/kitops-linux-arm64.tar.gz": tarball_path.read_bytes(),
        f"v{version}/kitops_v{version}_checksums.txt": checksums.read_bytes(),
    }
    server, port = start_fake_server(files)
    dest = tmp_path / "kit"
    try:
        result = run_install(["--version", version, "--base-url", f"http://127.0.0.1:{port}", "--arch", "arm64"], dest)
    finally:
        server.shutdown()
    assert result.returncode == 1
    assert "not a regular file" in result.stderr
    assert not (dest / "kit").exists()
