"""Bounded retry with backoff in the inference client."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from wattson.inference.client import InferenceClient, _sleep


class RetryHandler(BaseHTTPRequestHandler):
    """Fake server that returns a sequence of status codes then 200."""

    responses: list[int] = []
    request_count = 0
    retry_after: str | None = None

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        RetryHandler.request_count += 1
        if RetryHandler.request_count <= len(RetryHandler.responses):
            status = RetryHandler.responses[RetryHandler.request_count - 1]
            self.wfile.write(f"HTTP/1.1 {status} Test\r\n".encode())
            if RetryHandler.retry_after is not None:
                self.wfile.write(f"Retry-After: {RetryHandler.retry_after}\r\n".encode())
            self.wfile.write(b"Content-Type: application/json\r\n")
            self.wfile.write(b"\r\n")
            self.wfile.write(b'{"error": "test"}')
        else:
            self.wfile.write(b"HTTP/1.1 200 OK\r\n")
            self.wfile.write(b"Content-Type: application/json\r\n")
            self.wfile.write(b"\r\n")
            response = {
                "id": "test-1",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "done"}}],
                "model": "test-model",
            }
            self.wfile.write(json.dumps(response).encode("utf-8"))


@pytest.fixture
def fake_server():
    RetryHandler.responses = []
    RetryHandler.request_count = 0
    RetryHandler.retry_after = None
    server = HTTPServer(("127.0.0.1", 0), RetryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield port
    server.shutdown()


def test_429_retried_and_succeeds(fake_server, monkeypatch):
    """A 429 is retried; the result records the retry."""
    port = fake_server
    RetryHandler.responses = [429]

    waits: list[float] = []
    monkeypatch.setattr("wattson.inference.client._sleep", lambda s: waits.append(s))

    client = InferenceClient(f"http://127.0.0.1:{port}", max_retries=2)
    result = client.chat_completion("m", [{"role": "user", "content": "hi"}], max_tokens=10)
    client.close()

    assert RetryHandler.request_count == 2
    assert "retries" in result
    assert len(result["retries"]) == 1
    assert result["retries"][0]["status"] == 429
    assert waits == [1.0]


def test_500_retried_twice_then_raises(fake_server, monkeypatch):
    """Three 500s with max_retries=2 raises after exactly 3 requests."""
    port = fake_server
    RetryHandler.responses = [500, 500, 500]

    monkeypatch.setattr("wattson.inference.client._sleep", lambda s: None)

    client = InferenceClient(f"http://127.0.0.1:{port}", max_retries=2)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.chat_completion("m", [{"role": "user", "content": "hi"}], max_tokens=10)
    client.close()

    assert exc_info.value.response.status_code == 500
    assert RetryHandler.request_count == 3


def test_400_not_retried(fake_server):
    """A 400 is never retried."""
    port = fake_server
    RetryHandler.responses = [400]

    client = InferenceClient(f"http://127.0.0.1:{port}", max_retries=2)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.chat_completion("m", [{"role": "user", "content": "hi"}], max_tokens=10)
    client.close()

    assert exc_info.value.response.status_code == 400
    assert RetryHandler.request_count == 1


def test_default_no_retry(fake_server):
    """Default max_retries=0 does not retry a 429."""
    port = fake_server
    RetryHandler.responses = [429]

    client = InferenceClient(f"http://127.0.0.1:{port}")
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.chat_completion("m", [{"role": "user", "content": "hi"}], max_tokens=10)
    client.close()

    assert exc_info.value.response.status_code == 429
    assert RetryHandler.request_count == 1


def test_retry_after_header_honoured(fake_server, monkeypatch):
    """A Retry-After header overrides the backoff calculation."""
    port = fake_server
    RetryHandler.responses = [429]
    RetryHandler.retry_after = "3"

    waits: list[float] = []
    monkeypatch.setattr("wattson.inference.client._sleep", lambda s: waits.append(s))

    client = InferenceClient(f"http://127.0.0.1:{port}", max_retries=1, backoff_seconds=10.0)
    result = client.chat_completion("m", [{"role": "user", "content": "hi"}], max_tokens=10)
    client.close()

    assert RetryHandler.request_count == 2
    assert waits == [3.0]
    assert result["retries"][0]["wait_seconds"] == 3.0


def test_no_retries_key_when_none(fake_server):
    """When no retries happen, the result has no 'retries' key."""
    port = fake_server

    client = InferenceClient(f"http://127.0.0.1:{port}", max_retries=2)
    result = client.chat_completion("m", [{"role": "user", "content": "hi"}], max_tokens=10)
    client.close()

    assert "retries" not in result


def test_backoff_doubles(fake_server, monkeypatch):
    """Backoff doubles: 1s, 2s, 4s, ..."""
    port = fake_server
    RetryHandler.responses = [429, 503, 429]

    waits: list[float] = []
    monkeypatch.setattr("wattson.inference.client._sleep", lambda s: waits.append(s))

    client = InferenceClient(f"http://127.0.0.1:{port}", max_retries=3, backoff_seconds=1.0)
    result = client.chat_completion("m", [{"role": "user", "content": "hi"}], max_tokens=10)
    client.close()

    assert RetryHandler.request_count == 4
    assert waits == [1.0, 2.0, 4.0]
    assert len(result["retries"]) == 3
