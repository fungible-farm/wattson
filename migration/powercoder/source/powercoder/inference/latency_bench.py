"""Measure one served model's latency on a fixed workload, alone and one request at a time.

The 100-task evaluation runs 8 attempts at once, so its timings mix queueing,
batching and the model's own speed, and r4 ran servers side by side with
training (AGENTS.md FM-14: GPU utilisation is no proxy either). This sends
every model the same requests, one at a time, on an otherwise idle server:

- the first turn of each task in a frozen suite (system prompt, task, and the
  evaluation's own tool list), streamed;
- ``max_tokens = DECODE_TOKENS`` with vLLM's ``ignore_eos``, so every request
  decodes the same number of tokens whatever the model would have said;
- one unrecorded warm-up request first.

Reported per request: time to first streamed token (content, reasoning or tool
call), decode rate over the tokens after the first, and wall time; the summary
gives medians and the 90th percentile. Decode rates are per tokenizer, so
Qwen and Granite tokens/s are not directly comparable; wall time is. Memory is not measured here: the driver
samples host MemAvailable around the server's whole life (unified memory makes
that the only complete measure on GB10).
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any, TypedDict

import httpx

from powercoder.datasets.audit import load_rows
from powercoder.inference.mcp_server import tool_definitions
from powercoder.inference.tool_probe import SYSTEM_PROMPT

# Long enough that the decode rate dominates the first-token time, short enough
# that 20 requests take minutes at the ~10-19 tok/s single-stream rates measured
# in r2-r5.
DECODE_TOKENS: int = 512
# Same per-request limit as the evaluation (tool_probe.INFERENCE_TIMEOUT_S).
REQUEST_TIMEOUT_S: float = 1800.0
PERCENTILE: int = 90


class Request(TypedDict):
    """One streamed request's measured timings."""

    example_id: str
    prompt_tokens: int
    completion_tokens: int
    ttft_s: float
    decode_tokens_per_s: float
    wall_s: float


def measure(client: httpx.Client, base_url: str, model: str, prompt: str,
            tools: list[dict[str, Any]]) -> tuple[int, int, float, float, float]:
    """Stream one request; return prompt/completion tokens, TTFT, decode rate and wall time.

    Raises:
        RuntimeError: if the server did not decode exactly ``DECODE_TOKENS``.
    """
    payload = {"model": model, "temperature": 0.0, "max_tokens": DECODE_TOKENS, "ignore_eos": True,
               "stream": True, "stream_options": {"include_usage": True}, "tools": tools, "tool_choice": "auto",
               "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]}
    start = time.monotonic()
    first = last = None
    usage: dict[str, int] = {}
    with client.stream("POST", f"{base_url}/v1/chat/completions", json=payload) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[len("data: "):])
            usage = chunk.get("usage") or usage
            for choice in chunk.get("choices", []):
                delta = choice.get("delta") or {}
                if delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning") or delta.get("tool_calls"):
                    now = time.monotonic()
                    first = first if first is not None else now
                    last = now
    end = time.monotonic()
    if first is None or last <= first or usage.get("completion_tokens") != DECODE_TOKENS:
        raise RuntimeError(f"Expected {DECODE_TOKENS} streamed tokens, got {usage}")
    # Measured to the end of the stream, not the last streamed delta: a tool-call
    # parser may buffer tokens after a completed call, which would end the
    # measured window early and overstate the rate (merit review 2026-10-23, B7).
    decode = (DECODE_TOKENS - 1) / (end - first)
    return usage["prompt_tokens"], usage["completion_tokens"], first - start, decode, end - start


def percentile(values: list[float], q: int) -> float:
    """Inclusive-method percentile (statistics.quantiles), defined for 2+ values."""
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1]


def main() -> int:
    """Run the warm-up and the measured requests, then write the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tasks", type=Path, required=True, help="Frozen suite whose first turns are sent")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    tasks = load_rows(args.tasks)
    tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                               "parameters": t.inputSchema}}
             for t in tool_definitions(study_tools=True)]
    results: list[Request] = []
    with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
        measure(client, args.base_url, args.model, tasks[0]["task"]["prompt"], tools)
        for task in tasks:
            prompt_tokens, completion, ttft, decode, wall = measure(
                client, args.base_url, args.model, task["task"]["prompt"], tools)
            results.append({"example_id": task["example_id"], "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion, "ttft_s": ttft,
                            "decode_tokens_per_s": decode, "wall_s": wall})
    report = {
        "model": args.model, "tasks": str(args.tasks), "requests": len(results), "concurrency": 1,
        "decode_tokens": DECODE_TOKENS, "ignore_eos": True, "temperature": 0.0, "warmup_requests": 1,
        "median_ttft_s": statistics.median(r["ttft_s"] for r in results),
        f"p{PERCENTILE}_ttft_s": percentile([r["ttft_s"] for r in results], PERCENTILE),
        "median_decode_tokens_per_s": statistics.median(r["decode_tokens_per_s"] for r in results),
        "median_wall_s": statistics.median(r["wall_s"] for r in results),
        f"p{PERCENTILE}_wall_s": percentile([r["wall_s"] for r in results], PERCENTILE),
        "per_request": results,
    }
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: v for k, v in report.items() if k != "per_request"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
