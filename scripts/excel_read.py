#!/usr/bin/env python3
"""Deterministic bounded read from a workbook through the containerized Excel MCP. No inference anywhere.

Every layer logs one line to stderr (`LAYER key=value`); stdout carries only the JSON result.
  python3 scripts/excel_read.py WORKBOOK SHEET RANGE [--mode values|formulas] [--max-cells N]
"""
import argparse, hashlib, json, subprocess, sys, time
from pathlib import Path

RUN = Path(__file__).resolve().parents[1] / "integrations/excel-mcp/run.sh"


def log(layer: str, **kv: object) -> None:
    print(f"[excel-read] {layer:<9}" + " ".join(f"{k}={v}" for k, v in kv.items()), file=sys.stderr, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("workbook", type=Path); ap.add_argument("sheet"); ap.add_argument("range")
    ap.add_argument("--mode", choices=("values", "formulas"), default="values")
    ap.add_argument("--max-cells", type=int, default=100)
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()
    wb = a.workbook.resolve()
    sha = hashlib.sha256(wb.read_bytes()).hexdigest()
    log("source", path=wb, bytes=wb.stat().st_size, sha256=sha)
    log("policy", tools="describe_workbook,read_range", network="none", mount="ro", mode=a.mode, max_cells=a.max_cells)
    name = wb.name
    call = lambda i, n, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": n, "arguments": {"path": name, **args}}}
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "excel-read", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            call(2, "read_range", {"sheet": a.sheet, "range": a.range, "mode": a.mode, "max_cells": a.max_cells})]
    log("mcp", run=RUN, requests=len(msgs))
    t = time.monotonic()
    p = subprocess.Popen([str(RUN), "stdio", str(wb)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for m in msgs:
        p.stdin.write(json.dumps(m) + "\n")
    p.stdin.flush()
    result = None
    deadline = t + a.timeout
    while time.monotonic() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        r = json.loads(line)
        if r.get("id") == 2:
            result = r; break
    p.stdin.close(); p.terminate(); p.wait(timeout=20)
    log("done", seconds=round(time.monotonic() - t, 1), got_reply=result is not None)
    if result is None or "error" in result or result.get("result", {}).get("isError"):
        log("FAIL", reply=json.dumps(result)[:500]); return 1
    # Tool errors arrive as result.result.content[0].text starting with "Error executing tool"
    res = result.get("result", {})
    content = res.get("content", [])
    if content and isinstance(content[0], dict) and content[0].get("text", "").startswith("Error executing tool"):
        log("FAIL", reply=json.dumps(result)[:500]); return 1
    res = result["result"]
    payload = res.get("structuredContent") or json.loads(res["content"][0]["text"])
    out = {"workbook_sha256": sha, "sheet": a.sheet, "range": a.range, "mode": a.mode, "result": payload}
    print(json.dumps(out, indent=2)); log("PASS", sha256=sha[:12]); return 0


if __name__ == "__main__":
    sys.exit(main())
