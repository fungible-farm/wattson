"""Serve the canonical two-tool PowerMCP contract using the official MCP SDK.

This stdio endpoint wraps this repository's four-state contract. The existing
kube/powermcp_serve_http.py instead serves upstream Power-Agent's eight tools;
the two endpoints must not be treated as interchangeable.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from wattson.inference.powermcp import PowerMCP

# Wall-clock fields vary run to run; fed back to a greedy model they changed
# its next turn, making identical attempts diverge (AGENTS.md FM-3).
MODEL_HIDDEN_FIELDS: frozenset[str] = frozenset({"execution_time_s"})


def tool_definitions(study_tools: bool = False, namespace_tools: bool = False, python_tools: bool = False,
                     python_report_fields: bool = False) -> list[Tool]:
    """The tools this server lists; the latency benchmark sends the same list.

    ``namespace_tools`` adds ``ls``/``get`` (backlog 0026). It is off by default, so
    the r1-r6 tool set, and every prompt rendered from it, is unchanged.
    ``python_tools`` (backlog 0041) adds ``run_python`` and limits ``run_study_trial`` to reports;
    it needs ``study_tools``.
    """
    schemas = PowerMCP().get_tool_schemas()
    if study_tools:
        from wattson.inference.studies import tool_schema

        study = tool_schema()
        if python_tools:
            from wattson.inference.pyshell import STUDY_NOTE, TOOL_SCHEMA

            function = study["function"]
            study = {**study, "function": {**function, "description": STUDY_NOTE + function["description"]}}
            shell = TOOL_SCHEMA
            if python_report_fields:
                from wattson.inference.pyshell import REPORT_NOTE

                shell = {**TOOL_SCHEMA, "function": {**TOOL_SCHEMA["function"],
                                                     "description": TOOL_SCHEMA["function"]["description"] + REPORT_NOTE}}
            schemas += [study, shell]
        else:
            schemas.append(study)
    if namespace_tools:
        from wattson.inference.namespace import tool_schemas

        schemas += tool_schemas()
    return [Tool(name=f["name"], description=f["description"], inputSchema=f["parameters"])
            for schema in schemas for f in [schema["function"]]]


def study_reply(arguments: dict[str, Any]) -> dict[str, Any]:
    """One study trial as the model would see it (the dict ``run_study_trial`` returns, hidden fields removed)."""
    from wattson.inference.studies import run_study_trial

    return {k: v for k, v in asdict(run_study_trial(**arguments)).items() if k not in MODEL_HIDDEN_FIELDS}


def respond(backend: PowerMCP, name: str, arguments: dict[str, Any], study_tools: bool = False,
            namespace_tools: bool = False, python_tools: bool = False, python_report_fields: bool = False) -> str:
    """The text a tool call returns to the model; ``replay`` re-derives journals through this.

    Backend access must be serialized: its iteration observer temporarily patches
    pandapower's NR function and must not run concurrently in one process.
    """
    import json

    if namespace_tools and name in {"ls", "get"}:
        from wattson.inference.namespace import call

        return json.dumps(call(name, arguments), allow_nan=False)
    if python_tools and name == "run_python":
        from wattson.inference.pyshell import run_python

        outcome = run_python(arguments["code"], study_reply, python_report_fields)
        return json.dumps({"status": outcome["status"], "data": {k: outcome[k] for k in ("stdout", "trials", "checks") if k in outcome},
                           "error": outcome["error"]}, allow_nan=False)
    if python_tools and name == "run_study_trial" and arguments.get("operation") != "none":
        refused = {"status": "execution_failure", "data": {},
                   "error": "ValueError: studies that vary a scale run inside run_python through solver.trial"}
        return json.dumps(refused)
    if study_tools and name == "run_study_trial":
        from wattson.inference.studies import run_study_trial

        result = run_study_trial(**arguments)
    else:
        result = backend.execute_tool(name, arguments)
    visible = {k: v for k, v in asdict(result).items() if k not in MODEL_HIDDEN_FIELDS}
    if python_report_fields and name == "run_study_trial" and isinstance(visible.get("data"), dict):
        from wattson.inference.pyshell import report_text

        text = report_text(arguments, visible["data"])
        if text is not None:
            visible["data"] = {**visible["data"], "report_text": text}
    return json.dumps(visible, allow_nan=False)


def create_server(study_tools: bool = False, namespace_tools: bool = False, policy_role: str | None = None,
                  policy_networks: frozenset[str] | None = None, python_tools: bool = False,
                  python_report_fields: bool = False) -> Server:
    """Expose canonical schemas and real solver outcomes through SDK transport.

    ``policy_role`` puts the G1 policy gateway (``gateway.py``) in front of every call; a
    refused call returns ``status: policy_denied`` with the reason and never executes. The SDK
    validates arguments against the served schema first, so the gateway's own refusals are the
    policy ones (role, network, budget); its schema check remains for direct callers.
    """
    backend = PowerMCP()
    server = Server("wattson-canonical")
    tools = tool_definitions(study_tools, namespace_tools, python_tools, python_report_fields)
    gate = None
    if policy_role is not None:
        from wattson.inference.gateway import Gateway, Policy

        policy = Policy(role=policy_role) if policy_networks is None else Policy(role=policy_role, networks=policy_networks)
        gate = Gateway(policy, {t.name: t.inputSchema for t in tools})

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """Reuse the canonical schemas rather than duplicate their constraints."""
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Return actual solver data and evidence; stdout belongs to the SDK."""
        import json

        if gate is not None:
            decision = gate.check(name, arguments)
            if not decision["allowed"]:
                refused = {"status": "policy_denied", "data": {}, "error": f"PolicyDenied: {decision['reason']}"}
                return [TextContent(type="text", text=json.dumps(refused))]
        return [TextContent(type="text", text=respond(backend, name, arguments, study_tools, namespace_tools, python_tools,
                                                python_report_fields))]

    return server


async def serve(study_tools: bool = False, namespace_tools: bool = False, policy_role: str | None = None,
                policy_networks: frozenset[str] | None = None, python_tools: bool = False,
                python_report_fields: bool = False) -> None:
    """Run the SDK's standard parent-process pipe lifecycle."""
    server = create_server(study_tools, namespace_tools, policy_role, policy_networks, python_tools, python_report_fields)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-tools", action="store_true")
    parser.add_argument("--namespace-tools", action="store_true", help="Add ls/get (backlog 0026)")
    parser.add_argument("--python-tools", action="store_true",
                        help="Add run_python (a Python shell with a solver) and limit run_study_trial to reports (backlog 0041)")
    parser.add_argument("--python-report-fields", action="store_true",
                        help="With --python-tools: the v2 shell, whose trial replies carry the report numbers (backlog 0041)")
    parser.add_argument("--policy", default=None, help="Gate every call with this G1 policy role (gateway.py), e.g. analyst")
    parser.add_argument("--policy-networks", default=None, help="Comma-separated networks the policy allows")
    args = parser.parse_args()
    networks = frozenset(args.policy_networks.split(",")) if args.policy_networks else None
    asyncio.run(serve(args.study_tools, args.namespace_tools, args.policy, networks, args.python_tools,
                      args.python_report_fields))
