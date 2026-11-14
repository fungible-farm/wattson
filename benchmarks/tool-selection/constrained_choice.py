#!/usr/bin/env python3
"""Constrained-decoding control for the tool-selection benchmark.

One call per case to a local OpenAI-compatible endpoint; the answer must be a JSON
object under a json_schema grammar (enum of the four tool names plus "abstain"),
so there is no free text. Any failure - request error, empty reply, unparsable or
off-enum answer - counts as abstain, recorded with the reason.
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
URL = "http://192.168.1.137:8002/v1/chat/completions"
MODEL, ABSTAIN, TIMEOUT = "neo-coder", "abstain", 120.0


def prompt(tools, task):
    head = ["Choose the single tool that should handle the task.", "", "Tools:"]
    tail = ["", f"Task: {task}", "", "Answer with the one tool name that fits.",
            f"Answer \"{ABSTAIN}\" only if no tool fits.", "JSON:"]
    body = [f"- {t.get('name')}: {t.get('description')}" for t in tools]
    return "\n".join(head + body + tail)


def ask(names, text):
    """Return (pick_or_None, latency_s, note)."""
    enum = list(names) + [ABSTAIN]
    body = {"model": MODEL, "temperature": 0, "max_tokens": 1500,
            "messages": [{"role": "user", "content": text}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "pick", "strict": True, "schema": {
                    "type": "object", "properties": {"tool": {"enum": enum}},
                    "required": ["tool"], "additionalProperties": False}}}}
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "x-wattson-task": "tool-selection"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - any transport/JSON failure abstains
        return None, time.time() - t0, f"request error: {type(exc).__name__}: {exc}"[:180]
    lat = time.time() - t0
    try:
        content = payload["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return None, lat, "no message content"
    if not content or not str(content).strip():
        return None, lat, "empty reply"
    try:
        value = json.loads(content)["tool"]
    except Exception:  # noqa: BLE001
        return None, lat, f"bad json: {str(content)[:120]!r}"
    if value not in enum:
        return None, lat, f"non-enum value: {str(value)[:80]!r}"
    return value, lat, "" if value != ABSTAIN else "abstained"


def pct(vals, p):
    """Nearest-rank percentile of a latency list."""
    return sorted(vals)[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))] if vals else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "heldout"], required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    tools = json.loads((HERE / "tools.json").read_text())
    names = [t["name"] for t in tools]
    cases = [c for c in json.loads((HERE / "cases.json").read_text())
             if c.get("split") == args.split]
    rows, lats = [], []
    for c in cases:
        value, lat, note = ask(names, prompt(tools, c.get("task", "")))
        chosen = value if value is not None else ABSTAIN
        lats.append(lat)
        rows.append({"id": c.get("id"), "family": c.get("family"),
                     "correct_tool": c.get("correct_tool"), "pick": chosen,
                     "ok": chosen == c.get("correct_tool"),
                     "latency_s": round(lat, 3), "note": note})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rows, indent=2) + "\n")
    for fam in sorted({r["family"] for r in rows}):
        rs = [r for r in rows if r["family"] == fam]
        print(f"{fam} {sum(1 for r in rs if r['ok'])}/{len(rs)} "
              f"abstained={sum(1 for r in rs if r['pick'] == ABSTAIN)}")
    ok = sum(1 for r in rows if r["ok"])
    cov = sum(1 for r in rows if r["pick"] != ABSTAIN)
    print(f"TOTAL top1={ok}/{len(rows)} coverage={cov}/{len(rows)} "
          f"latency_p50={pct(lats, 50):.2f}s latency_p95={pct(lats, 95):.2f}s")


if __name__ == "__main__":
    main()
