"""CPU gates for the r6 driver: the smoke gate decides which students are evaluated."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattson.training import run_agent_study as driver


def test_a_student_failing_its_smoke_gate_is_neither_evaluated_nor_compared(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Qwen student passes 12/20, Granite student 3/20: only Granite's student is dropped."""
    smoke_passes = {"qwen-student": 12, "granite-student": 3}
    sessions: list[tuple[str, str, bool]] = []

    def fake_session(label: str, tag: str, _source: Path, _path: str, _fraction: float, root: Path,
                     _events: object, suites: list, latency: bool, restorebench: bool = False,
                     strict_level: str = "auto") -> None:
        sessions.append((label, tag, latency))
        for _stage, _tasks, out, *_choice in suites:
            out.mkdir()
            if tag == "smoke":
                (out / "summary.json").write_text(json.dumps({"n_pass": smoke_passes[label], "n_tasks": 20}))

    monkeypatch.setattr(driver, "frozen_task_count", lambda _path: 20)
    monkeypatch.setattr(driver, "verify_student", lambda directory, _events: directory / "merged-bf16")
    monkeypatch.setattr(driver, "session", fake_session)
    compared: list[str] = []
    monkeypatch.setattr(driver, "write_comparison",
                        lambda first, second, *_a: compared.append(f"{first.name}-vs-{second.name}")
                        or {"delta_percentage_points": 0.0})
    monkeypatch.setattr(driver, "write_numerical_comparison", lambda *_a: {"delta_percentage_points": 0.0})
    root = tmp_path / "r6"
    root.mkdir()
    journal = tmp_path / "pipeline.jsonl"
    with journal.open("x") as stream:
        driver.run(driver.argparse.Namespace(root=root), stream)
    assert sessions == [("qwen-student", "smoke", False), ("granite-student", "smoke", False),
                        ("qwen-base", "benchmark", True), ("qwen-student", "benchmark", True),
                        ("granite-base", "benchmark", True), ("qwen-base", "restorebench", False),
                        ("qwen-student", "restorebench", False), ("granite-base", "restorebench", False)]
    assert compared == ["qwen-base-vs-qwen-student", "qwen-base-vs-granite-base", "qwen-student-vs-granite-base"]
    final = json.loads(journal.read_text().splitlines()[-1])
    assert final["kind"] == "pipeline_complete" and final["payload"]["smoke"] == {
        "qwen-student": True, "granite-student": False}


def test_memory_peak_is_the_drop_from_the_pre_start_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unified memory: the server's footprint is baseline minus the lowest sample."""
    readings = iter([100, 90, 60, 80])
    monkeypatch.setattr(driver, "meminfo_bytes", lambda *_a: next(readings))
    sampler = driver.MemorySampler(tmp_path / "memory.jsonl")
    sampler.path.write_text("".join(json.dumps({"unix_time": t, "available": a}) + "\n"
                                    for t, a in ((1.0, 90), (2.0, 60), (3.0, 80))))
    assert sampler.peak() == {"baseline_available": 100, "min_available": 60, "peak_used": 40}
    assert sampler.peak(since=3.0)["peak_used"] == 20


def test_strict_arm_is_a_2x2_per_model_compared_with_its_own_baseline(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Backlog 0011: one server per strict level, both first-turn choices on each,
    and each comparison names exactly the conditions it spans."""
    sessions, compared = [], []
    monkeypatch.setattr(driver, "verify_student", lambda directory, _events: directory / "merged-bf16")
    monkeypatch.setattr(driver, "session", lambda label, tag, *_a, strict_level="auto", **_k:
                        sessions.append((label, tag, strict_level, [suite[3] for suite in _a[5]])))
    monkeypatch.setattr(driver, "write_comparison", lambda first, second, _dest, _events, spans:
                        compared.append((second.name, spans)) or {"delta_percentage_points": 0.0})
    monkeypatch.setattr(driver, "write_numerical_comparison", lambda *_a: {"delta_percentage_points": 0.0})
    root = tmp_path / "strict"
    root.mkdir()
    with (tmp_path / "events.jsonl").open("x") as stream:
        driver.strict_arm(driver.argparse.Namespace(root=root), stream)
    assert sessions[:2] == [("qwen-base", "strict-auto", "auto", ["auto", "required"]),
                            ("qwen-base", "strict-parameter", "parameter", ["auto", "required"])]
    assert len(sessions) == 8
    assert compared[:3] == [
        ("qwen-base-strict-auto-first-required", frozenset({"first_turn_tool_choice"})),
        ("qwen-base-strict-parameter-first-auto", frozenset({"server_tool_strict_level"})),
        ("qwen-base-strict-parameter-first-required",
         frozenset({"server_tool_strict_level", "first_turn_tool_choice"}))]


def test_prompted_arm_runs_each_base_and_compares_with_its_plain_run_and_student(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G0 baseline (b): one prompted smoke run per base, two named-condition comparisons each."""
    r6, strict = tmp_path / "r6b", tmp_path / "strict"
    for directory in (strict / "qwen-base-strict-auto-first-auto", r6 / "qwen-student-smoke",
                      strict / "granite-base-strict-auto-first-auto"):
        directory.mkdir(parents=True)
        (directory / "summary.json").write_text("{}")
    sessions, compared = [], []
    monkeypatch.setattr(driver, "session", lambda label, tag, *_a, evaluator=driver.EVALUATOR, **_k:
                        sessions.append((label, tag, evaluator)))
    monkeypatch.setattr(driver, "write_comparison", lambda first, second, _dest, _events, spans:
                        compared.append((first.name, second.name, spans)) or {"delta_percentage_points": 0.0})
    monkeypatch.setattr(driver, "write_numerical_comparison", lambda *_a: {"delta_percentage_points": 0.0})
    root = tmp_path / "prompted"
    root.mkdir()
    with (tmp_path / "events.jsonl").open("x") as stream:
        driver.prompted_arm(driver.argparse.Namespace(root=root, r6_root=r6, strict_root=strict), stream)
    assert sessions == [("qwen-base", "prompted", driver.PROMPTED_EVALUATOR),
                        ("granite-base", "prompted", driver.PROMPTED_EVALUATOR)]
    spans = frozenset({"system_prompt_variant"})
    assert compared == [("qwen-base-strict-auto-first-auto", "qwen-base-prompted-smoke", spans),
                        ("qwen-student-smoke", "qwen-base-prompted-smoke", spans),
                        ("granite-base-strict-auto-first-auto", "granite-base-prompted-smoke", spans)]
    final = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])
    assert final["kind"] == "pipeline_complete"


def test_restorebench_arm_reruns_the_pilot_for_exactly_the_models_r6_evaluated(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """r6b's pilot failed after its comparisons (FM-24); the rerun keeps r6's evaluated list."""
    r6 = tmp_path / "r6b"
    r6.mkdir()
    (r6 / "pipeline.jsonl").write_text(json.dumps(
        {"kind": "comparisons_complete", "payload": {"evaluated": ["qwen-base", "granite-student"]}}) + "\n")
    sessions = []
    monkeypatch.setattr(driver, "verify_student", lambda directory, _events: directory)
    monkeypatch.setattr(driver, "session", lambda label, tag, *_a, restorebench=False, **_k:
                        sessions.append((label, tag, restorebench)))
    root = tmp_path / "pilot"
    root.mkdir()
    with (tmp_path / "events.jsonl").open("x") as stream:
        driver.restorebench_arm(driver.argparse.Namespace(root=root, r6_root=r6), stream)
    assert sessions == [("qwen-base", "restorebench", True), ("granite-student", "restorebench", True)]
    final = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])
    assert final["kind"] == "pipeline_complete" and final["payload"]["arm"] == "restorebench-pilot"


def test_restorebench_arm_refuses_an_r6_root_without_comparisons(tmp_path: Path) -> None:
    (tmp_path / "pipeline.jsonl").write_text(json.dumps({"kind": "driver_error", "payload": {}}) + "\n")
    with (tmp_path / "events.jsonl").open("x") as stream, pytest.raises(RuntimeError, match="comparisons_complete"):
        driver.restorebench_arm(driver.argparse.Namespace(root=tmp_path, r6_root=tmp_path), stream)
