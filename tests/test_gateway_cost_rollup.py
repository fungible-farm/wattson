"""Tests for scripts/gateway_cost_rollup.py: per-(task, model) cost rollup of a gateway access log."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PY = sys.executable
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gateway_cost_rollup.py"
BASE: dict[str, Any] = {"protocol": "llm", "http.response.status_code": "200", "gen_ai.request.model": "sonnet",
                        "gen_ai.usage.input_tokens": "100", "gen_ai.usage.output_tokens": "40", "duration": "1ms"}


def line(**over: str | None) -> str:
    """One access-log record; pass field=None to omit it, task stays quoted like the gateway writes it."""
    f = dict(BASE, **over)
    parts = []
    for k, v in f.items():
        if v is None:
            continue
        parts.append(f'{k}="{v}"' if k == "task" else f"{k}={v}")
    return "timestamp=2026-11-15T02:00:00Z " + " ".join(parts)


def write_log(path: Path, lines: list[str]) -> Path:
    path.write_text("".join(s + "\n" for s in lines), encoding="utf-8")
    return path


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(SCRIPT), *[str(a) for a in args]], capture_output=True, text=True)


def rows(out: str) -> list[list[str]]:
    """Data rows only (header, TOTAL, separator and the skipped note have no place in a group assertion)."""
    return [[c.strip() for c in r] for r in (l.split(" | ") for l in out.splitlines())
            if len(r) == 8 and r[0].strip() not in ("task", "TOTAL") and set(r[0].strip()) != {"-"}]


def test_groups_by_task_and_model(tmp_path: Path) -> None:
    log = write_log(tmp_path / "a.log", [line(task="b-task"), line(task="b-task", **{"gen_ai.request.model": "opus"}),
                                        line(task="a-task"), line(task="a-task")])
    res = run("--log", log)
    assert res.returncode == 0, res.stderr
    assert [r[:3] for r in rows(res.stdout)] == [["a-task", "sonnet", "2"], ["b-task", "opus", "1"],
                                                 ["b-task", "sonnet", "1"]]
    assert rows(res.stdout)[0][4:] == ["200", "80", "2.000", "n/a"]  # a-task summed its two calls


def test_no_task_bucket_and_error_counted(tmp_path: Path) -> None:
    log = write_log(tmp_path / "a.log", [line(task=None), line(task=None, **{"http.response.status_code": "500"})])
    out = run("--log", log).stdout
    assert ["(no task)", "sonnet", "2", "1"] in [r[:4] for r in rows(out)]


def test_duration_units_ms_s_us(tmp_path: Path) -> None:
    log = write_log(tmp_path / "a.log", [line(duration="1ms"), line(duration="2.5s"), line(duration="300us")])
    r = rows(run("--log", log).stdout)[0]
    assert r[6] == "2501.300"


def test_est_cost_with_price_and_na(tmp_path: Path) -> None:
    log = write_log(tmp_path / "a.log", [line(task="t", **{"gen_ai.request.model": "priced"}),
                                         line(task="t", **{"gen_ai.request.model": "unpriced"})])
    prices = tmp_path / "p.json"
    prices.write_text(json.dumps({"priced": {"in_per_mtok": "1.50", "out_per_mtok": "2.50"}}), encoding="utf-8")
    res = run("--log", log, "--prices", prices)
    assert res.returncode == 0, res.stderr
    got = {r[1]: r[7] for r in rows(res.stdout)}
    assert got == {"priced": "0.000250", "unpriced": "n/a"}  # (100*1.5 + 40*2.5)/1e6
    assert "n/a" in res.stdout.split("TOTAL")[1]  # a total over an unpriced group is never a smaller number


def test_non_llm_lines_are_skipped(tmp_path: Path) -> None:
    log = write_log(tmp_path / "a.log", [line(task="t"), "protocol=http.path path=/v1/chat status_code=200",
                                         "upstream peer=10.0.0.1"])
    res = run("--log", log)
    assert res.returncode == 0, res.stderr
    assert "skipped 2 non-llm line(s)" in res.stdout


def test_missing_token_counts_fails_not_zero(tmp_path: Path) -> None:
    log = write_log(tmp_path / "a.log", [line(task="t"), line(task="t", **{"gen_ai.usage.output_tokens": None})])
    res = run("--log", log)
    assert res.returncode == 1
    assert "FAIL" in res.stdout and "line 2" in res.stdout
    assert "TOTAL" not in res.stdout


def test_unreadable_inputs_exit_two(tmp_path: Path) -> None:
    assert run("--log", tmp_path / "nope.log").returncode == 2
    log = write_log(tmp_path / "a.log", [line(task="t")])
    assert run("--log", log, "--prices", tmp_path / "nope.json").returncode == 2


def test_json_out_is_deterministic(tmp_path: Path) -> None:
    log = write_log(tmp_path / "a.log", [line(task="z"), line(task="a", duration="2s"), "protocol=http.path"])
    out1, out2 = tmp_path / "1.json", tmp_path / "2.json"
    assert run("--log", log, "--out", out1).returncode == 0
    run("--log", log, "--out", out2)
    b1, b2 = out1.read_bytes(), out2.read_bytes()
    assert b1 == b2 and b1.endswith(b"\n") and b"\n  " in b1  # indent 2, trailing newline
    doc = json.loads(b1)
    assert [g["task"] for g in doc["groups"]] == ["a", "z"] and doc["skipped"] == 1
    assert doc["groups"][0]["est_cost"] == "n/a" and doc["totals"]["calls"] == 2
