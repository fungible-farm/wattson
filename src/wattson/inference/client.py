"""OpenAI-compatible inference client for the NEM WattsOn mission.

Wraps the llama-swap gateway at http://127.0.0.1:8000, providing typed methods for
chat completion, tool calling, and model enumeration.
"""

import json
import time
from typing import Any, Optional

import httpx


class InferenceClient:
    """Client for the llama-swap OpenAI-compatible inference gateway."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout)

    def list_models(self) -> list[dict[str, Any]]:
        """List available models."""
        resp = self.client.get(f"{self.base_url}/v1/models")
        resp.raise_for_status()
        return resp.json()["data"]

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1000,
        temperature: float = 0.7,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Generate a chat completion; ``extra`` adds server-specific fields (e.g. vLLM's return_token_ids)."""
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if extra:
            payload.update(extra)

        start = time.time()
        resp = self.client.post(f"{self.base_url}/v1/chat/completions", json=payload)
        resp.raise_for_status()
        result = resp.json()
        result["elapsed_seconds"] = time.time() - start
        return result

    def close(self):
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
