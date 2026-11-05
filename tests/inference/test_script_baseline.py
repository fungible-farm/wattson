"""The scripted-procedure baseline must be a run compare_agents accepts, failures included."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from wattson.datasets import common
from wattson.datasets.audit import load_rows
from wattson.inference.compare_agents import verified_run
from wattson.inference.failure_modes import classify_run
from wattson.inference.script_baseline import (AGENT_NAME, SCRIPT_FAILED, TOOL_CALL_BUDGET,
                                                  ScriptBudgetExhausted, ScriptedConversation, evaluate)

SMOKE: Path = common.REPO_ROOT / "fixtures/smoke/tasks/examples.jsonl"
TRAIN: Path = common.REPO_ROOT / "data/tasks/train-pool-split-20261027/examples.jsonl"
# The smoke suite's first report (N-1, line 60) and first load-scale fit (bus 1823).
REPORT_ID: str = "ex_de33c72bf3eca1df"
FIT_ID: str = "ex_d1b0f724f39a446a"
# A line-rating fit whose target is moved far outside any loading the interval
# reaches, so the procedure's bracket check fails (this is not physics, only a
# way to make the script stop).
UNBRACKETED_ID: str = "ex_73af8d592b5a04b4"
UNREACHABLE_TARGET: float = 1.0e6


class FakeResponse:
    """An MCP CallToolResult stand-in carrying one JSON text block."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, **_: Any) -> dict[str, Any]:
        """Serialise like the real result."""
        return {"content": [{"type": "text", "text": json.dumps(self.payload)}], "isError": False}


class FakeSession:
    """Answers every call with a fixed body; no server, no solve."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeResponse:
        """Return a fixed payload regardless of the call."""
        return FakeResponse({"status": "success", "data": {}})


def test_budget_stop_is_typed_and_turns_carry_usage(tmp_path: Path) -> None:
    """The budget is the evaluation's, and failure_modes can read every turn's usage."""
    path = tmp_path / "journal.jsonl"
    journal = path.open("x")
    conversation = ScriptedConversation(FakeSession(), journal, [], [])  # type: ignore[arg-type]

    async def exhaust() -> None:
        for _ in range(TOOL_CALL_BUDGET + 1):
            await conversation.trial({"network_id": "x"})

    with pytest.raises(ScriptBudgetExhausted):
        asyncio.run(exhaust())
    journal.close()
    assert conversation.calls == TOOL_CALL_BUDGET
    responses = [json.loads(line)["payload"] for line in path.read_text().splitlines()
                 if json.loads(line)["kind"] == "model_response"]
    assert len(responses) == TOOL_CALL_BUDGET and all(r["usage"]["prompt_tokens"] == 0 for r in responses)


def frozen_suite(directory: Path, rows: list[dict[str, Any]]) -> Path:
    """Freeze rows as a suite with the manifest frozen_task_count checks."""
    directory.mkdir()
    tasks = directory / "examples.jsonl"
    tasks.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (directory / "manifest.json").write_text(json.dumps({
        "examples_sha256": hashlib.sha256(tasks.read_bytes()).hexdigest(), "n_tasks": len(rows)}))
    return tasks


def test_runs_through_the_real_server_and_scores_unfinishable_tasks(tmp_path: Path) -> None:
    """Real MCP calls: report and fit pass; an unbracketed fit is a failed, journaled attempt."""
    rows = {row["example_id"]: row for row in load_rows(SMOKE)}
    broken = copy.deepcopy(rows[UNBRACKETED_ID])
    broken["task"]["inputs"]["target"] = UNREACHABLE_TARGET
    tasks = frozen_suite(tmp_path / "suite", [rows[REPORT_ID], rows[FIT_ID], broken])
    out = tmp_path / "run"
    summary = asyncio.run(evaluate(tasks, TRAIN, out, concurrency=2))
    verified, results = verified_run(out)
    assert verified == summary and summary["model"] == summary["agent"] == AGENT_NAME
    assert [r["passed"] for r in results] == [True, True, False]
    terminal = load_rows(out / f"{UNBRACKETED_ID}.jsonl")[-1]
    assert terminal["kind"] == "terminal" and terminal["payload"]["state"] == SCRIPT_FAILED
    assert "not bracketed" in terminal["payload"]["reason"]
    assert [a["cause"] for a in classify_run(out, tasks)][:2] == ["pass", "pass"]
    with pytest.raises(ValueError):
        asyncio.run(evaluate(tasks, TRAIN, out))
