"""Refuse answers whose numbers did not come from the solver.

User rule, 2026-10-22: an agent must never guess or make up a number. The
first r6 Qwen student answered report tasks with invented voltages and no tool
call, and the solver-verified metric (``domain_scorecard`` v1) would have
counted a lucky or memorised value as a pass, because it checks only the final
numbers. This makes provenance part of every score:

a number in the final answer is *traced* when a number from one of this
attempt's tool responses, or from the task prompt (case names, element ids,
targets), equals it at the precision the answer printed (half a unit in its
last printed digit). An answer with any untraced number is ``fabricated``,
whatever its accuracy. Arithmetic the model did itself (a residual, a
difference) is untraced too: only software outputs count.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

# Numbers as an answer prints them: unsigned, optional decimals or exponent.
# The sign is ignored on both sides ("N-1" must not read as minus one).
ANSWER_NUMBER: re.Pattern[str] = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?![\w])")
SOURCE_NUMBER: re.Pattern[str] = re.compile(r"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
# Float noise allowance on top of half a unit in the last printed digit.
RELATIVE_SLACK: float = 1e-9


class Provenance(TypedDict):
    """Which of an answer's numbers trace to the solver or prompt."""

    numbers: list[str]
    untraced: list[str]
    traced: bool


def printed_tolerance(text: str) -> float:
    """Half a unit in the last digit ``text`` prints (``1.0941`` -> 5e-5, ``39`` -> 0.5)."""
    mantissa, _, exponent = text.lower().partition("e")
    decimals = len(mantissa.partition(".")[2])
    return 0.5 * 10.0 ** (-decimals + (int(exponent) if exponent else 0))


def json_numbers(value: Any) -> list[float]:
    """Numeric leaves of a decoded tool payload, plus numbers inside non-hash strings.

    Evidence hashes (``sha256:...``) are skipped: their hex digit runs would let a
    small invented integer trace by coincidence. A string leaf that is itself
    JSON (the MCP text content) is decoded and walked.
    """
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [n for item in value.values() for n in json_numbers(item)]
    if isinstance(value, list):
        return [n for item in value for n in json_numbers(item)]
    if isinstance(value, str):
        if value.startswith("sha256:"):
            return []
        try:
            decoded = json.loads(value)
        except ValueError:
            return [float(match) for match in SOURCE_NUMBER.findall(value)]
        return json_numbers(decoded) if isinstance(decoded, (dict, list)) else [float(match) for match in SOURCE_NUMBER.findall(value)]
    return []


def source_numbers(events: list[dict[str, Any]], prompt: str) -> list[float]:
    """Every number in the attempt's tool responses and in the task prompt."""
    numbers = [float(match) for match in SOURCE_NUMBER.findall(prompt)]
    for event in events:
        if event["kind"] == "tool_response":
            numbers += json_numbers(event["payload"]["result"])
    return numbers


def check(answer: str, events: list[dict[str, Any]], prompt: str, pattern: re.Pattern[str] = ANSWER_NUMBER) -> Provenance:
    """Trace each number in ``answer`` to the attempt's own tool responses or prompt.

    ``pattern`` finds the numbers (one capture group). The default is the scorer's and must not change, or published scores stop being
    comparable; the runtime guard passes a stricter one.
    """
    printed = pattern.findall(answer)
    sources = source_numbers(events, prompt)
    untraced = []
    for text in printed:
        value, tolerance = float(text), printed_tolerance(text)
        slack = tolerance + RELATIVE_SLACK * abs(value)
        if not any(abs(abs(source) - value) <= slack for source in sources):
            untraced.append(text)
    return {"numbers": printed, "untraced": untraced, "traced": not untraced}


# ---------------------------------------------------------------------------
# v3: field-level provenance (backlog 0010, review findings B4-B6).
#
# v2 (``check``) traces a number when it equals *any* number in the attempt's tool
# responses or prompt. That accepted a schema limit or the case name's "2000" as a
# violation count, ignored signs, split "1,039" in two, rejected truncation, and
# passed an empty answer. v3 traces each labelled report value to its own solver
# field, at no fewer decimals than the reference report prints; takes numbers only
# from solver outputs (not the echoed request, the declared limits or the case
# name); requires a fit's answer to be a scale that was actually tried; and fails
# an answer with no numbers. ``check`` is unchanged, so r6 scores stay comparable.

# A signed number, with thousands separators read as one number ("1,039" -> 1039).
# A sign counts only where it cannot be a hyphen ("N-1", "snem-sa-2000").
FIELD_NUMBER: re.Pattern[str] = re.compile(
    r"(?<![\w.,])((?:(?<![\w-])-)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?)(?![\w])")
# Labelled report values, in the reference wording (training trajectories and the benchmark's
# report references), with the decimals that wording prints: voltages :.4f, loading :.2f.
REPORT_FIELDS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("iterations", re.compile(r"converged in\s+(\S+)\s+iterations", re.I), 0),
    ("min_bus_voltage_pu", re.compile(r"min(?:imum)? bus voltage\s*[=:]\s*(\S+?)\s*pu", re.I), 4),
    ("max_bus_voltage_pu", re.compile(r"max(?:imum)? bus voltage\s*[=:]\s*(\S+?)\s*pu", re.I), 4),
    ("max_line_loading_percent", re.compile(r"max(?:imum)? line loading\s*[=:]\s*(\S+?)\s*%", re.I), 2),
    ("voltage_violations", re.compile(r"voltage violations\s*[=:]\s*(-?[\d,]+)", re.I), 0),
)
# Tool-response keys that are not solver outputs: the request echoed back, and declared limits.
# A labelled report value must come from its own output field, never from these. An
# unlabelled context number may cite a declared limit ("outside the 0.95-1.05 pu band")
# or a prompt id: quoting them is not making a number up (r6b, 2026-10-23).
NOT_OUTPUTS: frozenset[str] = frozenset({"study", "voltage_limit_pu", "thermal_limit_percent", "physical_scope"})
DECLARED_LIMITS: frozenset[str] = frozenset({"voltage_limit_pu", "thermal_limit_percent"})
# Markdown emphasis and code marks around a value ("**5**", "`39`"). Not "_": it is part of
# identifiers and namespace paths ("vn_kv"), and underscore emphasis is rare.
MARKUP: str = "*`"
# Namespace listing fields (backlog 0026): ids, paths and counts, not measurements. An
# answer may use them as identifiers (context), never as a measured value.
NAMESPACE_STRUCTURE: frozenset[str] = frozenset({"path", "children", "counts", "offset", "total", "next_offset"})
# A number with an optional unit, then a cited namespace path: "132 kV (/case/snem-sa-2000/bus/985/vn_kv)".
CITATION: re.Pattern[str] = re.compile(
    FIELD_NUMBER.pattern + r"\s*(?:[A-Za-z%/]+\s*)?\((?P<path>/case/[^\s)]+)\)")
CONTINGENCY_MENTION: re.Pattern[str] = re.compile(r"line\s+(\d+)\s+out of service", re.I)


class FieldProvenance(TypedDict):
    """v3 provenance: every number traced to a solver output field, a tried scale or the task's id."""

    numbers: list[str]
    untraced: list[str]
    reasons: list[str]
    traced: bool


def parse_number(text: str) -> float:
    """A printed number as a float, thousands separators removed."""
    return float(text.replace(",", ""))


def matches(text: str, source: float) -> bool:
    """Does printed ``text`` equal ``source``, rounded or truncated to the digits it prints?"""
    value = parse_number(text)
    unit = 2 * printed_tolerance(text.replace(",", "").lstrip("-"))
    slack = RELATIVE_SLACK * max(1.0, abs(source))
    rounded = abs(source - value) <= unit / 2 + slack
    truncated = 0 <= (source - value) * (1 if value >= 0 else -1) < unit + slack
    return (value < 0) == (source < 0) and (rounded or truncated) if value else rounded


# Keys of a run_python result that are the code's own words or the trial list, not solver output fields;
# each trial is read as a reply of its own (``observations``), and printed text is never evidence.
PYSHELL_KEYS: frozenset[str] = frozenset({"stdout", "trials", "checks"})


def observations(events: list[dict[str, Any]]) -> Any:
    """Every successful tool reply in a journal, and each solver trial run inside ``run_python`` as a reply of its own.

    A run_python result lists its trials with the solver's real replies (backlog 0041); reading them here lets
    the same checks grade a fit whether the model called ``run_study_trial`` or searched in code. A journal with
    no run_python call yields exactly what the plain loop did.
    """
    for event in events:
        if event["kind"] != "tool_response" or event["payload"]["result"].get("isError"):
            continue
        for content in event["payload"]["result"].get("content", []):
            if content.get("type", "text") != "text":
                continue
            try:
                observed = json.loads(content.get("text", ""))
            except ValueError:
                continue
            if not isinstance(observed, dict):
                continue
            yield observed
            data = observed.get("data")
            if isinstance(data, dict) and isinstance(data.get("trials"), list):
                for trial in data["trials"]:
                    if isinstance(trial, dict) and isinstance(trial.get("response"), dict):
                        yield trial["response"]


def solver_outputs(events: list[dict[str, Any]]) -> tuple[dict[str, list[float]], list[float], list[float]]:
    """Output fields (by name), all output numbers, and declared-limit numbers of successful responses."""
    by_field: dict[str, list[float]] = {}
    numbers: list[float] = []
    limits: list[float] = []
    for observed in observations(events):
        data = observed.get("data")
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if key in PYSHELL_KEYS:
                continue
            if key in DECLARED_LIMITS or key in NAMESPACE_STRUCTURE:
                limits += json_numbers(value) if not isinstance(value, str) else []
            if key in NOT_OUTPUTS or key in NAMESPACE_STRUCTURE:
                continue
            found = json_numbers(value)
            by_field.setdefault(key, []).extend(found)
            numbers += found
    return by_field, numbers, limits


def tried_scales(events: list[dict[str, Any]]) -> list[float]:
    """Every ``scale`` the attempt sent to ``run_study_trial``, or passed to ``solver.trial`` inside ``run_python``."""
    scales = []
    for event in events:
        if event["kind"] == "tool_request" and event["payload"]["function"]["name"] == "run_study_trial":
            arguments = json.loads(event["payload"]["function"]["arguments"] or "{}")
            if isinstance(arguments.get("scale"), (int, float)) and not isinstance(arguments["scale"], bool):
                scales.append(float(arguments["scale"]))
    for observed in observations(events):
        data = observed.get("data")
        for trial in (data.get("trials") or []) if isinstance(data, dict) else []:
            scale = (trial.get("arguments") or {}).get("scale") if isinstance(trial, dict) else None
            if isinstance(scale, (int, float)) and not isinstance(scale, bool):
                scales.append(float(scale))
    return scales


def check_fields(task: dict[str, Any], answer: str, events: list[dict[str, Any]],
                 resolve: Any = None) -> FieldProvenance:
    """v3 provenance (see the block comment above).

    Args:
        task: The benchmark row (its ``subfamily`` and ``task.inputs``).
        answer: The attempt's final answer.
        events: The attempt's journal.
        resolve: Resolves a cited namespace path to its ``get`` response; defaults to
            ``namespace.get``. A cited number must equal the value its path holds.

    Returns:
        The numbers found, those that did not trace, why, and whether all traced.
    """
    inputs = task["task"].get("inputs", {})
    text = "".join(" " if char in MARKUP else char for char in answer)
    citations = list(CITATION.finditer(text))
    # Cited numbers are checked against their paths below; blank them (path included) so
    # the case name inside a path is not read as a number and nothing is counted twice.
    for match in citations:
        text = text[:match.start()] + " " * (match.end() - match.start()) + text[match.end():]
    text = text.replace(str(inputs.get("case", "\0")), " ").replace("N-1", " ")
    by_field, outputs, limits = solver_outputs(events)
    context = outputs + limits + [float(n) for n in SOURCE_NUMBER.findall(task["task"].get("prompt", ""))]
    numbers, untraced, reasons = [], [], []
    claimed: list[tuple[int, int]] = []

    def fail(printed: str, reason: str) -> None:
        untraced.append(printed)
        reasons.append(f"{printed}: {reason}")

    for match in citations:
        printed, path = match.group(1), match.group("path")
        numbers.append(printed)
        if resolve is None:
            from wattson.inference.namespace import get as resolve_get
        try:
            held = (resolve or resolve_get)(path)["value"]
        except Exception as error:  # noqa: BLE001 -- any unresolvable citation fails the number
            fail(printed, f"cited path {path} does not resolve ({type(error).__name__})")
            continue
        if not isinstance(held, (int, float)) or isinstance(held, bool) or not matches(printed, float(held)):
            fail(printed, f"cited path {path} holds {held!r}")
    if task["subfamily"] == "pf-report":
        for name, pattern, decimals in REPORT_FIELDS:
            for match in pattern.finditer(text):
                printed = match.group(1).rstrip(".,;")
                claimed.append(match.span(1))
                numbers.append(printed)
                if not FIELD_NUMBER.fullmatch(printed):
                    fail(printed, f"not a number for {name}")
                elif len(printed.partition(".")[2]) < decimals:
                    fail(printed, f"{name} printed with fewer than {decimals} decimals")
                elif not any(matches(printed, source) for source in by_field.get(name, [])):
                    fail(printed, f"no {name} output equals it")
        for match in CONTINGENCY_MENTION.finditer(text):
            claimed.append(match.span(1))
            numbers.append(match.group(1))
            if int(match.group(1)) != inputs.get("contingency_line"):
                fail(match.group(1), f"the task's contingency is {inputs.get('contingency_line')}")
    elif task["subfamily"] == "pf-unsolvable":
        # A refusal claims no value: any number must be the prompt's own id or a tool output.
        pass
    else:
        scales = tried_scales(events)
        for match in FIELD_NUMBER.finditer(text):
            if any(matches(match.group(1), scale) for scale in scales):
                claimed.append(match.span(1))
                numbers.append(match.group(1))
        if not claimed:
            fail("", "a fit answer must include a scale this attempt tried")
    for match in FIELD_NUMBER.finditer(text):
        if any(start <= match.start(1) < end for start, end in claimed):
            continue
        printed = match.group(1)
        numbers.append(printed)
        if not any(matches(printed, source) for source in context):
            fail(printed, "no solver output, declared limit or prompt id equals it")
    if not numbers and not untraced:
        fail("", "the answer contains no number")
    return {"numbers": numbers, "untraced": untraced, "reasons": reasons, "traced": not untraced}
