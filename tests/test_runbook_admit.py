"""Tests for scripts/runbook_admit.py: admit or reject one runbook into the catalog (#130 step d) with no model call.

The container is unavailable, so `run_sql` is monkeypatched exactly as tests/test_runbook_replay.py does — the fixtures, canned
answers and cases are imported from that module rather than repeated. The interesting surface here is the decision: the accept path,
each of the five rejection reasons in `rejection` plus validate and leakage, and what the catalog file ends up holding — one entry
per id, replaced not duplicated, rejected entries kept as evidence, and byte-identical output for an identical re-run. A catalog
that cannot be read, or is not {accepted: [], rejected: []}, exits 2 before anything is decided.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]


def load(rel: str, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


admit = load("scripts/runbook_admit.py", "runbook_admit")
base = load("tests/test_runbook_replay.py", "runbook_replay_fixtures")  # RUNBOOK, QUESTIONS, ANSWERS, fake run_sql

# Lint gold: the question text, SQL and answers admission must not see in the runbook. No `sql` — the replay questions carry it.
GOLD_ITEMS = [
    {"id": "M1", "question": "Which region in the March window reported the highest average score above the floor?",
     "values": [1234.5, "Sydney"]},
    {"id": "M2", "question": "Which region in the April window reported the highest average score above the floor?",
     "values": [87.25, "Newcastle"]},
    {"id": "M3", "question": "Which region in the May window reported the highest average score above the floor?",
     "values": [60.0]},
]
SOURCE = {"question_id": "M1", "role": "source", "params": {"region": "SA1", "threshold": "100"}}
SIBLINGS = [{"question_id": qid, "role": "sibling", "params": {"region": region, "threshold": threshold}}
            for qid, region, threshold in (("M2", "VIC1", "200"), ("M3", "QLD1", "300"))]


def patch(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_sql(sql: str) -> dict[str, Any]:
        if sql in base.ANSWERS:
            return {"columns": ["region", "val"], "rows": base.ANSWERS[sql]}
        return {"error": f"no answer for {sql}"}

    monkeypatch.setattr(admit.replay.arm1, "run_sql", fake_run_sql)


def args(tmp_path: Path, cases: list[Any] | None = None, runbook: dict[str, Any] | None = None,
         gold: list[Any] | None = None) -> list[str]:
    """Write the four inputs and return the argv for them; the catalog path may or may not exist yet."""
    for name, data in (("r", runbook or base.RUNBOOK), ("c", base.CASES if cases is None else cases),
                       ("q", base.QUESTIONS), ("g", GOLD_ITEMS if gold is None else gold)):
        (tmp_path / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    at = lambda name: str(tmp_path / f"{name}.json")
    return ["--runbook", at("r"), "--cases", at("c"), "--questions", at("q"), "--gold", at("g"), "--catalog", at("catalog")]


def catalog(tmp_path: Path) -> dict[str, list[Any]]:
    return json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))


def test_accept_writes_one_accepted_entry_carrying_the_full_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    patch(monkeypatch)
    assert admit.main(args(tmp_path)) == 0
    assert capsys.readouterr().out.startswith(
        f"ACCEPTED {base.RUNBOOK['id']}: validator, leakage lint, source replay and siblings all pass")
    assert catalog(tmp_path)["rejected"] == []
    entry, = catalog(tmp_path)["accepted"]
    assert [entry["id"], entry["family"]] == [base.RUNBOOK["id"], base.RUNBOOK["family"]]
    assert entry["runbook_sha256"] == hashlib.sha256((tmp_path / "r.json").read_bytes()).hexdigest()
    assert entry["evidence"] == {"validate": True, "leakage": True, "replay_passed": 3, "replay_total": 3,
                                 "siblings_passed": 2, "siblings_total": 2}


def sibling(question_id: str, region: str, threshold: str) -> dict[str, Any]:
    return {"question_id": question_id, "role": "sibling", "params": {"region": region, "threshold": threshold}}


# (label, change to (cases, runbook), reason prefix, evidence validate/leakage/replay_total/siblings_total)
BREAKING = [
    ("invalid-runbook", lambda r, c: (c, dict(r, status="wip")), "validate: ", (False, None, 0, 0)),
    ("leaked-question", lambda r, c: (c, dict(r, notes=[GOLD_ITEMS[0]["question"]])),
     "leakage: FAIL question: M1 quotes", (True, False, 0, 0)),
    ("leaked-value", lambda r, c: (c, dict(r, notes=["the reported answer is 1234.5 for the March window"])),
     "leakage: FAIL values: M1 gold value 1234.5", (True, False, 0, 0)),
    ("source-fails", lambda r, c: ([dict(SOURCE, params={"region": "SA1", "threshold": "105"}), *SIBLINGS], r),
     "replay source M1: no single returned row", (True, True, 3, 2)),
    ("no-source", lambda r, c: (list(SIBLINGS), r), "replay source: no source case", (True, True, 2, 2)),
    ("one-sibling", lambda r, c: ([SOURCE, SIBLINGS[0]], r), "siblings: 1 sibling case(s), need at least 2",
     (True, True, 2, 1)),
    ("shared-question-id", lambda r, c: ([SOURCE, sibling("M1", "SA1", "100"), SIBLINGS[0]], r),
     "siblings: M1 replay(s) as both source and sibling", (True, True, 3, 2)),
    ("sibling-fails", lambda r, c: ([SOURCE, SIBLINGS[0], sibling("M3", "NSW1", "300")], r), "replay sibling M3: ",
     (True, True, 3, 2)),
]


@pytest.mark.parametrize("change, reason, evidence", [(c, r, e) for _, c, r, e in BREAKING], ids=[case[0] for case in BREAKING])
def test_every_rejection_reason_is_recorded_and_nothing_is_accepted(
    change: Any, reason: str, evidence: tuple[Any, ...], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    patch(monkeypatch)
    cases, runbook = change(base.RUNBOOK, list(base.CASES))
    assert admit.main(args(tmp_path, cases, runbook)) == 0, "a rejection is a decision, not a failed run"
    assert catalog(tmp_path)["accepted"] == []
    entry, = catalog(tmp_path)["rejected"]
    assert entry["decision_reason"].startswith(reason)
    assert f"REJECTED {entry['id']}: {reason}" in capsys.readouterr().out
    got = entry["evidence"]
    assert [got["validate"], got["leakage"], got["replay_total"], got["siblings_total"]] == list(evidence)


@pytest.mark.parametrize("bad", ["[1, 2]", "not json at all", '{"accepted": {}, "rejected": []}', '{"accepted": []}'])
def test_a_wrong_shaped_catalog_exits_two_and_is_left_untouched(bad: str, tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    patch(monkeypatch)
    argv = args(tmp_path)
    (tmp_path / "catalog.json").write_text(bad, encoding="utf-8")
    assert admit.main(argv) == 2
    err = capsys.readouterr().err
    assert err.startswith("ERROR runbook-admit:") and "catalog" in err
    assert (tmp_path / "catalog.json").read_text(encoding="utf-8") == bad, "a run that decided nothing writes nothing"


def test_readmitting_one_id_replaces_its_entry_rather_than_duplicating_it(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch)
    assert admit.main(args(tmp_path, [SOURCE, SIBLINGS[0]])) == 0  # one sibling only: rejected
    assert [entry["id"] for entry in catalog(tmp_path)["rejected"]] == [base.RUNBOOK["id"]]
    assert admit.main(args(tmp_path)) == 0
    after = catalog(tmp_path)
    assert [after["rejected"], [entry["id"] for entry in after["accepted"]]] == [[], [base.RUNBOOK["id"]]]


def test_two_identical_runs_write_identical_catalog_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch)
    assert admit.main(args(tmp_path)) == 0
    first = (tmp_path / "catalog.json").read_bytes()
    assert admit.main(args(tmp_path)) == 0
    assert (tmp_path / "catalog.json").read_bytes() == first
