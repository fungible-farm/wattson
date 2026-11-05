"""Every request the Rust IR emits (rust/command-ir/tests/requests.jsonl) runs on its executor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from wattson.commands import EXECUTORS, IR_VERSION, execute

REQUESTS: Path = Path(__file__).resolve().parents[1] / "rust/command-ir/tests/requests.jsonl"
ROWS: list[dict[str, Any]] = [json.loads(line) for line in REQUESTS.read_text().splitlines() if line.strip()]


def test_the_fixture_covers_every_command() -> None:
    assert {r["command"] for r in ROWS} == set(EXECUTORS)
    assert {r["ir_version"] for r in ROWS} == {IR_VERSION}


@pytest.mark.parametrize("request_", ROWS, ids=[r["command"] for r in ROWS])
def test_each_request_executes(request_: dict[str, Any]) -> None:
    if request_["command"] == "OPTIMIZE":
        from wattson.engineering.cases import CASES, REPO_ROOT

        record = CASES.get(request_["args"]["case"])
        if record and not (REPO_ROOT / record["path"]).is_file():
            pytest.skip("operator engineering artifact absent; stage configs/external-data.json inputs")
    if request_["command"] in {"RECALL", "FORECAST", "HINDCAST"}:
        from wattson.forecasting.hindcast import CACHE, TABLE_GLOB

        if not list(CACHE.glob(TABLE_GLOB)):
            pytest.skip("operator demand artifact absent; stage configs/external-data.json inputs")
    result = execute(request_)
    args, command = request_["args"], request_["command"]
    if command in {"SOLVE", "SIMULATE"}:
        assert result["data"]["converged"] and result["data"]["study"]["network_id"] == args["network_id"]
    elif command == "RECALL":
        assert result["values"] and all(pd.Timestamp(t) <= pd.Timestamp(args["as_of"]) for t, _ in result["values"])
    elif command == "RESOLVE":
        assert result["status"] == "success" and result["data"]["path"] == args["path"]
    elif command == "ESTIMATE":
        assert result["observable"] and result["feasibility"] == "feasible"
    elif command == "OPTIMIZE":
        assert result["solved"]
    elif command == "FORECAST":
        assert all(pd.Timestamp(t) > pd.Timestamp(args["issue_time"]) for t in result["valid_times"])
    elif command == "HINDCAST":
        assert result["issue_times"] > 0
    elif command == "COMPARE":
        assert sorted(result["worst_first"] + result["nonconvergent"]) == sorted(args["candidate_lines"])
    elif command == "EXPLAIN":
        assert result["all_verified"]


def test_another_ir_version_is_refused() -> None:
    with pytest.raises(ValueError, match="IR version"):
        execute(ROWS[0] | {"ir_version": "0.0.9"})


def test_an_explained_number_that_differs_from_its_path_is_not_verified() -> None:
    result = execute({"ir_version": IR_VERSION, "command": "EXPLAIN",
                      "args": {"claim": "Bus 985 is at 132 kV (/case/snem-sa-2000/bus/985/vn_kv)."}})
    assert not result["all_verified"] and result["evidence"][0]["held"] == 165.0
