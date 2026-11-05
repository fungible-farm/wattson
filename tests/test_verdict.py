import json
import os
import subprocess
import sys
import tempfile

PY = sys.executable
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "verdict.py")
THRESHOLDS_PATH = os.path.join(os.path.dirname(__file__), "..", "configs", "verdict-thresholds.proposed.json")


def load_thresholds():
    with open(THRESHOLDS_PATH) as f:
        return json.load(f)


def run_verdict_files(results, thresholds=None, tmp_path=None):
    """Write to temp files and run verdict.py; return (returncode, stdout, stderr)."""
    if tmp_path is None:
        tmp_path = tempfile.mkdtemp()
    results_path = os.path.join(tmp_path, "results.json")
    thresholds_path = os.path.join(tmp_path, "thresholds.json")
    if thresholds is None:
        thresholds = load_thresholds()
    with open(results_path, "w") as f:
        f.write(json.dumps(results))
    with open(thresholds_path, "w") as f:
        f.write(json.dumps(thresholds))
    p = subprocess.run([PY, SCRIPT, results_path, thresholds_path], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def make_arm(derived_pass=38, derived_total=40, smoke_pass=20, smoke_total=20,
             provenance_failures=0, guard="off", guard_refused=0,
             protocol_failures=0, tasks=40):
    return {
        "derived_pass": derived_pass,
        "derived_total": derived_total,
        "smoke_pass": smoke_pass,
        "smoke_total": smoke_total,
        "provenance_failures": provenance_failures,
        "guard": guard,
        "guard_refused": guard_refused,
        "protocol_failures": protocol_failures,
        "tasks": tasks,
    }


def make_results(arm_name, arm):
    return {"arms": {arm_name: arm}}


def test_derived_pass_36_not_contender():
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=36)))
    assert rc == 0
    assert "a NOT_CONTENDER" in out


def test_derived_pass_37_inconclusive():
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=37)))
    assert rc == 0
    assert "a INCONCLUSIVE" in out


def test_derived_pass_38_contender():
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38)))
    assert rc == 0
    assert "a CONTENDER" in out


def test_protocol_rate_exactly_010_not_above():
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38, protocol_failures=4)))
    assert rc == 0
    assert "a INCONCLUSIVE" in out


def test_protocol_rate_011_not_contender():
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38, protocol_failures=5)))
    assert rc == 0
    assert "a NOT_CONTENDER" in out


def test_guard_off_provenance():
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38, provenance_failures=1, guard="off")))
    assert rc == 0
    assert "a INCONCLUSIVE" in out
    assert "provenance_failures 1 > 0" in out


def test_guard_on_guard_refused():
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38, guard="on", guard_refused=2)))
    assert rc == 0
    assert "a INCONCLUSIVE" in out
    assert "guard_refused 2 > 1" in out


def test_smoke_19_of_20_blocks_contender():
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38, smoke_pass=19)))
    assert rc == 0
    assert "a INCONCLUSIVE" in out
    assert "smoke 19/20" in out


def test_unconfirmed_header():
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38)))
    assert rc == 0
    assert "THRESHOLDS UNCONFIRMED" in out


def test_confirmed_header():
    thresholds = load_thresholds()
    thresholds["confirmed_by_owner"] = True
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38)), thresholds)
    assert rc == 0
    assert "THRESHOLDS CONFIRMED" in out


def test_noise_note_emitted():
    results = make_results("a", make_arm(derived_pass=38))
    results["arms"]["b"] = make_arm(derived_pass=39)
    rc, out, _ = run_verdict_files(results)
    assert rc == 0
    assert "NOTE a vs b: difference of 1 tasks is within noise" in out


def test_noise_note_not_emitted_for_3():
    results = make_results("a", make_arm(derived_pass=38))
    results["arms"]["b"] = make_arm(derived_pass=40)
    rc, out, _ = run_verdict_files(results)
    assert rc == 0
    assert "difference of 3" not in out


def test_sorted_order():
    results = make_results("b", make_arm(derived_pass=38))
    results["arms"]["a"] = make_arm(derived_pass=38)
    rc, out, _ = run_verdict_files(results)
    assert rc == 0
    lines = [l for l in out.splitlines() if l.startswith("a") or l.startswith("b")]
    assert lines[0].startswith("a")
    assert lines[1].startswith("b")


def test_pass_greater_than_total_exits_2():
    rc, _, _ = run_verdict_files(make_results("a", make_arm(derived_pass=41)))
    assert rc == 2


def test_invalid_json_exits_2():
    tmp = tempfile.mkdtemp()
    bad_path = os.path.join(tmp, "bad.json")
    thr_path = os.path.join(tmp, "thr.json")
    with open(bad_path, "w") as f:
        f.write("not json")
    with open(thr_path, "w") as f:
        f.write(json.dumps(load_thresholds()))
    p = subprocess.run([PY, SCRIPT, bad_path, thr_path], capture_output=True, text=True)
    assert p.returncode == 2


def test_sample_thresholds_loads():
    data = load_thresholds()
    assert data["confirmed_by_owner"] is False
    assert data["derived_total"] == 40
    assert data["smoke_total"] == 20
    assert data["contender"]["derived_pass_min"] == 38
    assert data["not_contender"]["derived_pass_max"] == 36
    assert data["noise_tasks"] == 2


# --- new strict-behaviour tests ---

def test_missing_provenance_failures_exits_2():
    arm = make_arm(derived_pass=38)
    del arm["provenance_failures"]
    rc, _, err = run_verdict_files(make_results("a", arm))
    assert rc == 2
    assert "provenance_failures" in err


def test_missing_smoke_pass_exits_2():
    arm = make_arm(derived_pass=38)
    del arm["smoke_pass"]
    rc, _, err = run_verdict_files(make_results("a", arm))
    assert rc == 2
    assert "smoke_pass" in err


def test_missing_smoke_total_exits_2():
    arm = make_arm(derived_pass=38)
    del arm["smoke_total"]
    rc, _, err = run_verdict_files(make_results("a", arm))
    assert rc == 2
    assert "smoke_total" in err


def test_thresholds_missing_derived_pass_min_exits_2():
    thresholds = load_thresholds()
    del thresholds["contender"]["derived_pass_min"]
    rc, _, err = run_verdict_files(make_results("a", make_arm(derived_pass=38)), thresholds)
    assert rc == 2
    assert "derived_pass_min" in err


def test_derived_total_not_pre_registered_exits_2():
    rc, _, err = run_verdict_files(make_results("a", make_arm(derived_pass=38, derived_total=39)))
    assert rc == 2
    assert "pre-registered" in err


def test_smoke_total_not_pre_registered_exits_2():
    rc, _, err = run_verdict_files(make_results("a", make_arm(derived_pass=38, smoke_total=19)))
    assert rc == 2
    assert "pre-registered" in err


def test_boolean_where_number_expected_exits_2():
    arm = make_arm(derived_pass=38)
    arm["derived_pass"] = True
    rc, _, err = run_verdict_files(make_results("a", arm))
    assert rc == 2
    assert "boolean" in err


def test_guard_value_maybe_exits_2():
    rc, _, err = run_verdict_files(make_results("a", make_arm(derived_pass=38, guard="maybe")))
    assert rc == 2
    assert "guard" in err


def test_derived_total_zero_exits_2():
    rc, _, err = run_verdict_files(make_results("a", make_arm(derived_pass=38, derived_total=0)))
    assert rc == 2
    assert "derived_total" in err


def test_smoke_pass_greater_than_total_exits_2():
    rc, _, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38, smoke_pass=21)))
    assert rc == 2


def test_protocol_failures_greater_than_tasks_exits_2():
    rc, _, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38, protocol_failures=41)))
    assert rc == 2


def test_negative_number_exits_2():
    rc, _, _ = run_verdict_files(make_results("a", make_arm(derived_pass=-1)))
    assert rc == 2


def test_arm_given_as_list_exits_2_no_traceback():
    rc, _, err = run_verdict_files({"arms": {"a": [1, 2, 3]}})
    assert rc == 2
    assert "ERROR:" in err
    assert "Traceback" not in err


def test_protocol_rate_boundary_exact_arithmetic():
    # 4/40 = 0.10 exactly is NOT above 0.10
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38, protocol_failures=4)))
    assert rc == 0
    assert "NOT_CONTENDER" not in out
    # 5/40 = 0.125 is above 0.10
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38, protocol_failures=5)))
    assert rc == 0
    assert "NOT_CONTENDER" in out
    # 1/10 = 0.10 exactly is NOT above 0.10
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38, protocol_failures=1, tasks=10)))
    assert rc == 0
    assert "NOT_CONTENDER" not in out


def test_proposed_thresholds_load_and_unconfirmed_header():
    thresholds = load_thresholds()
    assert thresholds["confirmed_by_owner"] is False
    assert thresholds["derived_total"] == 40
    assert thresholds["smoke_total"] == 20
    rc, out, _ = run_verdict_files(make_results("a", make_arm(derived_pass=38)), thresholds)
    assert rc == 0
    assert "THRESHOLDS UNCONFIRMED" in out
