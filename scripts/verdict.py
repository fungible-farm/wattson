#!/usr/bin/env python3
"""Deterministic, no-inference verdict from a results table and pre-registered thresholds.

    verdict.py RESULTS.json THRESHOLDS.json

Strict by design: every field of every arm and every threshold must be present and correctly typed, there are no
hidden defaults, and the results must come from the pre-registered suites (derived_total and smoke_total in the
thresholds). Anything missing or inconsistent exits 2 with an ERROR line: a verdict is never guessed.
"""

import json
import sys
from fractions import Fraction

ARM_INTS = ("derived_pass", "derived_total", "smoke_pass", "smoke_total", "provenance_failures",
            "guard_refused", "protocol_failures", "tasks")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        fail(f"{path}: {e}")


def need(container, key, kinds, where):
    """Return container[key] if present and of an accepted type (a bool is never accepted as a number)."""
    if not isinstance(container, dict):
        fail(f"{where} must be an object")
    if key not in container:
        fail(f"{where} is missing required field '{key}'")
    value = container[key]
    if isinstance(value, bool) and bool not in kinds:
        fail(f"{where}.{key} must not be a boolean")
    if not isinstance(value, kinds):
        fail(f"{where}.{key} has the wrong type ({type(value).__name__})")
    return value


def read_thresholds(t):
    """Validate and return the thresholds as a flat dict of exact values."""
    need(t, "confirmed_by_owner", (bool,), "thresholds")
    flat = {"derived_total": need(t, "derived_total", (int,), "thresholds"),
            "smoke_total": need(t, "smoke_total", (int,), "thresholds"),
            "noise_tasks": need(t, "noise_tasks", (int,), "thresholds")}
    c = need(t, "contender", (dict,), "thresholds")
    flat["derived_pass_min"] = need(c, "derived_pass_min", (int,), "thresholds.contender")
    flat["smoke_pass_all"] = need(c, "smoke_pass_all", (bool,), "thresholds.contender")
    flat["provenance_failures_max_guard_off"] = need(c, "provenance_failures_max_guard_off", (int,), "thresholds.contender")
    flat["guard_refused_max_guard_on"] = need(c, "guard_refused_max_guard_on", (int,), "thresholds.contender")
    flat["protocol_failures_max"] = need(c, "protocol_failures_max", (int,), "thresholds.contender")
    n = need(t, "not_contender", (dict,), "thresholds")
    flat["derived_pass_max"] = need(n, "derived_pass_max", (int,), "thresholds.not_contender")
    rate = need(n, "protocol_failure_rate_above", (int, float), "thresholds.not_contender")
    flat["rate_above"] = Fraction(str(rate))  # exact: 0.10 means one tenth, not a binary float
    if flat["derived_total"] <= 0 or flat["smoke_total"] <= 0:
        fail("thresholds derived_total and smoke_total must be positive")
    return flat


def read_arm(name, data, t):
    """Validate one arm against the pre-registered suites and return it as a dict of ints plus the guard setting."""
    where = f"arm '{name}'"
    arm = {key: need(data, key, (int,), where) for key in ARM_INTS}
    arm["guard"] = need(data, "guard", (str,), where)
    if arm["guard"] not in ("on", "off"):
        fail(f"{where}.guard must be 'on' or 'off'")
    for key in ARM_INTS:
        if arm[key] < 0:
            fail(f"{where}.{key} must not be negative")
    if arm["tasks"] == 0:
        fail(f"{where} has 0 tasks")
    if arm["derived_total"] != t["derived_total"]:
        fail(f"{where}.derived_total {arm['derived_total']} is not the pre-registered {t['derived_total']}: different suites cannot be compared")
    if arm["smoke_total"] != t["smoke_total"]:
        fail(f"{where}.smoke_total {arm['smoke_total']} is not the pre-registered {t['smoke_total']}: different suites cannot be compared")
    if arm["derived_pass"] > arm["derived_total"] or arm["smoke_pass"] > arm["smoke_total"]:
        fail(f"{where} passes more tasks than exist")
    if arm["protocol_failures"] > arm["tasks"]:
        fail(f"{where}.protocol_failures exceeds tasks")
    return arm


def verdict_for(a, t):
    """Return (verdict, reasons) for one validated arm."""
    not_reasons = []
    if a["derived_pass"] <= t["derived_pass_max"]:
        not_reasons.append(f"derived_pass {a['derived_pass']} <= {t['derived_pass_max']}")
    if Fraction(a["protocol_failures"], a["tasks"]) > t["rate_above"]:
        not_reasons.append(f"protocol_failure_rate {a['protocol_failures']}/{a['tasks']} > {float(t['rate_above'])}")
    if not_reasons:
        return "NOT_CONTENDER", not_reasons

    short = []
    if a["derived_pass"] < t["derived_pass_min"]:
        short.append(f"derived_pass {a['derived_pass']} < {t['derived_pass_min']}")
    if t["smoke_pass_all"] and a["smoke_pass"] != a["smoke_total"]:
        short.append(f"smoke {a['smoke_pass']}/{a['smoke_total']} not all passed")
    if a["guard"] == "off" and a["provenance_failures"] > t["provenance_failures_max_guard_off"]:
        short.append(f"provenance_failures {a['provenance_failures']} > {t['provenance_failures_max_guard_off']} (guard off)")
    if a["guard"] == "on" and a["guard_refused"] > t["guard_refused_max_guard_on"]:
        short.append(f"guard_refused {a['guard_refused']} > {t['guard_refused_max_guard_on']} (guard on)")
    if a["protocol_failures"] > t["protocol_failures_max"]:
        short.append(f"protocol_failures {a['protocol_failures']} > {t['protocol_failures_max']}")
    return ("INCONCLUSIVE", short) if short else ("CONTENDER", [])


def main():
    if len(sys.argv) != 3:
        fail("usage: verdict.py RESULTS.json THRESHOLDS.json")
    raw_thresholds = load_json(sys.argv[2])
    t = read_thresholds(raw_thresholds)
    results = load_json(sys.argv[1])
    raw_arms = need(results, "arms", (dict,), "results")
    if not raw_arms:
        fail("results has no arms")
    arms = {name: read_arm(name, data, t) for name, data in raw_arms.items()}

    print("THRESHOLDS CONFIRMED" if raw_thresholds["confirmed_by_owner"] else "THRESHOLDS UNCONFIRMED (proposed, not binding)")
    for name in sorted(arms):
        verdict, reasons = verdict_for(arms[name], t)
        print(f"{name} {verdict}" + (f" reasons={'; '.join(reasons)}" if reasons else ""))
    names = sorted(arms)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            diff = abs(arms[a]["derived_pass"] - arms[b]["derived_pass"])
            if diff <= t["noise_tasks"]:
                print(f"NOTE {a} vs {b}: difference of {diff} tasks is within noise")


if __name__ == "__main__":
    main()
