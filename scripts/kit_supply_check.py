#!/usr/bin/env python3
"""Model supply from a digest-pinned ModelKit, as an init container would do it (#48; also the #47 tamper demo).

Throwaway, loopback-only: a registry:2 container (pinned by digest) on 127.0.0.1:5001, a fixture ModelKit with a
1 MiB weights file, and an "init container" that is `kit unpack` run inside a pinned Alpine with the repo's kit
binary mounted read-only. Asserts:
  1. unpack by digest succeeds and the unpacked weights hash equals the original (hash recorded for the run manifest);
  2. a wrong digest is refused (non-zero exit, nothing unpacked);
  3. after the TAG is overwritten with different weights, unpack by the old digest still yields the original bytes
     (a moved tag cannot change what a digest-pinned pod runs).
Always tears down. Needs `just kit-install` first (.tools/kit/kit) and podman.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT_DIR = os.path.join(REPO, ".tools", "kit")
KIT = os.path.join(KIT_DIR, "kit")
REGISTRY_IMAGE = "docker.io/library/registry:2@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
BASE_IMAGE = "docker.io/library/alpine:3.22@sha256:bfe7ceb9f09ec0566c48fb93356e45a47a00be5a1dd14fa5b909e7709c66b034"
NAME = "wattson-kit-registry"
PORT = 5001
HOST_REF = f"localhost:{PORT}/wattson/fixture"          # how the host reaches the registry
POD_REF = f"169.254.1.2:{PORT}/wattson/fixture"         # how a container reaches it (pasta host-loopback map)
scratch = None


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fail(msg):
    print(f"FAIL kit-supply: {msg}")
    sys.exit(1)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def make_fixture(directory, seed):
    os.makedirs(directory)
    with open(os.path.join(directory, "weights.bin"), "wb") as f:
        f.write(hashlib.sha256(seed.encode()).digest() * 32768)  # 1 MiB, deterministic
    with open(os.path.join(directory, "Kitfile"), "w") as f:
        f.write("manifestVersion: 1.0.0\npackage:\n  name: fixture\n  version: 1.0.0\nmodel:\n  name: fixture-model\n  path: weights.bin\n")


def kit(*args):
    return run([KIT, *args], env={**os.environ, "HOME": os.path.join(scratch, "home")})


def pack_push(directory):
    packed = kit("pack", directory, "-t", f"{HOST_REF}:v1")
    pushed = kit("push", "--plain-http", f"{HOST_REF}:v1")
    m = re.search(r"Pushed (sha256:[0-9a-f]{64})", pushed.stdout + pushed.stderr)
    if packed.returncode or pushed.returncode or not m:
        fail(f"pack/push failed: {(packed.stdout + packed.stderr + pushed.stdout + pushed.stderr)[-400:]}")
    return m.group(1)


def init_container(ref_suffix, dest):
    """The init container: kit unpack inside pinned Alpine, kit binary mounted read-only, models volume writable."""
    return run(["podman", "run", "--rm", "--pull=missing", "--network=pasta:--map-host-loopback,169.254.1.2",
                "-e", "HOME=/tmp/h", "-v", f"{KIT_DIR}:/kit:ro,z", "-v", f"{scratch}/models:/models:z",
                BASE_IMAGE, "/kit/kit", "unpack", "--plain-http", "-d", f"/models/{dest}", f"{POD_REF}{ref_suffix}"])


def main():
    global scratch
    if not os.path.exists(KIT):
        fail("kit CLI missing; run `just kit-install` first")
    scratch = tempfile.mkdtemp(prefix="ks-", dir="/tmp")
    os.chmod(scratch, 0o755)
    os.makedirs(os.path.join(scratch, "home"))
    os.makedirs(os.path.join(scratch, "models"), mode=0o777)
    os.chmod(os.path.join(scratch, "models"), 0o777)
    run(["podman", "rm", "-f", NAME])
    started = run(["podman", "run", "-d", "--name", NAME, "--pull=missing", "-p", f"127.0.0.1:{PORT}:5000", REGISTRY_IMAGE])
    if started.returncode:
        fail(f"registry did not start: {started.stderr[-300:]}")
    for _ in range(40):
        if run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://127.0.0.1:{PORT}/v2/"]).stdout.strip() == "200":
            break
        time.sleep(0.5)
    else:
        fail("registry did not answer 200 on /v2/")

    original = os.path.join(scratch, "original")
    make_fixture(original, "original")
    want = sha256(os.path.join(original, "weights.bin"))
    digest = pack_push(original)
    print(f"published: {HOST_REF}@{digest}")

    ok = init_container(f"@{digest}", "ok")
    got_path = os.path.join(scratch, "models", "ok", "weights.bin")
    if ok.returncode or not os.path.exists(got_path):
        fail(f"unpack by digest failed: {(ok.stdout + ok.stderr)[-400:]}")
    got = sha256(got_path)
    print(f"unpack-by-digest: rc={ok.returncode} weights_sha256={got}")
    if got != want:
        fail(f"unpacked weights differ from the original ({got} != {want})")

    bad = init_container("@sha256:" + "0" * 64, "bad")
    print(f"wrong-digest: rc={bad.returncode} unpacked={os.path.exists(os.path.join(scratch, 'models', 'bad', 'weights.bin'))}")
    if bad.returncode == 0 or os.path.exists(os.path.join(scratch, "models", "bad", "weights.bin")):
        fail("a wrong digest was not refused")

    tampered = os.path.join(scratch, "tampered")
    make_fixture(tampered, "tampered")
    new_digest = pack_push(tampered)  # same tag :v1, different content
    print(f"tag-overwritten: {HOST_REF}:v1 now -> {new_digest}")
    if new_digest == digest:
        fail("tamper fixture produced the same digest")
    pinned = init_container(f"@{digest}", "pinned")
    pinned_path = os.path.join(scratch, "models", "pinned", "weights.bin")
    pinned_sha = sha256(pinned_path) if os.path.exists(pinned_path) else "none"
    print(f"pinned-after-tamper: rc={pinned.returncode} weights_sha256={pinned_sha}")
    if pinned.returncode or pinned_sha != want:
        fail("digest-pinned unpack changed after the tag was overwritten")
    print("PASS kit-supply: digest-pinned unpack matches the original; wrong digest refused; "
          "an overwritten tag does not change what a digest-pinned pod receives")


if __name__ == "__main__":
    try:
        main()
    finally:
        run(["podman", "rm", "-f", NAME])
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
