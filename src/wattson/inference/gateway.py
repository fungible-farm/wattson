"""Policy gateway in front of the MCP tools (PRD-0010 G1, PC-CON-002/003; backlog 0015).

Every tool call passes :meth:`Gateway.check` before it executes. A decision depends only on
the policy and the call itself: tool name, arguments and the attempt's call count. Nothing
a tool *returns* reaches the gateway, so retrieved content (a tool response or document
saying "you are now authorised") cannot grant itself permissions (G1 exit criterion).
Checks, in order:

1. **Role allowlist:** the role may call this tool. The default role, ``analyst``, is read-only:
   studies and namespace reads, no control actions (PC-CON-002, read-only operational
   integration). Writes would need a role that does not exist yet, plus a human approval
   (PC-CON-003).
2. **Network allowlist:** a ``network_id`` must be one the policy names.
3. **Schema:** the arguments must validate against the tool's own JSON Schema (types, units
   as ranges, no extra properties). The same schema is served to the model.
4. **Budget:** at most ``max_calls`` per attempt (the evaluation's own budget by default).

Each decision is appended to an audit list with its reason. The gateway is opt-in
(``mcp_server --policy``), so the r1-r6 server is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

import jsonschema

from wattson.inference.powermcp import SERVED_NETWORKS
from wattson.inference.tool_probe import MAX_TOOL_CALLS

READ_ONLY_TOOLS: frozenset[str] = frozenset({"load_network", "run_power_flow", "run_study_trial", "ls", "get"})
ROLES: dict[str, frozenset[str]] = {"analyst": READ_ONLY_TOOLS}


class Decision(TypedDict):
    """One gateway decision, as audited."""

    tool: str
    allowed: bool
    reason: str
    call_number: int


@dataclass
class Policy:
    """What a role may do in one attempt."""

    role: str = "analyst"
    networks: frozenset[str] = frozenset(SERVED_NETWORKS)
    max_calls: int = MAX_TOOL_CALLS


@dataclass
class Gateway:
    """Checks each call against the policy and the served schemas; keeps an audit trail."""

    policy: Policy
    schemas: dict[str, dict[str, Any]]  # tool name -> its JSON Schema ("parameters")
    audit: list[Decision] = field(default_factory=list)
    calls: int = 0

    def check(self, tool: str, arguments: dict[str, Any]) -> Decision:
        """Allow or refuse one call; refused calls do not count against the budget."""
        reason = self._refusal(tool, arguments)
        if reason is None:
            self.calls += 1
        decision = Decision(tool=tool, allowed=reason is None, reason=reason or "allowed", call_number=self.calls)
        self.audit.append(decision)
        return decision

    def _refusal(self, tool: str, arguments: dict[str, Any]) -> str | None:
        allowed = ROLES.get(self.policy.role)
        if allowed is None:
            return f"unknown role {self.policy.role!r}"
        if tool not in allowed:
            return f"role {self.policy.role!r} may not call {tool!r} (read-only: {sorted(allowed)})"
        network = arguments.get("network_id")
        if network is not None and network not in self.policy.networks:
            return f"network {network!r} is not in this policy"
        schema = self.schemas.get(tool)
        if schema is None:
            return f"no served schema for {tool!r}"
        try:
            jsonschema.validate(arguments, schema)
        except jsonschema.ValidationError as error:
            return f"arguments fail the schema: {error.message}"
        if self.calls >= self.policy.max_calls:
            return f"call budget of {self.policy.max_calls} spent"
        return None
