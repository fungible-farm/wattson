"""Integration tests for the teacher inference gateway (llama-swap)."""

import json
import os
import pytest

from wattson.inference.client import InferenceClient

# These tests can load a large resident model. They require an explicit operator
# opt-in and are never part of a CPU-only migration check.
pytestmark = pytest.mark.skipif(
    os.environ.get("WATTSON_TEACHER_INTEGRATION") != "1",
    reason="live teacher service: opt in with WATTSON_TEACHER_INTEGRATION=1",
)


@pytest.fixture
def client():
    c = InferenceClient()
    yield c
    c.close()


def test_teacher_health(client):
    """Teacher gateway responds to health check."""
    resp = client.client.get("http://127.0.0.1:8000/health")
    assert resp.status_code == 200
    assert resp.text.strip() == "OK"


def test_teacher_lists_models(client):
    """Teacher gateway lists available models."""
    models = client.list_models()
    assert len(models) > 0
    model_ids = [m["id"] for m in models]
    assert "qwen3.8-flash-next" in model_ids


def test_teacher_basic_completion(client):
    """Teacher responds to basic chat completion."""
    result = client.chat_completion(
        model="qwen3.8-flash-next",
        messages=[{"role": "user", "content": "What is 2+2? Answer with just the number."}],
        max_tokens=50,
    )
    assert result["choices"][0]["finish_reason"] == "stop"
    content = result["choices"][0]["message"]["content"]
    assert "4" in content


def test_teacher_tool_calling(client):
    """Teacher can generate tool calls."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_power_flow",
                "description": "Run AC power flow analysis on a network",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "network_id": {"type": "string", "description": "Network identifier"}
                    },
                    "required": ["network_id"],
                },
            },
        }
    ]

    result = client.chat_completion(
        model="qwen3.8-flash-next",
        messages=[
            {"role": "system", "content": "You are a power systems engineer assistant."},
            {"role": "user", "content": "Run power flow analysis on network snem-sa-2000."},
        ],
        max_tokens=200,
        tools=tools,
    )

    assert result["choices"][0]["finish_reason"] == "tool_calls"
    tool_calls = result["choices"][0]["message"].get("tool_calls", [])
    assert len(tool_calls) > 0
    assert tool_calls[0]["function"]["name"] == "run_power_flow"
    args = json.loads(tool_calls[0]["function"]["arguments"])
    assert args["network_id"] == "snem-sa-2000"


def test_teacher_domain_question(client):
    """Teacher answers a power systems domain question."""
    result = client.chat_completion(
        model="qwen3.8-flash-next",
        messages=[
            {"role": "system", "content": "You are a power systems engineer assistant."},
            {"role": "user", "content": "Calculate the voltage drop across a 10-ohm resistor carrying 2 amps of current."},
        ],
        max_tokens=200,
    )
    content = result["choices"][0]["message"]["content"]
    assert "20" in content  # Expected answer: 20V
