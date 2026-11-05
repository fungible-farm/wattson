"""Integration tests for the PowerMCP canonical execution contract (M02).

Covers the 4-state status contract with real solver output:
- snem-sa-2000 (CSIRO)  -> CONSTRAINT_VIOLATION (converged, 39 voltage violations)
- snem-1803 (CSIRO)     -> EXECUTION_FAILURE (FloatingPointError divide by zero)
- fixture-2bus-ok       -> SUCCESS (named stand-in fixture)
- fixture-2bus-nonconverged -> NON_CONVERGENCE (named stand-in fixture)
- unknown ID            -> EXECUTION_FAILURE
plus the canonical execution-evidence schema (MISSION.md M02) and
persistence to disk.
"""

import json
import os
import pytest

from wattson.inference.client import InferenceClient
from wattson.inference.powermcp import PowerMCP, ExecutionStatus


@pytest.fixture
def powermcp():
    return PowerMCP()


def test_powermcp_tool_schemas(powermcp):
    """PowerMCP exposes valid OpenAI-compatible tool schemas."""
    schemas = powermcp.get_tool_schemas()
    assert len(schemas) == 2

    tool_names = [s["function"]["name"] for s in schemas]
    assert "load_network" in tool_names
    assert "run_power_flow" in tool_names

    # Validate schema structure
    for schema in schemas:
        assert schema["type"] == "function"
        assert "name" in schema["function"]
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]


def test_powermcp_load_network(powermcp):
    """PowerMCP can load a network case."""
    result = powermcp.load_network("snem-sa-2000")
    assert result.status == ExecutionStatus.SUCCESS
    assert result.data["network_id"] == "snem-sa-2000"
    assert result.data["buses"] > 0
    assert result.data["branches"] > 0


def test_powermcp_run_power_flow_constraint_violation(powermcp):
    """snem-sa-2000 converges but violates the voltage screening band.

    The base case of the CSIRO snem-sa-2000 case has 39 buses outside the
    0.95-1.05 pu band (measured), so a converged solution is NOT SUCCESS --
    the mission invariant that convergence != physical validity.
    """
    result = powermcp.run_power_flow("snem-sa-2000")
    assert result.status == ExecutionStatus.CONSTRAINT_VIOLATION
    assert result.data["iterations"] is not None
    assert result.data["iterations"] >= 1
    assert "max_line_loading_percent" in result.data
    assert result.data["voltage_violations"] > 0
    assert result.error is not None
    assert "Voltage limit violations" in result.error


def test_powermcp_run_power_flow_success_fixture(powermcp):
    """The named 2-bus fixture converges with no limit violations -> SUCCESS."""
    result = powermcp.run_power_flow("fixture-2bus-ok")
    assert result.status == ExecutionStatus.SUCCESS
    assert result.data["iterations"] is not None
    assert result.data["voltage_violations"] == 0
    assert result.data["min_bus_voltage_pu"] > 0.95
    assert result.data["max_bus_voltage_pu"] < 1.05
    assert result.error is None


def test_powermcp_run_power_flow_non_convergence_fixture(powermcp):
    """The named 2-bus overloaded fixture diverges -> NON_CONVERGENCE."""
    result = powermcp.run_power_flow("fixture-2bus-nonconverged")
    assert result.status == ExecutionStatus.NON_CONVERGENCE
    assert result.error is not None
    assert "did not converge" in result.error


def test_powermcp_run_power_flow_execution_failure(powermcp, monkeypatch):
    """A numerical exception is an execution failure, independent of case/version quirks.

    The historical snem-1803 FloatingPointError no longer occurs on Wattson's
    pinned stack. Inject only the solver exception on the named small fixture;
    this tests error transport, not a fabricated physical answer.
    """
    from wattson.inference import powermcp as canonical

    def fail_solver(*args, **kwargs):
        raise FloatingPointError("injected numeric failure")

    monkeypatch.setattr(canonical, "_runpp_counted", fail_solver)
    result = powermcp.run_power_flow("fixture-2bus-ok")
    assert result.status == ExecutionStatus.EXECUTION_FAILURE
    assert result.error is not None
    assert "FloatingPointError" in result.error


def test_powermcp_unknown_network(powermcp):
    """PowerMCP returns EXECUTION_FAILURE for unknown network IDs."""
    result = powermcp.load_network("unknown-network")
    assert result.status == ExecutionStatus.EXECUTION_FAILURE
    assert "Unknown network ID" in result.error


def test_powermcp_execute_tool(powermcp):
    """PowerMCP execute_tool dispatches correctly."""
    result = powermcp.execute_tool("load_network", {"network_id": "snem-sa-2000"})
    assert result.status == ExecutionStatus.SUCCESS
    assert result.data["buses"] > 0


def test_powermcp_canonical_evidence_schema(powermcp, tmp_path):
    """Canonical execution evidence follows the MISSION.md M02 schema and persists.

    Evidence must carry: case_id, solver, solver_version, tool_name,
    input_hash, status, result_hash, validity{schema,numerical,physical},
    diagnostics.
    """
    result = powermcp.run_power_flow("snem-sa-2000")
    assert result.evidence is not None
    ev = result.evidence

    for key in (
        "case_id",
        "solver",
        "solver_version",
        "tool_name",
        "input_hash",
        "status",
        "result_hash",
        "validity",
        "diagnostics",
    ):
        assert key in ev, f"evidence missing {key}"

    assert ev["case_id"] == "snem-sa-2000"
    assert ev["solver"] == "pandapower"
    assert ev["status"] == "constraint_violation"
    assert ev["input_hash"].startswith("sha256:")
    assert ev["result_hash"].startswith("sha256:")
    assert set(ev["validity"]) == {"schema", "numerical", "physical"}
    # Converged but physically invalid -> physical is False, schema/numerical True
    assert ev["validity"]["schema"] is True
    assert ev["validity"]["numerical"] is True
    assert ev["validity"]["physical"] is False
    assert len(ev["diagnostics"]) > 0

    # Persist and read back
    out = tmp_path / "evidence" / "snem-sa-2000.json"
    PowerMCP.persist_evidence(result, out)
    on_disk = json.loads(out.read_text())
    assert on_disk == ev


def test_powermcp_evidence_distinguishes_states(powermcp):
    """Each of the 4 states is explicitly distinguishable in evidence/status."""
    statuses = {
        powermcp.run_power_flow("fixture-2bus-ok").status,
        powermcp.run_power_flow("fixture-2bus-nonconverged").status,
        powermcp.run_power_flow("snem-sa-2000").status,
        powermcp.run_power_flow("snem-1803").status,
        powermcp.load_network("nope").status,
    }
    assert statuses == {
        ExecutionStatus.SUCCESS,
        ExecutionStatus.NON_CONVERGENCE,
        ExecutionStatus.CONSTRAINT_VIOLATION,
        ExecutionStatus.EXECUTION_FAILURE,
    }


@pytest.fixture
def client():
    c = InferenceClient()
    yield c
    c.close()


@pytest.mark.skipif(
    os.environ.get("WATTSON_TEACHER_INTEGRATION") != "1",
    reason="live teacher service: opt in with WATTSON_TEACHER_INTEGRATION=1",
)
def test_teacher_calls_powermcp_tools(client, powermcp):
    """Teacher model can discover and call PowerMCP tools."""
    schemas = powermcp.get_tool_schemas()

    result = client.chat_completion(
        model="qwen3.8-flash-next",
        messages=[
            {"role": "system", "content": "You are a power systems engineer assistant."},
            {
                "role": "user",
                "content": "I need to analyze the snem-sa-2000 network. First load it, then run power flow analysis.",
            },
        ],
        max_tokens=300,
        tools=schemas,
    )

    tool_calls = result["choices"][0]["message"].get("tool_calls", [])
    assert len(tool_calls) > 0

    # Execute the tool calls
    for tc in tool_calls:
        args = json.loads(tc["function"]["arguments"])
        result = powermcp.execute_tool(tc["function"]["name"], args)
        assert result.status in [
            ExecutionStatus.SUCCESS,
            ExecutionStatus.NON_CONVERGENCE,
            ExecutionStatus.CONSTRAINT_VIOLATION,
            ExecutionStatus.EXECUTION_FAILURE,
        ]
