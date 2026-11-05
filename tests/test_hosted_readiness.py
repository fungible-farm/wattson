"""Hosted-model readiness: API key auth and extra request body."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


class FakeInferenceHandler(BaseHTTPRequestHandler):
    """In-process fake OpenAI-compatible server that records requests."""

    captured: list[dict] = []

    def log_message(self, format, *args):
        pass  # silence request logging

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        self.captured.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(body),
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        # Return a valid chat completion response
        response = {
            "id": "test-1",
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "done"},
            }],
            "model": "test-model",
        }
        self.wfile.write(json.dumps(response).encode("utf-8"))


@pytest.fixture
def fake_server():
    """Start an in-process fake HTTP server on 127.0.0.1."""
    FakeInferenceHandler.captured = []
    server = HTTPServer(("127.0.0.1", 0), FakeInferenceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield port, FakeInferenceHandler
    server.shutdown()


def test_bearer_header_sent_when_api_key_env_set(fake_server, tmp_path, monkeypatch):
    """With --api-key-env set, the fake server receives the Bearer header."""
    port, handler = fake_server
    monkeypatch.setenv("TEST_API_KEY", "secret-key-value")

    from wattson.inference import tool_probe

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test")
    journal = tmp_path / "journal.jsonl"

    args = argparse.Namespace(
        base_url=f"http://127.0.0.1:{port}",
        model="test-model",
        prompt=prompt,
        journal=journal,
        study_tools=True,
        api_key_env="TEST_API_KEY",
        extra_body=None,
    )
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("w") as stream:
        asyncio.run(tool_probe.probe(args, stream))

    # Find the chat completion request
    chat_requests = [c for c in handler.captured if c["path"] == "/v1/chat/completions"]
    assert len(chat_requests) == 1
    assert chat_requests[0]["headers"]["Authorization"] == "Bearer secret-key-value"


def test_no_auth_header_without_api_key_env(fake_server, tmp_path, monkeypatch):
    """Without --api-key-env, no Authorization header is sent."""
    port, handler = fake_server

    from wattson.inference import tool_probe

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test")
    journal = tmp_path / "journal.jsonl"

    args = argparse.Namespace(
        base_url=f"http://127.0.0.1:{port}",
        model="test-model",
        prompt=prompt,
        journal=journal,
        study_tools=True,
        api_key_env=None,
        extra_body=None,
    )
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("w") as stream:
        asyncio.run(tool_probe.probe(args, stream))

    chat_requests = [c for c in handler.captured if c["path"] == "/v1/chat/completions"]
    assert len(chat_requests) == 1
    assert "Authorization" not in chat_requests[0]["headers"]


def test_extra_body_fields_arrive_in_request(fake_server, tmp_path):
    """The --extra-body JSON fields are merged into the request body."""
    port, handler = fake_server

    from wattson.inference import tool_probe

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test")
    journal = tmp_path / "journal.jsonl"

    args = argparse.Namespace(
        base_url=f"http://127.0.0.1:{port}",
        model="test-model",
        prompt=prompt,
        journal=journal,
        study_tools=True,
        api_key_env=None,
        extra_body={"reasoning_effort": "high"},
    )
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("w") as stream:
        asyncio.run(tool_probe.probe(args, stream))

    chat_requests = [c for c in handler.captured if c["path"] == "/v1/chat/completions"]
    assert len(chat_requests) == 1
    assert chat_requests[0]["body"]["reasoning_effort"] == "high"


def test_api_key_not_in_output_files(fake_server, tmp_path, monkeypatch):
    """The API key value appears in none of the files written under the run output directory."""
    port, handler = fake_server
    monkeypatch.setenv("TEST_API_KEY", "secret-key-value")

    from wattson.inference import tool_probe

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test")
    journal = tmp_path / "journal.jsonl"

    args = argparse.Namespace(
        base_url=f"http://127.0.0.1:{port}",
        model="test-model",
        prompt=prompt,
        journal=journal,
        study_tools=True,
        api_key_env="TEST_API_KEY",
        extra_body=None,
    )
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("w") as stream:
        asyncio.run(tool_probe.probe(args, stream))

    # Check all files in tmp_path for the key value
    for f in tmp_path.rglob("*"):
        if f.is_file():
            content = f.read_text()
            assert "secret-key-value" not in content, f"API key found in {f}"


def test_invalid_extra_body_json_exits_2():
    """Invalid --extra-body JSON is an argparse error with exit code 2."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "wattson.inference.tool_probe",
         "--base-url", "http://127.0.0.1:1",
         "--model", "test",
         "--prompt", "/dev/null",
         "--journal", "/tmp/journal.jsonl",
         "--extra-body", '{"invalid": json}'],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 2, f"Expected exit code 2, got {result.returncode}"


def test_unset_api_key_env_raises_value_error():
    """InferenceClient raises ValueError naming the variable when the env var is unset."""
    import os
    from wattson.inference.client import InferenceClient

    var = "UNSET_VAR_XYZ"
    # Ensure the variable is not set
    if var in os.environ:
        del os.environ[var]

    with pytest.raises(ValueError) as exc_info:
        InferenceClient("http://127.0.0.1:1", api_key_env=var)

    msg = str(exc_info.value)
    assert var in msg, f"Error message should name the variable: {msg}"


def test_api_key_env_set_constructs_and_sends_auth(fake_server, tmp_path, monkeypatch):
    """With the variable set, InferenceClient constructs fine and sends Bearer auth."""
    port, handler = fake_server
    monkeypatch.setenv("TEST_API_KEY", "sekrit-123")

    from wattson.inference.client import InferenceClient

    client = InferenceClient(f"http://127.0.0.1:{port}", api_key_env="TEST_API_KEY")
    try:
        client.chat_completion("test-model", [{"role": "user", "content": "hi"}], max_tokens=10)
    finally:
        client.close()

    chat_requests = [c for c in handler.captured if c["path"] == "/v1/chat/completions"]
    assert len(chat_requests) == 1
    assert chat_requests[0]["headers"]["Authorization"] == "Bearer sekrit-123"

    # The key value should not appear in any exception text (no exceptions expected)
    # and not in any file written
    for f in tmp_path.rglob("*"):
        if f.is_file():
            content = f.read_text()
            assert "sekrit-123" not in content, f"API key found in {f}"


def test_evaluate_agent_unset_api_key_env_exits_2(fake_server):
    """evaluate_agent main with --api-key-env naming an unset variable exits 2 before any request."""
    import subprocess
    import sys
    import os

    port, handler = fake_server
    var = "UNSET_VAR_XYZ"
    # Ensure the variable is not set
    env = os.environ.copy()
    if var in env:
        del env[var]

    result = subprocess.run(
        [sys.executable, "-m", "wattson.inference.evaluate_agent",
         "--base-url", f"http://127.0.0.1:{port}",
         "--model", "test",
         "--tasks", str(Path(__file__).parents[2] / "fixtures/smoke/tasks/examples.jsonl"),
         "--exclude", str(Path(__file__).parents[2] / "fixtures/smoke/tasks/examples.jsonl"),
         "--out", "/tmp/eval_test_out",
         "--api-key-env", var],
        capture_output=True,
        env=env,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 2, f"Expected exit code 2, got {result.returncode}"
    assert var in result.stderr.decode("utf-8"), f"Error should name the variable: {result.stderr.decode('utf-8')}"
    assert len(handler.captured) == 0, "No requests should be made before the error"


def test_compare_agents_lists_extra_body_as_a_condition():
    """Constant check only: extra_body is a CONDITIONS field (default None). That compare() then refuses mixed arms is NOT exercised here."""
    from wattson.inference.compare_agents import CONDITIONS

    assert "extra_body" in CONDITIONS
    assert CONDITIONS["extra_body"] is None


def test_probe_journal_never_contains_the_key_value(fake_server, tmp_path, monkeypatch):
    """A probe run with --api-key-env set writes a journal that does not contain the key value."""
    port, handler = fake_server
    monkeypatch.setenv("TEST_API_KEY", "secret-key-value")

    from wattson.inference import tool_probe

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test")
    journal = tmp_path / "journal.jsonl"

    args = argparse.Namespace(
        base_url=f"http://127.0.0.1:{port}",
        model="test-model",
        prompt=prompt,
        journal=journal,
        study_tools=True,
        api_key_env="TEST_API_KEY",
        extra_body=None,
    )
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("w") as stream:
        asyncio.run(tool_probe.probe(args, stream))

    # Check that the journal does not contain the key value
    journal_content = journal.read_text()
    assert "secret-key-value" not in journal_content, "API key value should not appear in journal"
