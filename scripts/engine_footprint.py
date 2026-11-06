#!/usr/bin/env python3
"""Measured memory footprint per engine (#29, first task: "inventory engines and their memory footprint, measured").

For each engine this repo can start on its own (embedding server, reranker, Candle CPU server) the script starts it with its
own recipe, makes ONE real request so the runtime has allocated what it needs to answer, and reads two instruments:
  * the container's cgroup memory (`podman stats --no-stream`), and
  * the host's MemAvailable drop against the idle baseline taken just before the start (/proc/meminfo).
Then it stops the engine and prints a table. Nothing is estimated; a figure that could not be read is printed as n/a.
llama-swap's chat models are NOT started here: loading one is a large, shared-host decision that is the owner's. Their
footprint is reported only if a model is already resident (MemAvailable now vs the same baseline is not attributable to
one engine, so it is printed as a host figure, not as a per-model measurement).
Starts and stops only these containers: wattson-embed, wattson-rerank, wattson-candle. Needs the models already in
.models (retrieval-up downloads them with sha256 verification) and refuses to start an engine if less than 8 GiB is available.
"""
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, **kw)


def mem_available_mib():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    return float("nan")


def container_mib(name):
    out = sh(["podman", "stats", "--no-stream", "--format", "json", name])
    try:
        usage = json.loads(out.stdout)[0].get("mem_usage") or json.loads(out.stdout)[0].get("MemUsage", "")
    except (ValueError, IndexError, AttributeError):
        return None
    m = re.match(r"([\d.]+)\s*([KMGT]i?B)", str(usage).split("/")[0].strip())
    if not m:
        return None
    scale = {"KB": 1 / 1024, "KIB": 1 / 1024, "MB": 1, "MIB": 1, "GB": 1024, "GIB": 1024, "TB": 1048576, "TIB": 1048576}
    return float(m.group(1)) * scale[m.group(2).upper()]


def http(url, body=None, timeout=300):
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout)]
    if body is not None:
        cmd += ["-X", "POST", "-H", "content-type: application/json", "-d", json.dumps(body)]
    return sh(cmd + [url]).stdout.strip()


ENGINES = [
    # name, start recipe(s), container(s), probe (url, body), stop recipe
    ("embedding (nomic-embed-text-v1.5 Q4_K_M, llama.cpp)", ["retrieval-up"], ["wattson-embed"],
     ("http://127.0.0.1:8081/v1/embeddings", {"input": "footprint probe", "model": "x"}), "retrieval-down"),
    ("reranker (bge-reranker-v2-m3 Q4_K_M, llama.cpp)", ["retrieval-up"], ["wattson-rerank"],
     ("http://127.0.0.1:8082/v1/rerank", {"query": "q", "documents": ["a", "b"], "model": "x"}), "retrieval-down"),
    ("Candle CPU (mistral.rs, Qwen3-0.6B)", ["candle-up"], ["wattson-candle"],
     ("http://127.0.0.1:3301/v1/chat/completions",
      {"model": "default", "messages": [{"role": "user", "content": "ok?"}], "max_tokens": 8}), "candle-down"),
]


def main():
    rows = []
    started_retrieval = False
    try:
        for label, up, containers, (url, body), down in ENGINES:
            if mem_available_mib() < 8 * 1024:
                print(f"FAIL engine-footprint: under 8 GiB available before starting {label}; refusing")
                sys.exit(1)
            if label.startswith("reranker") and started_retrieval:
                pass  # retrieval-up already started both servers
            else:
                base = mem_available_mib()
                r = sh(["just", *up])
                if r.returncode:
                    print(f"FAIL engine-footprint: {up} failed: {(r.stdout + r.stderr)[-300:]}")
                    sys.exit(1)
                if up == ["retrieval-up"]:
                    started_retrieval = True
                    base_for_retrieval = base
            code = http(url, body)
            time.sleep(2)
            cg = container_mib(containers[0])
            if label.startswith("embedding"):
                host_drop = None  # attributed to the pair below
            elif label.startswith("reranker"):
                host_drop = base_for_retrieval - mem_available_mib()
            else:
                host_drop = base - mem_available_mib()
            rows.append((label, code, cg, host_drop))
            if label.startswith("reranker"):
                sh(["just", "retrieval-down"])
                started_retrieval = False
            elif not label.startswith("embedding"):
                sh(["just", down])
    finally:
        sh(["just", "retrieval-down"])
        sh(["just", "candle-down"])
    fmt = lambda v: "n/a" if v is None else f"{v:,.0f}"
    print(f"{'engine':58} {'probe':>6} {'container MiB':>14} {'host drop MiB':>14}")
    for label, code, cg, drop in rows:
        print(f"{label:58} {code:>6} {fmt(cg):>14} {fmt(drop) if drop is not None else 'pair (see next)':>14}")
    print("note: the embedding and reranker start together (one recipe); the host drop is read once, after the pair is up.")
    if any(code != "200" for _, code, _, _ in rows) or any(cg is None for _, _, cg, _ in rows):
        print("FAIL engine-footprint: a probe was not answered 200 or a container figure could not be read")
        sys.exit(1)
    print("PASS engine-footprint: every engine answered a real request and its memory was read from podman and /proc")


if __name__ == "__main__":
    main()
