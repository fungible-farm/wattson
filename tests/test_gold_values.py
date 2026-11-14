"""Tests for scripts/gold_values.py: gold SQL -> gold values file for the leakage lint (#130 step 4).

The container is not available in tests, so `run_sql` is monkeypatched with canned container answers and the tests read the file
the script writes. The gold file exists to be consumed by scripts/runbook_leakage_lint.py, so the last tests feed it to that
script's gold loader and run its checks over a runbook.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
GOLD_VALUES: Path = REPO / "scripts" / "gold_values.py"
LINT: Path = REPO / "scripts" / "runbook_leakage_lint.py"

M1_SQL = "SELECT station, round(sum(e)*5/60,1) FROM dispatchload WHERE INTERVENTION=0 LIMIT 1"
M2_SQL = "SELECT count(DISTINCT DUID) FROM dispatchload WHERE TOTALCLEARED>0"
M3_SQL = "SELECT region, round(avg(NETINTERCHANGE),1) FROM dispatchinterconnectorres GROUP BY 1"

QUESTIONS: list[dict[str, str]] = [
    {"id": "M1", "question": "Which station cleared the most energy, and how much (1 decimal)?", "sql": M1_SQL},
    {"id": "M2", "question": "How many units in SA1 cleared a positive quantity?", "sql": M2_SQL},
    {"id": "M3", "question": "Which region had the highest average interchange (1 decimal)?", "sql": M3_SQL},
]

# What the container prints for each statement: numbers as numbers, strings as strings.
ANSWERS: dict[str, dict[str, Any]] = {
    M1_SQL: {"columns": ["station", "e"], "rows": [["BBOOLBA1", 50501.6]]},
    M2_SQL: {"columns": ["count"], "rows": [[4127]]},
    M3_SQL: {"columns": ["region", "x"], "rows": [["VIC1", -812.4]]},
}

# The whole file, written out rather than derived from the input above: the point of the script is this exact content.
EXPECTED: list[dict[str, Any]] = [
    {
        "id": "M1",
        "question": "Which station cleared the most energy, and how much (1 decimal)?",
        "sql": "SELECT station, round(sum(e)*5/60,1) FROM dispatchload WHERE INTERVENTION=0 LIMIT 1",
        "values": ["BBOOLBA1", 50501.6],
    },
    {
        "id": "M2",
        "question": "How many units in SA1 cleared a positive quantity?",
        "sql": "SELECT count(DISTINCT DUID) FROM dispatchload WHERE TOTALCLEARED>0",
        "values": [4127],
    },
    {
        "id": "M3",
        "question": "Which region had the highest average interchange (1 decimal)?",
        "sql": "SELECT region, round(avg(NETINTERCHANGE),1) FROM dispatchinterconnectorres GROUP BY 1",
        "values": ["VIC1", -812.4],
    },
]

CLEAN_RUNBOOK = """# Runbook: answering the multi-table questions

Work from the schema, never from an answer key.

1. Join the dispatch table to the unit-detail table on DUID, keeping the latest dated row per unit.
2. Sum the cleared quantity, convert to MWh, then round to one decimal before reporting.
3. Report the region with the biggest total and leave the arithmetic to the SQL.
"""

# 50501.6 is a gold value with 5 significant digits, so the lint's values check gates on it.
VALUE_RUNBOOK = "# Operator notes\n\nRead the station total straight off the sheet: 50501.6 MWh.\n"


def load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gold_values = load(GOLD_VALUES, "gold_values")


def patch(monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any]) -> list[str]:
    """Answer each statement from `answers` instead of the container; return the SQL the script asked to run."""
    asked: list[str] = []

    def run_sql(sql: str) -> dict[str, Any]:
        asked.append(sql)
        return answers[sql]

    monkeypatch.setattr(gold_values.arm1, "run_sql", run_sql)
    return asked


def questions_file(tmp_path: Path, items: list[dict[str, str]] = QUESTIONS) -> str:
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return str(path)


def make_gold(tmp_path: Path, items: list[dict[str, str]] = QUESTIONS) -> Path:
    """Run the script to `tmp_path/gold.json` (run_sql must be patched already); asserts success and returns the path."""
    out = tmp_path / "gold.json"
    assert gold_values.main(["--questions", questions_file(tmp_path, items), "--out", str(out)]) == 0
    return out


def test_it_writes_the_gold_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Each item gains the flattened values of its own SQL, and the script says where it wrote them."""
    asked = patch(monkeypatch, ANSWERS)
    out = tmp_path / "gold.json"
    code = gold_values.main(["--questions", questions_file(tmp_path), "--out", str(out)])
    assert code == 0
    assert asked == [M1_SQL, M2_SQL, M3_SQL]
    assert capsys.readouterr().out == f"PASS gold-values: {len(QUESTIONS)} questions -> {out}\n"
    assert json.loads(out.read_text(encoding="utf-8")) == EXPECTED


def test_the_file_shape_is_the_lints_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Objects with id, question, sql, values in that order; indent 2; trailing newline; values are row order, cells in order."""
    patch(monkeypatch, ANSWERS)
    out = make_gold(tmp_path)
    text = out.read_text(encoding="utf-8")
    assert text.endswith("]\n") and not text.endswith("\n\n")
    assert text.startswith('[\n  {\n    "id": "M1",\n')
    entries = json.loads(text)
    assert [list(entry) for entry in entries] == [["id", "question", "sql", "values"]] * len(QUESTIONS)
    assert entries[0]["values"] == ["BBOOLBA1", 50501.6]  # one row, both cells, in column order


def test_numbers_stay_numbers_and_strings_stay_strings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The lint compares numbers numerically, which needs the container's types kept, not stringified."""
    patch(monkeypatch, ANSWERS)
    entries = json.loads(make_gold(tmp_path).read_text(encoding="utf-8"))
    assert [type(v).__name__ for v in entries[0]["values"]] == ["str", "float"]
    assert [type(v).__name__ for v in entries[1]["values"]] == ["int"]
    assert entries[2]["values"][1] == -812.4


def test_the_output_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same questions, same container answers: byte-for-byte the same file."""
    patch(monkeypatch, ANSWERS)
    first = make_gold(tmp_path).read_text(encoding="utf-8")
    second = tmp_path / "again.json"
    assert gold_values.main(["--questions", questions_file(tmp_path), "--out", str(second)]) == 0
    assert second.read_text(encoding="utf-8") == first


def test_a_failing_statement_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An error anywhere means no gold file at all: the lint must not report PASS over questions whose SQL never answered."""
    answers = dict(ANSWERS)
    answers[M2_SQL] = {"error": 'CatalogException: Table with name "dispatchload" does not exist'}
    patch(monkeypatch, answers)
    out = tmp_path / "gold.json"
    code = gold_values.main(["--questions", questions_file(tmp_path), "--out", str(out)])
    assert code == 1
    assert capsys.readouterr().out == (
        'FAIL gold-values: M2 CatalogException: Table with name "dispatchload" does not exist\n'
    )
    assert not out.exists()


def test_an_answer_without_rows_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Something other than rows back from the container is an error too: unknown values must not ship as gold."""
    patch(monkeypatch, {**ANSWERS, M1_SQL: {"columns": ["station"]}})
    out = tmp_path / "gold.json"
    assert gold_values.main(["--questions", questions_file(tmp_path), "--out", str(out)]) == 1
    assert "FAIL gold-values: M1 returned no rows" in capsys.readouterr().out
    assert not out.exists()


def test_the_input_order_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gold entries follow the questions file, including when that order is not sorted."""
    items = [QUESTIONS[2], QUESTIONS[0], QUESTIONS[1]]
    asked = patch(monkeypatch, ANSWERS)
    entries = json.loads(make_gold(tmp_path, items).read_text(encoding="utf-8"))
    assert [entry["id"] for entry in entries] == ["M3", "M1", "M2"]
    assert asked == [M3_SQL, M1_SQL, M2_SQL]
    assert entries[0]["values"] == ["VIC1", -812.4]


def test_a_questions_file_without_sql_fails_before_the_container_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An entry with no SQL to run is refused up front, and still nothing is written."""
    patch(monkeypatch, ANSWERS)
    out = tmp_path / "gold.json"
    code = gold_values.main(["--questions", questions_file(tmp_path, [{"id": "M9"}]), "--out", str(out)])
    assert code == 1
    assert "FAIL gold-values" in capsys.readouterr().out
    assert not out.exists()


# ------------------------------------------------------------------------------------------- the gold file reaches the lint


def lint_against(
    gold: Path, runbook: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, list[dict[str, Any]], str]:
    """Read the produced file with the lint's own gold loader, then run the lint as its CLI does; return (code, gold items, output)."""
    lint = load(LINT, "runbook_leakage_lint")
    capsys.readouterr()
    gold_items = lint.load_gold(str(gold))
    code = lint.main(["--runbook", str(runbook), "--gold", str(gold)])
    return code, gold_items, capsys.readouterr().out


def test_the_leakage_lint_reads_the_file_as_gold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The gold loader accepts the file and the lint runs clean over a runbook that quotes no answer."""
    patch(monkeypatch, ANSWERS)
    runbook = tmp_path / "runbook.md"
    runbook.write_text(CLEAN_RUNBOOK, encoding="utf-8")
    code, gold_items, printed = lint_against(make_gold(tmp_path), runbook, capsys)
    assert [item["id"] for item in gold_items] == ["M1", "M2", "M3"]
    assert gold_items[0]["values"] == ["BBOOLBA1", 50501.6]
    assert code == 0, printed
    assert "PASS values" in printed


def test_a_runbook_with_a_gold_value_of_four_digits_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The point of the file: 50501.6 came out of M1's SQL, so no runbook may carry it."""
    patch(monkeypatch, ANSWERS)
    runbook = tmp_path / "operator-notes.md"
    runbook.write_text(VALUE_RUNBOOK, encoding="utf-8")
    code, _, printed = lint_against(make_gold(tmp_path), runbook, capsys)
    assert code == 1
    assert "FAIL values: M1" in printed
    assert "operator-notes.md" in printed
