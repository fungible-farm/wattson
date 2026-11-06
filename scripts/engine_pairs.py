#!/usr/bin/env python3
"""Concurrent-hosting gate for the engines this repo starts itself (#15, #29): a PASS/FAIL per engine set.

OWNER RULE (set 2026-11-07, recorded on #15 and #29): a set PASSES if, with every engine in it loaded and under load,
  (a) total resident memory, (MemTotal - MemAvailable) / MemTotal read from /proc/meminfo, is at most 85%, AND
  (b) every request succeeds (zero failures out of at least 48 requests at 4 concurrent), otherwise it FAILS.
Both numbers come from instruments (/proc/meminfo, HTTP status codes); nothing is estimated.

Engines: embedding (llama.cpp, :8081), reranker (llama.cpp, :8082), Candle CPU (mistral.rs, :3301). Sets measured: every pair
and all three. The script starts all three with their own recipes (`retrieval-up`, `candle-up`), then for each set starts only
that set's containers (`podman stop` / `podman start`), waits until they answer, sends 6 cycles of 4 concurrent requests to
every engine in the set (24 requests per engine, so a pair is 48), and samples MemAvailable throughout; the LOWEST MemAvailable
seen gives the peak resident memory. llama-swap chat models are not part of these sets: loading one is the owner's call, so a
set containing one is reported SKIPPED, never passed. Tears everything down at the end (`retrieval-down`, `candle-down`).
Usage: python3 scripts/engine_pairs.py
"""
import itertools
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_CEILING = 0.85
CYCLES, CONCURRENCY = 6, 4

ENGINES = {
    "embedding": {"container": "wattson-embed", "url": "http://127.0.0.1:8081/v1/embeddings",
                  "body": {"input": ["footprint probe sentence"] * 8, "model": "x"}},
    "reranker": {"container": "wattson-rerank", "url": "http://127.0.0.1:8082/v1/rerank",
                 "body": {"query": "q", "documents": ["a", "b", "c", "d", "e", "f", "g", "h"], "model": "x"}},
    "candle": {"container": "wattson-candle", "url": "http://127.0.0.1:3301/v1/chat/completions",
               "body": {"model": "default", "messages": [{"role": "user", "content": "Reply with one word: ok"}], "max_tokens": 8}},
}


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, **kw)


def meminfo():
    values = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, rest = line.split(":", 1)
            values[key] = int(rest.split()[0]) * 1024
    return values["MemTotal"], values["MemAvailable"]


def post(url, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"content-type": "application/json"})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            return r.status == 200, time.time() - start
    except (urllib.error.URLError, OSError, TimeoutError):
        return False, time.time() - start


def healthy(url):
    probe = url.rsplit("/v1/", 1)[0] + "/v1/models"
    code = sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "3", probe]).stdout.strip()
    return code == "200"


def measure(names):
    for name, spec in ENGINES.items():
        if name not in names:
            sh(["podman", "stop", "-t", "2", spec["container"]])
            continue
        for attempt in range(1, 6):  # a killed container can leave its port held for a moment; retry, and say what failed
            started = sh(["podman", "start", spec["container"]])
            if started.returncode == 0:
                break
            print(f"  podman start {spec['container']} failed (attempt {attempt}): {(started.stdout + started.stderr).strip()[-300:]!r}")
            time.sleep(3)
    deadline = time.time() + 300
    while not all(healthy(ENGINES[n]["url"]) for n in names):
        if time.time() > deadline:
            for n in names:
                if not healthy(ENGINES[n]["url"]):
                    state = sh(["podman", "ps", "-a", "--filter", f"name=^{ENGINES[n]['container']}$", "--format", "{{.Status}}"]).stdout.strip()
                    logs = sh(["podman", "logs", "--tail", "5", ENGINES[n]["container"]])
                    print(f"  unhealthy: {n} container status={state!r} last log: {(logs.stdout + logs.stderr)[-400:]!r}")
            return None
        time.sleep(2)
    time.sleep(3)
    lowest = [meminfo()[1]]
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            lowest.append(meminfo()[1])
            time.sleep(0.5)

    thread = threading.Thread(target=sampler)
    thread.start()
    results = {n: [] for n in names}
    try:
        with ThreadPoolExecutor(max_workers=CONCURRENCY * len(names)) as pool:
            for _ in range(CYCLES):
                futures = [(n, pool.submit(post, ENGINES[n]["url"], ENGINES[n]["body"])) for n in names for _ in range(CONCURRENCY)]
                for n, f in futures:
                    results[n].append(f.result())
    finally:
        stop.set()
        thread.join()
    total = meminfo()[0]
    peak_used = total - min(lowest)
    requests = sum(len(v) for v in results.values())
    failures = sum(1 for v in results.values() for ok, _ in v if not ok)
    p50 = {n: sorted(t for _, t in v)[len(v) // 2] for n, v in results.items()}
    return {"used_pct": 100 * peak_used / total, "peak_gib": peak_used / 2**30, "requests": requests, "failures": failures, "p50": p50}


def main():
    sets = [(a, b) for a, b in itertools.combinations(ENGINES, 2)] + [tuple(ENGINES)]
    total, available = meminfo()
    if (total - available) / total > 0.5:
        print(f"FAIL engine-pairs: {100 * (total - available) / total:.0f}% of memory already in use before starting; refusing")
        sys.exit(1)
    verdicts = []
    try:
        for recipe in ("retrieval-up", "candle-up"):
            r = sh(["just", recipe])
            if r.returncode:
                print(f"FAIL engine-pairs: {recipe} failed: {(r.stdout + r.stderr)[-300:]}")
                sys.exit(1)
        print(f"rule: PASS if peak resident memory <= {MEMORY_CEILING:.0%} of MemTotal and 0 failed requests of >= 48 at {CONCURRENCY} concurrent")
        print(f"{'set':32} {'requests':>8} {'failed':>6} {'peak GiB':>8} {'peak %':>7}  verdict   p50 s")
        for names in sets:
            m = measure(names)
            label = " + ".join(names)
            if m is None:
                print(f"{label:32} did not become healthy -> FAIL")
                verdicts.append(False)
                continue
            ok = m["used_pct"] <= MEMORY_CEILING * 100 and m["failures"] == 0 and m["requests"] >= 48
            verdicts.append(ok)
            p50 = " ".join(f"{n}={t:.1f}" for n, t in m["p50"].items())
            print(f"{label:32} {m['requests']:>8} {m['failures']:>6} {m['peak_gib']:>8.1f} {m['used_pct']:>6.1f}%  {'PASS' if ok else 'FAIL':7}   {p50}")
        print("llama-swap chat model + any engine: SKIPPED (loading it is the owner's call; not counted as a pass)")
    finally:
        sh(["just", "retrieval-down"])
        sh(["just", "candle-down"])
    if not all(verdicts):
        print("FAIL engine-pairs: at least one set failed the rule (the table above is the evidence)")
        sys.exit(1)
    print("PASS engine-pairs: every measured set met the owner rule")


if __name__ == "__main__":
    main()
