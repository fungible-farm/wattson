# Local dbt MCP example

This independent container packages the **actual upstream** [dbt-labs/dbt-mcp](https://github.com/dbt-labs/dbt-mcp/tree/v2.10.1)
server, pinned to PyPI `dbt-mcp==2.10.1` (release source `e5ca168ef4ecfeedd80e8e5ecb34b591256fedcb`),
and `dbt-duckdb==1.11.1`. Its own `uv.lock` pins dbt Core, DuckDB and every transitive dependency;
it does not change WattsOn's runtime environment. No LLM, GPU, dbt Platform account, API token,
warehouse service, or host Python installation is needed. Host requirements: Podman and Bash;
`just` is optional. Building needs image-registry and PyPI access. Runtime uses `--network=none`.

## Run the example

From the repository root:

```sh
just dbt-mcp-build
just dbt-mcp-check
# Without just:
integrations/dbt-mcp/run.sh build
integrations/dbt-mcp/run.sh smoke
```

The proof initializes an MCP stdio session against `dbt-mcp`, asserts the exposed tools are exactly
`parse`, `list`, `compile`, `build`, calls all four, checks the manifest and successful build/test
statuses, and queries the resulting DuckDB table. Expected rows are `sample_a,25` and `sample_b,7`.
`sample/` contains a deliberately tiny, deterministic, **illustrative** CSV fixture; it is not
measured NEM data, a physics result, or a WattsOn benchmark.

The named volume `wattson-dbt-mcp-output` holds `mcp-proof.json`, `target/manifest.json`,
`target/run_results.json`, `target/compiled/`, `logs/dbt.log`, and `sample.duckdb`.
The volume survives container exit; each smoke run rebuilds the same sample. Inspect using:

```sh
podman volume inspect wattson-dbt-mcp-output
# Or copy artifacts out of a stopped, unstarted helper container:
podman create --name dbt-artifacts -v wattson-dbt-mcp-output:/output:ro localhost/wattson-dbt-mcp:2.10.1
podman cp dbt-artifacts:/output ./dbt-output
podman rm dbt-artifacts
```

## Agent stdio configuration

Use an **absolute checkout path**; this example is standard MCP JSON for clients accepting
`mcpServers` (adapt the key names to your client). The script uses `podman run --rm -i`, never `-t`.
Stdout is reserved for MCP messages. Diagnostics go to stderr.

```json
{
  "mcpServers": {
    "dbt-local": {
      "command": "/absolute/path/to/wattson/integrations/dbt-mcp/run.sh",
      "args": ["serve"],
      "env": {
        "DBT_MCP_VOLUME": "wattson-dbt-mcp-output"
      }
    }
  }
}
```

An agent can call `list` with `{"resource_type":["model"]}`, then `build` with `{}`.
No model or external agent is exercised by the smoke check: it proves the actual server/tool/CLI
integration directly. Run one server per output volume: separate MCP processes do not coordinate
DuckDB writes or dbt target files. Do not attach an agent to the smoke volume during a smoke run.

## Mounts and writes

The launcher mounts project and profiles at `/project:ro` and `/profiles:ro`. The root filesystem
is read-only, with bounded temporary space at `/tmp` and the separate writable `/output` volume.
**Read-only source does not mean a read-only database:** `build` seeds/materializes tables and
runs tests. dbt also writes compiled SQL, manifests, partial-parse state and logs; these go to
`DBT_TARGET_PATH=/output/target`, `DBT_LOG_PATH=/output/logs`, and the profile's database
`/output/sample.duckdb`. There is no package-install/deps or codegen tool exposed.

To use your own local DuckDB project and profiles, supply absolute directories:

```sh
DBT_MCP_PROJECT=/absolute/project DBT_MCP_PROFILES=/absolute/profiles \
  DBT_MCP_VOLUME=my-project-dbt-output integrations/dbt-mcp/run.sh serve
```

Its profile must select DuckDB with a database path under `/output`; source project/package files
must already be complete. The project must tolerate read-only source/profiles and target/log
redirection through the above environment variables. Projects requiring dependency installation,
network warehouses, remote extensions, or source edits need their own deliberate configuration.
`smoke` validates only the included sample. `DBT_MCP_IMAGE` overrides the local image tag.
The launcher uses SELinux `:Z` on source/profile binds; use dedicated directories when SELinux
labels must not be shared with other containers.

## Upstream settings and containment

[Upstream settings](https://github.com/dbt-labs/dbt-mcp/blob/v2.10.1/src/dbt_mcp/config/settings.py)
support `DBT_MCP_ENABLE_TOOLS=parse,list,compile,build`. Cloud semantic-layer, discovery, SQL,
admin, remote tools, LSP, codegen, hosted product docs, MCP apps/CDN and metadata tools are
explicitly disabled. Usage tracking is disabled with `DO_NOT_TRACK=1` and
`DBT_SEND_ANONYMOUS_USAGE_STATS=false`. This is a local dbt Core configuration, not a fake Cloud
response or a replacement server. The upstream package still includes its normal transitive Cloud
libraries; disabled features are not removed from the distribution.

Runtime caps: 2 CPUs, 2 GiB RAM, 256 PIDs, 256 MiB `/tmp`; all capabilities dropped and privilege
escalation disabled. Build caps use Podman's supported `--cpu-period/--cpu-quota` (2 CPUs), 4 GiB,
and `nproc=256` ulimit (the build backend lacks a cgroup PID-limit flag). dbt SQL/Jinja and adapter
code execute inside this container: use trusted projects and isolate output volumes. The container
runs as container root, mapped by rootless Podman to the invoking user. These settings are a local
example, not a claim that arbitrary projects are safe.

## Dependencies and licenses

See [LICENSES.md](LICENSES.md). The uv and Python base-image tags are versioned but mutable;
`uv.lock` records package artifact hashes, not an immutable OCI-image digest. Rebuilding at a later
date can change base OS/Python patches. No image is published by this integration.

## Observed verification

On 2026-11-05, rootless Podman 6.1.3 on Linux aarch64 built the image and
`just dbt-mcp-check` passed with runtime networking disabled. The actual installed versions were
`dbt-mcp 2.10.1`, `dbt-core 1.12.6`, `dbt-duckdb 1.11.1`, `duckdb 2.0.0`, `mcp 1.30.0` on Python 3.14.
The check observed exactly four allowed tools, successful seed and model builds, three passing
dbt data tests, and the expected two query rows. The artifact records package versions separately
from MCP `serverInfo.version`, which upstream reports as the MCP SDK version (`1.30.0`).
Other host architectures and real user projects have not been exercised.
