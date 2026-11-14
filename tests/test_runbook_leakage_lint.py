"""Tests for scripts/runbook_leakage_lint.py: a runbook must not carry the gold set it is meant to help answer (#130).

The lint is a CLI observation (PASS/FAIL lines plus an exit code), so the tests run the script as a subprocess exactly the
way `just` would, and read what it prints. Gold fixtures are written per test so a leak is visible in the test itself.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PY = sys.executable
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "runbook_leakage_lint.py"

M1_QUESTION = (
    "Which station (STATIONID) had the highest total cleared energy in MWh on the day, "
    "and how much (1 decimal)?"
)
M1_SQL = (
    "WITH u AS (SELECT DUID, arg_max(STATIONID, START_DATE) AS station FROM dudetailsummary "
    "GROUP BY DUID) SELECT u.station, round(sum(l.TOTALCLEARED)*5/60,1) AS e FROM dispatchload l "
    "JOIN u USING (DUID) WHERE l.INTERVENTION = 0 GROUP BY 1 ORDER BY e DESC LIMIT 1"
)
M2_QUESTION = "What was the total cleared energy for the Newcastle station, and which region was it in?"
M2_SQL = "SELECT round(sum(TOTALCLEARED)*5/60, 1) FROM dispatchload WHERE DUID = 'NEE1'"
SHORT_QUESTION = "How long is one dispatch interval?"  # 33 normalised chars: no 40-char window exists

GOLD: list[dict[str, Any]] = [
    {"id": "M1", "question": M1_QUESTION, "sql": M1_SQL, "values": ["NSW1", 50501.6, 12]},
    {"id": "M2", "question": M2_QUESTION, "sql": M2_SQL, "values": [1234.5, "Newcastle", 0.5, "QLD1"]},
    {"id": "M3", "question": SHORT_QUESTION, "sql": "SELECT 5", "values": [5]},
]

CLEAN_RUNBOOK = """# Runbook: answering the multi-table questions

Work from the schema, never from an answer key.

1. Join the dispatch table to the unit-detail table on DUID, keeping the latest dated row per unit.
2. Sum the cleared quantity, convert to MWh, then round to one decimal before reporting.
3. Name the region with the biggest total.

Common mistakes: forgetting the intervention filter, and summing before the join has collapsed the dated rows.
"""


def write(path: Path, content: str) -> str:
    path.write_text(content, encoding="utf-8")
    return str(path)


def gold_arg(tmp_path: Path, gold: list[dict[str, Any]] = GOLD) -> str:
    return write(tmp_path / "gold.json", json.dumps(gold))


def run(*runbooks: str, gold: str) -> subprocess.CompletedProcess[str]:
    args = [PY, str(SCRIPT)]
    for rb in runbooks:
        args += ["--runbook", rb]
    args += ["--gold", gold]
    return subprocess.run(args, capture_output=True, text=True)


def test_clean_runbook_passes_all_three_checks_and_exits_zero(tmp_path: Path) -> None:
    """Guidance in the runbook's own words is not a leak."""
    rb = write(tmp_path / "runbook.md", CLEAN_RUNBOOK)
    result = run(rb, gold=gold_arg(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS question" in result.stdout
    assert "PASS sql" in result.stdout
    assert "PASS values" in result.stdout


def test_a_verbatim_question_fails_the_question_check_only(tmp_path: Path) -> None:
    """Copying the gold question into the runbook is the leak the check exists for, and it names the gold id."""
    rb = write(tmp_path / "runbook.md", f"# Hint\n\nThe question is: {M1_QUESTION}\n")
    result = run(rb, gold=gold_arg(tmp_path))
    assert result.returncode == 1
    assert "FAIL question: M1" in result.stdout
    assert "PASS sql" in result.stdout
    assert "PASS values" in result.stdout


def test_a_question_leak_survives_rewrapping_and_case(tmp_path: Path) -> None:
    """Matching is whitespace- and case-normalised, so a blockquoted rewrap is still the same text."""
    wrapped = "\n".join(
        f"> {line}" for line in ("WHICH STATION (STATIONID) HAD THE HIGHEST TOTAL CLEARED ENERGY IN MWH", "ON THE DAY, AND HOW MUCH (1 DECIMAL)?")
    )
    rb = write(tmp_path / "runbook.md", f"# Hint\n\n{wrapped}\n")
    result = run(rb, gold=gold_arg(tmp_path))
    assert result.returncode == 1
    assert "FAIL question: M1" in result.stdout


def test_a_quote_shorter_than_the_window_is_not_a_leak(tmp_path: Path) -> None:
    """The rule is a 40+ character window: a short shared phrase (39 chars) is allowed, 40 is not."""
    short = M1_QUESTION[:39]
    assert len(" ".join(short.split())) == 39
    clean = write(tmp_path / "clean.md", f"# Phrase\n\nThe runbook says: {short}\n")
    assert run(clean, gold=gold_arg(tmp_path)).returncode == 0
    leaky = write(tmp_path / "leaky.md", f"# Phrase\n\nThe runbook says: {M1_QUESTION[:40]}\n")
    result = run(leaky, gold=gold_arg(tmp_path))
    assert result.returncode == 1
    assert "FAIL question: M1" in result.stdout


def test_a_gold_text_shorter_than_the_window_cannot_leak(tmp_path: Path) -> None:
    """A gold question with no 40-char window is reported as unchecked, not silently skipped."""
    rb = write(tmp_path / "runbook.md", f"# Intervals\n\nAnswer: {SHORT_QUESTION} Five minutes.\n")
    result = run(rb, gold=gold_arg(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INFO question: M3" in result.stdout


def test_pretty_printed_gold_sql_fails_the_sql_check(tmp_path: Path) -> None:
    """Whitespace normalisation catches SQL pasted in with newlines and indentation."""
    pretty = M1_SQL.replace(" FROM ", "\nFROM ").replace(" SELECT ", "\n  SELECT ").replace(" WHERE ", "\nWHERE ")
    rb = write(tmp_path / "runbook.md", f"# Worked example\n\n```sql\n{pretty}\n```\n")
    result = run(rb, gold=gold_arg(tmp_path))
    assert result.returncode == 1
    assert "FAIL sql: M1" in result.stdout
    assert "PASS question" in result.stdout
    assert "PASS values" in result.stdout


def test_a_gold_value_string_leaks_as_a_whole_word_case_insensitively(tmp_path: Path) -> None:
    rb = write(tmp_path / "runbook.md", "# Answer row\n\nReport NEWCASTLE for that station.\n")
    result = run(rb, gold=gold_arg(tmp_path))
    assert result.returncode == 1
    assert 'FAIL values: M2' in result.stdout
    assert "Newcastle" in result.stdout


def test_a_gold_value_inside_a_longer_word_is_not_a_leak(tmp_path: Path) -> None:
    rb = write(tmp_path / "runbook.md", "# Towns\n\nNewcastleton is not in the NEM.\n")
    result = run(rb, gold=gold_arg(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_gold_number_leaks_whatever_the_formatting(tmp_path: Path, text: str = "1,234.5") -> None:
    rb = write(tmp_path / "runbook.md", f"# Figure\n\nThe station total was {text} MWh.\n")
    result = run(rb, gold=gold_arg(tmp_path))
    assert result.returncode == 1
    assert "FAIL values: M2" in result.stdout
    assert "1234.5" in result.stdout


def test_a_number_that_is_not_the_gold_number_is_not_a_leak(tmp_path: Path) -> None:
    """Reformattings that change the value, and digits inside a longer number, are not matches."""
    prose = "Total 12345 MWh, then 51234.5 MWh, then 1.2345 MWh, then 1,234,500 MWh, and 50501 MWh."
    rb = write(tmp_path / "runbook.md", f"# Figures\n\n{prose}\n")
    result = run(rb, gold=gold_arg(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_short_gold_value_is_reported_as_unchecked_not_flagged(tmp_path: Path) -> None:
    """12, 0.5 and QLD1 are too common to gate on: the lint says so instead of failing (#130)."""
    rb = write(tmp_path / "runbook.md", "# Notes\n\nInterval 12 for QLD1 scored 0.5 of the cap.\n")
    result = run(rb, gold=gold_arg(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS values" in result.stdout
    assert "INFO values: M2" in result.stdout
    assert "QLD1" in result.stdout


def test_two_runbooks_name_the_file_that_leaked(tmp_path: Path) -> None:
    clean = write(tmp_path / "clean.md", CLEAN_RUNBOOK)
    leaky = write(tmp_path / "operator-notes.md", f"# Notes\n\n{M2_QUESTION}\n")
    result = run(clean, leaky, gold=gold_arg(tmp_path))
    assert result.returncode == 1
    assert "FAIL question: M2" in result.stdout
    assert "operator-notes.md" in result.stdout
    assert "clean.md" not in result.stdout


def test_a_missing_runbook_is_an_error_not_a_fail(tmp_path: Path) -> None:
    result = run(str(tmp_path / "nope.md"), gold=gold_arg(tmp_path))
    assert result.returncode == 2
    assert "ERROR" in result.stdout + result.stderr
    assert "FAIL" not in result.stdout


def test_a_gold_entry_without_an_id_is_an_error(tmp_path: Path) -> None:
    gold = write(tmp_path / "gold.json", json.dumps([{"question": "Which station?"}]))
    rb = write(tmp_path / "runbook.md", CLEAN_RUNBOOK)
    result = run(rb, gold=gold)
    assert result.returncode == 2
    assert "ERROR" in result.stdout + result.stderr
