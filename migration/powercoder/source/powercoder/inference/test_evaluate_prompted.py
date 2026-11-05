"""The prompted baseline differs from the evaluation only in its system turn, and says so."""

from __future__ import annotations

import json
from pathlib import Path

from powercoder.inference import evaluate_prompted, tool_probe


def test_the_prompt_extends_the_evaluation_prompt_with_the_procedure() -> None:
    """Same opening system turn; the procedure follows it (backlog 0008, baseline b)."""
    assert evaluate_prompted.PROCEDURE_SYSTEM_PROMPT.startswith(tool_probe.SYSTEM_PROMPT)
    assert "bisect" in evaluate_prompted.PROCEDURE_SYSTEM_PROMPT


def test_a_finished_run_is_marked_as_the_procedure_variant(tmp_path: Path) -> None:
    """compare_agents reads system_prompt_variant from both files."""
    for name in ("manifest.json", "summary.json"):
        (tmp_path / name).write_text(json.dumps({"model": "m"}))
    evaluate_prompted.mark(tmp_path)
    for name in ("manifest.json", "summary.json"):
        assert json.loads((tmp_path / name).read_text())["system_prompt_variant"] == "procedure"
