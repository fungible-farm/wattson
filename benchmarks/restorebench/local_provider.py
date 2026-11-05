"""Serve RestoreBench's agents from a local OpenAI-compatible endpoint (vLLM / llama-swap).

Runs inside RestoreBench's own environment (``uv run --project <level_3>``),
never this repo's, so its pinned pandapower 3.4.0 and solver policies apply.

RestoreBench ships three transports: Amazon Bedrock Converse, the Anthropic
Messages API and the OpenAI Responses API, all cloud services with API keys,
which this repo's golden path excludes (AGENTS.md: no cloud LLM API keys).
Named stand-in: ``install()`` replaces ``restorebench.llm.providers._converse``,
the Bedrock call that ``llm_call`` dispatches every non-Anthropic, non-OpenAI
model id to, with a Chat Completions call to a local server. Everything around
it stays RestoreBench's: the agents and prompts, the Converse-shaped messages
they build, retries, truncation handling, the environment and the scorer.
Model ids are registered with a results-directory slug and a zero token price
(``models._MODEL_SLUGS``, ``models.BEDROCK_PRICES``), because the harness names
result files by slug and prices every cell; local inference has no per-token
price, and the price source says so.

Only ``httpx`` (already in RestoreBench's lockfile, BSD-3-Clause) is used.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Any

import httpx

# A model id names its endpoint's served model behind this prefix, e.g.
# "local/powercoder-qwen-student"; the prefix keeps it clear of every cloud id.
LOCAL_PREFIX: str = "local/"
# The evaluation's per-request limit (powercoder tool_probe.INFERENCE_TIMEOUT_S).
REQUEST_TIMEOUT_S: float = 1800.0
# RestoreBench's agents run at temperature 1.0 by default; local runs are greedy
# so a repeated cell reproduces, matching this repo's evaluations.
TEMPERATURE: float = 0.0
# RestoreBench asks for 16,384 output tokens (providers.DEFAULT_MAX_TOKENS). Its
# IEEE 118 first turn is ~43k characters of card and tool schemas, so prompt plus
# 16,384 overran the r6 servers' 32,768-token context and every call was refused
# (AGENTS.md FM-5). Local calls use the PowerCoder evaluation's own response
# budget (powercoder tool_probe.MAX_TOKENS) instead.
LOCAL_MAX_TOKENS: int = 8192


def install(base_url: str, served_models: list[str]) -> list[str]:
    """Route ``local/<model>`` ids to ``base_url`` and register them; return the ids.

    Args:
        base_url: The OpenAI-compatible server root, e.g. http://127.0.0.1:8101.
        served_models: Model names the server lists under /v1/models.
    """
    # Imported here, not at module load, so the message translators stay testable
    # from this repo's environment (labs/10-.../test_lab10.py).
    from restorebench.llm import models, providers

    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=REQUEST_TIMEOUT_S)
    ids = [LOCAL_PREFIX + name for name in served_models]
    for model_id in ids:
        models._MODEL_SLUGS[model_id] = model_id.replace("/", "-")
        models.BEDROCK_PRICES[model_id] = models.ModelPrice(0.0, 0.0, "local inference: no per-token price")
    models.SUPPORTED_MODELS = tuple(models._MODEL_SLUGS)
    bedrock = providers._converse

    def converse(model_id: str, messages: list[Any], **kwargs: Any) -> Any:
        if not model_id.startswith(LOCAL_PREFIX):
            return bedrock(model_id, messages, **kwargs)
        return chat(client, model_id, messages, **kwargs)

    providers._converse = converse
    return ids


def to_chat(messages: list[Any]) -> list[dict[str, Any]]:
    """Translate RestoreBench's Converse-shaped turns (``ChatMessage``) into Chat Completions messages.

    Text blocks join into one content string; ``toolUse`` blocks become the
    assistant's ``tool_calls``; each ``toolResult`` becomes its own ``tool``
    message, ahead of any text the same turn carried.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message.content, str):
            out.append({"role": message.role, "content": message.content})
            continue
        texts, calls, results = [], [], []
        for block in message.content:
            if "text" in block:
                texts.append(block["text"])
            elif "toolUse" in block:
                use = block["toolUse"]
                calls.append({"id": use["toolUseId"], "type": "function", "function": {
                    "name": use["name"], "arguments": json.dumps(use.get("input") or {}, sort_keys=True)}})
            elif "toolResult" in block:
                result = block["toolResult"]
                results.append({"role": "tool", "tool_call_id": result["toolUseId"],
                                "content": "\n".join(json.dumps(item["json"], sort_keys=True) if "json" in item
                                                     else str(item.get("text", item))
                                                     for item in result.get("content", []))})
        out.extend(results)
        if calls:
            out.append({"role": "assistant", "content": "\n\n".join(texts) or None, "tool_calls": calls})
        elif texts:
            out.append({"role": message.role, "content": "\n\n".join(texts)})
    return out


def to_tools(tools: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate a Converse ``toolConfig`` into Chat Completions function tools."""
    specs = []
    for entry in tools.get("tools", []):
        spec = entry.get("toolSpec", entry)
        specs.append({"type": "function", "function": {
            "name": spec["name"], "description": spec.get("description", ""),
            "parameters": spec.get("inputSchema", {}).get("json", {})}})
    return specs


def chat(client: httpx.Client, model_id: str, messages: list[Any], *, temperature: float,
         max_tokens: int, thinking: bool, tools: dict[str, Any] | None,
         raise_on_truncation: bool = True) -> Any:
    """One Chat Completions turn, returned in RestoreBench's shared response shape.

    ``temperature`` and ``thinking`` from the agents are not forwarded: local
    runs are greedy (``TEMPERATURE``), and the served models reason through
    vLLM's reasoning parser whatever the flag says. ``max_tokens`` is capped at
    ``LOCAL_MAX_TOKENS``.

    Raises:
        LLMFailureError: on an HTTP error, a truncated response (when asked), or
            tool arguments that are not a JSON object.
    """
    from restorebench.llm import providers
    from restorebench.schemas.errors import LLMFailureError

    del temperature, thinking
    max_tokens = min(max_tokens, LOCAL_MAX_TOKENS)
    payload: dict[str, Any] = {"model": model_id[len(LOCAL_PREFIX):], "messages": to_chat(messages),
                               "max_tokens": max_tokens, "temperature": TEMPERATURE}
    if tools is not None:
        payload["tools"] = to_tools(tools)
        payload["tool_choice"] = "auto"
    start = time.monotonic()
    try:
        response = client.post("/v1/chat/completions", json=payload)
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        # Keep the server's reason (e.g. a context overflow) in the recorded failure.
        raise LLMFailureError(model_id=model_id,
                              underlying_exception=f"{error!r}: {error.response.text[:500]}") from error
    except httpx.HTTPError as error:
        raise LLMFailureError(model_id=model_id, underlying_exception=repr(error)) from error
    latency = time.monotonic() - start
    body = response.json()
    choice = body["choices"][0]
    message = choice["message"]
    if choice.get("finish_reason") == "length" and raise_on_truncation:
        raise LLMFailureError(model_id=model_id,
                              underlying_exception=f"response truncated at max_tokens ({max_tokens})")
    blocks: list[dict[str, Any]] = []
    if message.get("content"):
        blocks.append({"text": message["content"]})
    uses = []
    for call in message.get("tool_calls") or []:
        try:
            arguments = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError as error:
            raise LLMFailureError(model_id=model_id,
                                  underlying_exception=f"tool call arguments are not valid JSON: {error}") from error
        if not isinstance(arguments, dict):
            raise LLMFailureError(model_id=model_id, underlying_exception="tool call arguments are not an object")
        block = {"toolUse": {"toolUseId": call["id"], "name": call["function"]["name"], "input": arguments}}
        blocks.append(block)
        uses.append(providers.ToolUse.model_validate(block["toolUse"]))
    usage = body.get("usage") or {}
    raw: dict[str, Any] = {"usage": usage, "stop_reason": choice.get("finish_reason"),
                           "assistant_content": deepcopy(blocks)}
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if reasoning:
        raw["reasoning"] = reasoning
    return providers.LLMResponse(
        text=message.get("content") or "", model_id=model_id,
        tokens_in=int(usage.get("prompt_tokens", 0)), tokens_out=int(usage.get("completion_tokens", 0)),
        tokens_total=int(usage.get("total_tokens", 0)), latency_seconds=latency, raw=raw,
        tool_uses=tuple(uses), assistant_content=tuple(deepcopy(blocks)))
