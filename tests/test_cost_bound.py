"""Tests for scripts/cost_bound.py: deterministic upper-bound cost estimator from journals."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PY = sys.executable
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cost_bound.py"


def write_journal(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def run_cost_bound(run_dir: Path, *extra_args: str) -> subprocess.CompletedProcess:
    args = [PY, str(SCRIPT), str(run_dir)] + list(extra_args)
    return subprocess.run(args, capture_output=True, text=True)


def test_totals_on_two_journals(tmp_path: Path) -> None:
    """Sum tokens across multiple journal files."""
    j1 = tmp_path / "run1.jsonl"
    j2 = tmp_path / "run2.jsonl"
    write_journal(j1, [
        {"kind": "model_response", "payload": {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}},
        {"kind": "tool_request", "payload": {}},
    ])
    write_journal(j2, [
        {"kind": "model_response", "payload": {"usage": {"prompt_tokens": 200, "completion_tokens": 75}}},
    ])
    result = run_cost_bound(tmp_path)
    assert result.returncode == 0
    assert "tokens_in=300 tokens_out=125 cached_in=0 calls=2 attempts=2" in result.stdout


def test_cached_tokens_reported_but_priced_at_full_input_rate(tmp_path: Path) -> None:
    """Cached tokens are reported but the bound prices them at the full input rate."""
    j = tmp_path / "run.jsonl"
    write_journal(j, [
        {"kind": "model_response", "payload": {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 400},
            }
        }},
    ])
    result = run_cost_bound(tmp_path, "--price-in", "1.0", "--price-out", "2.0")
    assert result.returncode == 0
    # 1000 * $1.0 / 1M + 100 * $2.0 / 1M = 0.001000 + 0.000200 = 0.0012
    assert "ESTIMATE upper_bound_usd=0.0012" in result.stdout
    assert "cached_in=400" in result.stdout
    assert "no prompt caching assumed" in result.stdout


def test_estimate_label_on_every_dollar_line(tmp_path: Path) -> None:
    """Every line with a dollar amount must contain ESTIMATE."""
    j = tmp_path / "run.jsonl"
    write_journal(j, [
        {"kind": "model_response", "payload": {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}},
    ])
    result = run_cost_bound(tmp_path, "--price-in", "1.0", "--price-out", "2.0",
                            "--cells", "10", "--tasks-in-run", "5")
    assert result.returncode == 0
    for line in result.stdout.splitlines():
        if "usd=" in line:
            assert "ESTIMATE" in line


def test_malformed_json_exits_2_with_file_line(tmp_path: Path) -> None:
    """Malformed JSON line produces exit code 2 naming file:line."""
    j = tmp_path / "run.jsonl"
    j.write_text('{"kind": "ok"}\nnot json\n{"kind": "ok"}\n')
    result = run_cost_bound(tmp_path)
    assert result.returncode == 2
    assert "run.jsonl:2" in result.stderr


def test_empty_directory_exits_2(tmp_path: Path) -> None:
    """Directory with no journals exits 2."""
    result = run_cost_bound(tmp_path)
    assert result.returncode == 2
    assert "no *.jsonl journals" in result.stderr


def test_scaling_arithmetic_exact(tmp_path: Path) -> None:
    """Scaled bound = (bound / tasks_in_run) * cells, exact with Decimal."""
    j = tmp_path / "run.jsonl"
    write_journal(j, [
        {"kind": "model_response", "payload": {"usage": {"prompt_tokens": 1000000, "completion_tokens": 0}}},
    ])
    # bound = 1M tokens * $1.0 / 1M = $1.00
    # scaled for 10 cells from 5 tasks = (1.00 / 5) * 10 = $2.00
    result = run_cost_bound(tmp_path, "--price-in", "1.0", "--price-out", "1.0",
                            "--cells", "10", "--tasks-in-run", "5")
    assert result.returncode == 0
    assert "ESTIMATE upper_bound_usd=1.0000" in result.stdout
    assert "ESTIMATE scaled_upper_bound_usd=2.0000" in result.stdout


def test_scaling_requires_tasks_in_run(tmp_path: Path) -> None:
    """--cells without --tasks-in-run exits 2."""
    j = tmp_path / "run.jsonl"
    write_journal(j, [
        {"kind": "model_response", "payload": {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}},
    ])
    result = run_cost_bound(tmp_path, "--price-in", "1.0", "--price-out", "2.0", "--cells", "10")
    assert result.returncode == 2
    assert "--tasks-in-run" in result.stderr


def test_no_prices_no_dollar_lines(tmp_path: Path) -> None:
    """Without prices, no dollar lines are printed."""
    j = tmp_path / "run.jsonl"
    write_journal(j, [
        {"kind": "model_response", "payload": {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}},
    ])
    result = run_cost_bound(tmp_path)
    assert result.returncode == 0
    assert "usd=" not in result.stdout
    assert "tokens_in=100 tokens_out=50" in result.stdout
