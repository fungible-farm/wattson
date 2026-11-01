"""Derived questions are graded by value, whatever the wording, and a wrong sum or a wrong line fails."""

from __future__ import annotations

from wattson.datasets import derived


def row(kind: str, value: float, line: int | None = None) -> dict:
    return {"task": {"inputs": {"case": "ieee39", "kind": kind, "candidate_lines": [1, 2, 3]}},
            "expected": {"structured": {"value": value, **({"line": line} if line is not None else {})}}}


def test_a_sum_must_be_exact_and_can_be_worded_any_way() -> None:
    task = row("sum_violations", 237.0)
    assert derived.grade(task, "The total is 237.")[0]
    assert derived.grade(task, "Across the three outages: 80 + 79 + 78 = 237 voltage violations.")[0]
    assert not derived.grade(task, "The total is 238.")[0]


def test_a_mean_and_a_difference_use_the_loading_tolerance() -> None:
    mean = row("mean_loading", 97.84)
    assert derived.grade(mean, "mean max loading = 97.8 %")[0], "within 0.1 percentage points"
    assert not derived.grade(mean, "mean max loading = 98.5 %")[0]
    diff = row("loading_difference", -3.25)
    assert derived.grade(diff, "It is -3.25 percentage points (lower).")[0]
    assert not derived.grade(diff, "It is 3.25 points higher.")[0], "the sign matters"


def test_the_ranking_needs_the_right_line_and_a_voltage_within_1e_3() -> None:
    task = row("min_voltage_outage", 0.8992, line=2)
    assert derived.grade(task, "Line 2, min bus voltage = 0.8992 pu")[0]
    assert derived.grade(task, "Line 2, min bus voltage = 0.90 pu")[0], "0.8992 and 0.9000 are the same voltage"
    assert not derived.grade(task, "Line 1, min bus voltage = 0.8992 pu")[0]
    assert not derived.grade(task, "Line 2, min bus voltage = 0.91 pu")[0]


def test_every_prompt_names_its_lines() -> None:
    for kind, template in derived.PROMPTS.items():
        text = template.format(case="ieee39", lines="1, 2 and 3", a=1, b=2)
        assert "ieee39" in text and ("1" in text), kind


def test_the_frozen_suite_is_unchanged_and_its_expected_answers_pass_their_own_grader() -> None:
    """The shipped 40-task suite matches its manifest hash, and each row's expected answer is graded correct."""
    import hashlib
    import json
    from pathlib import Path

    suite = Path(__file__).resolve().parents[2] / "data" / "suites" / "derived-heldout-20261030"
    manifest = json.loads((suite / "manifest.json").read_text())
    assert hashlib.sha256((suite / "examples.jsonl").read_bytes()).hexdigest() == manifest["examples_sha256"]
    rows = [json.loads(line) for line in (suite / "examples.jsonl").read_text().splitlines() if line.strip()]
    assert len(rows) == manifest["n_tasks"] == 40
    for row in rows:
        structured = row["expected"]["structured"]
        kind = row["task"]["inputs"]["kind"]
        answer = f"Line {structured['line']}, min bus voltage = {structured['value']} pu" if kind == "min_voltage_outage" else row["expected"]["answer"]
        assert derived.grade(row, answer)[0], row["example_id"]
