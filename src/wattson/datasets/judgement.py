"""A judgement suite: tasks the trained 30-line procedure does not solve by itself (backlog 0008, G0).

0008: every r6 benchmark task is solved by a fixed policy (bisection, or one
``operation="none"`` trial) that the students were trained to imitate, so r6 measures
"can a LoRA learn the tool protocol", not judgement. This builds four families, each
removing one thing the procedure relies on, graded by the existing solver-checked graders
or by a new one that re-solves:

- **intent**: N-1 / base reports phrased as an engineer's question, with no procedure
  named (``pf-report``, the existing report grader);
- **open fit**: load-scale, line-rating and dispatch fits whose prompt states no interval,
  so the bracket comes from the tool's own limits (the existing independent fit checker);
- **worst contingency** (``pf-worst-contingency``, new): of three named lines, whose
  outage gives the highest maximum line loading, and that loading. Several studies and a
  comparison. Ranked by loading, not voltage: on snemSA an outage moves the minimum
  voltage by as little as 1.8e-7 pu, so a voltage ranking would be ill-posed. Candidate
  sets are kept only when the top two differ by at least ``RANKING_MARGIN_PERCENT``;
- **held-out network**: reports on ``snem-1803``, which no training row uses
  (``pf-report``, the existing grader).

Seeds are this suite's own (``JUDGEMENT_SEED``), and the suite is audited against the
training split before use (``sample_benchmark``'s audit; FM-21).

Named stand-in: the "violations versus the base case" family 0008 lists is not built,
because no tool lists violating elements by id (``run_power_flow`` caps the list at 10).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from pathlib import Path
from typing import Any

import pandapower as pp

from wattson.datasets import common, generate
from wattson.inference import powermcp as pm

# This suite's base seed; distinct from every training/benchmark seed in the repo's manifests.
JUDGEMENT_SEED: int = 20261023
PER_FAMILY: int = 10
CASE: str = "snem-sa-2000"
HELD_OUT_CASE: str = "snem-1803"
CANDIDATES_PER_TASK: int = 3
# The top outage must beat the runner-up by 5x the report loading tolerance (0.01 %), so
# the ranking is decided by the physics, not by rounding.
RANKING_MARGIN_PERCENT: float = 5 * 0.01
# Draws per kept task, so duplicates and quarantined rows can be skipped.
DRAWS_PER_TASK: int = 3
# Line candidates tried per worst-contingency task before giving up on a clear ranking.
MAX_CANDIDATE_DRAWS: int = 200
WORST_SUBFAMILY: str = "pf-worst-contingency"
WORST_ANSWER: re.Pattern[str] = re.compile(r"[Ll]ine\s+(\d+)\s*,\s*max(?:imum)? line loading\s*=\s*([\d.]+)\s*%")
LOADING_TOLERANCE_PERCENT: float = 0.01  # wattson.training.unsloth.evaluate.REPORT_LOAD_TOL
INTENT_PROMPTS: tuple[str, ...] = (
    "Line {line} on '{case}' has just tripped. Is the network still inside its voltage limits? "
    "Give convergence, iterations, the minimum and maximum bus voltage (pu), the maximum line loading (%) "
    "and how many buses are outside the voltage band.",
    "We are planning an outage of line {line} on '{case}'. What does the network look like with it out: "
    "iterations to converge, min/max bus voltage in pu, max line loading in percent, and voltage-violation count?",
    "Before switching anything on '{case}', what is the present state of the network? Report convergence, "
    "iterations, min/max bus voltage (pu), max line loading (%) and the number of voltage violations.",
)


def max_loading_after(case: str, line: int) -> float | None:
    """Max line loading (%) with ``line`` out, from a fresh pandapower solve; None if it does not converge."""
    net, _ = pm._load_network_object(case)
    net.line.loc[line, "in_service"] = False
    # snem-1803 has a zero-reactance branch that makes pandapower's default DC start divide by zero, and the project's
    # own solver wrapper retries it from a flat start (powermcp); this does the same, so the grader can score it.
    for init in ("auto", "flat"):
        try:
            pp.runpp(net, numba=True, init=init)
            break
        except FloatingPointError:
            continue
        except pp.LoadflowNotConverged:
            return None
    else:
        return None
    loading = net.res_line.loading_percent.astype(float)
    finite = loading[loading.apply(math.isfinite)]
    return float(finite.max()) if len(finite) else None


def worst_contingency_task(i: int) -> dict[str, Any] | None:
    """One task whose candidate set has a clear worst outage, or None."""
    rng = random.Random(common.derive_example_seed(JUDGEMENT_SEED, "judgement", WORST_SUBFAMILY, CASE, i))
    net, _ = pm._load_network_object(CASE)
    lines = [int(x) for x in net.line.index]
    for _ in range(MAX_CANDIDATE_DRAWS):
        candidates = sorted(rng.sample(lines, CANDIDATES_PER_TASK))
        loadings = {line: max_loading_after(CASE, line) for line in candidates}
        if any(v is None for v in loadings.values()):
            continue
        ranked = sorted(loadings.items(), key=lambda item: item[1], reverse=True)
        if ranked[0][1] - ranked[1][1] >= RANKING_MARGIN_PERCENT:
            worst, value = ranked[0]
            inputs = {"case": CASE, "candidate_lines": candidates}
            prompt = (f"On case '{CASE}', which single outage among lines {candidates[0]}, {candidates[1]} and "
                      f"{candidates[2]} gives the highest maximum line loading? Answer in the form "
                      f"'Line N, max line loading = X%'.")
            return {"example_id": common.compute_example_id("judgement", WORST_SUBFAMILY, CASE, i, inputs),
                    "family": "judgement", "subfamily": WORST_SUBFAMILY, "topology": CASE, "seed": i,
                    "task": {"prompt": prompt, "inputs": inputs},
                    "expected": {"answer": f"Line {worst}, max line loading = {value:.2f}%",
                                 "structured": {"worst_line": worst, "max_line_loading_percent": value,
                                                "loadings": {str(k): v for k, v in loadings.items()}},
                                 "validity": "valid"},
                    "lifecycle": {"status": "verified", "split": "test", "rejection_reason": None}}
    return None


def grade_worst_contingency(task: dict[str, Any], answer: str) -> tuple[bool, dict[str, Any], list[str]]:
    """Re-solve every candidate; pass iff the answer names the worst one and its loading."""
    match = WORST_ANSWER.search(answer)
    if match is None:
        return False, {}, ["No 'Line N, max line loading = X%' in the answer"]
    line, loading = int(match.group(1)), float(match.group(2))
    candidates = task["task"]["inputs"]["candidate_lines"]
    solved = {c: max_loading_after(task["task"]["inputs"]["case"], c) for c in candidates}
    worst = max(solved, key=lambda c: solved[c] if solved[c] is not None else -math.inf)
    evidence = {"verification": "candidates re-solved with pandapower", "loadings": solved, "worst_line": worst}
    if line != worst:
        return False, evidence, [f"Named line {line}; the worst is line {worst}"]
    if abs(loading - solved[worst]) > LOADING_TOLERANCE_PERCENT:
        return False, evidence, [f"Loading {loading}% vs solver {solved[worst]:.4f}%"]
    return True, evidence, []


def studied_every_candidate(task: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """Tool evidence: a converged operation="none" trial for every candidate outage on the task's case."""
    inputs = task["task"]["inputs"]
    from wattson.inference.provenance import observations

    seen = set()
    for observed in observations(events):
        data = observed.get("data") or {}
        study = data.get("study") or {}
        if (study.get("network_id") == inputs["case"] and study.get("operation") == "none"
                and data.get("converged") and study.get("contingency_line") in inputs["candidate_lines"]):
            seen.add(study["contingency_line"])
    return seen == set(inputs["candidate_lines"])


def intent_task(i: int) -> dict[str, Any]:
    """A report whose prompt asks the question an engineer would, without naming the procedure."""
    row = generate._gen_pf_report_example(CASE, i, JUDGEMENT_SEED)
    line = row["task"]["inputs"]["contingency_line"]
    templates = [t for t in INTENT_PROMPTS if ("{line}" in t) == (line is not None)]
    template = templates[random.Random(row["seed"]).randrange(len(templates))]
    row["task"]["prompt"] = template.format(line=line, case=CASE)
    row["task"]["inputs"]["prompt_variant"] = f"intent-{INTENT_PROMPTS.index(template)}"
    row["family"], row["lifecycle"]["split"] = "judgement", "test"
    return row


def open_fit_task(subfamily: str, i: int, elements: list[Any], cache: Any) -> dict[str, Any]:
    """A fit whose prompt states no interval: the bracket must come from the tool's limits."""
    row = generate._gen_fit_example(subfamily, CASE, elements, i, JUDGEMENT_SEED, cache)
    row["task"]["prompt"] = re.sub(r" in \[[^\]]*\]", "", row["task"]["prompt"])
    row["task"]["inputs"]["prompt_variant"] = "open-interval"
    row["family"], row["lifecycle"]["split"] = "judgement", "test"
    return row


def held_out_task(i: int) -> dict[str, Any]:
    """A report on a network no training row uses."""
    row = generate._gen_pf_report_example(HELD_OUT_CASE, i, JUDGEMENT_SEED)
    row["task"]["inputs"]["prompt_variant"] = "held-out-network"
    row["family"], row["lifecycle"]["split"] = "judgement", "test"
    return row


def build(per_family: int = PER_FAMILY) -> list[dict[str, Any]]:
    """The suite: intent, open fit, worst contingency and held-out network, ``per_family`` each."""
    lists = json.loads((Path(__file__).resolve().parent / "element_lists.json").read_text())
    elements = {"pf-load-scale-fit": lists["load_scale_buses"], "pf-line-rating-fit": lists["line_rating_lines"],
                "pf-gen-dispatch-fit": lists["gen_dispatch_pairs"]}
    cache = generate.EndpointCache()
    fits = list(elements)
    def unique(make: Any) -> list[dict[str, Any]]:
        # A network has one base case, so report draws repeat; keep distinct, verified prompts.
        kept, prompts = [], set()
        for i in range(per_family * DRAWS_PER_TASK):
            row = make(i)
            if row is None or row["lifecycle"]["status"] != "verified" or row["task"]["prompt"] in prompts:
                continue
            kept.append(row)
            prompts.add(row["task"]["prompt"])
            if len(kept) == per_family:
                break
        return kept

    return (unique(intent_task)
            + unique(lambda i: open_fit_task(fits[i % len(fits)], i, elements[fits[i % len(fits)]], cache))
            + unique(worst_contingency_task) + unique(held_out_task))


def main() -> int:
    """Write the suite as examples.jsonl plus a manifest of its families."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.out_dir.exists():
        raise ValueError("Refusing to overwrite a frozen suite directory")
    rows = build()
    args.out_dir.mkdir(parents=True)
    with (args.out_dir / "examples.jsonl").open("x") as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    families: dict[str, int] = {}
    for row in rows:
        variant = row["task"]["inputs"].get("prompt_variant") or row["subfamily"]
        families[variant] = families.get(variant, 0) + 1
    examples = args.out_dir / "examples.jsonl"
    # Frozen like every other suite: evaluate_agent.frozen_task_count checks this sha256 and count.
    (args.out_dir / "manifest.json").write_text(json.dumps({
        "suite": "judgement-v1", "seed": JUDGEMENT_SEED, "n_tasks": len(rows), "by_variant": families,
        "examples_sha256": hashlib.sha256(examples.read_bytes()).hexdigest()}, indent=2) + "\n")
    print(json.dumps({"tasks": len(rows), "by_variant": families}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
