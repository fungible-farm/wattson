"""Questions whose answer is not in any tool reply: it must be computed from several (backlog 0041).

Every suite so far is solved by one search or one comparison, and both v11 students sit at its ceiling, so a comparison of
thinking with non-thinking, or of the Python shell with plain tools, has nothing left to separate. These questions are about
the goal itself: **math should always be written in Python**. Each needs arithmetic over solver results, which a model that
adds or averages in its head gets wrong, and one needs a metric (minimum voltage) the students never saw.

* ``sum_violations``: the total number of voltage violations over three single-line outages;
* ``loading_difference``: how many percentage points higher the maximum line loading is with one line out than another;
* ``mean_loading``: the mean over three outages of the maximum line loading;
* ``min_voltage_outage``: which of three outages leaves the lowest minimum bus voltage (a metric never trained).

Held-out networks only (``snem-1803``, ``ieee39``), no training rows. The expected value comes from the same study the tools
run (``studies.run_study_trial`` with ``operation="none"``). Grading is by value, wording-independent, using the normalised
scorer's number reading; a derived number is not in any tool reply, so the provenance gate does not apply here and
arithmetic is measured separately (``arithmetic_exposure``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from wattson.datasets import common
from wattson.inference.domain_scorecard import answer_numbers, normalise_number
from wattson.inference.studies import run_study_trial

SUBFAMILY: str = "pf-derived"
KINDS: tuple[str, ...] = ("sum_violations", "loading_difference", "mean_loading", "min_voltage_outage")
SEED: int = 20261030
CASES: tuple[str, ...] = ("snem-1803", "ieee39")
PER_CASE_AND_KIND: int = 5
LINES_PER_KIND: dict[str, int] = {"sum_violations": 3, "loading_difference": 2, "mean_loading": 3, "min_voltage_outage": 3}
DRAWS: int = 80
# The metric of a "highest" or "lowest" ranking must be decided by physics, not by rounding.
MIN_VOLTAGE_MARGIN_PU: float = 5e-3
MIN_DIFFERENCE_PP: float = 0.5
# Agreement tolerances: the normalised scorer's (voltages 1e-3 pu, loading 0.1 pp, counts exact), plus the 2-decimal answer form.
TOLERANCE: dict[str, float] = {"sum_violations": 0.0, "loading_difference": 0.1, "mean_loading": 0.1, "min_voltage_outage": 1e-3}
PROMPTS: dict[str, str] = {
    "sum_violations": ("On case '{case}', add up the number of voltage violations over the three single outages of lines {lines}, "
                       "each studied on its own. Give the total as one whole number."),
    "loading_difference": ("On case '{case}', how many percentage points higher is the maximum line loading with line {a} out of "
                           "service than with line {b} out of service? Answer with one number to two decimals (negative if lower)."),
    "mean_loading": ("On case '{case}', what is the mean, over the single outages of lines {lines}, of the maximum line loading "
                     "in percent? Answer with one number to two decimals."),
    "min_voltage_outage": ("On case '{case}', which single outage among lines {lines} leaves the lowest minimum bus voltage? "
                           "Answer in the form 'Line N, min bus voltage = X pu'."),
}
_STUDIES: dict[tuple[str, int], dict[str, Any] | None] = {}


def study(case: str, line: int) -> dict[str, Any] | None:
    """The report numbers with ``line`` out of service, from the tools' own study; None if it does not converge. Cached."""
    key = (case, line)
    if key not in _STUDIES:
        result = run_study_trial(network_id=case, operation="none", contingency_line=line, max_iter=50)
        data = result.data or {}
        ok = result.status in {"success", "constraint_violation"} and data.get("converged")
        _STUDIES[key] = {k: data[k] for k in ("min_bus_voltage_pu", "max_line_loading_percent", "voltage_violations")} if ok else None
    return _STUDIES[key]


def lines_listing(lines: list[int]) -> str:
    return ", ".join(str(x) for x in lines[:-1]) + f" and {lines[-1]}"


def expected(kind: str, case: str, lines: list[int]) -> dict[str, Any] | None:
    """The expected value (and line, for the ranking) of one question, or None if a study failed or the answer is ambiguous."""
    data = [study(case, line) for line in lines]
    if any(d is None for d in data):
        return None
    if kind == "sum_violations":
        return {"value": float(sum(d["voltage_violations"] for d in data))}
    loadings = [d["max_line_loading_percent"] for d in data]
    if kind == "loading_difference":
        diff = loadings[0] - loadings[1]
        return {"value": round(diff, 2)} if abs(diff) >= MIN_DIFFERENCE_PP else None
    if kind == "mean_loading":
        return {"value": round(sum(loadings) / len(loadings), 2)}
    ranked = sorted(zip(lines, (d["min_bus_voltage_pu"] for d in data)), key=lambda item: item[1])
    if ranked[1][1] - ranked[0][1] < MIN_VOLTAGE_MARGIN_PU:
        return None
    return {"value": ranked[0][1], "line": ranked[0][0]}


def task(kind: str, case: str, i: int) -> dict[str, Any] | None:
    """One task of ``kind`` on ``case``, or None if no unambiguous draw was found."""
    from wattson.inference import powermcp as pm

    net, _ = pm._load_network_object(case)
    pool = [int(x) for x in net.line.index]
    rng = random.Random(common.derive_example_seed(SEED, "derived", kind, case, i))
    for _ in range(DRAWS):
        lines = sorted(rng.sample(pool, LINES_PER_KIND[kind]))
        if kind == "loading_difference":
            lines = rng.sample(pool, 2)
        truth = expected(kind, case, lines)
        if truth is None:
            continue
        prompt = PROMPTS[kind].format(case=case, lines=lines_listing(lines) if len(lines) > 2 else "", a=lines[0], b=lines[-1])
        inputs = {"case": case, "kind": kind, "candidate_lines": lines}
        return {"example_id": common.compute_example_id("derived", SUBFAMILY, case, i, inputs), "family": "derived",
                "subfamily": SUBFAMILY, "topology": case, "seed": i,
                "task": {"prompt": prompt, "inputs": inputs},
                "expected": {"answer": str(truth["value"]), "structured": truth, "validity": "valid"},
                "lifecycle": {"status": "verified", "split": "test", "rejection_reason": None}}
    return None


def grade(task_row: dict[str, Any], answer: str) -> tuple[bool, dict[str, Any], list[str]]:
    """Correct iff the answer states the expected value (and, for the ranking, the right line), whatever its wording."""
    inputs, truth = task_row["task"]["inputs"], task_row["expected"]["structured"]
    kind = inputs["kind"]
    if kind == "min_voltage_outage":
        named = re.search(r"[Ll]ine\s+(\d+)", answer)
        if named is None or int(named.group(1)) != truth["line"]:
            return False, {"expected": truth}, [f"The lowest minimum voltage is with line {truth['line']} out"]
    target = normalise_number(repr(truth["value"]))
    allowed = Decimal(repr(TOLERANCE[kind])) + Decimal("1e-9") * max(Decimal(1), abs(target))
    if any(abs(n - target) <= allowed for n in answer_numbers(answer, inputs["case"])):
        return True, {"expected": truth}, []
    return False, {"expected": truth}, [f"No number in the answer is within {TOLERANCE[kind]} of {truth['value']}"]


def build() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in CASES:
        for kind in KINDS:
            kept, seen = [], set()
            for i in range(PER_CASE_AND_KIND * 6):
                row = task(kind, case, i)
                if row is None:
                    continue
                key = tuple(row["task"]["inputs"]["candidate_lines"])
                if key in seen:
                    continue
                seen.add(key)
                kept.append(row)
                if len(kept) == PER_CASE_AND_KIND:
                    break
            if len(kept) < PER_CASE_AND_KIND:
                raise RuntimeError(f"Only {len(kept)} of {PER_CASE_AND_KIND} {kind} tasks for {case}")
            rows += kept
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.out_dir.exists():
        raise ValueError("Refusing to overwrite a frozen suite directory")
    rows = build()
    args.out_dir.mkdir(parents=True)
    examples = args.out_dir / "examples.jsonl"
    with examples.open("x") as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    by = {f"{r['task']['inputs']['case']}:{r['task']['inputs']['kind']}": 0 for r in rows}
    for r in rows:
        by[f"{r['task']['inputs']['case']}:{r['task']['inputs']['kind']}"] += 1
    (args.out_dir / "manifest.json").write_text(json.dumps({
        "suite": "derived-v1", "seed": SEED, "n_tasks": len(rows), "by_case_and_kind": by,
        "examples_sha256": hashlib.sha256(examples.read_bytes()).hexdigest()}, indent=2) + "\n")
    print(json.dumps({"tasks": len(rows), "by_case_and_kind": by}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
