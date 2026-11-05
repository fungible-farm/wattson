"""Targeted repair for the M05 distillation work package.

When a candidate is rejected (numerically wrong) or quarantined (could not be
evaluated), the repair loop hands the teacher its OWN previous script plus the
ACTUAL execution diagnostics and asks for a corrected script. This is the
mission's "permit targeted correction using actual execution diagnostics" --
the correction is grounded in solver observation, not re-derivation. Accepted
repair trajectories (the full sequence of code + diagnostics + outcome) are
retained as training material.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from powercoder.distillation import filter as flt
from powercoder.distillation.teacher import TeacherClient, TeacherResult

# How many targeted-correction rounds to allow after the initial generation.
# Each round is one extra teacher call; 2 keeps the worst case at 3 calls per
# example while still giving the model a real chance to fix a sign/index bug.
DEFAULT_MAX_REPAIR_ATTEMPTS: int = 2

# How much of the subprocess stderr to feed back into the repair prompt. A
# Python traceback is usually < 2 KB; we cap it so a runaway log cannot blow
# the context, but keep the tail (where the actual error line lives).
DIAGNOSTIC_STDERR_TAIL: int = 1500


@dataclass
class RepairStep:
    """One repair round: the corrected code, its outcome, and its cost."""

    attempt: int
    code: str
    status: str  # accepted / rejected / quarantined
    diagnostics: list[str]
    prompt_tokens: int
    completion_tokens: int
    elapsed_s: float
    exec_wall_s: float
    exec_returncode: int | None
    exec_stderr: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class RepairOutcome:
    """The full repair trajectory for one example."""

    example_id: str
    final_status: str  # accepted / rejected / quarantined
    final_code: str
    final_answer: Any
    final_evidence: dict[str, Any]
    total_prompt_tokens: int
    total_completion_tokens: int
    total_teacher_calls: int  # includes the initial generation
    total_exec_wall_s: float
    steps: list[RepairStep] = field(default_factory=list)

    @property
    def is_accepted(self) -> bool:
        return self.final_status == "accepted"


def _diagnostics_for_prompt(result: "flt.FilterResult") -> str:
    """Build the concise, actionable diagnostics string for the repair prompt."""
    parts: list[str] = []
    for line in result.diagnostics:
        if line:
            parts.append(line)
    if result.exec_stderr:
        tail = result.exec_stderr.strip()[-DIAGNOSTIC_STDERR_TAIL:]
        parts.append("--- stderr tail ---\n" + tail)
    if not parts:
        parts.append("(no diagnostics captured)")
    return "\n".join(parts)


def repair_example(
    teacher: TeacherClient,
    example: dict[str, Any],
    initial_code: str,
    initial_result: "flt.FilterResult",
    initial_teacher_result: TeacherResult,
    max_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS,
) -> RepairOutcome:
    """Run the targeted-correction loop for one example.

    Args:
        teacher: the TeacherClient to draw corrections from.
        example: the M04 training example.
        initial_code: the first-generation script.
        initial_result: its FilterResult.
        initial_teacher_result: its TeacherResult (for token accounting).
        max_attempts: number of repair rounds after the initial generation.

    Returns:
        A RepairOutcome carrying the final status, the full repair trajectory
        (steps), and the aggregate token/wall-clock cost.
    """
    total_prompt = initial_teacher_result.prompt_tokens
    total_completion = initial_teacher_result.completion_tokens
    total_calls = 1
    total_exec_wall = initial_result.exec_wall_s

    current_code = initial_code
    current_result = initial_result
    steps: list[RepairStep] = []

    if current_result.is_accepted:
        return RepairOutcome(
            example_id=example["example_id"],
            final_status=current_result.status,
            final_code=current_code,
            final_answer=current_result.answer,
            final_evidence=current_result.evidence,
            total_prompt_tokens=total_prompt,
            total_completion_tokens=total_completion,
            total_teacher_calls=total_calls,
            total_exec_wall_s=total_exec_wall,
            steps=steps,
        )

    for attempt in range(1, max_attempts + 1):
        diagnostics = _diagnostics_for_prompt(current_result)
        repaired_teacher = teacher.repair(
            example, current_code, diagnostics, attempt
        )
        total_prompt += repaired_teacher.prompt_tokens
        total_completion += repaired_teacher.completion_tokens
        total_calls += 1

        repaired_result = flt.filter_example(example, repaired_teacher.code)
        total_exec_wall += repaired_result.exec_wall_s

        steps.append(
            RepairStep(
                attempt=attempt,
                code=repaired_teacher.code,
                status=repaired_result.status,
                diagnostics=repaired_result.diagnostics,
                prompt_tokens=repaired_teacher.prompt_tokens,
                completion_tokens=repaired_teacher.completion_tokens,
                elapsed_s=repaired_teacher.elapsed_s,
                exec_wall_s=repaired_result.exec_wall_s,
                exec_returncode=repaired_result.exec_returncode,
                exec_stderr=repaired_result.exec_stderr,
                evidence=repaired_result.evidence,
            )
        )

        current_code = repaired_teacher.code
        current_result = repaired_result
        if current_result.is_accepted:
            break

    return RepairOutcome(
        example_id=example["example_id"],
        final_status=current_result.status,
        final_code=current_code,
        final_answer=current_result.answer,
        final_evidence=current_result.evidence,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        total_teacher_calls=total_calls,
        total_exec_wall_s=total_exec_wall,
        steps=steps,
    )
