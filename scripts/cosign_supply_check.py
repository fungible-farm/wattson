#!/usr/bin/env python3
"""Signed model supply: cosign (key-based) verification refuses unsigned and tampered kits (#47 acceptance).

Throwaway, loopback-only: a registry:2 container (pinned by digest) on 127.0.0.1:5001, two fixture ModelKits, a fresh cosign
key pair, and cosign run with no transparency log (offline; `--tlog-upload=false`, `--insecure-ignore-tlog`) because the
registry is local and the trust anchor is the public key, not Rekor. Asserts, in order:
  1. a kit signed with the private key verifies with the public key (exit 0);
  2. a kit that was never signed is refused (non-zero exit, "no signatures found");
  3. a tampered kit (same tag re-pushed with different weights, so a different digest) is refused, while the signed digest still
     verifies; a verifier holding a DIFFERENT public key refuses the signed kit too.
The private key lives in a scratch directory that is deleted at the end; the key password is the empty string and is passed
through the COSIGN_PASSWORD environment variable by name only. Always tears down. Needs `just kit-install` and
`just cosign-install`.
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
KIT = os.path.join(REPO, ".tools", "kit", "kit")
COSIGN = os.path.join(REPO, ".tools", "cosign", "cosign")
REGISTRY_IMAGE = "docker.io/library/registry:2@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
NAME = "wattson-cosign-registry"
PORT = 5001
REF = f"localhost:{PORT}/wattson/fixture"
scratch = None


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fail(msg):
    print(f"FAIL cosign-supply: {msg}")
    sys.exit(1)


def make_fixture(directory, seed):
    os.makedirs(directory)
    with open(os.path.join(directory, "weights.bin"), "wb") as f:
        f.write(hashlib.sha256(seed.encode()).digest() * 8192)
    with open(os.path.join(directory, "Kitfile"), "w") as f:
        f.write("manifestVersion: 1.0.0\npackage:\n  name: fixture\n  version: 1.0.0\nmodel:\n  name: fixture-model\n  path: weights.bin\n")


def kit(*args):
    return run([KIT, *args], env={**os.environ, "HOME": os.path.join(scratch, "home")})


def pack_push(directory):
    packed = kit("pack", directory, "-t", f"{REF}:v1")
    pushed = kit("push", "--plain-http", f"{REF}:v1")
    m = re.search(r"Pushed (sha256:[0-9a-f]{64})", pushed.stdout + pushed.stderr)
    if packed.returncode or pushed.returncode or not m:
        fail(f"pack/push failed: {(packed.stdout + packed.stderr + pushed.stdout + pushed.stderr)[-300:]}")
    return m.group(1)


def cosign(*args, cwd=None):
    env = {**os.environ, "HOME": os.path.join(scratch, "home"), "COSIGN_PASSWORD": ""}
    return run([COSIGN, *args], env=env, cwd=cwd or scratch)


def sign(key, digest):
    return cosign("sign", "--key", key, "--allow-insecure-registry", "--use-signing-config=false", "--tlog-upload=false",
                  "--yes", f"{REF}@{digest}")


def verify(pub, digest):
    return cosign("verify", "--key", pub, "--allow-insecure-registry", "--insecure-ignore-tlog=true", f"{REF}@{digest}")


def main():
    global scratch
    for tool in (KIT, COSIGN):
        if not os.path.exists(tool):
            fail(f"{os.path.basename(tool)} missing; run `just kit-install` and `just cosign-install` first")
    scratch = tempfile.mkdtemp(prefix="cs-", dir="/tmp")
    os.makedirs(os.path.join(scratch, "home"))
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

    for sub in ("trusted", "other"):
        os.makedirs(os.path.join(scratch, sub))
        gen = cosign("generate-key-pair", cwd=os.path.join(scratch, sub))
        if gen.returncode:
            fail(f"key generation failed: {gen.stderr[-300:]}")
    key, pub = os.path.join(scratch, "trusted", "cosign.key"), os.path.join(scratch, "trusted", "cosign.pub")
    other_pub = os.path.join(scratch, "other", "cosign.pub")

    good = os.path.join(scratch, "good")
    make_fixture(good, "good")
    digest = pack_push(good)
    signed = sign(key, digest)
    if signed.returncode:
        fail(f"signing failed: {signed.stderr[-300:]}")
    ok = verify(pub, digest)
    print(f"signed-verifies: rc={ok.returncode} digest={digest[:19]}...")
    if ok.returncode != 0:
        fail(f"a signed kit did not verify: {ok.stderr[-300:]}")

    evil = os.path.join(scratch, "evil")
    make_fixture(evil, "evil")
    evil_digest = pack_push(evil)  # same tag :v1, different content, never signed
    if evil_digest == digest:
        fail("the tamper fixture produced the same digest")
    refused = verify(pub, evil_digest)
    unsigned_msg = (refused.stdout + refused.stderr)
    print(f"unsigned-or-tampered: rc={refused.returncode} no_signatures_found={'no signatures found' in unsigned_msg}")
    if refused.returncode == 0:
        fail("an unsigned/tampered kit verified")
    still = verify(pub, digest)
    print(f"signed-digest-after-tag-overwrite: rc={still.returncode}")
    if still.returncode != 0:
        fail("the signed digest stopped verifying after the tag was overwritten")
    wrong = verify(other_pub, digest)
    print(f"wrong-public-key: rc={wrong.returncode}")
    if wrong.returncode == 0:
        fail("the signed kit verified under a different public key")
    print("PASS cosign-supply: signed kit verifies; unsigned/tampered kit and a wrong key are refused; "
          "an overwritten tag does not affect the signed digest")


if __name__ == "__main__":
    try:
        main()
    finally:
        run(["podman", "rm", "-f", NAME])
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
