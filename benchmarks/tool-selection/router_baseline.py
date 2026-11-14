#!/usr/bin/env python3
"""Deterministic keyword router for the tool-selection benchmark (no model).

Rules were written from the dev split only; they use general word/regex patterns.
"""
import argparse
import json
import re
from pathlib import Path

CASES = Path(__file__).resolve().parent / "cases.json"
NETWORK_ID = re.compile(r"\bcase[\s_-]*\d+\b", re.I)
DECIMAL = re.compile(r"\b\d+(?:\.\d+)?\b")

# Pure arithmetic asks: numbers to work on, no network involved.
ARITH = re.compile(
    r"\badd up\b|\bsum\b|\btotal\b|\bmean\b|\baverage\b|\bmedian\b|standard deviation"
    r"|\bdeviation\b|\bminus\b|\bplus\b|\btimes\b|\bdivide[ds]?\b|multiplied|difference"
    r"|\bbigger\b|\bsmaller\b|\bgreater\b|\bwithin\b|\bcompute\b|\bcalculate\b|\bround\b"
    r"|\bpercent of\b|\bsquare root\b|\bpercentage\b", re.I)

# A study changes something in the network (load/rating/dispatch/contingency).
STUDY = re.compile(
    r"every load|all loads|\braise[ds]?\b|\blower[ds]?\b|\braised\b|\bcut\b|\bdouble\b"
    r"|\bhalve\b|\bincrease[sd]?\b|\bdecrease[sd]?\b|\btrip[s]?\b|out of service"
    r"|contingency|outage|take(?:n)? line|set line|push generator|\brating of\b"
    r"|\bscal(?:e|ed|ing)\b|\bbump\w*\b|by \d+(?:\.\d+)?%|\d+(?:\.\d+)?% load"
    r"|\bworst bus\b|\bviolations?\b|\bloading\b|modified|modification|\bnew rating\b",
    re.I)

# Base-case analysis of the intact network.
POWER_FLOW = re.compile(
    r"power[\s_-]*flow|load[\s_-]*flow|\bconverg\w*\b|\bsolv\w*\b|operating point"
    r"|base[\s_-]*case|\bpublished\b|\bflows?\b|\bangles?\b|bus voltage|\bvoltage\b"
    r"|\bunchanged\b|as it is|generator output|\bintact\b|\bunmodified\b", re.I)

# Just making the case available.
LOAD = re.compile(
    r"\bopen\b|\bload\b|\bloads\b|\bget\b|\bgets\b|\bgather\b|bring up|read in|\bimport\b"
    r"|\bavailab\w*\b|\bsession\b|\bprepar\w*\b|\bstage\b|\bfetch\b|\bupdat\w*\b"
    r"|\bcaseload\b|\battach\b", re.I)


def route(task: str):
    """Return the tool name for a task, or None to abstain."""
    t = task or ""
    nums = len(DECIMAL.findall(t))
    network = bool(NETWORK_ID.search(t))
    if not network and nums >= 2:
        return "run_python"
    if STUDY.search(t):
        return "run_study_trial"
    if POWER_FLOW.search(t):
        return "run_power_flow"
    if not network and (ARITH.search(t) or nums >= 1):
        return "run_python"
    return "load_network" if LOAD.search(t) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "heldout"], required=True)
    args = ap.parse_args()
    cases = [c for c in json.loads(CASES.read_text()) if c.get("split") == args.split]
    stats, total, correct, covered = {}, 0, 0, 0
    for c in cases:
        fam, gold = c.get("family", "unknown"), c.get("correct_tool")
        pred = route(c.get("task", ""))
        st = stats.setdefault(fam, [0, 0, 0])
        st[1] += 1
        total += 1
        if pred is None:
            st[2] += 1
        else:
            covered += 1
            if pred == gold:
                st[0] += 1
                correct += 1
    for fam in sorted(stats):
        hit, n, abst = stats[fam]
        print(f"{fam} {hit}/{n} abstained={abst}")
    print(f"TOTAL top1={correct}/{total} coverage={covered}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
