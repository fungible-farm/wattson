"""M05 distillation self-check (no live LLM required).

Verifies the two load-bearing mechanics of the distillation pipeline:

1. **Scaffold validity** — every per-subfamily scaffold in
   ``teacher.SCAFFOLDS`` is syntactically valid Python once its ``@@TOKEN@@``
   placeholders are substituted (substituted with a neutral literal ``0``;
   string-embedded tokens like ``"@@CASE@@"`` become ``"0"``).

2. **Independent re-solve filter** — the filter's acceptance rule is
   solver-verified, not teacher-trusted: a real accepted candidate
   (``correct_code`` in the committed fixture) is re-executed and its answer
   must match an independent pandapower re-solve -> ``accepted``; a candidate
   whose answer is offset by +0.05 pu on ``min_bus_voltage_pu``
   (``wrong_code``) must be rejected or quarantined because the independent
   re-solve no longer matches.

The fixture (``fixtures/expected_pf_report_filter.json``) holds one real
flash-next accepted ``pf-report`` candidate on ``snem-sa-2000`` plus its
mutated twin. Re-derive the fixture from a fresh distillation run if the
scaffold or filter contract changes.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from powercoder.distillation import teacher
from powercoder.distillation.filter import filter_example

FIXTURE = Path(__file__).parent / "fixtures" / "expected_pf_report_filter.json"
_TOKEN_RE = re.compile(r"@@[A-Z_]+@@")


def _substitute_tokens(scaffold: str) -> str:
    """Replace every ``@@NAME@@`` placeholder with the neutral literal ``0``.

    Works for both bare numeric tokens (``BUS = @@BUS_ID@@`` -> ``BUS = 0``)
    and string-embedded tokens (``"@@CASE@@"`` -> ``"0"``), which is all the
    parse check needs — no execution.
    """
    return _TOKEN_RE.sub("0", scaffold)


def test_all_scaffolds_parse_as_valid_python() -> None:
    """Every scaffold must be valid Python after token substitution."""
    assert set(teacher.SCAFFOLDS) == {
        "pf-report",
        "pf-load-scale-fit",
        "pf-line-rating-fit",
        "pf-gen-dispatch-fit",
        "mcp-trajectory",
    }, f"unexpected scaffold subfamilies: {sorted(teacher.SCAFFOLDS)}"
    for subfamily, scaffold in teacher.SCAFFOLDS.items():
        substituted = _substitute_tokens(scaffold)
        try:
            ast.parse(substituted)
        except SyntaxError as exc:  # pragma: no cover - failure path
            pytest.fail(
                f"scaffold {subfamily!r} does not parse after token "
                f"substitution: {exc}"
            )


def test_filter_accepts_real_accepted_candidate() -> None:
    """A real accepted candidate must pass the independent re-solve."""
    fixture = json.loads(FIXTURE.read_text())
    result = filter_example(fixture["example"], fixture["correct_code"])
    assert result.is_accepted, (
        f"expected accepted, got {result.status}; "
        f"diagnostics={result.diagnostics}"
    )


def test_filter_rejects_offset_candidate() -> None:
    """A candidate whose answer is offset +0.05 pu must not be accepted."""
    fixture = json.loads(FIXTURE.read_text())
    result = filter_example(fixture["example"], fixture["wrong_code"])
    assert not result.is_accepted, (
        "offset candidate was accepted — the independent re-solve did not "
        "catch the +0.05 pu min_bus_voltage_pu offset"
    )
    assert result.status in {"rejected", "quarantined"}, result.status
