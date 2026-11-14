#!/usr/bin/env python3
"""Labelled tool-selection cases (issue #151, step 2 of docs/tool-selection-plan.md).

The family name is the correct tool and each family's templates make that
choice true by construction. Stdlib only, fixed seed, no model, no solver.
"""
import json
import random
import sys
from pathlib import Path

SEED = 20261115
HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "cases.json"
TOOLS_PATH = HERE / "tools.json"
PER_FAMILY = 8
FAMILIES = ["load_network", "run_power_flow", "run_study_trial", "run_python"]
SPLITS = ["dev", "heldout"]
NETS = ["case14", "case30", "case33", "case39", "case57", "case118",
        "case300", "case500", "case200", "case2383", "case9", "case24"]
LINES = ["1", "6", "12", "19", "27", "35"]
GENS = ["1", "2", "5", "8", "11"]
PCT = ["65", "80", "115", "140", "175"]
KA = ["0.35", "0.6", "1.2", "2.5"]
MW = ["25", "60", "140", "300"]
MULT = ["0.7", "1.4", "1.8", "2"]
TOL = ["0.001", "0.01", "0.05"]

DEV = {
    "load_network": [
        "Open {net} so I can look at it later.",
        "Load the {net} case and tell me it worked.",
        "Get {net} into the session; no analysis yet.",
        "Read in {net} so we know what buses it has.",
        "Can you bring up {net} for me?",
        "I only need {net} available for now.",
    ],
    "run_power_flow": [
        "Solve the AC power flow for {net} and report the bus voltages.",
        "Run a power flow on {net} exactly as it is: does it converge?",
        "What are the line flows in {net} at the published base case?",
        "Converge {net} and list the generator outputs.",
        "Do a standard load flow on {net} and give me voltages and angles.",
        "I need the solved operating point of {net} with the data unchanged.",
    ],
    "run_study_trial": [
        "In {net}, raise every load by {pct}% and tell me the worst bus voltage.",
        "Set line {line} of {net} to a rating of {ka} kA and report its loading.",
        "Take line {line} out of service in {net} and report the voltage violations.",
        "Push generator {gen} in {net} up to {mw} MW and show the transformer loading.",
        "Double every load in {net} and tell me whether it still converges.",
        "Cut all loads in {net} to {pct}% and give me the minimum voltage.",
    ],
    "run_python": [
        "Add up {nums} and give me the total.",
        "Is {a} within {tol} of {b}? Work it out and say yes or no.",
        "What is the mean of {nums2}?",
        "Take {a} minus {b}, then divide the result by {c}.",
        "Standard deviation of {nums2}, please.",
        "Which is bigger, {a} or {b}, and by how much?",
    ],
}
HELDOUT = {
    "load_network": [
        "Just open {net}, nothing after that.",
        "Fetch {net} from the case library.",
        "Make {net} the current network.",
        "Give me {net} on its own, untouched.",
    ],
    "run_power_flow": [
        "Power flow on {net}, no edits to the case.",
        "Give me the converged solution for {net} as shipped.",
        "What is the base operating point of {net}? Change nothing.",
        "Run AC load flow on {net} and print the minimum voltage.",
    ],
    "run_study_trial": [
        "Scale all demand in {net} by {mult} and measure the maximum line loading.",
        "Re-rate line {line} in {net} to {ka} kA, then read off that line's loading.",
        "Redispatch unit {gen} of {net} to {mw} MW and report transformer loading.",
        "N-1 check for {net} with line {line} contingent: list the voltage violations.",
    ],
    "run_python": [
        "Sum these figures: {nums}.",
        "Compare {a} with {b} using a tolerance of {tol}: same or different?",
        "Median of {nums2}, to three decimals.",
        "What fraction of {a} is {b}? Give the decimal.",
    ],
}


def number(rng, count):
    return ", ".join(f"{rng.uniform(1, 500):.2f}" for _ in range(count))


def make_slots(rng):
    return {"net": rng.choice(NETS), "line": rng.choice(LINES), "gen": rng.choice(GENS),
            "pct": rng.choice(PCT), "ka": rng.choice(KA), "mw": rng.choice(MW),
            "mult": rng.choice(MULT), "tol": rng.choice(TOL), "a": number(rng, 1),
            "b": number(rng, 1), "c": number(rng, 1), "nums": number(rng, 5),
            "nums2": number(rng, 4)}


def build_cases():
    rng, cases, used = random.Random(SEED), [], set()
    for split in SPLITS:
        templates = DEV if split == "dev" else HELDOUT
        for family in FAMILIES:
            for i in range(PER_FAMILY):
                template = templates[family][i % len(templates[family])]
                task = template.format(**make_slots(rng))
                for _ in range(50):
                    if task not in used:
                        break
                    task = template.format(**make_slots(rng))
                used.add(task)
                cases.append({"id": f"{split}-{family}-{i + 1}", "split": split,
                              "family": family, "task": task, "correct_tool": family})
    return cases


def report(flag, message):
    print(("PASS " if flag else "FAIL ") + message)
    return flag


def check(cases):
    tools = {tool["name"] for tool in json.loads(TOOLS_PATH.read_text())}
    ok = True
    for split in SPLITS:
        for family in FAMILIES:
            n = sum(1 for c in cases if c["split"] == split and c["family"] == family)
            ok = report(n == PER_FAMILY, f"{split} {family}: {n} cases, want {PER_FAMILY}") and ok
    ok = report(len(cases) == PER_FAMILY * len(FAMILIES) * len(SPLITS),
                f"total cases: {len(cases)}, want 64") and ok
    for family in FAMILIES:
        shared = sorted(set(DEV[family]) & set(HELDOUT[family]))
        ok = report(not shared, f"{family}: templates shared across splits: {shared or 'none'}") and ok
    both = {c["task"] for c in cases if c["split"] == "dev"} & {c["task"] for c in cases if c["split"] == "heldout"}
    ok = report(not both, f"task text shared across splits: {len(both)}") and ok
    ok = report(all(c["correct_tool"] in tools for c in cases),
                "every correct_tool is listed in tools.json") and ok
    ok = report(len({c["id"] for c in cases}) == len(cases), "case ids are unique") and ok
    keys = {"id", "split", "family", "task", "correct_tool"}
    return report(all(set(c) == keys for c in cases), "every case has the five required keys") and ok


def main(argv):
    if "--check" in argv:
        cases = json.loads(CASES_PATH.read_text()) if CASES_PATH.exists() else build_cases()
        sys.exit(0 if check(cases) else 1)
    cases = build_cases()
    CASES_PATH.write_text(json.dumps(cases, indent=2) + "\n")
    print(f"wrote {len(cases)} cases to {CASES_PATH.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
