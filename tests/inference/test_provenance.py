"""Numbers in an answer must come from this attempt's tool responses or its prompt."""

from __future__ import annotations

import json

from wattson.inference.provenance import check, printed_tolerance

PROMPT: str = ("Run an AC power flow on case 'snem-sa-2000' with line 208 out of service (N-1 contingency) "
               "and report the result.")
SOLVED: list[dict] = [{"kind": "tool_response", "payload": {"result": {"content": [{"type": "text", "text": json.dumps(
    {"status": "constraint_violation", "data": {"iterations": 5, "min_bus_voltage_pu": 0.9069716219,
     "max_bus_voltage_pu": 1.09411, "max_line_loading_percent": 2.661366, "voltage_violations": 39}})}]}}}]
ANSWER: str = ("Power flow on 'snem-sa-2000' with line 208 out of service (N-1 contingency) converged in 5 "
               "iterations. Min bus voltage = 0.9070 pu, max bus voltage = 1.0941 pu, max line loading = 2.66%, "
               "voltage violations = 39.")


def test_a_reference_report_built_from_the_tool_response_is_traced() -> None:
    """Rounded solver values, prompt ids and 'N-1' all trace."""
    assert check(ANSWER, SOLVED, PROMPT) == {"numbers": ["2000", "208", "1", "5", "0.9070", "1.0941", "2.66", "39"],
                                             "untraced": [], "traced": True}


def test_the_same_answer_without_a_tool_call_is_fabricated() -> None:
    """The first r6 Qwen student's failure: right shape, no solver behind it."""
    result = check(ANSWER, [], PROMPT)
    assert not result["traced"] and result["untraced"] == ["5", "0.9070", "1.0941", "2.66", "39"]


def test_a_value_off_by_more_than_its_printed_precision_is_untraced() -> None:
    """0.9071 is not 0.90697 at four decimals."""
    assert check("Min bus voltage = 0.9071 pu", SOLVED, PROMPT)["untraced"] == ["0.9071"]


def test_printed_tolerance_is_half_a_unit_in_the_last_digit() -> None:
    assert printed_tolerance("1.0941") == 0.5e-4 and printed_tolerance("39") == 0.5


def test_digits_inside_evidence_hashes_do_not_trace() -> None:
    """'5' must not trace to the hex digits of a sha256 evidence hash."""
    hashed = [{"kind": "tool_response", "payload": {"result": {"content": [{"type": "text", "text": json.dumps(
        {"evidence": {"input_hash": "sha256:146f5102"}})}]}}}]
    assert check("converged in 5 iterations", hashed, "")["untraced"] == ["5"]


# --- v3, field-level provenance (backlog 0010): each reproduced case from the review --------------
from wattson.inference.provenance import check_fields  # noqa: E402

REPORT_TASK: dict = {"subfamily": "pf-report", "task": {"prompt": PROMPT,
                                                        "inputs": {"case": "snem-sa-2000", "contingency_line": 208}}}
FIT_TASK: dict = {"subfamily": "pf-load-scale-fit", "task": {"prompt": "Find s in [0.9, 1.1] ...",
                                                             "inputs": {"case": "snem-sa-2000"}}}


def trial(scale: float) -> list[dict]:
    """A run_study_trial request at ``scale`` and its response."""
    request = {"kind": "tool_request", "payload": {"function": {"name": "run_study_trial", "arguments": json.dumps(
        {"network_id": "snem-sa-2000", "operation": "load_scale", "scale": scale})}}}
    response = {"kind": "tool_response", "payload": {"result": {"content": [{"type": "text", "text": json.dumps(
        {"status": "constraint_violation", "data": {"study": {"scale": scale}, "metric": 0.95,
                                                     "voltage_limit_pu": [0.95, 1.05]}})}]}}}
    return [request, response]


def test_v3_traces_the_reference_report_field_by_field() -> None:
    result = check_fields(REPORT_TASK, ANSWER, SOLVED)
    assert result["traced"], result["reasons"]
    assert result["numbers"] == ["5", "0.9070", "1.0941", "2.66", "39", "208"]


def test_v3_rejects_a_value_that_matches_some_number_but_not_its_own_field() -> None:
    """Review B4: 'converged in 100 iterations, voltage violations = 2000' traced to a limit and the case name."""
    answer = ANSWER.replace("in 5 iterations", "in 100 iterations").replace("violations = 39", "violations = 2000")
    assert set(check_fields(REPORT_TASK, answer, SOLVED)["untraced"]) == {"100", "2000"}


def test_v3_fails_the_same_report_without_a_tool_call() -> None:
    assert not check_fields(REPORT_TASK, ANSWER, [])["traced"]


def test_v3_keeps_signs_and_reads_thousands_separators() -> None:
    assert "-0.9070" in check_fields(REPORT_TASK, ANSWER.replace("= 0.9070", "= -0.9070"), SOLVED)["untraced"]
    assert check_fields(REPORT_TASK, ANSWER.replace("= 39.", "= 1,039."), SOLVED)["untraced"] == ["1,039"]


def test_v3_accepts_truncation_but_not_fewer_digits_than_the_report_prints() -> None:
    assert check_fields(REPORT_TASK, ANSWER.replace("0.9070", "0.9069"), SOLVED)["traced"]
    reasons = check_fields(REPORT_TASK, ANSWER.replace("0.9070", "0.91"), SOLVED)["reasons"]
    assert reasons == ["0.91: min_bus_voltage_pu printed with fewer than 4 decimals"]


def test_v3_requires_the_tasks_contingency() -> None:
    assert check_fields(REPORT_TASK, ANSWER.replace("line 208", "line 94"), SOLVED)["untraced"] == ["94"]


def test_v3_fit_answer_must_be_a_tried_scale_not_the_interval_end() -> None:
    """Review B5: the prompt's interval end (1.1) traced under v2."""
    assert check_fields(FIT_TASK, "1.037500", trial(1.0375))["traced"]
    assert not check_fields(FIT_TASK, "1.100000", trial(1.0375))["traced"]


def test_v3_an_empty_answer_is_not_traced() -> None:
    """Review B6: v2 traced an empty answer vacuously."""
    assert not check_fields(REPORT_TASK, "", SOLVED)["traced"]
    assert not check_fields(FIT_TASK, "", trial(1.0))["traced"]


def test_v3_context_may_quote_declared_limits_and_markdown_is_ignored() -> None:
    """r6b Qwen base: 'outside the 0.95-1.05 pu band' cites the tool's declared limits, not a made-up number."""
    solved = json.loads(json.dumps(SOLVED))
    payload = json.loads(solved[0]["payload"]["result"]["content"][0]["text"])
    payload["data"]["voltage_limit_pu"] = [0.95, 1.05]
    solved[0]["payload"]["result"]["content"][0]["text"] = json.dumps(payload)
    answer = ANSWER.replace("in 5 iterations", "in **5** iterations") + " 39 buses are outside the 0.95-1.05 pu band."
    assert check_fields(REPORT_TASK, answer, solved)["traced"]
    # A limit still cannot stand in for a labelled field.
    assert not check_fields(REPORT_TASK, ANSWER.replace("violations = 39", "violations = 0.95"), solved)["traced"]
