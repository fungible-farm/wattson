# One launch contract for containers, agents and IDEs

Wattson packages engineering tools and data workflows with their dependencies.
An agent proposes a study or read request; a tool executes it and records the
result. A benchmark can then check the result independently. Tool execution and
model narration are separate evidence. These public-case checks do not certify
operation on a national grid or authorize control actions on a live network.

The root `./wattson` launcher is the host control plane. It needs a POSIX shell,
core utilities and provisioned rootless Podman; it does not install host Python,
Rust or Node. `just` is an optional convenience over the same commands. Workers
receive explicit data mounts, rather than the host engine socket or whole home.

```sh
# Inspect required host capabilities and every optional component before acquiring it.
./wattson components
# Prepare CPU solver, harness/pi, model downloader and CSIRO fetcher images.
./wattson install core
# Check the local software image without a model, GPU or network.
./wattson check
# Acquire pinned public cases separately into a volume; this step needs network once.
./wattson cases
# Model weights are a separate explicit, large preparation profile.
# ./wattson install models
```

From a checkout with host just, `just components`, `just install core` and
`just service dbt-mcp check` route through this same launcher. Existing `just up`,
`just ask`, `just eval` and `just pi` retain the quick-start model-pod interface.
Question/flag arguments are passed positionally through both shell layers.

| Capability | Prepare | Check or run | Boundary |
|---|---|---|---|
| Power studies | `./wattson install core` | `./wattson service power-mcp stdio` | stdio MCP, explicit case volume |
| PLEXOS XML | `./wattson install plexosdb` | `./wattson service plexosdb inspect /input/dir model.xml` | one-shot JSON, directory mounted read-only |
| dbt | `./wattson install dbt-mcp` | `./wattson service dbt-mcp check` or `stdio` | upstream MCP, read-only project/profile, output volume |
| ODBC | `./wattson install pyodbc-mcp` | `./wattson service pyodbc-mcp stdio /private/config readonly_dsn` | upstream MCP, database-side read-only grants required |
| Excel | `./wattson install excel-mcp` | `./wattson service excel-mcp stdio /approved/workbook.xlsm` | read-only MCP, one explicit workbook |
| Developer proofs | `./wattson install developer` | `just developer-test` | engineering/graph/forecast extras; approved data mounted separately |

An optional profile is available only when its source integration exists in the
checkout. Unknown service/action pairs fail explicitly. PLEXOS is a file command,
not an invented MCP endpoint. `stdio` reserves stdout for the protocol and uses no
TTY; preparation and check commands have separate output. ODBC needs a reachable
database and verified privileges, whereas local fixture proofs run offline.

Generate an IDE launch entry after rebuilding the core image from this revision:

```sh
# Standard mcpServers envelope; no credentials are emitted.
./wattson mcp-config excel-mcp --workbook '/approved/2026 ISP.xlsm'
# VS Code uses the same command/argv under a servers envelope, with type=stdio.
./wattson mcp-config excel-mcp --workbook '/approved/2026 ISP.xlsm' --client vscode
# Or run the generator in an already synced developer environment.
uv run wattson mcp-config power-mcp --launcher "$PWD/wattson"
```

Merge the selected entry into the client's existing MCP configuration; do not
overwrite unrelated servers. The absolute launcher path belongs to the machine
running the IDE. Remote IDEs need the checkout and data path on their execution
host. ODBC credentials remain in its private `odbc.ini`, and its launcher requires
`WATTSON_DATABASE_READONLY_CONFIRMED` only after actual permissions are verified.
This generator emits client JSON; it does not claim to install or test every IDE.

Skills are maintained once in `skills/<name>/SKILL.md`. Use a client's explicit
skill-path option or approved discovery location to expose that directory. For pi,
`pi --skill /absolute/checkout/skills/pi-delegate` loads local task delegation;
the Excel skill describes the bounded workbook-reading contract. This is distinct
from `wattson pi`, which intentionally restricts the trained engineering students
to their study tools. Broader review tools belong in the delegation profile.

For a read-only local review without installing host pi/Python, use
`./wattson delegate /absolute/task.txt /absolute/new-evidence-dir`. It uses the
prepared harness image, mounts source/task read-only and retains reports in
`new-evidence-dir/run`. The selected local model must already be running. Model
traffic uses the container network; task execution receives no engine socket.

The model endpoint URL must be a literal host-reachable private IP address
(e.g., `http://192.168.1.137:8002/v1`), not `127.0.0.1` or `localhost`, because
inside the container `127.0.0.1` refers to the container's own loopback. Override
the default URL, model, or timeout with environment variables:
`WATTSON_DELEGATE_URL`, `WATTSON_DELEGATE_MODEL`, and `WATTSON_DELEGATE_TIMEOUT`.

`local-runtime` remains an experimental IPC/GenCost/HF packaging profile.
`./wattson package` delegates to its existing offline bundle/SBOM builder. It does
not yet package every core/integration image, approved dataset and skill as one
tested release. A complete disconnected restore/check is still a release gate.
The dbt image also remains on a redistribution hold pending its documented
upstream dependency license review.
