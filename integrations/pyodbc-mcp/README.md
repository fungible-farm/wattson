# Optional pyodbc MCP container

Runs the actual MIT-licensed [`pyodbc-mcp-server==0.4.0`](https://pypi.org/project/pyodbc-mcp-server/0.4.0/)
with its ten upstream tools over agent stdio. This directory is independent of WattsOn's
runtime and root dependency lock. No GPU, web port, cloud key, or proprietary driver is required.

Upstream [source](https://github.com/jjones-wps/pyodbc-mcp-server) targets Windows-authenticated
SQL Server, hardcodes its connection string, and runs SQL Server-specific startup health queries.
`server.py` is an explicit Linux DSN adapter: it imports the installed upstream FastMCP server,
sets its configuration, substitutes only `create_connection`, and starts the same tools lazily.
It requests `pyodbc.connect(..., readonly=True)` with connection/query timeouts. It deliberately
replaces the SQL Server-only startup health check; initialization alone does not prove connectivity.
FastMCP <3 and MCP <2 preserve the API generation used by this release; the full dependency
graph and artifact hashes are frozen by `uv.lock`.

```bash
# From repository root: build + real MCP/ODBC fixture proof (Podman 5+).
./integrations/pyodbc-mcp/bootstrap.sh

# Or use this directory's independent command index.
just --justfile integrations/pyodbc-mcp/justfile --list
just --justfile integrations/pyodbc-mcp/justfile proof
```

The build uses `uv sync --frozen`; host Python, uv, and database installations are unnecessary.
Only deliberate lock refresh needs host uv. Container builds are limited to 2 GiB/two CPUs;
runs use 512 MiB/one CPU/128 PIDs. The non-root runtime has a read-only root filesystem,
dropped capabilities, no added privileges, and a limited temporary directory.

## Private SQL Server DSN and agent use

Copy `odbc.ini.example` to a private directory **outside the checkout** and edit it for your
server. That directory alone is mounted at `/config:ro`; its `odbc.ini` is also mounted at
`/etc/odbc.ini:ro` because some ODBC drivers ignore the user DSN path. Input exports,
certificates, or private driver configuration can live in this explicitly chosen directory.
No home directory is mounted. Match read permissions to the container UID 65532; never make
credentials world-readable merely to fix access. For sensitive configuration use an appropriately
mapped UID/Podman secret deployment rather than the example wrapper. Do not commit credentials.

The image supplies FreeTDS, an open-source SQL Server driver, and unixODBC. Set authentication
according to your DBA's policy; no Windows credential forwarding, Kerberos setup, remote
connectivity, or TLS configuration has been verified here. Do not disable certificate validation
to obtain a connection. Other SQL dialects are not supported by upstream's schema tools.

**Read-only authorization must be enforced by the database**: use a principal granted SELECT
only on approved views/tables, or a database/replica that rejects writes. Deny DDL, DML,
procedure execution and privileged role membership. Verify this using your DBA's permission
checks and a direct ODBC write attempt before exposing it to an agent. Upstream's SELECT
keyword filter and the driver's read-only request are defense in depth; neither is a security
boundary. The wrapper requires an explicit acknowledgment of that external prerequisite,
which is a deployment guard, not a permissions verifier.

```bash
# Only after database permissions are independently verified; no secrets in command arguments.
export WATTSON_DATABASE_READONLY_CONFIRMED=yes
./integrations/pyodbc-mcp/run.sh /absolute/private/config readonly_sql
```

Agent MCP configuration (replace the absolute paths):

```json
{
  "mcpServers": {
    "readonly_sql": {
      "command": "/absolute/wattson/integrations/pyodbc-mcp/run.sh",
      "args": ["/absolute/private/config", "readonly_sql"],
      "env": {"WATTSON_DATABASE_READONLY_CONFIRMED": "yes"}
    }
  }
}
```

The agent launches Podman with `-i` and no TTY so stdout remains MCP traffic. Server logs and
startup banners go to stderr. Query text, server names and returned data can appear in upstream
errors/logs; treat agent transcripts as sensitive. The wrapper exposes no HTTP endpoint.

## Proof and limits

`proof.sh` creates disposable SQLite bytes using Python's sqlite3, mounts them read-only, and
runs a real MCP client and the upstream stdio server inside a `--network=none` container. It
initializes MCP, discovers all ten tools, executes upstream `ReadData` against the real SQLite
ODBC driver, checks the exact row, verifies DELETE rejection, and bypasses the MCP filter to
verify a direct ODBC INSERT fails. SQLite is **only a test fixture**, not the client SQL
architecture. The direct denial proves the fixture's read-only storage policy; it does not
prove a remote principal's permissions. Upstream returns security errors as JSON inside a
successful MCP result, so the proof checks `SECURITY_ERROR` rather than only `isError`.

`proof-result.json` records the successful local gate; rederive it using `./proof.sh`.
No remote database credentials were available. SQL Server schema calls, remote authentication,
TLS and remote read-only grants remain unverified. No successful remote connection is claimed.
The optional fixture driver remains in this small integration image to keep the committed gate
self-contained. No test database, credentials, or private DSN is copied into the image.

Python package artifacts are hash-locked; base image tags and Debian package versions are not
fully reproducible across time. Capture image digests in a deployment if required.

## Licenses

- pyodbc-mcp-server 0.4.0: MIT (upstream distribution `LICENSE`).
- pyodbc: MIT-0; FastMCP and MCP Python SDK: MIT; AnyIO: MIT.
- unixODBC: LGPL-2.1-or-later libraries, GPL-2.0-or-later CLI components; FreeTDS: LGPL-2.0-or-later
  library, GPL-2.0-or-later utilities; SQLite ODBC: LGPL-2.1-or-later.
- Python: PSF-2.0; uv: MIT OR Apache-2.0; Debian packages retain their individual licenses.

Native package notices are installed under `/usr/share/doc/<package>/copyright`; Python
notices remain in `.venv/lib/python3.12/site-packages/*dist-info/`. No new root dependency
or client driver license obligation is implied by opting out of this integration.
