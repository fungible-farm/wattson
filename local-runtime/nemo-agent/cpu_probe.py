"""Prove a real CPU completion inside a pod with loopback as its only interface."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import time
import urllib.request

BASE_URL: str = "http://127.0.0.1:1234/v1"
# Small startup allowance and output budget for a dependency/runtime smoke test.
STARTUP_ATTEMPTS: int = 30
STARTUP_POLL_SECONDS: float = 1.0
REQUEST_TIMEOUT_SECONDS: int = 120
OUTPUT_TOKENS: int = 8


def probe(output: Path) -> None:
    """Require isolated networking and save the model's actual response/usage."""
    interfaces = [name for _, name in socket.if_nameindex()]
    if interfaces != ["lo"]:
        raise AssertionError(f"expected loopback only, observed {interfaces}")
    models = None
    for attempt in range(STARTUP_ATTEMPTS):
        try:
            with urllib.request.urlopen(BASE_URL + "/models", timeout=STARTUP_POLL_SECONDS) as response:
                models = json.load(response)
            break
        except OSError:
            if attempt == STARTUP_ATTEMPTS - 1:
                raise
            time.sleep(STARTUP_POLL_SECONDS)
    assert models is not None
    payload = {"model": models["data"][0]["id"], "messages": [
        {"role": "user", "content": "Reply with the word READY."}],
        "max_tokens": OUTPUT_TOKENS, "temperature": 0}
    request = urllib.request.Request(BASE_URL + "/chat/completions",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        result = json.load(response)
    if not result["choices"][0]["message"]["content"] or result["usage"]["completion_tokens"] <= 0:
        raise AssertionError("no actual completion was produced")
    output.mkdir(parents=True, exist_ok=True)
    (output / "cpu-smoke.json").write_text(json.dumps({"interfaces": interfaces,
        "models": models, "request": payload, "response": result}, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print("PASS: actual CPU completion with loopback-only networking")


if __name__ == "__main__":
    probe(Path(sys.argv[1]))
