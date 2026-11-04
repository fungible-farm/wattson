"""Nontrivial source/provenance and grading checks; executed inside the agent image."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_run import summarize
from query import lookup


class NemoTest(unittest.TestCase):
    """Neither missing data nor guessed model numbers may pass the task checker."""

    def test_actual_upstream_literal_and_provenance(self) -> None:
        """The selected upstream table must reproduce the independently reviewed fixture."""
        fixture = json.loads(Path(__file__).with_name("expected_costs.json").read_text())
        for technology, cost in fixture["capital_costs"].items():
            result = lookup(technology, 2030, "CP")
            self.assertEqual(result["capital_cost"], cost)
            self.assertEqual(result["source_commit"], fixture["source_commit"])
            self.assertIn("#L", result["source_url"])

    def test_unsupported_selectors_are_refused(self) -> None:
        """No inferred date/scenario/technology fallback is permitted."""
        for technology, year, scenario in (("not_a_technology", 2030, "CP"), ("Wind", 2031, "CP"), ("Wind", 2030, "other")):
            with self.assertRaises(ValueError):
                lookup(technology, year, scenario)

    def test_guessed_correct_value_without_a_tool_cannot_pass(self) -> None:
        """An answer matching the fixture is insufficient without actual tool evidence."""
        expected = lookup("PV1Axis", 2030, "CP")
        events = [{"type": "message_end", "message": {"role": "assistant",
                   "content": [{"type": "text", "text": "1123 AUD/kW"}]}}]
        self.assertFalse(summarize(events, expected)["passed"])

    def test_successful_tool_output_and_default_arguments_can_pass(self) -> None:
        """The real MCP defaults are valid, while mismatched results and errors are not."""
        expected = lookup("PV1Axis", 2030, "CP")
        events = [
            {"type": "message_end", "message": {"role": "assistant", "content": [
                {"type": "toolCall", "name": "mcp__gencost__gencost_capital_cost", "arguments": {}}]}},
            {"type": "tool_execution_end", "isError": False, "result": {"content": [
                {"type": "text", "text": json.dumps(expected)}]}},
            {"type": "message_end", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "1,123 AUD/kW"}]}},
        ]
        self.assertTrue(summarize(events, expected)["passed"])
        errors = copy.deepcopy(events)
        errors[1]["isError"] = True
        self.assertFalse(summarize(errors, expected)["passed"])
        different = copy.deepcopy(events)
        different[1]["result"]["content"][0]["text"] = json.dumps(dict(expected, technology="Wind"))
        self.assertFalse(summarize(different, expected)["passed"])

    def test_source_expressions_are_not_evaluated(self) -> None:
        """A new expression in upstream data requires review, never Python execution."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "nemo/costs/gencost2025.py"
            module.parent.mkdir(parents=True)
            module.write_text("class GenCost2025_2030_CP:\n def __init__(self):\n  table = self.capcost_per_kw\n  table[tech.PV1Axis] = 1000 + 123\n")
            with self.assertRaises(ValueError):
                lookup("PV1Axis", 2030, "CP", root)


if __name__ == "__main__":
    unittest.main()
