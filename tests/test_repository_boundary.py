"""The migrated capabilities must not depend on an installed legacy lab package."""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pandapower as pp
import pandapower.networks as pn
import pytest

ROOT: Path = Path(__file__).resolve().parents[1]
LEGACY_PACKAGES: tuple[str, ...] = ("powercoder", "training")
# Bound local inspection commands; neither operation may start a model or unit.
INSPECTION_TIMEOUT_SECONDS: int = 30


def test_runtime_imports_are_owned_by_wattson() -> None:
    """Catch a newly introduced import of the removed legacy package names."""
    invalid: list[str] = []
    for path in (ROOT / "src/wattson").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            if any(module.split(".")[0] in LEGACY_PACKAGES for module in modules):
                invalid.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not invalid, invalid


def test_installed_modules_and_schema_load_outside_the_checkout(tmp_path: Path) -> None:
    """Installed package resources work with cwd off both repository trees."""
    command = [sys.executable, "-I", "-c",
               "from wattson.commands import IR_VERSION; "
               "from wattson.inference.studies import tool_schema; "
               "from wattson.datasets.validate import SCHEMA_PATH; "
               "assert SCHEMA_PATH.is_file(); assert tool_schema(); print(IR_VERSION)"]
    result = subprocess.run(command, cwd=tmp_path, text=True, capture_output=True,
                            timeout=INSPECTION_TIMEOUT_SECONDS, check=True)
    assert result.stdout.strip() == "0.3.0"


def test_external_topology_uses_a_data_artifact_not_an_import(tmp_path: Path) -> None:
    """The former Lab 5 bridge accepts a real network export without its code tree."""
    pytest.importorskip("rdflib")
    from wattson.semantic.peer_check import load_named

    source = pn.case9()
    artifact = tmp_path / "network.json"
    pp.to_json(source, str(artifact))
    loaded = load_named("lab5-chaosnet", artifact)
    assert loaded.bus.equals(source.bus) and loaded.line.equals(source.line)
    with pytest.raises(ValueError, match="--network-json"):
        load_named("lab5-chaosnet")


def test_training_plan_is_bounded_and_uses_wattson_modules() -> None:
    """Rendering a launch cannot start GPU work; cap and ownership regressions fail."""
    result = subprocess.run(["bash", str(ROOT / "scripts/launch_agent_sft.sh"), "--dry-run"],
                            cwd=ROOT, text=True, capture_output=True,
                            timeout=INSPECTION_TIMEOUT_SECONDS, check=True)
    assert "wattson.training.agent_sft" in result.stdout
    assert "--memory" in result.stdout and "--memory-swap" in result.stdout
    assert "--pids-limit" in result.stdout and "--cpus" in result.stdout
    assert "nem-poweragent-lab" not in result.stdout
    assert "c890642db172" not in result.stdout
