"""The latency benchmark must time real streamed tokens and refuse a short decode."""

from __future__ import annotations

import json

import httpx
import pytest

from wattson.inference import latency_bench


def stream(tokens: int) -> httpx.MockTransport:
    """A server that streams ``tokens`` reasoning deltas, then usage."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["ignore_eos"] and body["max_tokens"] == latency_bench.DECODE_TOKENS and body["tools"]
        chunks = [{"choices": [{"delta": {"reasoning_content": "x"}}]} for _ in range(tokens)]
        chunks.append({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": tokens}})
        text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


def test_a_full_decode_reports_its_token_counts() -> None:
    """Prompt and completion counts come from the server's usage record."""
    with httpx.Client(transport=stream(latency_bench.DECODE_TOKENS)) as client:
        prompt, completion, ttft, decode, wall = latency_bench.measure(
            client, "http://x", "m", "p", [{"type": "function"}])
    assert (prompt, completion) == (7, latency_bench.DECODE_TOKENS)
    assert 0 <= ttft <= wall and decode > 0


def test_a_short_decode_is_refused() -> None:
    """A server that stopped early would overstate the decode rate."""
    with httpx.Client(transport=stream(3)) as client, pytest.raises(RuntimeError):
        latency_bench.measure(client, "http://x", "m", "p", [{"type": "function"}])
