import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "results_table.py")
VERDICT = os.path.join(os.path.dirname(__file__), "..", "scripts", "verdict.py")


def make_run(tmp_path, name, n_tasks, n_pass, enforce=False, terminal_states=None):
    """Build a synthetic run directory. Returns the path."""
    p = tmp_path / name
    p.mkdir()
    if terminal_states is None:
        terminal_states = ["answered"] * n_tasks

    def sha256_file(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            h.update(f.read())
        return h.hexdigest()

    results = []
    for i in range(n_tasks):
        eid = f"ex_{i:04d}"
        journal_path = p / f"{eid}.jsonl"
        journal = [
            {"kind": "turn", "payload": {"turn": 1, "tool": "python"}},
            {"kind": "terminal", "payload": {"state": terminal_states[i]}},
        ]
        journal_path.write_text("\n".join(json.dumps(e) for e in journal))
        passed = i < n_pass
        results.append({
            "kind": "result",
            "payload": {
                "example_id": eid,
                "passed": passed,
                "numerical_pass": passed,
                "tool_evidence_pass": passed,
                "provenance_pass": not (i == n_tasks - 1 and not passed),
                "journal_sha256": sha256_file(journal_path),
            },
        })
    (p / "results.jsonl").write_text("\n".join(json.dumps(r) for r in results))
    summary = {
        "model": "test-model",
        "complete": True,
        "independence_audit": {"clean": True, "rows": n_tasks},
        "n_tasks": n_tasks,
        "n_pass": n_pass,
        "pass_at_1": n_pass / n_tasks,
        "enforce_provenance": enforce,
        "temperature": 0.0,
        "max_turns": 12,
        "max_tool_calls": 24,
        "max_tokens": 8192,
        "tool_set": "study+python2",
        "first_turn_tool_choice": "auto",
        "extra_body": None,
        "max_retries": 0,
        "inputs": {},
    }
    (p / "summary.json").write_text(json.dumps(summary))
    return p


def run_script(*args):
    return subprocess.run([sys.executable, SCRIPT] + list(args),
                          capture_output=True, text=True)


def run_verdict(results_path, thresholds_path):
    return subprocess.run([sys.executable, VERDICT, results_path, thresholds_path],
                          capture_output=True, text=True)


def test_valid_two_arm_table(tmp_path):
    d1 = make_run(tmp_path, "d1", 10, 8)
    s1 = make_run(tmp_path, "s1", 5, 5)
    d2 = make_run(tmp_path, "d2", 10, 6)
    s2 = make_run(tmp_path, "s2", 5, 4)
    r = run_script("--arm", "arm1", str(d1), str(s1),
                   "--arm", "arm2", str(d2), str(s2))
    assert r.returncode == 0
    table = json.loads(r.stdout)
    assert table["arms"]["arm1"]["derived_pass"] == 8
    assert table["arms"]["arm1"]["derived_total"] == 10
    assert table["arms"]["arm1"]["smoke_pass"] == 5
    assert table["arms"]["arm1"]["smoke_total"] == 5
    assert table["arms"]["arm1"]["provenance_failures"] == 1
    assert table["arms"]["arm1"]["guard"] == "off"
    assert table["arms"]["arm1"]["guard_refused"] == 0
    assert table["arms"]["arm1"]["protocol_failures"] == 0
    assert table["arms"]["arm1"]["tasks"] == 15


@pytest.mark.parametrize("broken,reason", [
    ("incomplete", "summary.complete is not true"),
    ("audit", "summary.independence_audit.clean is not true"),
    ("rows", "result rows but n_tasks="),
    ("journals", "journal files but n_tasks="),
    ("dups", "duplicate example_ids"),
    ("n_pass", "summary.n_pass="),
])
def test_strictness_failures(tmp_path, broken, reason):
    p = make_run(tmp_path, "derived", 5, 3)
    s = make_run(tmp_path, "smoke", 5, 3)
    if broken == "incomplete":
        ss = json.loads((p / "summary.json").read_text())
        ss["complete"] = False
        (p / "summary.json").write_text(json.dumps(ss))
    elif broken == "audit":
        ss = json.loads((p / "summary.json").read_text())
        ss["independence_audit"]["clean"] = False
        (p / "summary.json").write_text(json.dumps(ss))
    elif broken == "rows":
        (p / "results.jsonl").write_text((p / "results.jsonl").read_text().split("\n")[0])
    elif broken == "journals":
        (p / "ex_0004.jsonl").unlink()
    elif broken == "dups":
        lines = (p / "results.jsonl").read_text().split("\n")
        lines[0] = lines[1]
        (p / "results.jsonl").write_text("\n".join(lines))
    elif broken == "n_pass":
        ss = json.loads((p / "summary.json").read_text())
        ss["n_pass"] = 99
        (p / "summary.json").write_text(json.dumps(ss))
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2
    assert reason in r.stderr


def test_tampered_journal_sha(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    (p / "ex_0000.jsonl").write_text('{"kind":"terminal","payload":{"state":"answered"}}')
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2
    assert "journal sha256 mismatch" in r.stderr


def test_missing_terminal_event(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    journal_lines = (p / "ex_0000.jsonl").read_text().split("\n")
    journal_lines[-1] = json.dumps({"kind": "turn", "payload": {"turn": 2}})
    (p / "ex_0000.jsonl").write_text("\n".join(journal_lines))
    # Update results.jsonl to match new sha256 so we get past that check
    import hashlib
    h = hashlib.sha256((p / "ex_0000.jsonl").read_bytes()).hexdigest()
    lines = (p / "results.jsonl").read_text().split("\n")
    obj = json.loads(lines[0])
    obj["payload"]["journal_sha256"] = h
    lines[0] = json.dumps(obj)
    (p / "results.jsonl").write_text("\n".join(lines))
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2
    assert "not terminal" in r.stderr


def test_non_boolean_passed(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    lines = (p / "results.jsonl").read_text().split("\n")
    obj = json.loads(lines[0])
    obj["payload"]["passed"] = 1
    lines[0] = json.dumps(obj)
    (p / "results.jsonl").write_text("\n".join(lines))
    ss = json.loads((p / "summary.json").read_text())
    ss["n_pass"] = 1
    ss["pass_at_1"] = 1 / 3
    (p / "summary.json").write_text(json.dumps(ss))
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2
    assert "'passed' is not a bool" in r.stderr


def test_mismatched_settings(tmp_path):
    d = make_run(tmp_path, "derived", 5, 3)
    s = make_run(tmp_path, "smoke", 5, 3)
    ss = json.loads((s / "summary.json").read_text())
    ss["temperature"] = 1.0
    (s / "summary.json").write_text(json.dumps(ss))
    r = run_script("--arm", "x", str(d), str(s))
    assert r.returncode == 2
    assert "temperature differs" in r.stderr


def test_duplicate_arm_name(tmp_path):
    d = make_run(tmp_path, "derived", 5, 3)
    s = make_run(tmp_path, "smoke", 5, 3)
    r = run_script("--arm", "x", str(d), str(s), "--arm", "x", str(d), str(s))
    assert r.returncode == 2
    assert "duplicate arm name" in r.stderr


def test_protocol_failures_counted(tmp_path):
    states = ["incomplete_generation", "turn_budget_exhausted", "inference_timeout",
              "context_exhausted", "invalid_tool_protocol", "invalid_tool_arguments",
              "answered", "guard_refused"]
    d = make_run(tmp_path, "derived", 8, 0, terminal_states=states)
    s = make_run(tmp_path, "smoke", 8, 0, terminal_states=states)
    r = run_script("--arm", "x", str(d), str(s))
    assert r.returncode == 0
    table = json.loads(r.stdout)
    assert table["arms"]["x"]["protocol_failures"] == 12


def test_guard_refused_counted(tmp_path):
    states = ["guard_refused", "answered", "answered", "answered"]
    d = make_run(tmp_path, "derived", 4, 0, enforce=True, terminal_states=states)
    s = make_run(tmp_path, "smoke", 4, 0, enforce=True, terminal_states=states)
    r = run_script("--arm", "x", str(d), str(s))
    assert r.returncode == 0
    table = json.loads(r.stdout)
    assert table["arms"]["x"]["guard"] == "on"
    assert table["arms"]["x"]["guard_refused"] == 1


def test_end_to_end_verdict(tmp_path):
    d = make_run(tmp_path, "derived", 10, 9)
    s = make_run(tmp_path, "smoke", 5, 5)
    r = run_script("--arm", "contestant", str(d), str(s))
    assert r.returncode == 0
    results_path = tmp_path / "results.json"
    results_path.write_text(r.stdout)
    thresholds = {
        "confirmed_by_owner": True,
        "derived_total": 10,
        "smoke_total": 5,
        "noise_tasks": 1,
        "contender": {
            "derived_pass_min": 8,
            "smoke_pass_all": True,
            "provenance_failures_max_guard_off": 2,
            "guard_refused_max_guard_on": 1,
            "protocol_failures_max": 2,
        },
        "not_contender": {
            "derived_pass_max": 5,
            "protocol_failure_rate_above": 0.1,
        },
    }
    thresh_path = tmp_path / "thresholds.json"
    thresh_path.write_text(json.dumps(thresholds))
    v = run_verdict(str(results_path), str(thresh_path))
    assert v.returncode == 0
    assert "contestant CONTENDER" in v.stdout


def test_protocol_set_matches_ledger():
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from wattson.inference.error_ledger import UNANSWERED as LEDGER
    except ImportError:
        pytest.skip("wattson.inference.error_ledger not importable")
    from scripts.results_table import UNANSWERED
    assert UNANSWERED == LEDGER


def test_missing_pass_at_1_exits_2(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    ss = json.loads((p / "summary.json").read_text())
    del ss["pass_at_1"]
    (p / "summary.json").write_text(json.dumps(ss))
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2


def test_extra_journal_file_exits_2(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    (p / "stray.jsonl").write_text('"junk"\n')
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2


def test_journal_last_line_not_json_exits_2(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    (p / "ex_0000.jsonl").write_text('not json\n')
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2
    assert "ERROR" in r.stderr
    assert "Traceback" not in r.stderr


def test_terminal_event_without_state_exits_2(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    (p / "ex_0000.jsonl").write_text(
        '{"kind": "turn", "payload": {"turn": 1}}\n' +
        '{"kind": "terminal", "payload": {}}\n')
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2


def test_result_row_without_example_id_exits_2(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    lines = (p / "results.jsonl").read_text().split("\n")
    obj = json.loads(lines[0])
    obj["payload"]["example_id"] = ""
    lines[0] = json.dumps(obj)
    (p / "results.jsonl").write_text("\n".join(lines))
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr


def test_missing_temperature_exits_2(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    ss = json.loads((p / "summary.json").read_text())
    del ss["temperature"]
    (p / "summary.json").write_text(json.dumps(ss))
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2
    assert "temperature" in r.stderr


def test_enforce_provenance_string_false_exits_2(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    ss = json.loads((p / "summary.json").read_text())
    ss["enforce_provenance"] = "false"
    (p / "summary.json").write_text(json.dumps(ss))
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2


def test_missing_optional_in_both_accepted(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    for run in (p, s):
        ss = json.loads((run / "summary.json").read_text())
        del ss["extra_body"]
        del ss["max_retries"]
        (run / "summary.json").write_text(json.dumps(ss))
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 0


def test_missing_optional_in_one_exits_2(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    ss = json.loads((p / "summary.json").read_text())
    ss["extra_body"] = {"seed": 42}
    (p / "summary.json").write_text(json.dumps(ss))
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2


def test_same_directory_exits_2(tmp_path):
    p = make_run(tmp_path, "base", 3, 2)
    r = run_script("--arm", "x", str(p), str(p))
    assert r.returncode == 2
    assert "same" in r.stderr


def test_pass_at_1_boolean_exits_2(tmp_path):
    p = make_run(tmp_path, "derived", 3, 2)
    s = make_run(tmp_path, "smoke", 3, 2)
    ss = json.loads((p / "summary.json").read_text())
    ss["pass_at_1"] = True
    (p / "summary.json").write_text(json.dumps(ss))
    r = run_script("--arm", "x", str(p), str(s))
    assert r.returncode == 2
