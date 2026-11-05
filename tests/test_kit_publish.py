"""CI-safe tests for scripts/kit_publish.py.

Uses a fake 'kit' script written to tmp_path to capture argv and simulate
pack/push behavior. No network, no real kit binary required.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PY = sys.executable
SCRIPT = Path(__file__).parent.parent / "scripts" / "kit_publish.py"
REPO_ROOT = Path(__file__).parent.parent


def make_fake_kit(tmp_path, behavior="ok"):
    """Create a fake kit script that records argv and simulates pack/push.

    behavior: 'ok' - normal success
              'mismatch' - push returns different digest
              'pack_fail' - pack exits non-zero
    """
    kit_dir = tmp_path / "fakekit"
    kit_dir.mkdir()
    kit_script = kit_dir / "kit"
    kit_script.write_text(f"""#!/usr/bin/env {PY}
import sys, json, hashlib
log_file = "{tmp_path}/kit_log.jsonl"
args = sys.argv[1:]
# Record the call
with open(log_file, "a") as f:
    f.write(json.dumps({{"args": args}}) + "\\n")
if not args:
    sys.exit(0)
cmd = args[0]
if cmd == "pack":
    # Find the -t flag
    tag = None
    staging = None
    i = 1
    while i < len(args):
        if args[i] == "-t":
            tag = args[i+1]
            i += 2
        elif not args[i].startswith("-"):
            staging = args[i]
            i += 1
        else:
            i += 1
    # Generate a digest based on the staging dir contents
    digest = "a" * 64
    if "{behavior}" == "pack_fail":
        print("error: pack failed", file=sys.stderr)
        sys.exit(1)
    import os
    listing = sorted(os.path.relpath(os.path.join(d, f), staging) for d, _, fs in os.walk(staging) for f in fs)
    open("{tmp_path}/staging_listing.json", "w").write(json.dumps(listing))
    for fname in ("Kitfile", "REVISION"):
        fp = os.path.join(staging, fname)
        if os.path.exists(fp):
            open("{tmp_path}/" + fname + ".txt", "w").write(open(fp).read())
    print(f"Model saved: sha256:{{digest}}")
    sys.exit(0)
elif cmd == "push":
    ref = args[0]
    if "{behavior}" == "mismatch":
        print(f"Pushed sha256:b{'b' * 63}")
    else:
        print(f"Pushed sha256:a{'a' * 63}")
    sys.exit(0)
else:
    sys.exit(0)
""")
    kit_script.chmod(0o755)
    return kit_script


def run_publish(args, tmp_path=None):
    """Run kit_publish.py with the given args."""
    cmd = [PY, str(SCRIPT)] + args
    env = os.environ.copy()
    if tmp_path:
        env["HOME"] = str(tmp_path)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return result


def read_kit_log(tmp_path):
    """Read the fake kit's log file."""
    log_file = tmp_path / "kit_log.jsonl"
    if not log_file.exists():
        return []
    entries = []
    for line in log_file.read_text().splitlines():
        if line:
            entries.append(json.loads(line))
    return entries


@pytest.fixture
def run_dir(tmp_path):
    """Create a fixture run directory with evidence files."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text('{"version": 1}')
    (run / "summary.json").write_text('{"score": 0.9}')
    (run / "results.jsonl").write_text('{"task": 1, "correct": true}\n')
    (run / "verdict.json").write_text('{"verdict": "pass"}')
    (run / "thresholds.json").write_text('{"min": 0.5}')
    (run / "thresholds.md").write_text("# Thresholds")
    (run / "results.md").write_text("# Results")
    (run / "results.json").write_text('{"detail": true}')
    prompts = run / "prompts"
    prompts.mkdir()
    (prompts / "a.txt").write_text("prompt text")
    # Unrelated file that should not be staged
    (run / "notes.bin").write_bytes(b"\x00\x01\x02")
    return run


def test_evidence_happy_path(tmp_path, run_dir):
    """Evidence happy path: only selected files staged, pack then push."""
    kit = make_fake_kit(tmp_path, "ok")
    result = run_publish([
        "evidence", str(run_dir),
        "--ref", "localhost:5000/ev/test:v1",
        "--kit", str(kit),
        "--push"
    ], tmp_path)
    assert result.returncode == 0, result.stderr
    # Check final line format
    assert "kit evidence digest=sha256:" in result.stdout
    assert "ref=localhost:5000/ev/test:v1" in result.stdout
    assert "pushed=yes" in result.stdout
    # Check kit was called with pack then push
    log = read_kit_log(tmp_path)
    assert len(log) == 2
    assert log[0]["args"][0] == "pack"
    assert log[1]["args"][0] == "push"


def test_evidence_denied_file_refuses(tmp_path, run_dir):
    """A .env file in run_dir refuses with exit 1, kit never run."""
    (run_dir / ".env").write_text("SECRET=1")
    kit = make_fake_kit(tmp_path, "ok")
    result = run_publish([
        "evidence", str(run_dir),
        "--ref", "localhost:5000/ev/test:v1",
        "--kit", str(kit)
    ], tmp_path)
    assert result.returncode == 1, result.stdout
    assert "DENY" in result.stderr
    # Kit should not have been called
    log = read_kit_log(tmp_path)
    assert len(log) == 0


def test_evidence_symlink_refuses(tmp_path, run_dir):
    """A symlink in run_dir refuses with exit 1."""
    target = tmp_path / "outside.txt"
    target.write_text("outside")
    os.symlink(target, run_dir / "link.txt")
    kit = make_fake_kit(tmp_path, "ok")
    result = run_publish([
        "evidence", str(run_dir),
        "--ref", "localhost:5000/ev/test:v1",
        "--kit", str(kit)
    ], tmp_path)
    assert result.returncode == 1, result.stdout
    assert "DENY" in result.stderr


def test_evidence_unrelated_files_not_staged(tmp_path, run_dir):
    """Unrelated files in run_dir are not staged."""
    kit = make_fake_kit(tmp_path, "ok")
    result = run_publish([
        "evidence", str(run_dir),
        "--ref", "localhost:5000/ev/test:v1",
        "--kit", str(kit)
    ], tmp_path)
    assert result.returncode == 0, result.stderr
    # Check that notes.bin is not in the output
    assert "files=9" in result.stdout  # 8 evidence files + prompts/a.txt


def test_evidence_digest_mismatch(tmp_path, run_dir):
    """Digest mismatch on push exits 1."""
    kit = make_fake_kit(tmp_path, "mismatch")
    result = run_publish([
        "evidence", str(run_dir),
        "--ref", "localhost:5000/ev/test:v1",
        "--kit", str(kit),
        "--push"
    ], tmp_path)
    assert result.returncode == 1, result.stdout
    assert "digest mismatch" in result.stderr


def test_evidence_pack_fail(tmp_path, run_dir):
    """Kit pack failing exits 1."""
    kit = make_fake_kit(tmp_path, "pack_fail")
    result = run_publish([
        "evidence", str(run_dir),
        "--ref", "localhost:5000/ev/test:v1",
        "--kit", str(kit)
    ], tmp_path)
    assert result.returncode == 1, result.stdout


def test_evidence_source_not_modified(tmp_path, run_dir):
    """Source files are never modified."""
    # Compute hashes before
    hashes_before = {}
    for f in run_dir.rglob("*"):
        if f.is_file():
            hashes_before[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()

    kit = make_fake_kit(tmp_path, "ok")
    result = run_publish([
        "evidence", str(run_dir),
        "--ref", "localhost:5000/ev/test:v1",
        "--kit", str(kit)
    ], tmp_path)
    assert result.returncode == 0, result.stderr

    # Compute hashes after
    for f in run_dir.rglob("*"):
        if f.is_file():
            after_hash = hashlib.sha256(f.read_bytes()).hexdigest()
            assert after_hash == hashes_before[str(f)], f"{f} was modified"


GIT = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com"]


def make_agent_repo(path):
    """A minimal git repo with the files the agent kit selects, committed."""
    path.mkdir()
    (path / "gateway").mkdir()
    (path / "gateway" / "agentgateway.yaml").write_text("config: true\n")
    (path / "gateway" / "README.md").write_text("gateway docs\n")
    (path / "skills" / "test-skill").mkdir(parents=True)
    (path / "skills" / "test-skill" / "SKILL.md").write_text("# Test Skill\n")
    (path / "skills" / "test-skill" / "other.txt").write_text("not selected\n")
    (path / "justfile").write_text("default:\n    echo hi\n")
    (path / "docs" / "diagrams").mkdir(parents=True)
    (path / "docs" / "diagrams" / "flow.d2").write_text("A -> B\n")
    (path / "docs" / "diagrams" / "flow.svg").write_text("<svg></svg>\n")
    (path / "README.md").write_text("not selected\n")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(GIT + ["commit", "-q", "-m", "init"], cwd=path, check=True, capture_output=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True).stdout.strip()


def run_agent(tmp_path, repo, *extra, cwd=None):
    kit = make_fake_kit(tmp_path, "ok")
    cmd = [PY, str(SCRIPT), "agent", "--ref", "localhost:5000/agent/main:v1", "--kit", str(kit), "--repo", str(repo), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or tmp_path)


def test_agent_stages_only_selected_files_and_records_a_clean_revision(tmp_path):
    repo = tmp_path / "repo"
    sha = make_agent_repo(repo)
    result = run_agent(tmp_path, repo)
    assert result.returncode == 0, result.stderr
    listing = set(json.loads((tmp_path / "staging_listing.json").read_text())) - {"Kitfile", "allow.txt"}
    assert listing == {"REVISION", "justfile", "gateway/agentgateway.yaml", "gateway/README.md",
                       "skills/test-skill/SKILL.md", "docs/diagrams/flow.d2", "docs/diagrams/flow.svg"}
    assert (tmp_path / "REVISION.txt").read_text().splitlines() == [sha, "clean"]
    assert "files=7" in result.stdout and "pushed=no" in result.stdout


def test_agent_refuses_uncommitted_changes_to_selected_files(tmp_path):
    repo = tmp_path / "repo"
    make_agent_repo(repo)
    (repo / "justfile").write_text("default:\n    echo changed\n")
    result = run_agent(tmp_path, repo)
    assert result.returncode == 1
    assert "uncommitted" in result.stderr
    assert not (tmp_path / "kit_log.jsonl").exists(), "kit must not run when the tree is dirty"


def test_agent_allow_dirty_records_dirty(tmp_path):
    repo = tmp_path / "repo"
    make_agent_repo(repo)
    (repo / "justfile").write_text("default:\n    echo changed\n")
    result = run_agent(tmp_path, repo, "--allow-dirty")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "REVISION.txt").read_text().splitlines()[1] == "dirty"


def test_agent_ignores_the_callers_working_directory(tmp_path):
    """git must read --repo, not whatever repository the command happens to be run from."""
    repo = tmp_path / "repo"
    sha = make_agent_repo(repo)
    other = tmp_path / "other"
    make_agent_repo(other)
    (other / "justfile").write_text("dirty in the other repo\n")
    result = run_agent(tmp_path, repo, cwd=other)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "REVISION.txt").read_text().splitlines() == [sha, "clean"]


def test_evidence_kitfile_quotes_awkward_directory_names(tmp_path):
    awkward = tmp_path / "x: y # z"
    awkward.mkdir()
    (awkward / "summary.json").write_text("{}")
    kit = make_fake_kit(tmp_path, "ok")
    result = run_publish(["evidence", str(awkward), "--ref", "localhost:5000/ev/run1:v1", "--kit", str(kit)], tmp_path)
    assert result.returncode == 0, result.stderr
    kitfile = (tmp_path / "Kitfile.txt").read_text()
    assert 'description: "evidence for x: y # z"' in kitfile
    assert 'name: "run1"' in kitfile


def test_evidence_kitfile_has_no_empty_datasets_block(tmp_path):
    only_docs = tmp_path / "run"
    only_docs.mkdir()
    (only_docs / "results.md").write_text("# r")
    kit = make_fake_kit(tmp_path, "ok")
    result = run_publish(["evidence", str(only_docs), "--ref", "localhost:5000/ev/run1:v1", "--kit", str(kit)], tmp_path)
    assert result.returncode == 0, result.stderr
    kitfile = (tmp_path / "Kitfile.txt").read_text()
    assert "datasets:" not in kitfile and "docs:" in kitfile


def test_evidence_dot_uses_the_resolved_directory_name(tmp_path):
    run = tmp_path / "myrun"
    run.mkdir()
    (run / "summary.json").write_text("{}")
    kit = make_fake_kit(tmp_path, "ok")
    cmd = [PY, str(SCRIPT), "evidence", ".", "--ref", "localhost:5000/ev/run1:v1", "--kit", str(kit)]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=run)
    assert result.returncode == 0, result.stderr
    assert 'description: "evidence for myrun"' in (tmp_path / "Kitfile.txt").read_text()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
