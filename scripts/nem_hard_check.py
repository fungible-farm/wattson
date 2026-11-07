#!/usr/bin/env python3
"""Run every proposed hard question's ground-truth SQL in the wattson-nem container and print the answers (a proposal check, not a benchmark)."""
import json, subprocess, sys
IMAGE = "localhost/wattson-nem:local"
bad = 0
for q in json.load(open("configs/nem-hard-questions.proposed.json")):
    out = subprocess.run(["podman", "run", "--rm", "-i", "--network=none", "--userns=keep-id:uid=1000,gid=1000", "-v", "out/nem:/data:ro,z", IMAGE, "query"],
                         input=q["sql"], capture_output=True, text=True, timeout=120)
    try:
        r = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        r = {"error": out.stderr[-200:]}
    ok = "error" not in r and r["rows"]
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'} {q['id']} {r.get('rows', r.get('error'))}")
print(f"{'PASS' if not bad else 'FAIL'} nem-hard-check: {10 - bad}/10 ground-truth queries returned rows")
sys.exit(1 if bad else 0)
