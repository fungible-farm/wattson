#!/usr/bin/env python3
"""The pod spec renders both ways and keeps exactly one pull-model init container (#48).

Runs `just pod-render` (nothing is started) in the two modes and parses the YAML it wrote:
  * Hugging Face mode (no WATTSON_MODEL_KIT): pull-model uses the wattson-hf image and no volume mounts a public key;
  * kit mode (digest-pinned WATTSON_MODEL_KIT + a readable public key): pull-model uses the wattson-kit image, the digest-pinned
    ref is its argument, the key is mounted read-only at /keys/cosign.pub from the file you named;
and that kit mode REFUSES a tag, and refuses a missing public key, with exit code 2.
"""
import os
import subprocess
import sys
import tempfile

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIGEST = "sha256:" + "ab" * 32


def render(out, env_extra, model="qwen"):
    env = {k: v for k, v in os.environ.items() if not k.startswith("WATTSON_MODEL_KIT") and k != "WATTSON_COSIGN_PUBKEY"}
    env.update(env_extra)
    return subprocess.run(["just", "pod-render", model, out], cwd=REPO, env=env, capture_output=True, text=True)


def pull_models(path):
    pod = yaml.safe_load(open(path))
    inits = [c for c in pod["spec"]["initContainers"] if c["name"] == "pull-model"]
    return pod, inits


def fail(msg):
    print(f"FAIL pod-render: {msg}")
    sys.exit(1)


def main():
    with tempfile.TemporaryDirectory(prefix="pr-") as d:
        pub = os.path.join(d, "cosign.pub")
        open(pub, "w").write("-----BEGIN PUBLIC KEY-----\nfixture\n-----END PUBLIC KEY-----\n")
        hf = os.path.join(d, "hf.yaml")
        r = render(hf, {})
        pod, inits = pull_models(hf) if r.returncode == 0 else (None, [])
        hf_ok = (r.returncode == 0 and len(inits) == 1 and "wattson-hf" in inits[0]["image"]
                 and not any(v["name"] == "cosign-pub" for v in pod["spec"]["volumes"]))
        print(f"hugging-face mode: rc={r.returncode} pull_model_blocks={len(inits)} image={inits[0]['image'] if inits else None}")
        if not hf_ok:
            fail(f"Hugging Face mode did not render one wattson-hf pull-model: {r.stderr[-300:]}")

        kit = os.path.join(d, "kit.yaml")
        ref = f"ghcr.io/fungible-farm/model@{DIGEST}"
        r = render(kit, {"WATTSON_MODEL_KIT": ref, "WATTSON_COSIGN_PUBKEY": pub})
        pod, inits = pull_models(kit) if r.returncode == 0 else (None, [])
        vols = {v["name"]: v for v in pod["spec"]["volumes"]} if pod else {}
        mounts = {m["name"]: m for m in inits[0]["volumeMounts"]} if inits else {}
        kit_ok = (r.returncode == 0 and len(inits) == 1 and "wattson-kit" in inits[0]["image"]
                  and inits[0]["args"][:2] == ["pull", ref]
                  and vols.get("cosign-pub", {}).get("hostPath", {}).get("path") == pub
                  and mounts.get("cosign-pub", {}).get("mountPath") == "/keys/cosign.pub" and mounts["cosign-pub"].get("readOnly"))
        print(f"kit mode: rc={r.returncode} pull_model_blocks={len(inits)} image={inits[0]['image'] if inits else None} "
              f"ref_pinned={bool(inits) and inits[0]['args'][1] == ref} key_mounted_ro={bool(mounts.get('cosign-pub', {}).get('readOnly'))}")
        if not kit_ok:
            fail(f"kit mode did not render one wattson-kit pull-model with the key mounted read-only: {r.stderr[-300:]}")

        tag = render(os.path.join(d, "tag.yaml"), {"WATTSON_MODEL_KIT": "ghcr.io/fungible-farm/model:v1", "WATTSON_COSIGN_PUBKEY": pub})
        nokey = render(os.path.join(d, "nokey.yaml"), {"WATTSON_MODEL_KIT": ref})
        print(f"refusals: tag rc={tag.returncode} missing_key rc={nokey.returncode}")
        if tag.returncode != 2 or nokey.returncode != 2:
            fail("a tag or a missing public key was not refused with exit 2")
    print("PASS pod-render: one pull-model either way; kit mode pins the digest and mounts the key read-only; tag and missing key refused")


if __name__ == "__main__":
    main()
