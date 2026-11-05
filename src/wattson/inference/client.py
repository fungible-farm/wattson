"""OpenAI-compatible inference client for the NEM WattsOn mission.

Wraps the llama-swap gateway at http://127.0.0.1:8000, providing typed methods for
chat completion, tool calling, and model enumeration.
"""

import json
import os
import time
from typing import Any, Optional

import httpx

# Module-level sleep so tests can patch it; the real implementation sleeps.
_sleep = time.sleep


class InferenceClient:
    """Client for the llama-swap OpenAI-compatible inference gateway."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 120.0, api_key_env: Optional[str] = None,
                 max_retries: int = 0, backoff_seconds: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key_env = api_key_env
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        if api_key_env is not None and not os.environ.get(api_key_env):
            # Refuse rather than silently send no credentials; name the variable, never print a value.
            raise ValueError(f"--api-key-env {api_key_env}: that environment variable is not set or is empty")
        self.client = httpx.Client(timeout=timeout)

    def _headers(self) -> Optional[dict[str, str]]:
        """Build request headers, including Authorization if an API key env var is set."""
        if self.api_key_env is None:
            return None
        key = os.environ.get(self.api_key_env)
        if not key:
            return None
        return {"Authorization": f"Bearer {key}"}

    def list_models(self) -> list[dict[str, Any]]:
        """List available models."""
        resp = self.client.get(f"{self.base_url}/v1/models", headers=self._headers())
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
        retries: list[dict[str, Any]] = []
        attempt = 0
        while True:
            resp = self.client.post(f"{self.base_url}/v1/chat/completions", json=payload, headers=self._headers())
            try:
                resp.raise_for_status()
                break
            except httpx.HTTPStatusError as error:
                status = resp.status_code
                # Retry only on 429 or 5xx, up to max_retries times.
                if attempt >= self.max_retries or (status < 500 and status != 429):
                    raise
                # Honour Retry-After header if present (cap at 60 s).
                wait = self.backoff_seconds * (2 ** attempt)
                retry_after = resp.headers.get("retry-after")
                if retry_after is not None:
                    try:
                        wait = min(float(retry_after), 60.0)
                    except ValueError:
                        pass
                retries.append({"status": status, "wait_seconds": wait})
                attempt += 1
                _sleep(wait)
        result = resp.json()
        result["elapsed_seconds"] = time.time() - start
        if retries:
            result["retries"] = retries
        return result

    def close(self):
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
