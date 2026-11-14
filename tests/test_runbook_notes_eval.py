"""Tests for scripts/runbook_notes_eval.py: do notes from accepted runbooks beat arm 1's plain prompt (#130 notes-eval)?

No model and no container: `ask_model` records the prompt each arm was shown and returns a canned statement, `run_sql` answers every
statement with one row, and `main()` runs on a patched sys.argv. The scores come from the real selector; THRESHOLD (0.10, lowered
after the scores were seen) stays the script's own constant.
"""
from __future__ import annotations

import hashlib, importlib.util, json, sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RBS, FLAG = REPO / "benchmarks" / "runbooks", "- the data holds exactly the intervals of September 2025"
AVG, LOWEST = "What was the simple average price (rrp) in VIC1 over September 2025?", "Which region had the lowest price?"
SCHEMA0 = "Table price_demand(region VARCHAR, settlement_ts TIMESTAMP, total_demand_mw DOUBLE, rrp DOUBLE, period_type VARCHAR)"
NOTES = "Notes from verified runbooks:"
spec = importlib.util.spec_from_file_location("runbook_notes_eval", REPO / "scripts" / "runbook_notes_eval.py")
assert spec is not None and spec.loader is not None
ev = importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)


def ids(chosen: list[dict]) -> list[str]:
    return [str(rb.get("id")) for rb in chosen]


def patch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Canned model and container; returns the prompts both arms were shown, in the order main() ran them."""
    seen: list[str] = []
    monkeypatch.setattr(ev.arm1, "PROMPT", ev.arm1.PROMPT)  # run_one() swaps the module-level prompt; put it back afterwards
    monkeypatch.setattr(ev.arm1, "ask_model", lambda url, model, text: (seen.append(ev.arm1.PROMPT), ("SELECT 1", ""))[1])
    monkeypatch.setattr(ev.arm1, "run_sql", lambda sql: {"columns": ["v"], "rows": [[1.0]]})
    return seen


# ---------------------------------------------------------------------------------- the selector picks who gets notes
def test_the_same_question_selects_the_same_runbooks_every_time() -> None:
    tied = [{"id": i, "family": "average"} for i in ("b-avg", "a-avg")]
    assert ids(ev.selector("average", tied)) == ids(ev.selector("average", tied)) == ["a-avg", "b-avg"]


def test_a_runbook_that_is_not_accepted_never_reaches_the_selector(tmp_path: Path) -> None:
    for rid in ("kept", "unlisted"):
        (tmp_path / rid).mkdir()
        (tmp_path / rid / "runbook.json").write_text(json.dumps({"id": rid, "family": "region-window-average"}), encoding="utf-8")
    chosen = ev.accepted({"accepted": [{"id": "kept"}], "rejected": [{"id": "unlisted"}]}, str(tmp_path))
    both = [json.loads((tmp_path / rid / "runbook.json").read_text(encoding="utf-8")) for rid in ("kept", "unlisted")]
    assert ids(chosen) == ["kept"] == ids(ev.selector("average window region", chosen))
    assert ids(ev.selector("average window region", both)) == ["kept", "unlisted"], "both would score, so accepted() drops it"


def test_the_average_question_scores_over_threshold_and_the_lowest_price_question_does_not() -> None:
    runbooks = ev.accepted(json.loads((RBS / "catalog.json").read_text(encoding="utf-8")), str(RBS))
    vocab = ev.vocabulary(runbooks[0])
    # AVG shares 5 of the 36 vocabulary tokens (average, over, rrp, the, vic1): 0.139, over THRESHOLD. LOWEST shares 2 (region, the):
    # 0.056, so it gets no notes - 'lowest' is nowhere in the runbook, exactly as much as a clearly unrelated question would be.
    assert ids(ev.selector(AVG, runbooks)) == ["region-window-average"] and ev.selector(LOWEST, runbooks) == []
    assert len(ev.tokens(AVG) & vocab) / len(vocab) >= ev.THRESHOLD > len(ev.tokens(LOWEST) & vocab) / len(vocab)


# --------------------------------------------------------------------------------------------------------- the prompts
def test_the_base_prompt_drops_the_pr_141_line_and_the_notes_arm_sits_before_the_answer() -> None:
    lines, base = ev.arm1.SCHEMA.split("\n"), ev.base_prompt()
    assert FLAG not in base and len([l for l in lines if l in base]) == len(lines) - 1  # every other schema line survives
    assert base.endswith(ev.PROMPT0.split(ev.ANSWER, 1)[1]) and ev.notes_prompt(base, []) == base
    head = ev.notes_prompt(base, ["first note", "second note"]).split(ev.ANSWER, 1)[0]
    assert head.index(SCHEMA0) < head.index(NOTES) and head.endswith(NOTES + "\n- first note\n- second note")


def test_a_schema_without_that_line_exits_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ev.arm1, "SCHEMA", "\n".join(l for l in ev.arm1.SCHEMA.split("\n") if not l.startswith(FLAG)))
    with pytest.raises(SystemExit) as stop: ev.base_prompt()
    assert "FATAL runbook-notes-eval: cannot rebuild the pre-#141 prompt" in str(stop.value)


# -------------------------------------------------------------------------------------------------- the evidence file
def test_the_evidence_records_the_threshold_and_its_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen, out = patch(monkeypatch), tmp_path / "EVIDENCE.json"
    monkeypatch.setattr(sys, "argv", ["runbook_notes_eval.py", "--catalog", str(RBS / "catalog.json"),
                                      "--runbooks-dir", str(RBS), "--out", str(out)])
    ev.main()
    e = json.loads(out.read_text(encoding="utf-8"))
    notes = json.loads((RBS / "region-window-average" / "runbook.json").read_text(encoding="utf-8"))["notes"]
    assert e["selector_threshold"] == ev.THRESHOLD == 0.10 and e["threshold_set_after_seeing_scores"] is True
    n = len(ev.arm1.QUESTIONS)  # the built-in set, not a hard-coded 10
    assert e["pass"] == {"base": n, "notes": n} and all(FLAG not in p for p in seen)
    assert [NOTES in p for p in seen] == [r["arm"] == "notes" and bool(r["selected"]) for r in e["results"]]
    q4 = [i for i, r in enumerate(e["results"]) if r["id"] == "Q4"]
    assert all(f"- {n}" in seen[q4[1]] for n in notes) and all(n not in seen[q4[0]] for n in notes)


def test_a_questions_file_replaces_the_built_in_set_and_is_fingerprinted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    qs, out = tmp_path / "heldout.json", tmp_path / "EVIDENCE.json"
    qs.write_text(json.dumps([{"id": "H1", "question": AVG, "sql": "SELECT 1"}, {"id": "H2", "question": LOWEST, "sql": "SELECT 2"}]), encoding="utf-8")
    patch(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["runbook_notes_eval.py", "--catalog", str(RBS / "catalog.json"), "--runbooks-dir", str(RBS),
                                      "--out", str(out), "--questions", str(qs)])
    ev.main()
    e = json.loads(out.read_text(encoding="utf-8"))
    assert e["questions_file"] == str(qs) and e["questions_sha256"] == hashlib.sha256(qs.read_bytes()).hexdigest()
    assert {arm: [r["id"] for r in e["results"] if r["arm"] == arm] for arm in ("base", "notes")} == {"base": ["H1", "H2"], "notes": ["H1", "H2"]}
    assert e["pass"] == {"base": 2, "notes": 2}  # the counts follow the two questions, not the built-in ten
    assert f"(questions from {qs.name}, one run each)" in capsys.readouterr().out  # not the in-sample dev-set wording
