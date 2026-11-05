"""Validate lineage bookkeeping; real GPU training/resume is a separate gate."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from wattson.training.axolotl.run import PARENT_FILES, digest, plan_run

ROOT: Path = Path(__file__).parent
# The migrated configuration belongs to this repository's configs tree.
CONFIG_ROOT: Path = Path(__file__).resolve().parents[3] / "configs" / "axolotl"


@pytest.fixture
def prepared(tmp_path: Path) -> tuple[Path, dict]:
    """Use synthetic token rows only to exercise file integrity and mode guards."""
    fixture = json.loads((ROOT / "fixtures/lineage_cases.json").read_text())
    config = yaml.safe_load((CONFIG_ROOT / "sft-4b.yaml").read_text())
    config["output_dir"] = str(tmp_path / "out" / "reproduction")
    data = tmp_path / "out" / "data"
    data.mkdir(parents=True)
    entries = {}
    for group, split in (("datasets", "train"), ("test_datasets", "validation")):
        path = data / f"tokenized-{split}.jsonl"
        path.write_text(json.dumps(fixture[f"{split}_row"]) + "\n")
        config[group][0]["path"] = str(path)
        entries[str(path)] = {"sha256": digest(path), "rows": 1}
    (data / "tokenized-manifest.json").write_text(json.dumps({"files": entries, "benchmark_access": False}))
    source = tmp_path / "sft.yaml"
    source.write_text(yaml.safe_dump(config))
    return source, fixture


def make_parent(source: Path, fixture: dict) -> Path:
    """Build synthetic checkpoint markers; no tensor deserialization or training occurs."""
    plan = plan_run(source, "reproduction")
    root = Path(plan["effective_config"]["output_dir"])
    parent = root / f"checkpoint-{fixture['parent_step']}"
    parent.mkdir(parents=True)
    for name in PARENT_FILES:
        (parent / name).write_text(fixture["checkpoint_marker"])
    config = plan["effective_config"]
    (parent / "adapter_config.json").write_text(json.dumps({
        "base_model_name_or_path": config["base_model"],
        "r": config["lora_r"], "lora_alpha": config["lora_alpha"],
    }))
    (parent / "trainer_state.json").write_text(json.dumps({"global_step": fixture["parent_step"]}))
    plan.update({"state": "complete", "checkpoint_files_sha256": {
        f"{parent.name}/{name}": digest(parent / name) for name in PARENT_FILES
    }})
    (root / "run_manifest.json").write_text(json.dumps(plan))
    return parent


def test_reproduction_has_no_parent_and_refuses_output_reuse(prepared: tuple[Path, dict]) -> None:
    """A reproduction remains independent and cannot silently reuse an old run."""
    source, _fixture = prepared
    plan = plan_run(source, "reproduction")
    assert plan["parent_checkpoint"] is None
    assert plan["effective_config"]["resume_from_checkpoint"] is None
    Path(plan["effective_config"]["output_dir"]).mkdir()
    with pytest.raises(ValueError, match="already exists"):
        plan_run(source, "reproduction")


def test_implicit_parent_fields_are_rejected(prepared: tuple[Path, dict]) -> None:
    """YAML cannot bypass the explicit reproduction/continuation choice."""
    source, fixture = prepared
    original = yaml.safe_load(source.read_text())
    for case in fixture["implicit_parent_cases"]:
        config = copy.deepcopy(original)
        config[case["field"]] = case["value"]
        source.write_text(yaml.safe_dump(config))
        with pytest.raises(ValueError, match="implicit"):
            plan_run(source, "reproduction")


def test_data_tampering_and_external_paths_are_rejected(prepared: tuple[Path, dict]) -> None:
    """The training launcher cannot consume changed data or another data directory."""
    source, fixture = prepared
    config = yaml.safe_load(source.read_text())
    path = Path(config["datasets"][0]["path"])
    path.write_text(fixture["tampered_marker"])
    with pytest.raises(ValueError, match="fingerprint"):
        plan_run(source, "reproduction")
    config["datasets"][0]["path"] = str(source.parent / "benchmark" / path.name)
    source.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="Only the prepared"):
        plan_run(source, "reproduction")


def test_continuation_requires_and_hashes_full_parent(prepared: tuple[Path, dict]) -> None:
    """A continuation names every optimizer/RNG/adapter file from an attributed parent."""
    source, fixture = prepared
    with pytest.raises(ValueError, match="explicit parent"):
        plan_run(source, "continuation")
    parent = make_parent(source, fixture)
    output = parent.parent.parent / "continued"
    plan = plan_run(source, "continuation", parent=parent, output_dir=output)
    assert plan["parent_checkpoint"] == str(parent.resolve())
    assert set(plan["parent_files_sha256"]) == set(PARENT_FILES)
    (parent / "adapter_model.safetensors").write_text(fixture["tampered_marker"])
    with pytest.raises(ValueError, match="Parent checkpoint fingerprint"):
        plan_run(source, "continuation", parent=parent, output_dir=output)


def test_finished_parent_cannot_masquerade_as_more_training(prepared: tuple[Path, dict]) -> None:
    """Loading a finished step at the same step budget does not demonstrate continuation."""
    source, fixture = prepared
    parent = make_parent(source, fixture)
    output = parent.parent.parent / "continued"
    with pytest.raises(ValueError, match="steps beyond"):
        plan_run(source, "continuation", parent=parent, output_dir=output, max_steps=fixture["parent_step"])
