"""CPU regression gates for leakage, trajectory scoring, and adapter lineage."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from powercoder.datasets.audit import audit_rows
from training.unsloth import evaluate as evaluator
from training.unsloth import export, runtime
from training.unsloth.evaluate import compare, extract_scale, uncertainty

FIXTURE: Path = Path(__file__).parent / "fixtures" / "evaluation_cases.json"


@pytest.fixture
def cases() -> dict:
    """Load adversarial cases from the versioned JSON fixture."""
    return json.loads(FIXTURE.read_text())


def test_reference_and_adversarial_scores(cases: dict) -> None:
    """Distinguish final solver status and reject contradictory convergence."""
    for case in cases["comparisons"]:
        assert compare(case["subfamily"], case["candidate"], case["reference"]) == case["pass"]


def test_scale_parser_respects_sign_exponent_and_ambiguity(cases: dict) -> None:
    """Do not turn s=1e2 into s=1 or reject a correct bare numeric answer."""
    for case in cases["scale_cases"]:
        assert extract_scale(case["text"]) == case["value"]


def test_uncertainty_uses_paired_task_ids(cases: dict) -> None:
    """Intervals preserve pairing and reject reordered model results."""
    base = [
        {"example_id": row["example_id"], "pass": outcome}
        for row, outcome in zip(cases["tasks"], cases["paired_base"], strict=True)
    ]
    tuned = [
        {"example_id": row["example_id"], "pass": outcome}
        for row, outcome in zip(cases["tasks"], cases["paired_tuned"], strict=True)
    ]
    report = uncertainty(base, tuned)
    delta = sum(cases["paired_tuned"]) / len(tuned) - sum(cases["paired_base"]) / len(base)
    assert report["delta"][0] <= delta <= report["delta"][1]
    assert uncertainty(base, base)["delta"] == [0.0, 0.0]
    with pytest.raises(ValueError, match="IDs"):
        uncertainty(base, list(reversed(tuned)))


def test_seeded_ids_do_not_hide_leakage(cases: dict) -> None:
    """A new ID or paraphrase cannot make identical solver inputs independent."""
    train, duplicate, paraphrase, independent = cases["tasks"]
    assert not audit_rows([duplicate], [train])["clean"]
    assert not audit_rows([paraphrase], [train])["clean"]
    assert not audit_rows([train, duplicate], [])["clean"]
    assert not audit_rows([], [train])["clean"]
    assert audit_rows([independent], [train])["clean"]


def test_adapter_is_loaded_once_and_checked_before_gpu_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use the saved adapter directory, never a fresh random adapter wrapper."""
    adapter_config = tmp_path / "adapter_config.json"
    adapter_config.write_text(json.dumps({"base_model_name_or_path": "base"}))
    (tmp_path / "adapter_model.safetensors").touch()
    loader = Mock(return_value=("model", "processor"))
    monkeypatch.setitem(sys.modules, "unsloth", SimpleNamespace(
        FastLanguageModel=SimpleNamespace(from_pretrained=loader)
    ))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        bfloat16="bf16", cuda=SimpleNamespace(is_available=lambda: True)
    ))
    assert runtime.load_checkpoint("base", str(tmp_path)) == ("model", "processor")
    loader.assert_called_once_with(
        model_name=str(tmp_path), dtype="bf16", load_in_4bit=False,
        device_map={"": "cuda:0"},
    )
    loader.reset_mock()
    with pytest.raises(ValueError, match="base model"):
        runtime.load_checkpoint("wrong-base", str(tmp_path))
    loader.assert_not_called()


def test_export_merges_saved_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Export must load trained weights, merge safely, and save the merged model."""
    config = {
        "model": {"name": "base", "dtype": "bfloat16"},
        "output": {"dir": str(tmp_path / "out"), "adapter_name": "adapter"},
    }
    adapter = tmp_path / "out" / "adapter"
    adapter.mkdir(parents=True)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    model, merged, tokenizer = Mock(), Mock(), Mock()
    model.merge_and_unload.return_value = merged
    loader = Mock(return_value=(model, tokenizer))
    monkeypatch.setattr(export, "load_checkpoint", loader)
    monkeypatch.setattr(sys, "argv", ["export.py", str(config_path)])
    export.main()
    loader.assert_called_once_with(
        model_name="base", adapter_path=str(adapter), dtype="bfloat16"
    )
    model.merge_and_unload.assert_called_once_with(safe_merge=True)
    merged.save_pretrained.assert_called_once()
    model.save_pretrained.assert_not_called()
    manifest = json.loads((tmp_path / "out" / "hash_manifest.json").read_text())
    assert "../config.json" in manifest["files"]
    assert manifest["total_size_bytes"] == sum(
        item["size_bytes"] for item in manifest["files"].values()
    )


def test_interrupted_evaluation_retains_completed_raw_rows(
    cases: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later generation failure must retain prior answers and prevent mixing runs."""
    case = cases["comparisons"][-1]
    tasks = [dict(row, expected={"answer": case["reference"]}) for row in cases["tasks"]]
    generator = Mock(side_effect=[case["candidate"], RuntimeError("simulated interruption")])
    monkeypatch.setattr(evaluator, "generate_answer", generator)
    output = tmp_path / "base.jsonl"
    with pytest.raises(RuntimeError, match="simulated interruption"):
        evaluator.evaluate_model("base", Mock(), Mock(), tasks, output)
    retained = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(retained) == 1
    assert retained[0]["example_id"] == tasks[0]["example_id"]
    assert retained[0]["model_answer"] == case["candidate"]
    with pytest.raises(FileExistsError):
        evaluator.evaluate_model("base", Mock(), Mock(), tasks, output)
