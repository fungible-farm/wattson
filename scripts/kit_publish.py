#!/usr/bin/env python3
"""Publish evaluation evidence or the agent kit as a kit package.

Usage:
    python scripts/kit_publish.py evidence RUN_DIR --ref REGISTRY/NAME:TAG [--kit PATH] [--plain-http] [--push]
    python scripts/kit_publish.py agent --ref REGISTRY/NAME:TAG [--kit PATH] [--plain-http] [--push] [--allow-dirty]

Both subcommands share the same staging/gate/pack path. The evidence subcommand
copies only selected files from a run directory; the agent subcommand stages
the gateway config, skills, justfile, and diagrams from the repository root.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Import the fail-closed pack check logic
sys.path.insert(0, str(Path(__file__).parent))
from kit_pack_check import is_denied, load_allow, matches_allow


def repo_root() -> Path:
    """Return the repository root (directory containing scripts/)."""
    return Path(__file__).resolve().parent.parent


def find_kit(kit_path: str | None) -> str:
    """Resolve the kit binary path."""
    if kit_path:
        return kit_path
    # Check .tools/kit/kit
    default = repo_root() / ".tools" / "kit" / "kit"
    if default.exists():
        return str(default)
    # Fall back to PATH
    return "kit"


def run_cmd(cmd, check=True, capture=True, cwd=None):
    """Run a command and return the CompletedProcess (git calls pass cwd so they never read the caller's repo)."""
    kwargs = {"capture_output": capture, "text": True, "cwd": cwd}
    result = subprocess.run(cmd, **kwargs)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{result.stderr}")
    return result


def stage_evidence(run_dir: Path, staging: Path) -> list[str]:
    """Stage evidence files from a run directory. Returns list of staged relative paths."""
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise RuntimeError(f"run directory does not exist: {run_dir}")

    # First, check that no denied files exist in the run directory
    for root, dirs, files in os.walk(run_dir, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            full = Path(root) / f
            rel = full.relative_to(run_dir).as_posix()
            if full.is_symlink():
                print(f"DENY {rel}: symlink (not followed, never packed)", file=sys.stderr)
                sys.exit(1)
            reason = is_denied(rel)
            if reason:
                print(f"DENY {rel}: {reason}", file=sys.stderr)
                sys.exit(1)

    # Select only the allowed evidence files
    allowed_patterns = [
        "manifest.json", "summary.json", "*.jsonl", "verdict*.json",
        "thresholds*.json", "thresholds*.md", "results*.md", "results*.json",
        "prompts/*.txt"
    ]

    staged = []
    for root, dirs, files in os.walk(run_dir, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            full = Path(root) / f
            rel = full.relative_to(run_dir).as_posix()
            if not matches_allow(rel, allowed_patterns):
                continue
            if full.is_symlink():
                continue
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, dest)
            staged.append(rel)

    return staged


def stage_agent(staging: Path, allow_dirty: bool, root: Path) -> list[str]:
    """Stage agent kit files from the repository at ROOT. Returns list of staged relative paths."""

    # Check git status
    try:
        result = run_cmd(["git", "rev-parse", "HEAD"], check=True, cwd=root)
        sha = result.stdout.strip()
        short_sha = sha[:7]
    except RuntimeError:
        sha = "unknown"
        short_sha = "unknown"

    # Check for uncommitted changes to the selected files
    selected_files = [
        "gateway/agentgateway.yaml", "gateway/README.md",
        "justfile"
    ]
    # Add skills/*/SKILL.md
    for skill_dir in root.glob("skills/*/SKILL.md"):
        selected_files.append(skill_dir.relative_to(root).as_posix())
    # Add docs/diagrams/*.d2 and *.svg
    for diagram in root.glob("docs/diagrams/*.d2"):
        selected_files.append(diagram.relative_to(root).as_posix())
    for diagram in root.glob("docs/diagrams/*.svg"):
        selected_files.append(diagram.relative_to(root).as_posix())

    dirty = False
    try:
        result = run_cmd(["git", "status", "--porcelain"], check=True, cwd=root)
        changed = set()
        # Porcelain lines are 'XY path': two status columns (the first may be a space), a space, then the path.
        # Do NOT strip the whole output: that would eat the first line's leading status space.
        for line in result.stdout.splitlines():
            if len(line) > 3:
                path = line[3:]
                if " -> " in path:  # a rename: the new name is what is in the tree
                    path = path.split(" -> ")[-1]
                changed.add(path.strip('"'))
        for sf in selected_files:
            if sf in changed:
                dirty = True
                break
    except RuntimeError:
        dirty = True

    if dirty and not allow_dirty:
        print("error: uncommitted changes in selected files; use --allow-dirty", file=sys.stderr)
        sys.exit(1)

    # Write REVISION file
    revision = staging / "REVISION"
    revision.write_text(f"{sha}\n{'dirty' if dirty else 'clean'}\n")

    # Stage the files
    staged = ["REVISION"]
    for rel in selected_files:
        src = root / rel
        if not src.exists():
            continue
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        staged.append(rel)

    return staged


def write_allow_file(staging: Path, staged: list[str]) -> Path:
    """Write an allow file listing exactly the staged paths."""
    allow_path = staging / "allow.txt"
    allow_path.write_text("\n".join(staged) + "\n")
    return allow_path


def run_pack_check(staging: Path, allow_path: Path) -> None:
    """Run the fail-closed pack check on the staging directory."""
    script = Path(__file__).parent / "kit_pack_check.py"
    result = run_cmd([sys.executable, str(script), str(staging), "--allow-file", str(allow_path)])
    if result.returncode != 0:
        print(f"pack check failed:\n{result.stdout}", file=sys.stderr)
        sys.exit(1)


def write_kitfile(staging: Path, kit_type: str, ref: str, run_dir: Path | None, root: Path | None = None) -> None:
    """Write a Kitfile for the staged content."""
    name = ref.split("/")[-1].split(":")[0]

    if kit_type == "evidence":
        desc = f"evidence for {run_dir.name}"
        # datasets: jsonl files and json files
        # docs: md files
        datasets = []
        docs = []
        for f in sorted(staging.iterdir()):
            if f.name == "Kitfile" or f.name == "allow.txt":
                continue
            if f.is_file():
                if f.suffix in (".jsonl", ".json"):
                    datasets.append({"path": f.name})
                elif f.suffix == ".md":
                    docs.append({"path": f.name})
            elif f.is_dir():
                # prompts directory
                docs.append({"path": f.name})

        # JSON strings are valid YAML scalars: a run directory called 'x: y # z' cannot break or alter the Kitfile.
        kitfile = f"""manifestVersion: 1.0.0
package:
  name: {json.dumps(name)}
  description: {json.dumps(desc)}
"""
        if datasets:
            kitfile += "datasets:\n"
        for d in datasets:
            kitfile += f"  - path: {json.dumps(d['path'])}\n"
        if docs:
            kitfile += "docs:\n"
            for d in docs:
                kitfile += f"  - path: {json.dumps(d['path'])}\n"

        kitfile_path = staging / "Kitfile"
        kitfile_path.write_text(kitfile)
    else:  # agent
        desc = f"agent kit at {short_sha(root)}"
        kitfile = f"""manifestVersion: 1.0.0
package:
  name: {json.dumps(name)}
  description: {json.dumps(desc)}
code:
  - path: justfile
  - path: gateway
  - path: skills
docs:
  - path: REVISION
  - path: docs/diagrams
"""
        kitfile_path = staging / "Kitfile"
        kitfile_path.write_text(kitfile)


def kitfile_paths(text: str) -> list[str]:
    """Every '- path: X' entry of a Kitfile (values written by write_kitfile are bare or JSON-quoted)."""
    paths = []
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*path:\s*(.+?)\s*$", line)
        if m:
            value = m.group(1)
            paths.append(json.loads(value) if value.startswith('"') else value)
    return paths


def assert_kitfile_covers(staging: Path, staged: list[str]) -> None:
    """kit pack packs only what the Kitfile names. Refuse if any staged file is not covered (it would silently be missing)."""
    paths = [p.rstrip("/") for p in kitfile_paths((staging / "Kitfile").read_text())]
    uncovered = [rel for rel in staged if not any(rel == p or rel.startswith(p + "/") for p in paths)]
    if uncovered:
        print("error: staged file(s) not listed in the Kitfile, so they would NOT be in the kit: " + ", ".join(uncovered), file=sys.stderr)
        sys.exit(1)


def short_sha(root: Path) -> str:
    """Get the short git sha of the repository at ROOT."""
    try:
        result = run_cmd(["git", "rev-parse", "--short", "HEAD"], check=True, cwd=root)
        return result.stdout.strip()
    except RuntimeError:
        return "unknown"


def pack_and_push(kit_bin: str, staging: Path, ref: str, push: bool, plain_http: bool) -> tuple[str, bool]:
    """Pack the staging directory and optionally push. Returns the digest."""
    # Pack
    cmd = [kit_bin, "pack", str(staging), "-t", ref]
    result = run_cmd(cmd, check=True)
    # Parse digest from "Model saved: sha256:<hex>"
    digest = None
    for line in result.stdout.splitlines():
        if "Model saved:" in line and "sha256:" in line:
            digest = line.split("sha256:")[-1].strip()
            break
    if not digest:
        print(f"error: could not parse digest from pack output:\n{result.stdout}", file=sys.stderr)
        sys.exit(1)

    pushed = False
    if push:
        push_cmd = [kit_bin, "push", ref]
        if plain_http:
            push_cmd.append("--plain-http")
        result = run_cmd(push_cmd, check=True)
        # Parse pushed digest
        pushed_digest = None
        for line in result.stdout.splitlines():
            if "Pushed" in line and "sha256:" in line:
                pushed_digest = line.split("sha256:")[-1].strip()
                break
        if not pushed_digest:
            print(f"error: could not parse digest from push output:\n{result.stdout}", file=sys.stderr)
            sys.exit(1)
        if pushed_digest != digest:
            print(f"error: push digest mismatch: packed={digest} pushed={pushed_digest}", file=sys.stderr)
            sys.exit(1)
        pushed = True

    return digest, pushed


def main():
    parser = argparse.ArgumentParser(description="Publish evaluation evidence or agent kit")
    sub = parser.add_subparsers(dest="command", required=True)

    # Evidence subcommand
    ev = sub.add_parser("evidence", help="Publish evaluation evidence")
    ev.add_argument("run_dir", help="Path to the run directory")
    ev.add_argument("--ref", required=True, help="Registry reference (name:tag)")
    ev.add_argument("--kit", help="Path to kit binary")
    ev.add_argument("--plain-http", action="store_true", help="Use plain HTTP for registry")
    ev.add_argument("--push", action="store_true", help="Push to registry after packing")

    # Agent subcommand
    ag = sub.add_parser("agent", help="Publish agent kit")
    ag.add_argument("--ref", required=True, help="Registry reference (name:tag)")
    ag.add_argument("--kit", help="Path to kit binary")
    ag.add_argument("--plain-http", action="store_true", help="Use plain HTTP for registry")
    ag.add_argument("--push", action="store_true", help="Push to registry after packing")
    ag.add_argument("--allow-dirty", action="store_true", help="Allow uncommitted changes")
    ag.add_argument("--repo", default=None, help="Repository to publish (default: the repository that contains this script)")

    args = parser.parse_args()

    kit_bin = find_kit(args.kit)

    with tempfile.TemporaryDirectory() as tmpdir:
        staging = Path(tmpdir) / "staging"
        staging.mkdir()

        if args.command == "evidence":
            staged = stage_evidence(args.run_dir, staging)
            if not staged:
                print("error: no evidence files found to stage", file=sys.stderr)
                sys.exit(1)
        else:
            root = Path(args.repo).resolve() if args.repo else repo_root()
            staged = stage_agent(staging, args.allow_dirty, root)

        # Write allow file and run pack check
        allow_path = write_allow_file(staging, staged)
        run_pack_check(staging, allow_path)

        # Write Kitfile
        write_kitfile(staging, args.command, args.ref,
                      Path(args.run_dir).resolve() if args.command == "evidence" else None,
                      root if args.command == "agent" else None)

        assert_kitfile_covers(staging, staged)

        # Pack and optionally push
        digest, pushed = pack_and_push(kit_bin, staging, args.ref, args.push, args.plain_http)

        # Final output line
        print(f"kit {args.command} digest=sha256:{digest} ref={args.ref} files={len(staged)} pushed={'yes' if pushed else 'no'}")


if __name__ == "__main__":
    main()
