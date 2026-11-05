"""The served study schema is the Rust command IR's (rust/command-ir); its enums and bounds must match Python's."""

from __future__ import annotations

import json

from wattson.inference import powermcp as pm
from wattson.inference.studies import (IR_STUDY_SCHEMA, LOAD_LEVEL_MAX, LOAD_LEVEL_MIN, MAX_ITERATIONS, SCALE_MAX,
                                          SCALE_MIN, tool_schema)


def test_the_served_study_schema_is_the_one_the_ir_emits() -> None:
    """The IR's own test pins its output to this file (FM-23: train/serve schema skew)."""
    assert json.dumps(tool_schema()) + "\n" == IR_STUDY_SCHEMA.read_text()


def test_the_ir_schema_agrees_with_the_solver_it_fronts() -> None:
    """A case, scale bound or iteration cap changed on one side only fails here."""
    props = tool_schema()["function"]["parameters"]["properties"]
    assert props["network_id"]["enum"] == list(pm.SERVED_NETWORKS)
    assert (props["scale"]["minimum"], props["scale"]["maximum"]) == (SCALE_MIN, SCALE_MAX)
    assert props["max_iter"]["maximum"] == MAX_ITERATIONS
    assert (props["load_level"]["minimum"], props["load_level"]["maximum"]) == (LOAD_LEVEL_MIN, LOAD_LEVEL_MAX)
