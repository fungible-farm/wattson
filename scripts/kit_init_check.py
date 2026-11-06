#!/usr/bin/env python3
"""The model-supply init container end to end (#48): wattson-kit pulls a signed, digest-pinned ModelKit into the models volume.

Uses the real image (`just kit-image-build`, localhost/wattson-kit:local), a throwaway registry:2 on 127.0.0.1:5001 and fixture
kits signed with a fresh cosign key (the same machinery as `just cosign-supply-check`, imported from it). The init container runs
the way the pod runs it: a named volume at /models, the public key mounted read-only, the registry reached through the pasta
host-loopback map. Asserted:
  1. signed digest      -> exit 0, weights present with the original sha256, `.model-digest` equals the digest;
  2. unsigned digest    -> exit 1 and nothing unpacked (a tampered tag cannot get in);
  3. a tag, not digest  -> exit 2 (tags are mutable);
  4. wrong public key   -> exit 1 and nothing unpacked.
Always tears down (registry, volume, scratch). Needs `just kit-install`, `just cosign-install`, `just kit-image-build`.
"""
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.environ.get("WATTSON_KIT_IMAGE", "localhost/wattson-kit:local")
VOLUME = "wattson-kit-init-check"
POD_REF = "169.254.1.2:5001/wattson/fixture"


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "scripts", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CS = None


def main():
    cs = CS
    for tool in (cs.KIT, cs.COSIGN):
        if not os.path.exists(tool):
            cs.fail(f"{os.path.basename(tool)} missing; run `just kit-install` and `just cosign-install` first")
    if cs.run(["podman", "image", "exists", IMAGE]).returncode:
        cs.fail(f"{IMAGE} missing; run `just kit-image-build` first")
    cs.scratch = tempfile.mkdtemp(prefix="ki-", dir="/tmp")
    os.chmod(cs.scratch, 0o755)
    os.makedirs(os.path.join(cs.scratch, "home"))
    cs.run(["podman", "rm", "-f", cs.NAME])
    cs.run(["podman", "volume", "rm", "-f", VOLUME])
    if cs.run(["podman", "run", "-d", "--name", cs.NAME, "--pull=missing", "-p", f"127.0.0.1:{cs.PORT}:5000",
               cs.REGISTRY_IMAGE]).returncode:
        cs.fail("registry did not start")
    for _ in range(40):
        if cs.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://127.0.0.1:{cs.PORT}/v2/"]).stdout.strip() == "200":
            break
        time.sleep(0.5)
    else:
        cs.fail("registry did not answer 200")
    for sub in ("trusted", "other"):
        os.makedirs(os.path.join(cs.scratch, sub))
        if cs.cosign("generate-key-pair", cwd=os.path.join(cs.scratch, sub)).returncode:
            cs.fail("key generation failed")
    key = os.path.join(cs.scratch, "trusted", "cosign.key")
    pub = os.path.join(cs.scratch, "trusted", "cosign.pub")
    other_pub = os.path.join(cs.scratch, "other", "cosign.pub")
    for path in (pub, other_pub):
        os.chmod(path, 0o644)

    good = os.path.join(cs.scratch, "good")
    cs.make_fixture(good, "good")
    want = hashlib.sha256(open(os.path.join(good, "weights.bin"), "rb").read()).hexdigest()
    digest = cs.pack_push(good)
    if cs.sign(key, digest).returncode:
        cs.fail("signing failed")
    evil = os.path.join(cs.scratch, "evil")
    cs.make_fixture(evil, "evil")
    evil_digest = cs.pack_push(evil)  # same tag, other content, never signed

    def init(ref, dest, pubkey):
        """Run the init container as the pod does; return (rc, stderr tail)."""
        out = cs.run(["podman", "run", "--rm", "--network=pasta:--map-host-loopback,169.254.1.2", "--userns=keep-id:uid=1000,gid=1000",
                      "-v", f"{VOLUME}:/models", "-v", f"{pubkey}:/keys/cosign.pub:ro,z",
                      "-e", "WATTSON_COSIGN_PUBKEY=/keys/cosign.pub", "-e", "WATTSON_KIT_INSECURE=1",
                      IMAGE, "pull", ref, "--dest", f"/models/{dest}"])
        return out.returncode, (out.stdout + out.stderr)[-300:]

    def present(dest, name):
        out = cs.run(["podman", "run", "--rm", "-v", f"{VOLUME}:/models", "--entrypoint", "sh", IMAGE, "-c",
                      f"cat /models/{dest}/{name} 2>/dev/null | sha256sum | cut -d' ' -f1; test -s /models/{dest}/{name} && echo yes || echo no"])
        lines = out.stdout.split()
        return lines[-1] == "yes", lines[0] if lines else ""

    rc, tail = init(f"{POD_REF}@{digest}", "ok", pub)
    weights_ok, got = present("ok", "weights.bin")
    digest_file = cs.run(["podman", "run", "--rm", "-v", f"{VOLUME}:/models", "--entrypoint", "cat", IMAGE, "/models/ok/.model-digest"]).stdout.strip()
    print(f"signed-digest: rc={rc} weights_present={weights_ok} sha256_matches={got == want} model_digest_file={digest_file == digest}")
    if rc != 0 or not weights_ok or got != want or digest_file != digest:
        cs.fail(f"the signed digest did not unpack correctly: {tail}")

    rc, tail = init(f"{POD_REF}@{evil_digest}", "evil", pub)
    print(f"unsigned-digest: rc={rc} unpacked={present('evil', 'weights.bin')[0]}")
    if rc != 1 or present("evil", "weights.bin")[0]:
        cs.fail(f"an unsigned/tampered digest was not refused with exit 1: rc={rc} {tail}")

    rc, tail = init(f"{POD_REF}:v1", "tag", pub)
    print(f"tag-not-digest: rc={rc}")
    if rc != 2:
        cs.fail(f"a tag was not refused with exit 2: rc={rc} {tail}")

    rc, tail = init(f"{POD_REF}@{digest}", "wrongkey", other_pub)
    print(f"wrong-public-key: rc={rc} unpacked={present('wrongkey', 'weights.bin')[0]}")
    if rc != 1 or present("wrongkey", "weights.bin")[0]:
        cs.fail(f"a wrong public key was not refused with exit 1: rc={rc} {tail}")
    print("PASS kit-init: the init container unpacked the signed digest (hash and digest recorded) and refused an unsigned "
          "digest, a tag and a wrong key")


if __name__ == "__main__":
    CS = mod = load("cosign_supply_check")
    try:
        main()
    finally:
        mod.run(["podman", "rm", "-f", mod.NAME])
        mod.run(["podman", "volume", "rm", "-f", VOLUME])
        if getattr(mod, "scratch", None):
            shutil.rmtree(mod.scratch, ignore_errors=True)
