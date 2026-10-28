"""A student's failing first program becomes context; the oracle's program and answer become the supervised turns."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest

from wattson.datasets import dagger, trajectories
from tests.datasets.test_trajectories import LINE_FIT, NEEDS_DATA
from wattson.inference.mcp_server import tool_definitions
from wattson.training import parity_check


@NEEDS_DATA
def test_a_first_program_error_is_relabelled_with_the_oracles_program(tmp_path: Path) -> None:
    bad = trajectories.fit_program(LINE_FIT, True).replace("scale=scale, ", "", 1)
    tools = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.inputSchema}}
             for t in tool_definitions(True, False, True, True)]
    # A "student" that writes the program without scale, then (after the solver's refusal) answers a guess.
    student = {"example_id": LINE_FIT["example_id"], "fault": None, "tools": tools, "messages": [
        {"role": "system", "content": "s"}, {"role": "user", "content": LINE_FIT["task"]["prompt"]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_0", "type": "function", "function": {
            "name": "run_python", "arguments": json.dumps({"code": bad})}}]},
        {"role": "tool", "tool_call_id": "call_0", "content": "x"},
        {"role": "assistant", "content": "0.95"}]}
    journals = tmp_path / "journals"
    journals.mkdir()
    asyncio.run(parity_check.replay(student, tmp_path))
    (tmp_path / f"{LINE_FIT['example_id']}-None.jsonl").rename(journals / f"{LINE_FIT['example_id']}.jsonl")
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(LINE_FIT) + "\n")

    manifest = dagger.collect(argparse.Namespace(journals=journals, tasks=tasks, out_dir=tmp_path / "out", thinking=False,
                                                 report_fields=True))
    assert manifest["counts"]["failed"] == 1 and manifest["counts"]["relabelled"] == 1, manifest["counts"]
    (row,) = [json.loads(line) for line in (tmp_path / "out" / "trajectories.jsonl").read_text().splitlines()]
    assert row["fault"] == dagger.FAULT
    roles = [m["role"] for m in row["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "tool", "assistant"], roles
    programs = [json.loads(m["tool_calls"][0]["function"]["arguments"])["code"] for m in row["messages"] if m.get("tool_calls")]
    assert "scale=scale" not in programs[0] and "scale=scale" in programs[1], "the oracle's turn fixes the student's mistake"
    ids = [c["id"] for m in row["messages"] if m.get("tool_calls") for c in m["tool_calls"]]
    assert len(ids) == len(set(ids)), "renumbered ids stay unique"
    assert row["messages"][-1]["content"].replace(".", "").isdigit(), "the supervised answer is the oracle's printed scale"


def test_only_a_first_program_error_counts() -> None:
    reply = lambda status: {"role": "tool", "content": json.dumps({"content": [{"type": "text", "text": json.dumps({"status": status})}]})}  # noqa: E731
    call = {"role": "assistant", "tool_calls": [{"function": {"name": "run_python"}}]}
    assert dagger.first_program_errored([{}, {}, call, reply("error")])
    assert not dagger.first_program_errored([{}, {}, call, reply("ok")])
    assert not dagger.first_program_errored([{}, {}, {"role": "assistant", "tool_calls": [{"function": {"name": "run_study_trial"}}]}, reply("error")])
