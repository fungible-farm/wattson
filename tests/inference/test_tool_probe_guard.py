"""The runtime provenance guard: a final answer with a number no tool produced is sent back, then refused; off by default."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from pathlib import Path

import pytest

from wattson.inference import tool_probe
from wattson.training.parity_check import stub_server

PROMPT: str = "Find the scale for transformer 4 so that loading equals 0.184093 percent."
UNTRACED: str = "The scale factor is 0.987654."          # no tool produced 0.987654
TRACED: str = "Target loading is 0.184093."               # 0.184093 is in the prompt


def run(tmp_path: Path, replies: list[str], enforce: bool) -> tuple[int, list[dict], list[dict]]:
    """Run the real probe (real MCP server) against a stub model that gives ``replies`` in order; return (exit code, journal, requests)."""
    captured: list = []
    server = stub_server([{"role": "assistant", "content": r} for r in replies], captured)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(PROMPT)
    journal_path = tmp_path / "journal.jsonl"
    options = argparse.Namespace(base_url=f"http://127.0.0.1:{server.server_address[1]}", model="stub", prompt=prompt, study_tools=True,
                                 first_turn_tool_choice="auto", python_tools=True, python_report_fields=True, enforce_provenance=enforce)
    try:
        with journal_path.open("x") as stream:
            code = asyncio.run(tool_probe.probe(options, stream))
    finally:
        server.shutdown()
    return code, [json.loads(line) for line in journal_path.read_text().splitlines()], captured


def kinds(journal: list[dict]) -> list[str]:
    return [e["kind"] for e in journal]


def test_the_guard_is_off_by_default_and_the_untraced_answer_is_accepted(tmp_path: Path) -> None:
    code, journal, _ = run(tmp_path, [UNTRACED], enforce=False)
    assert code == 0 and "guard" not in kinds(journal) and journal[-1]["payload"]["state"] == "answered"


def test_a_traced_answer_passes_the_guard_untouched(tmp_path: Path) -> None:
    code, journal, _ = run(tmp_path, [TRACED], enforce=True)
    assert code == 0 and "guard" not in kinds(journal) and journal[-1]["payload"]["state"] == "answered"


def test_an_untraced_answer_is_sent_back_naming_the_number_and_a_clean_second_answer_is_accepted(tmp_path: Path) -> None:
    code, journal, requests = run(tmp_path, [UNTRACED, TRACED], enforce=True)
    assert code == 0 and journal[-1]["payload"]["state"] == "answered"
    retries = [e["payload"] for e in journal if e["kind"] == "guard"]
    assert retries == [{"action": "retry", "attempt": 1, "untraced": ["0.987654"]}]
    sent_back = requests[1]["messages"][-1]
    assert sent_back["role"] == "user" and "0.987654" in sent_back["content"] and "run_python" in sent_back["content"]


def test_a_model_that_keeps_inventing_numbers_is_refused_and_no_answer_is_emitted(tmp_path: Path) -> None:
    code, journal, _ = run(tmp_path, [UNTRACED] * 5, enforce=True)
    actions = [e["payload"]["action"] for e in journal if e["kind"] == "guard"]
    assert actions == ["retry"] * tool_probe.MAX_GUARD_RETRIES + ["refused"]
    assert code == 1 and journal[-1]["payload"]["state"] == "guard_refused"


def test_the_retry_budget_is_bounded(tmp_path: Path) -> None:
    assert 1 <= tool_probe.MAX_GUARD_RETRIES <= 3
