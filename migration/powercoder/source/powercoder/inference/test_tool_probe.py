"""Gate: a slow inference request fails its own attempt, not the whole run."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from powercoder.inference import powermcp, tool_probe

# These tests stub the inference client, which never reads the prompt; any text
# will do, so it names no case or element.
UNREAD_PROMPT: str = "Prompt text the stubbed inference client never reads."
# A network the real MCP server loads in milliseconds (a built fixture, not a case
# file), for the one tool call the two-turn stub makes.
FIXTURE_NETWORK: str = next(iter(powermcp.FIXTURE_BUILDERS))


class SlowClient:
    """Stands in for InferenceClient; every request exceeds the read timeout."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> "SlowClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass

    def chat_completion(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise httpx.ReadTimeout("timed out")


def test_inference_timeout_is_a_terminal_attempt_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # r4 2026-10-22 04:41: one httpx.ReadTimeout aborted both Granite evaluators.
    monkeypatch.setattr(tool_probe, "InferenceClient", SlowClient)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(UNREAD_PROMPT)
    args = argparse.Namespace(base_url="http://127.0.0.1:1", model="m", prompt=prompt, study_tools=True)
    journal = tmp_path / "journal.jsonl"
    with journal.open("w") as stream:
        assert asyncio.run(tool_probe.probe(args, stream)) == 1
    terminal = json.loads(journal.read_text().splitlines()[-1])
    assert terminal["kind"] == "terminal"
    assert terminal["payload"]["state"] == "inference_timeout"


class TwoTurnClient(SlowClient):
    """Replies with one tool call, then an answer, recording each turn's tool_choice."""

    choices: list[str] = []

    def chat_completion(self, *_args: Any, **kwargs: Any) -> dict[str, Any]:
        TwoTurnClient.choices.append(kwargs["tool_choice"])
        if len(TwoTurnClient.choices) == 1:
            call = {"id": "c0", "type": "function", "function": {
                "name": "load_network", "arguments": json.dumps({"network_id": FIXTURE_NETWORK})}}
            return {"choices": [{"finish_reason": "tool_calls",
                                 "message": {"role": "assistant", "content": None, "tool_calls": [call]}}]}
        return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}]}


def test_required_tool_choice_applies_to_the_first_turn_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement PC-REQ-2: only the first turn is forced to be a tool call.

    Turn 0 sends tool_choice="required" (vLLM builds the model-native grammar
    from the request's tools); turn 1 sends "auto" so the model can answer once
    it has a solver result. The journal records the condition for compare_agents.
    """
    monkeypatch.setattr(tool_probe, "InferenceClient", TwoTurnClient)
    TwoTurnClient.choices = []
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(UNREAD_PROMPT)
    args = argparse.Namespace(base_url="http://127.0.0.1:1", model="m", prompt=prompt, study_tools=True,
                              first_turn_tool_choice="required")
    journal = tmp_path / "journal.jsonl"
    with journal.open("w") as stream:
        asyncio.run(tool_probe.probe(args, stream))
    assert TwoTurnClient.choices == ["required", "auto"]
    assert json.loads(journal.read_text().splitlines()[0])["payload"]["first_turn_tool_choice"] == "required"
