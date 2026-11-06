#!/usr/bin/env python3
"""Import a released student from Hugging Face as a ModelKit and prove the bytes match the originals (#48).

`kit import <repo> --ref <commit>` packages the repo as a ModelKit in a private kit store; `kit unpack` writes it
back out; every unpacked file is then hashed and compared with the Hugging Face original:
  * LFS files: the sha256 `oid` the Hub's tree API reports (independent of kit);
  * small non-LFS files: a fresh download of `resolve/<commit>/<path>`, hashed here.
The repo is pinned to the commit the Hub reports at the start of the run, which is printed so the run can be repeated.
No host-wide state: kit gets its own --config directory under a scratch dir that is deleted at the end.
Usage: python3 scripts/kit_import_check.py [REPO]     (default PromptExecutionAU/wattson-qwen3.5-4b-pyt, about 9 GB)
Needs `just kit-install` first. Network: huggingface.co only.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(REPO_DIR, ".tools", "kit", "kit")
DEFAULT = "PromptExecutionAU/wattson-qwen3.5-4b-pyt"


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_url(url):
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as r:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fail(msg):
    print(f"FAIL kit-import: {msg}")
    sys.exit(1)


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    if not os.path.exists(KIT):
        fail("kit CLI missing; run `just kit-install` first")
    commit = get_json(f"https://huggingface.co/api/models/{repo}")["sha"]
    tree = [e for e in get_json(f"https://huggingface.co/api/models/{repo}/tree/{commit}?recursive=true")
            if e.get("type") == "file"]
    # Measured on the first run: `kit import` leaves out Git metadata (.gitattributes) and nothing else. That is a
    # documented, justified difference, not a byte mismatch, so it is excluded by name and printed.
    git_metadata = [e["path"] for e in tree if os.path.basename(e["path"]) == ".gitattributes"]
    tree = [e for e in tree if e["path"] not in git_metadata]
    print(f"excluded (git metadata, not packaged by kit import): {git_metadata}")
    print(f"repo={repo} commit={commit} files={len(tree)}")
    scratch = tempfile.mkdtemp(prefix="ki-", dir="/tmp")
    try:
        cfg = os.path.join(scratch, "kitstore")
        env = {**os.environ, "HOME": os.path.join(scratch, "home")}
        os.makedirs(env["HOME"])
        tag = "wattson/import-check:v1"
        imp = subprocess.run([KIT, "--config", cfg, "import", repo, "--ref", commit, "--tool", "hf", "-t", tag,
                              "--progress", "none"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        if imp.returncode:
            fail(f"kit import failed: {(imp.stdout + imp.stderr)[-500:]}")
        print(f"imported: {tag}")
        out = os.path.join(scratch, "out")
        unp = subprocess.run([KIT, "--config", cfg, "unpack", tag, "-d", out, "--progress", "none"],
                             capture_output=True, text=True, env=env)
        if unp.returncode:
            fail(f"kit unpack failed: {(unp.stdout + unp.stderr)[-500:]}")
        matched, mismatched, missing = 0, [], []
        for e in sorted(tree, key=lambda e: e["path"]):
            path = e["path"]
            local = os.path.join(out, path)
            if not os.path.isfile(local):
                missing.append(path)
                continue
            want = (e.get("lfs") or {}).get("oid") or sha256_url(f"https://huggingface.co/{repo}/resolve/{commit}/{path}")
            got = sha256_file(local)
            if got == want:
                matched += 1
                print(f"match {path} {got[:12]}")
            else:
                mismatched.append(path)
                print(f"MISMATCH {path} kit={got[:12]} hub={want[:12]}")
        print(f"import-hash: files={len(tree)} matched={matched} mismatched={len(mismatched)} missing={len(missing)}")
        if missing:
            print(f"missing from the unpacked kit: {missing}")
        if mismatched or missing or matched == 0:
            fail("kit import did not reproduce the Hub files exactly")
        print(f"PASS kit-import: all {matched} files of {repo}@{commit[:12]} unpack byte-identical to the Hub originals")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
