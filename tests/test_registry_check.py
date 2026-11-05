"""Tests for scripts/registry_check.py - validate and render gateway registries.

Uses tmp_path for isolation, runs the script via sys.executable, and checks exit
codes, output lines, and rendered content. No network.
"""
import json
import os
import subprocess
import sys

import pytest
import yaml

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "registry_check.py")
REGISTRY_DIR = os.path.join(os.path.dirname(__file__), "..", "registries")


def run_check(reg_dir, args=None):
    """Run registry_check.py against a registry directory."""
    cmd = [sys.executable, SCRIPT, "--registry-dir", reg_dir, "--root", os.path.join(os.path.dirname(__file__), "..")]
    if args:
        cmd.extend(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result


def copy_registries(tmp_path, reg_dir=REGISTRY_DIR):
    """Copy shipped registries to tmp_path for mutation."""
    import shutil
    dest = tmp_path / "registries"
    shutil.copytree(reg_dir, dest)
    return str(dest)


def write_yaml(path, data):
    """Write data as YAML to path."""
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)


def load_yaml(path):
    """Load YAML from path."""
    with open(path, "r") as f:
        return yaml.safe_load(f)


class TestShippedRegistries:
    """The shipped registries should validate cleanly."""

    def test_all_pass(self):
        result = run_check(REGISTRY_DIR)
        assert result.returncode == 0, result.stderr
        assert "PASS registries:" in result.stdout
        # Verify counts
        assert "backends=3" in result.stdout
        assert "tools=2" in result.stdout
        assert "skills=2" in result.stdout
        assert "prompts=2" in result.stdout

    def test_render_produces_mcp_targets(self):
        result = run_check(REGISTRY_DIR, ["--render"])
        assert result.returncode == 0, result.stderr
        assert "mcp targets" in result.stdout
        assert "excel-read" in result.stdout
        assert "power-solver" in result.stdout

    def test_render_produces_model_list(self):
        result = run_check(REGISTRY_DIR, ["--render"])
        assert result.returncode == 0, result.stderr
        assert "llm models" in result.stdout
        assert "local-llama" in result.stdout
        assert "hosted-vertex" in result.stdout


class TestValidationFailures:
    """Invalid registries should be rejected with clear messages."""

    def test_duplicate_tool_name(self, tmp_path):
        reg_dir = copy_registries(tmp_path)
        tools = load_yaml(os.path.join(reg_dir, "tools.yaml"))
        # Add duplicate name
        tools["entries"].append({
            "name": "excel-read",
            "mcp_url": "http://169.254.1.2:9999/mcp",
            "writable_by": ["operator"],
            "description": "Duplicate entry"
        })
        write_yaml(os.path.join(reg_dir, "tools.yaml"), tools)

        result = run_check(reg_dir)
        assert result.returncode == 1
        assert "FAIL tools.yaml:" in result.stdout
        assert "duplicate entry name: excel-read" in result.stdout

    def test_hosted_backend_missing_spend_cap(self, tmp_path):
        reg_dir = copy_registries(tmp_path)
        backends = load_yaml(os.path.join(reg_dir, "backends.yaml"))
        # Remove spend_cap_usd from hosted backend
        for entry in backends["entries"]:
            if entry["kind"] == "hosted":
                del entry["spend_cap_usd"]
        write_yaml(os.path.join(reg_dir, "backends.yaml"), backends)

        result = run_check(reg_dir)
        assert result.returncode == 1
        assert "FAIL backends.yaml:" in result.stdout
        assert "hosted backend missing spend_cap_usd" in result.stdout

    def test_literal_api_key_rejected(self, tmp_path):
        reg_dir = copy_registries(tmp_path)
        backends = load_yaml(os.path.join(reg_dir, "backends.yaml"))
        # Add literal api_key field
        backends["entries"][0]["api_key"] = "sk-1234567890abcdef"
        write_yaml(os.path.join(reg_dir, "backends.yaml"), backends)

        result = run_check(reg_dir)
        assert result.returncode == 1
        assert "FAIL backends.yaml:" in result.stdout
        assert "literal secret field 'api_key'" in result.stdout

    def test_nonexistent_path_rejected(self, tmp_path):
        reg_dir = copy_registries(tmp_path)
        skills = load_yaml(os.path.join(reg_dir, "skills.yaml"))
        skills["entries"][0]["path"] = "nonexistent/path/SKILL.md"
        write_yaml(os.path.join(reg_dir, "skills.yaml"), skills)

        result = run_check(reg_dir)
        assert result.returncode == 1
        assert "FAIL skills.yaml:" in result.stdout
        assert "path does not exist" in result.stdout


class TestRenderDeterminism:
    """The acceptance test: adding a tool entry changes only that entry in output."""

    def test_add_tool_entry_grows_rendered_list(self, tmp_path):
        reg_dir = copy_registries(tmp_path)

        # Render with original tools (2 entries)
        result1 = run_check(reg_dir, ["--render"])
        assert result1.returncode == 0, result1.stderr
        stdout1 = result1.stdout

        # Parse the rendered mcp targets from result1
        mcp_section1 = stdout1.split("--- mcp targets ---")[1].split("--- llm models ---")[0]
        targets1 = yaml.safe_load(mcp_section1)["mcp"]["targets"]
        assert len(targets1) == 2

        # Add a new tool entry
        tools = load_yaml(os.path.join(reg_dir, "tools.yaml"))
        new_tool = {
            "name": "new-tool",
            "mcp_url": "http://169.254.1.2:3300/mcp",
            "writable_by": ["operator"],
            "description": "A new tool added for testing"
        }
        tools["entries"].append(new_tool)
        write_yaml(os.path.join(reg_dir, "tools.yaml"), tools)

        # Render again
        result2 = run_check(reg_dir, ["--render"])
        assert result2.returncode == 0, result2.stderr
        stdout2 = result2.stdout

        # Parse rendered mcp targets from result2
        mcp_section2 = stdout2.split("--- mcp targets ---")[1].split("--- llm models ---")[0]
        targets2 = yaml.safe_load(mcp_section2)["mcp"]["targets"]

        # The rendered target list should have grown by exactly one entry
        assert len(targets2) == 3
        assert len(targets2) == len(targets1) + 1

        # The new entry should be present
        new_target_names = [t["name"] for t in targets2 if t["name"] not in [t1["name"] for t1 in targets1]]
        assert new_target_names == ["new-tool"]

        # The new target should have the correct host
        new_target = [t for t in targets2 if t["name"] == "new-tool"][0]
        assert new_target["mcp"]["host"] == "http://169.254.1.2:3300/mcp"

        # Original entries should all still be present with unchanged content
        for t1 in targets1:
            matching = [t for t in targets2 if t["name"] == t1["name"]]
            assert len(matching) == 1
            assert matching[0]["mcp"]["host"] == t1["mcp"]["host"]

        # The llm models section should be identical (deterministic)
        llm1 = stdout1.split("--- llm models ---")[1].strip()
        llm2 = stdout2.split("--- llm models ---")[1].strip()
        assert llm1 == llm2


class TestWritableBy:
    """writable_by is enforced against a git base: who may add, change or remove which entry."""

    def _repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        import shutil
        shutil.copytree(REGISTRY_DIR, repo / "registries")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["init", "-q"], cwd=repo, check=True)
        subprocess.run(git + ["add", "-A"], cwd=repo, check=True)
        subprocess.run(git + ["commit", "-q", "-m", "base"], cwd=repo, check=True)
        return repo

    def _run(self, repo, role):
        root = os.path.join(os.path.dirname(__file__), "..")
        return subprocess.run(
            [sys.executable, SCRIPT, "--registry-dir", "registries", "--root", root, "--role", role, "--base", "HEAD"],
            cwd=repo, capture_output=True, text=True)

    def test_no_change_passes_for_any_role(self, tmp_path):
        result = self._run(self._repo(tmp_path), "nobody")
        assert result.returncode == 0, result.stdout
        assert "PASS writable_by" in result.stdout

    def test_permitted_role_may_change_entry(self, tmp_path):
        repo = self._repo(tmp_path)
        tools = load_yaml(repo / "registries" / "tools.yaml")
        tools["entries"][0]["description"] = "edited by an operator"
        write_yaml(repo / "registries" / "tools.yaml", tools)
        result = self._run(repo, "operator")
        assert result.returncode == 0, result.stdout

    def test_unlisted_role_cannot_add_entry(self, tmp_path):
        repo = self._repo(tmp_path)
        tools = load_yaml(repo / "registries" / "tools.yaml")
        tools["entries"].append({"name": "sneaky", "mcp_url": "http://169.254.1.2:3999/mcp",
                                 "writable_by": ["intern"], "description": "x"})
        write_yaml(repo / "registries" / "tools.yaml", tools)
        result = self._run(repo, "someone-else")
        assert result.returncode == 1
        assert "entry sneaky added: role 'someone-else' not in writable_by ['intern']" in result.stdout

    def test_entry_cannot_grant_itself_write_access(self, tmp_path):
        repo = self._repo(tmp_path)
        tools = load_yaml(repo / "registries" / "tools.yaml")
        tools["entries"][0]["writable_by"].append("intern")
        write_yaml(repo / "registries" / "tools.yaml", tools)
        result = self._run(repo, "intern")
        assert result.returncode == 1
        assert "changed: role 'intern' not in writable_by" in result.stdout

    def test_unlisted_role_cannot_remove_entry(self, tmp_path):
        repo = self._repo(tmp_path)
        prompts = load_yaml(repo / "registries" / "prompts.yaml")
        removed = prompts["entries"].pop()
        write_yaml(repo / "registries" / "prompts.yaml", prompts)
        result = self._run(repo, "intern")
        assert result.returncode == 1
        assert f"entry {removed['name']} removed" in result.stdout
