"""Tests for scripts/runbook_replay.py: fill a runbook, run it, score it against the question's gold SQL (#130 step c).

The container is not available here, so `run_sql` is monkeypatched with canned answers keyed by the exact statement text — which
also proves the fill, since an unfilled or mis-filled template finds no answer and reports the container error instead. The first
test runs the three cases admission wants (one source, two siblings) through the real `nem_scoring.score`; the rest check the exits,
the parameter rules that keep a bad value from ever reaching DuckDB, and the evidence file the next step admits or rejects on.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
REPLAY: Path = REPO / "scripts" / "runbook_replay.py"

TEMPLATE = "SELECT region, val FROM fact WHERE region = {region} AND val > {threshold}"
GOLD = {"M1": "SELECT region, val FROM fact WHERE region = 'SA1' AND val > 100",
        "M2": "SELECT region, val FROM fact WHERE region = 'VIC1' AND val > 200",
        "M3": "SELECT region, val FROM fact WHERE region = 'QLD1' AND val > 300"}
QUESTIONS = [{"id": key, "question": f"question {key}", "sql": sql, "decimals": 1} for key, sql in GOLD.items()]
ROWS = {"SA1": [["SA1", 1234.5]], "VIC1": [["VIC1", 87.25]], "QLD1": [["QLD1", 60.0]]}


def filled(region: str, threshold: str) -> str:
    return TEMPLATE.replace("{region}", region).replace("{threshold}", threshold)


# The wrong-value answer is the one that reaches the scorer: right shape, wrong number, so the reason names the mismatch.
ANSWERS: dict[str, Any] = {GOLD[k]: ROWS[r] for k, r in (("M1", "SA1"), ("M2", "VIC1"), ("M3", "QLD1"))}
# The filled statements are unquoted (the template carries no quotes), so each is its own key; NSW1 stays unanswered on purpose.
ANSWERS.update({filled(r, t): ROWS[r] for r, t in (("SA1", "100"), ("VIC1", "200"), ("QLD1", "300"))})
ANSWERS[filled("SA1", "105")] = [["SA1", 42.0]]

CASES = [{"question_id": q, "role": role, "params": {"region": r, "threshold": t}}
         for q, role, r, t in (("M1", "source", "SA1", "100"), ("M2", "sibling", "VIC1", "200"), ("M3", "sibling", "QLD1", "300"))]

RUNBOOK: dict[str, Any] = {
    "id": "replay-region-average", "family": "region-average", "status": "draft",
    "source": {"adapter": "manual", "run_id": "replay-test", "journal_sha256": "0" * 64},
    "parameters": [{"name": "region", "type": "string", "description": "the region to read"},
                   {"name": "threshold", "type": "number", "description": "the floor on the value"}],
    "sql_template": TEMPLATE,
    "steps": [{"cmd": "run the filled statement", "pass": "a row comes back"}],
    "notes": [],
    "manual_refs": [{"document": "operations-manual.md", "section": "Reading the fact table", "sha256": "1" * 64}],
}


def load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replay = load(REPLAY, "runbook_replay")


def patch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Canned answers keyed by exact SQL; returns the list of statements the fake was asked to run."""
    seen: list[str] = []

    def fake(sql: str) -> dict[str, Any]:
        seen.append(sql)
        return {"columns": ["region", "val"], "rows": ANSWERS[sql]} if sql in ANSWERS else {"error": f"no answer for {sql[:40]}"}

    monkeypatch.setattr(replay.arm1, "run_sql", fake)
    return seen


def inputs(tmp_path: Path, cases: list[Any], runbook: dict[str, Any] = RUNBOOK) -> list[str]:
    for name, data in (("r", runbook), ("c", cases), ("q", QUESTIONS)):
        (tmp_path / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    return ["--runbook", str(tmp_path / "r.json"), "--cases", str(tmp_path / "c.json"),
            "--questions", str(tmp_path / "q.json")]


def case(**kwargs: Any) -> dict[str, Any]:
    return {"question_id": "M1", "role": "source", "params": {"region": "SA1", "threshold": "100"}, **kwargs}


# --------------------------------------------------------------------------------------------- the verdict comes from the scorer


def test_every_case_passes_and_the_two_siblings_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    patch(monkeypatch)
    assert replay.main(inputs(tmp_path, CASES)) == 0
    printed = capsys.readouterr().out
    assert "PASS replay M1 source: a returned row contains every expected value" in printed
    assert "PASS replay M3 sibling:" in printed
    assert "PASS runbook-replay: 3/3 cases, siblings 2/2" in printed


def test_a_wrong_value_fails_on_the_scorer_not_on_the_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    """Filled with 105 the statement runs and answers, so only the strict comparison can catch it."""
    seen = patch(monkeypatch)
    assert replay.main(inputs(tmp_path, [case(params={"region": "SA1", "threshold": "105"})])) == 1
    printed = capsys.readouterr().out
    assert "FAIL replay M1 source: no single returned row contains every expected value" in printed
    assert seen == [GOLD["M1"], filled("SA1", "105")]


def test_the_summary_counts_siblings_apart_when_one_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    patch(monkeypatch)
    sibling = {"question_id": "M3", "role": "sibling", "params": {"region": "NSW1", "threshold": "300"}}
    assert replay.main(inputs(tmp_path, [CASES[0], CASES[1], sibling])) == 1
    assert "FAIL runbook-replay: 2/3 cases, siblings 1/2" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------ parameters never reach SQL


def test_a_missing_parameter_fails_the_case_without_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    seen = patch(monkeypatch)
    assert replay.main(inputs(tmp_path, [case(params={"region": "SA1"})])) == 1
    assert "FAIL replay M1 source: missing parameter(s): threshold" in capsys.readouterr().out
    assert seen == []


def test_an_extra_parameter_fails_the_case_without_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    seen = patch(monkeypatch)
    assert replay.main(inputs(tmp_path, [case(params={"region": "SA1", "threshold": "100", "limit": "5"})])) == 1
    assert "FAIL replay M1 source: undeclared parameter(s): limit" in capsys.readouterr().out
    assert seen == []


@pytest.mark.parametrize("value", ["SA1'; DROP TABLE fact", "SA1\nAND 1=1", "SA1`x`", "rrp -- hide"])
def test_an_unsafe_parameter_value_fails_the_case_without_running(value: str, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    seen = patch(monkeypatch)
    assert replay.main(inputs(tmp_path, [case(params={"region": value, "threshold": "100"})])) == 1
    printed = capsys.readouterr().out
    assert "FAIL replay M1 source: unsafe parameter region:" in printed
    assert seen == []


def test_a_question_that_is_not_in_the_file_fails_without_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    seen = patch(monkeypatch)
    assert replay.main(inputs(tmp_path, [case(question_id="M9")])) == 1
    assert "FAIL replay M9 source: no question 'M9' in the questions file" in capsys.readouterr().out
    assert seen == []


# ---------------------------------------------------------------------------------------------------------------------- the exits


@pytest.mark.parametrize("break_it", ["missing_key", "undeclared_slot"])
def test_an_invalid_runbook_exits_one_before_any_case(break_it: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    seen = patch(monkeypatch)
    runbook = dict(RUNBOOK)
    if break_it == "missing_key":
        runbook.pop("notes")
    else:
        runbook["sql_template"] = TEMPLATE + " AND x = {extra}"
    assert replay.main(inputs(tmp_path, CASES, runbook)) == 1
    printed = capsys.readouterr().out
    assert "FAIL runbook-valid" in printed and "runbook-replay" not in printed
    assert seen == []


def test_an_unreadable_or_malformed_input_exits_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    patch(monkeypatch)
    argv = inputs(tmp_path, CASES)
    (tmp_path / "r.json").unlink()
    assert replay.main(argv) == 2, "a runbook that cannot be read is an input error, not a case verdict"
    (tmp_path / "r.json").write_text(json.dumps(RUNBOOK), encoding="utf-8")
    (tmp_path / "c.json").write_text("{not json", encoding="utf-8")
    assert replay.main(argv) == 2
    (tmp_path / "q.json").write_text('{"id": "M1"}', encoding="utf-8")
    assert replay.main(argv) == 2, "the questions file has to be a list of cases, too"


# ----------------------------------------------------------------------------------------------------------------- the evidence


def test_the_evidence_file_has_the_expected_shape_and_two_runs_are_the_same_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch(monkeypatch)
    out = tmp_path / "evidence.json"
    argv = inputs(tmp_path, CASES) + ["--out", str(out)]
    assert replay.main(argv) == 0
    first, evidence = out.read_bytes(), json.loads(out.read_text(encoding="utf-8"))
    assert sorted(evidence) == ["cases", "runbook_id", "runbook_sha256"]
    assert evidence["runbook_id"] == RUNBOOK["id"]
    assert evidence["runbook_sha256"] == hashlib.sha256((tmp_path / "r.json").read_bytes()).hexdigest()
    assert [sorted(item) for item in evidence["cases"]] == [["filled_sql", "passed", "question_id", "reason", "role"]] * 3
    assert evidence["cases"][0]["filled_sql"] == filled("SA1", "100")
    assert [item["passed"] for item in evidence["cases"]] == [True, True, True]
    replay.main(argv)
    assert out.read_bytes() == first


def test_evidence_records_a_failed_case_and_a_runbook_that_never_ran_writes_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch(monkeypatch)
    out = tmp_path / "evidence.json"
    argv = inputs(tmp_path, [case(params={"region": "SA1"})]) + ["--out", str(out)]
    assert replay.main(argv) == 1
    failed = json.loads(out.read_text(encoding="utf-8"))["cases"][0]
    assert failed["passed"] is False and failed["filled_sql"] is None
    broken = dict(RUNBOOK, status="wip")
    assert replay.main(inputs(tmp_path, CASES, broken) + ["--out", str(tmp_path / "none.json")]) == 1
    assert not (tmp_path / "none.json").exists()
