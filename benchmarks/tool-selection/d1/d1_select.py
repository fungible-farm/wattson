#!/usr/bin/env python3
"""d1-3B tool selection (#151): one typed `choice` question per case, CPU, offline. Run inside wattson-d1-eval.

Fixed before any run: the question lists the four tools with the first sentence of each description; the model abstains
when its top probability is below ABSTAIN_BELOW = 0.5 (not tuned). Prints per-family counts, top-1 with and without abstain,
latency and peak memory; --out writes one row per case.
"""
import argparse, json, re, resource, statistics, time
from pathlib import Path

ABSTAIN_BELOW = 0.5
HERE = Path(__file__).resolve().parent.parent


def first_sentence(text):
    return re.split(r"(?<=[.])\s", text.strip(), maxsplit=1)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "heldout"], required=True)
    ap.add_argument("--model", default="/models/d1-3B")
    ap.add_argument("--out")
    a = ap.parse_args()
    import torch
    from transformers import AutoModel
    torch.set_num_threads(4)
    model = AutoModel.from_pretrained(a.model, trust_remote_code=True, dtype=torch.float32).eval()
    tools = json.loads((HERE / "tools.json").read_text())
    question = {"type": "choice", "instructions": "Which one tool performs this task?",
                "criteria": {t["name"]: first_sentence(t["description"]) for t in tools}}
    cases = [c for c in json.loads((HERE / "cases.json").read_text()) if c["split"] == a.split]
    model.system_one("warm up", {"tool": question})
    rows = []
    for c in cases:
        t0 = time.perf_counter()
        ans = model.system_one(c["task"], {"tool": question})["answers"]["tool"]
        rows.append({"id": c["id"], "family": c["family"], "correct_tool": c["correct_tool"], "pick": ans["choice"],
                     "confidence": round(ans["confidence"], 4), "latency_s": round(time.perf_counter() - t0, 3)})
    fam = {}
    for r in rows:
        f = fam.setdefault(r["family"], [0, 0, 0, 0])  # n, top1, answered, answered correct
        f[0] += 1
        f[1] += r["pick"] == r["correct_tool"]
        if r["confidence"] >= ABSTAIN_BELOW:
            f[2] += 1
            f[3] += r["pick"] == r["correct_tool"]
    for k in sorted(fam):
        n, t1, an, ac = fam[k]
        print(f"{k} top1={t1}/{n} answered={an} answered_correct={ac} abstained={n - an}")
    n = len(rows); t1 = sum(v[1] for v in fam.values()); an = sum(v[2] for v in fam.values()); ac = sum(v[3] for v in fam.values())
    lat = sorted(r["latency_s"] for r in rows)
    print(f"TOTAL top1_no_abstain={t1}/{n} abstain_below={ABSTAIN_BELOW} coverage={an}/{n} correct_of_answered={ac}/{an}")
    print(f"latency_p50={statistics.median(lat):.3f}s latency_max={lat[-1]:.3f}s peak_rss_mb={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}")
    if a.out:
        Path(a.out).write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
