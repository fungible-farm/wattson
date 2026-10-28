"""Multi-candidate planning tasks: which of three outages is worst? Training rows and a held-out suite (backlog 0041).

The judgement suite asks this on one network in one wording and the Python-shell students scored 1 of 10 on it (the
plain control 9 of 10). Two causes were found: the v1 shell hid the numbers a comparison reads (fixed in the v2 shell),
and the Python training data had no multi-candidate task at all. These rows supply it, on networks no evaluation uses,
in two wordings; the held-out suite then asks on two other networks, in the original wording and in a third wording
that is never trained, so a pass cannot be template recall.

The grader is the judgement suite's (``grade_worst_contingency``): it re-solves every candidate with pandapower and
requires the right line and its loading to the report tolerance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from wattson.datasets import common
from wattson.datasets.judgement import (CANDIDATES_PER_TASK, MAX_CANDIDATE_DRAWS, RANKING_MARGIN_PERCENT,
                                           WORST_SUBFAMILY, max_loading_after)
from wattson.inference import powermcp as pm

# Distinct from every other seed in the repo's manifests (judgement 20261023, procedural 20261024).
PLANNING_SEED: int = 20261029
# Networks no evaluation suite uses (the held-out fits are snem-1803 and ieee39; judgement is snem-sa-2000, whose judgement triples are excluded).
# ieee118 was tried and rejected: 171 of its 173 outages leave the maximum line loading at 4.48%, so no outage is "worst".
# pegase89 has 28 lines whose outage changes the maximum (132 of 160 give 96.41%), so candidates are drawn from those.
# snem-sa-2000 is the judgement suite's network: its training triples exclude every judgement candidate set, so this is
# a same-network, unseen-triples setting (the held-out planning suite is the cross-network one).
TRAIN_CASES: tuple[str, ...] = ("pegase89", "snem-sa-2000")
JUDGEMENT_SUITE: Path = Path("training/unsloth/out/judgement-20261024-v2/examples.jsonl")
HELD_OUT_CASES: tuple[str, ...] = ("snem-1803", "ieee39")
TRAIN_PER_CASE: int = 40
HELD_OUT_PER_CASE_AND_WORDING: int = 10
ANSWER_FORM: str = "'Line N, max line loading = X%'"
# Wordings 0 and 1 are trained; 2 is only in the held-out suite. All ask for the judgement suite's answer form.
WORDINGS: tuple[str, ...] = (
    "On case '{case}', which single outage among lines {a}, {b} and {c} gives the highest maximum line loading? "
    "Answer in the form " + ANSWER_FORM + ".",
    "Of lines {a}, {b} and {c} on case '{case}', which one, if it tripped, would leave the most heavily loaded line "
    "at the highest loading? Answer in the form " + ANSWER_FORM + ".",
    "For case '{case}', an operator is weighing an outage of line {a}, {b} or {c}. Which of the three is the most "
    "severe in terms of peak line loading? Reply exactly as " + ANSWER_FORM + ".",
)
TRAINED_WORDINGS: tuple[int, ...] = (0, 1)


def task(case: str, i: int, wording: int, split: str) -> dict[str, Any] | None:
    """One task with a clear worst outage among three lines, or None if no clear ranking was found."""
    rng = random.Random(common.derive_example_seed(PLANNING_SEED, "planning", WORST_SUBFAMILY, f"{case}:w{wording}", i))
    net, _ = pm._load_network_object(case)
    lines = [int(x) for x in net.line.index]
    for _ in range(MAX_CANDIDATE_DRAWS):
        candidates = sorted(rng.sample(lines, CANDIDATES_PER_TASK))
        loadings = {line: max_loading_after(case, line) for line in candidates}
        if any(v is None for v in loadings.values()):
            continue
        ranked = sorted(loadings.items(), key=lambda item: item[1], reverse=True)
        if ranked[0][1] - ranked[1][1] >= RANKING_MARGIN_PERCENT:
            worst, value = ranked[0]
            inputs = {"case": case, "candidate_lines": candidates, "wording": wording}
            prompt = WORDINGS[wording].format(case=case, a=candidates[0], b=candidates[1], c=candidates[2])
            return {"example_id": common.compute_example_id("planning", WORST_SUBFAMILY, case, i, inputs),
                    "family": "planning", "subfamily": WORST_SUBFAMILY, "topology": case, "seed": i,
                    "task": {"prompt": prompt, "inputs": inputs},
                    "expected": {"answer": f"Line {worst}, max line loading = {value:.2f}%",
                                 "structured": {"worst_line": worst, "max_line_loading_percent": value,
                                                "loadings": {str(k): v for k, v in loadings.items()}},
                                 "validity": "valid"},
                    "lifecycle": {"status": "verified", "split": split, "rejection_reason": None}}
    return None


_ALL_LOADINGS: dict[str, dict[int, float]] = {}


def all_loadings(case: str) -> dict[int, float]:
    """Max line loading (%) after each single line outage that converges, solved once per network and kept."""
    if case not in _ALL_LOADINGS:
        net, _ = pm._load_network_object(case)
        solved = {int(line): max_loading_after(case, int(line)) for line in net.line.index}
        _ALL_LOADINGS[case] = {line: value for line, value in solved.items() if value is not None}
    return _ALL_LOADINGS[case]


def excluded_triples() -> set[tuple[int, ...]]:
    """The judgement suite's candidate sets, which no training row may repeat."""
    if not JUDGEMENT_SUITE.exists():
        return set()
    rows = [json.loads(line) for line in JUDGEMENT_SUITE.read_text().splitlines() if line.strip()]
    return {tuple(r["task"]["inputs"]["candidate_lines"]) for r in rows if r["subfamily"] == WORST_SUBFAMILY}


def impactful_task(case: str, i: int, wording: int, split: str, avoid: set[tuple[int, ...]]) -> dict[str, Any] | None:
    """A task whose three candidates come from lines whose outage changes the network's maximum loading.

    Lines at the network's modal loading (the maximum is set by something no candidate affects) are not candidates, so the
    ranking is decided by the candidates; the margin is the judgement suite's.
    """
    loadings = all_loadings(case)
    modal = max(set(round(v, 2) for v in loadings.values()), key=lambda v: sum(round(x, 2) == v for x in loadings.values()))
    pool = sorted(line for line, v in loadings.items() if abs(v - modal) > RANKING_MARGIN_PERCENT)
    if len(pool) < CANDIDATES_PER_TASK:
        raise RuntimeError(f"{case} has only {len(pool)} lines whose outage changes the maximum loading")
    rng = random.Random(common.derive_example_seed(PLANNING_SEED, "planning", WORST_SUBFAMILY, f"{case}:w{wording}:impactful", i))
    for _ in range(MAX_CANDIDATE_DRAWS):
        candidates = sorted(rng.sample(pool, CANDIDATES_PER_TASK))
        if tuple(candidates) in avoid:
            continue
        ranked = sorted(((line, loadings[line]) for line in candidates), key=lambda item: item[1], reverse=True)
        if ranked[0][1] - ranked[1][1] >= RANKING_MARGIN_PERCENT:
            worst, value = ranked[0]
            inputs = {"case": case, "candidate_lines": candidates, "wording": wording}
            prompt = WORDINGS[wording].format(case=case, a=candidates[0], b=candidates[1], c=candidates[2])
            return {"example_id": common.compute_example_id("planning", WORST_SUBFAMILY, case, i, inputs),
                    "family": "planning", "subfamily": WORST_SUBFAMILY, "topology": case, "seed": i,
                    "task": {"prompt": prompt, "inputs": inputs},
                    "expected": {"answer": f"Line {worst}, max line loading = {value:.2f}%",
                                 "structured": {"worst_line": worst, "max_line_loading_percent": value,
                                                "loadings": {str(k): loadings[k] for k in candidates}},
                                 "validity": "valid"},
                    "lifecycle": {"status": "verified", "split": split, "rejection_reason": None}}
    return None


def collect_training(per: int) -> list[dict[str, Any]]:
    """``per`` distinct impactful tasks for each (training case, trained wording)."""
    avoid = excluded_triples()
    rows: list[dict[str, Any]] = []
    for case in TRAIN_CASES:
        for wording in TRAINED_WORDINGS:
            kept: list[dict[str, Any]] = []
            seen: set[tuple[int, ...]] = set()
            for i in range(per * 6):
                row = impactful_task(case, i, wording, "train", avoid)
                triple = None if row is None else tuple(row["task"]["inputs"]["candidate_lines"])
                if row is None or triple in seen:
                    continue
                kept.append(row)
                seen.add(triple)
                avoid = avoid | {triple}
                if len(kept) == per:
                    break
            if len(kept) < per:
                raise RuntimeError(f"Only {len(kept)} of {per} planning tasks for {case}, wording {wording}")
            rows += kept
    return rows


def collect(cases: tuple[str, ...], wordings: tuple[int, ...], per: int, split: str) -> list[dict[str, Any]]:
    """``per`` distinct tasks for each (case, wording), in a fixed order."""
    rows: list[dict[str, Any]] = []
    for case in cases:
        for wording in wordings:
            kept: list[dict[str, Any]] = []
            seen: set[tuple[int, ...]] = set()
            for i in range(per * 4):
                row = task(case, i, wording, split)
                if row is None or tuple(row["task"]["inputs"]["candidate_lines"]) in seen:
                    continue
                kept.append(row)
                seen.add(tuple(row["task"]["inputs"]["candidate_lines"]))
                if len(kept) == per:
                    break
            if len(kept) < per:
                raise RuntimeError(f"Only {len(kept)} of {per} planning tasks for {case}, wording {wording}")
            rows += kept
    return rows


def write(rows: list[dict[str, Any]], out_dir: Path, suite: str) -> None:
    """Freeze ``rows`` as examples.jsonl plus the manifest ``evaluate_agent.frozen_task_count`` checks."""
    if out_dir.exists():
        raise ValueError("Refusing to overwrite a frozen directory")
    out_dir.mkdir(parents=True)
    examples = out_dir / "examples.jsonl"
    with examples.open("x") as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    by = {f"{r['task']['inputs']['case']}:wording{r['task']['inputs']['wording']}": 0 for r in rows}
    for r in rows:
        by[f"{r['task']['inputs']['case']}:wording{r['task']['inputs']['wording']}"] += 1
    (out_dir / "manifest.json").write_text(json.dumps({
        "suite": suite, "seed": PLANNING_SEED, "n_tasks": len(rows), "by_case_and_wording": by,
        "examples_sha256": hashlib.sha256(examples.read_bytes()).hexdigest()}, indent=2) + "\n")
    print(json.dumps({"suite": suite, "tasks": len(rows), "by_case_and_wording": by}))


def main() -> int:
    """``train`` writes the training rows, ``heldout`` the frozen evaluation suite."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=["train", "heldout"])
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.kind == "train":
        write(collect_training(TRAIN_PER_CASE // len(TRAINED_WORDINGS)), args.out_dir, "planning-train-v1")
    else:
        write(collect(HELD_OUT_CASES, (0, 2), HELD_OUT_PER_CASE_AND_WORDING, "test"), args.out_dir, "planning-heldout-v1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
