"""Report M09 solver-verified correctness alongside the stricter agent gate.

This post-processing never invokes a model or changes its attempt. It checks
the complete saved journals, re-solves all numerical candidates, and tolerates
labelled plain-text, Markdown and JSON report formatting. Original agent scores
and raw responses are preserved. All models use this same frozen grader.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import ROUND_HALF_EVEN, Decimal
import math
import re
from pathlib import Path
from typing import Any, TypedDict

from wattson.datasets.audit import load_rows
from wattson.inference.provenance import check as provenance_check
from wattson.inference.provenance import check_fields
from wattson.inference.compare_agents import compare, verified_run
from wattson.training.unsloth.evaluate import (DOMAIN_IMPROVEMENT_THRESHOLD, FIT_SUBFAMILIES,
                                       REPORT_LOAD_TOL, REPORT_VOLT_TOL, score_answer, uncertainty)

# Version the metric before the first complete base/student comparison. M09
# specifies numerical correctness and tool behaviour as separate scorecard rows.
# v2 (r6 on): a numerically correct answer also needs every number traced to the
# attempt's tool responses or prompt (provenance.py); v1 checked values only, so a
# guessed or memorised value could pass. r4's v1 comparison files are unchanged.
SCORER_VERSION: str = "solver-verified-traced-v2"
# v3 (backlog 0010), reported alongside v2, which stays the headline so r6 comparisons
# are unchanged: also requires the original tool evidence, and traces each number to
# its own solver field or a tried scale (provenance.check_fields).
SCORER_VERSION_V3: str = "solver-verified-field-traced-v3"

# v4 (2026-10-30), reported beside v2: a report is judged on its VALUES, not on the phrases that label them.
# v2 reads a value only after a fixed label ("converged in N iterations", "voltage violations = N"), so an answer
# that says "converges in 5 iterations" or "voltage-violation count = 39" scores as missing those fields however
# correct they are, and the judgement prompts do not dictate the wording. The tool-printed sentence of the v2 shell
# gave one arm that wording for free. v4 finds every number in the answer, normalises each to a standard 4-decimal
# form with exact decimal arithmetic, and requires each expected solver value to be matched by a distinct number
# within the tolerances in NORMALISED_TOLERANCES; a tiny relative slack absorbs float representation error.
SCORER_VERSION_NORMALISED: str = "solver-verified-normalised-v4"
NORMAL_PLACES: int = 4
FLOAT_SLACK: float = 1e-9
# Tolerances of the normalised scorer. User decision 2026-10-30: "from a modelling perspective 0.8992 is the same as
# 0.9000, especially fine for comparison", so voltages agree within 1e-3 pu (v2 used 1e-4). The loading tolerance of 0.1
# percentage points is my extension of that example to a quantity of order 100 (the same ~0.1% relative); change it here.
# Counts stay exact. The earlier scorer's tolerances (REPORT_TOLERANCES) are unchanged.
NORMALISED_TOLERANCES: dict[str, float] = {"iterations": 0.0, "min_v": 1e-3, "max_v": 1e-3, "max_load": 0.1, "violations": 0.0}
# The case name and "line N" are identifiers, not report values: remove them before reading the answer's numbers.
IDENTIFIER_PHRASES: re.Pattern[str] = re.compile(r"\bline\s+\d+|\blines\s+\d+(?:\s*(?:,|and|&)\s*\d+)*", re.IGNORECASE)
NUMBER_TOKEN: re.Pattern[str] = re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w])")


def normalise_number(token: str) -> Decimal:
    """A written number as exact decimal text rounded half-even to ``NORMAL_PLACES`` places: ``0.90``, ``0.9000`` and
    ``0.9000000000001`` normalise alike, so representation error cannot decide a result."""
    return Decimal(token.replace(",", "")).quantize(Decimal(1).scaleb(-NORMAL_PLACES), rounding=ROUND_HALF_EVEN)


def answer_numbers(answer: str, case: str = "") -> list[Decimal]:
    """Every number the answer writes, normalised, without the case name and ``line N`` identifiers."""
    text = answer.replace("**", " ").replace("`", " ")
    if case:
        text = text.replace(case, " ")
    text = IDENTIFIER_PHRASES.sub(" ", text)
    out = []
    for match in NUMBER_TOKEN.finditer(text):
        try:
            out.append(normalise_number(match.group(0)))
        except ArithmeticError:
            continue
    return out


def report_matches_normalised(answer: str, expected: ReportFields, case: str = "") -> bool:
    """Wording-independent value check of a report against the solver's fields (see ``SCORER_VERSION_NORMALISED``).

    A converged report needs no particular word, only no claim of failure; each of the five expected values must be
    matched by a distinct written number within its physical tolerance (iterations and violations exactly).
    """
    claimed = parse_report(answer).get("outcome")
    if expected["outcome"] != "converged":
        return claimed == expected["outcome"]
    if claimed in {"execution_failure", "non_convergence"}:
        return False
    remaining = answer_numbers(answer, case)
    for key, tolerance in NORMALISED_TOLERANCES.items():
        target = normalise_number(repr(expected[key]))
        allowed = Decimal(repr(tolerance)) + Decimal(repr(FLOAT_SLACK)) * max(Decimal(1), abs(target))
        near = [n for n in remaining if abs(n - target) <= allowed]
        if not near:
            return False
        remaining.remove(min(near, key=lambda n: abs(n - target)))
    return True

NUMBER: str = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
# Use the existing documented tolerances, including exact discrete counts.
REPORT_TOLERANCES: dict[str, float] = {
    "iterations": 0.0, "min_v": REPORT_VOLT_TOL, "max_v": REPORT_VOLT_TOL,
    "max_load": REPORT_LOAD_TOL, "violations": 0.0,
}
JSON_KEYS: dict[str, str] = {
    "iterations": "iterations", "min_bus_voltage_pu": "min_v",
    "max_bus_voltage_pu": "max_v", "max_line_loading_percent": "max_load",
    "voltage_violations": "violations",
}
REPORT_PATTERNS: dict[str, tuple[str, ...]] = {
    "iterations": (r"converged\s+in\s+" + NUMBER + r"\s+iterations", r"\biterations\s*[:=]\s*" + NUMBER),
    "min_v": (
        r"\bmin(?:imum)?\s+(?:bus\s+)?voltage(?:\s*\(pu\))?\s*(?:[:=]|is)\s*" + NUMBER,
        r"bus\s+voltage\s*(?:\(pu\))?\s*:\s*(?:-\s*)?minimum\s*:\s*" + NUMBER,
    ),
    "max_v": (
        r"\bmax(?:imum)?\s+(?:bus\s+)?voltage(?:\s*\(pu\))?\s*(?:[:=]|is)\s*" + NUMBER,
        r"bus\s+voltage\s*(?:\(pu\))?\s*:\s*(?:-\s*)?minimum[^\n]*\n\s*(?:-\s*)?maximum\s*:\s*" + NUMBER,
    ),
    "max_load": (
        r"\bmax(?:imum)?\s+line\s+loading(?:\s*\([^)]*\))?\s*(?:[:=]|is)\s*" + NUMBER,
        r"line\s+loading\s*:\s*(?:-\s*)?max(?:imum)?\s*:\s*" + NUMBER,
    ),
    "violations": (r"\bvoltage\s+violations\s*[:=]\s*" + NUMBER,),
}


class ReportFields(TypedDict, total=False):
    """Explicit numerical report claims, with unknown fields absent."""

    outcome: str
    iterations: float
    min_v: float
    max_v: float
    max_load: float
    violations: float


def parse_report(answer: str) -> ReportFields:
    """Extract labelled fields without requiring the teacher's sentence style.

    Explicit non-convergence and execution failure take precedence over an
    incidental 'converged' later in the text. Unknown or unlabelled values are
    not guessed. When a report repeats its values, use its first labelled value
    consistently, matching the original evaluator's extraction convention.
    """
    text = answer.replace("**", "").replace("`", "")
    fenced = re.fullmatch(r"\s*```(?:json)?\s*(\{.*\})\s*```\s*", answer, re.DOTALL | re.IGNORECASE)
    try:
        document = json.loads(fenced.group(1) if fenced else answer)
    except ValueError:
        document = None
    if isinstance(document, dict):
        data = document.get("data", document)
        if not isinstance(data, dict):
            return {}
        fields: ReportFields = {}
        if data.get("status") == "execution_failure" or document.get("status") == "execution_failure":
            fields["outcome"] = "execution_failure"
        elif data.get("converged") is False or data.get("status") == "non_convergence" or document.get("status") == "non_convergence":
            fields["outcome"] = "non_convergence"
        elif data.get("converged") is True or data.get("status") == "converged":
            fields["outcome"] = "converged"
        for source, destination in JSON_KEYS.items():
            value = data.get(source)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                fields[destination] = float(value)
        return fields
    result: ReportFields = {}
    if re.search(r"failed to execute|execution[_ -]failure", text, re.IGNORECASE):
        result["outcome"] = "execution_failure"
    elif re.search(r"(?:did\s+not|not)\s+converge|non[-_ ]convergen|converged\s*:\s*false|convergence(?:\s+status)?\s*:\s*(?:false|no|failed)", text, re.IGNORECASE):
        result["outcome"] = "non_convergence"
    elif re.search(r"\bconverged\b|convergence\s*:\s*true", text, re.IGNORECASE):
        result["outcome"] = "converged"
    for key, patterns in REPORT_PATTERNS.items():
        matches = [m for pattern in patterns for m in re.finditer(pattern, text, re.IGNORECASE)]
        if matches:
            value = float(min(matches, key=lambda m: m.start()).group(1))
            if math.isfinite(value):
                result[key] = value
    return result


def report_matches(answer: str, expected: ReportFields) -> bool:
    """Apply the fixed physical tolerances and require every requested field."""
    actual = parse_report(answer)
    if actual.get("outcome") != expected["outcome"]:
        return False
    if expected["outcome"] != "converged":
        return True
    return all(key in actual and abs(actual[key] - expected[key]) <= tolerance
               for key, tolerance in REPORT_TOLERANCES.items())


def solve_report(task: dict[str, Any]) -> tuple[ReportFields, dict[str, Any]]:
    """Re-derive a report with real AC physics for exactly the requested outage.

    This independent grader deliberately calls the canonical loader and solver
    directly; it does not stand in for a model tool call or establish evidence
    use by the model. A fresh network prevents one outage leaking into another.
    """
    import pandapower as pp
    from wattson.datasets.generate import _solve
    from wattson.inference.powermcp import _load_network_object

    inputs = task["task"]["inputs"]
    net, case_hash = _load_network_object(inputs["case"])
    outage = inputs.get("contingency_line")
    if outage is not None:
        if outage not in net.line.index:
            raise ValueError(f"Unknown grader contingency line: {outage}")
        net.line.loc[outage, "in_service"] = False
    evidence = {"case_id": inputs["case"], "case_sha256": case_hash,
                "contingency_line": outage, "solver": "pandapower", "solver_version": pp.__version__}
    try:
        iterations, low, high, violations = _solve(net)
    except pp.LoadflowNotConverged:
        return {"outcome": "non_convergence"}, evidence
    fields: ReportFields = {"outcome": "converged", "iterations": iterations,
        "min_v": low, "max_v": high, "max_load": float(net.res_line.loading_percent.max()),
        "violations": violations}
    if not all(math.isfinite(fields[k]) for k in REPORT_TOLERANCES):
        raise ValueError("Independent report solve returned nonfinite results")
    return fields, evidence


def score_numerical(task: dict[str, Any], answer: str) -> tuple[bool, dict[str, Any], list[str]]:
    """Check fits at the final candidate and reports against a fresh AC solve."""
    if task["subfamily"] in FIT_SUBFAMILIES:
        return score_answer(task, answer)
    if task["subfamily"] == "pf-worst-contingency":
        from wattson.datasets.judgement import grade_worst_contingency

        return grade_worst_contingency(task, answer)
    if task["subfamily"] == "pf-unsolvable":
        from wattson.datasets.unsolvable import grade as grade_unsolvable

        return grade_unsolvable(task, answer)
    if task["subfamily"] != "pf-report":
        raise ValueError(f"Unsupported domain family: {task['subfamily']}")
    expected, evidence = solve_report(task)
    passed = report_matches(answer, expected)
    return passed, {**evidence, "solver_fields": expected, "parsed_fields": parse_report(answer)}, (
        [] if passed else ["Missing, ambiguous or incorrect report fields"])


def rescore_run(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Require the complete original run, preserving every original score and hash."""
    summary, original = verified_run(directory)
    tasks = load_rows(Path(next(iter(summary["inputs"]))))
    results = []
    for task, row in zip(tasks, original, strict=True):
        events = load_rows(directory / f"{row['example_id']}.jsonl")
        terminal = events[-1]
        completed = terminal["kind"] == "terminal" and terminal["payload"]["state"] == "answered"
        responses = [e["payload"] for e in events if e["kind"] == "model_response"]
        answer = (responses[-1]["choices"][0]["message"].get("content") or "") if responses else ""
        if answer != row["answer"]:
            raise ValueError("Scored answer disagrees with the actual model response")
        correct, evidence, diagnostics = score_numerical(task, answer)
        # v4: the same score except a report is read by value, not by label (SCORER_VERSION_NORMALISED).
        normalised = correct
        if task["subfamily"] == "pf-report":
            expected, _evidence = solve_report(task)
            normalised = report_matches_normalised(answer, expected, task["task"]["inputs"].get("case", ""))
        provenance = provenance_check(answer, events, task["task"].get("prompt", ""))
        fields = check_fields(task, answer, events)
        results.append({"example_id": row["example_id"], "subfamily": task["subfamily"],
            "pass": completed and correct and provenance["traced"], "completed_attempt": completed,
            "pass_norm": completed and normalised and provenance["traced"],
            "untraced_numbers": provenance["untraced"],
            "pass_v3": completed and correct and row["tool_evidence_pass"] and fields["traced"],
            "untraced_v3": fields["reasons"],
            "strict_original_agent_pass": row["passed"], "tool_evidence_pass": row["tool_evidence_pass"],
            "answer": answer, "journal_sha256": row["journal_sha256"],
            "numerical_verification": evidence, "diagnostics": diagnostics})
    return summary, results


def v3_comparison(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> dict[str, Any]:
    """The v3 score of both runs, their delta and its paired interval (same method as v2)."""
    as_v3 = lambda rows: [{"example_id": r["example_id"], "pass": r["pass_v3"]} for r in rows]
    base, student = as_v3(first), as_v3(second)
    base_rate, student_rate = sum(r["pass"] for r in base) / len(base), sum(r["pass"] for r in student) / len(student)
    return {"scorer_version": SCORER_VERSION_V3, "base_n_pass": sum(r["pass"] for r in base),
            "student_n_pass": sum(r["pass"] for r in student), "base_pass_at_1": base_rate,
            "student_pass_at_1": student_rate, "delta_percentage_points": (student_rate - base_rate) * 100,
            "uncertainty": uncertainty(base, student)}


def compare_domain(base_dir: Path, student_dir: Path,
                   allowed_differences: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Report M09's numerical metric and retain the original evidence-gated metric."""
    # Also verifies equal generation conditions (see compare for the one exception).
    strict = compare(base_dir, student_dir, allowed_differences)
    base, first = rescore_run(base_dir)
    student, second = rescore_run(student_dir)
    first_rate = sum(r["pass"] for r in first) / len(first)
    second_rate = sum(r["pass"] for r in second) / len(second)
    delta = second_rate - first_rate
    return {"scorer_version": SCORER_VERSION, "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "metric": "solver-verified numerical pass@1", "n_tasks": len(first),
        "base_model": base["model"], "student_model": student["model"],
        "base_n_pass": sum(r["pass"] for r in first), "student_n_pass": sum(r["pass"] for r in second),
        "base_pass_at_1": first_rate, "student_pass_at_1": second_rate,
        "delta_pass_at_1": delta, "delta_percentage_points": delta * 100,
        "domain_threshold_met": delta >= DOMAIN_IMPROVEMENT_THRESHOLD,
        "uncertainty": uncertainty(first, second), "strict_agent_comparison": strict,
        "by_subfamily": {family: {
            "n_tasks": sum(r["subfamily"] == family for r in first),
            "base_n_pass": sum(r["pass"] for r in first if r["subfamily"] == family),
            "student_n_pass": sum(r["pass"] for r in second if r["subfamily"] == family),
        } for family in sorted({r["subfamily"] for r in first})},
        "v3": v3_comparison(first, second),
        "report_tolerances": REPORT_TOLERANCES, "base_results": first, "student_results": second,
        "g5_met": False, "g5_pending": "General Python regression and critical fabrication suites remain separate gates"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--student-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = compare_domain(args.base_dir, args.student_dir)
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: v for k, v in report.items() if not k.endswith("results")}, indent=2))
