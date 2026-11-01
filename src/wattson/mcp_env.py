"""The environment an MCP tool server is started with.

The MCP client library starts a stdio server with a minimal default environment (HOME, PATH, USER, ...) and does NOT pass on the
parent's. Everything the server needs from the container, such as ``WATTSON_CASE_DIR`` (where the CSIRO case files live), therefore
has to be passed explicitly, or the server silently falls back to its built-in defaults and reports a missing file.
"""

from __future__ import annotations

import os

from mcp.client.stdio import get_default_environment

PREFIX: str = "WATTSON_"


def server_environment() -> dict[str, str]:
    """The library's safe default environment plus every ``WATTSON_*`` variable of this process."""
    environment = dict(get_default_environment())
    environment.update({key: value for key, value in os.environ.items() if key.startswith(PREFIX)})
    return environment
