# Local Excel MCP access and issue 4

The optional integration uses the actual MIT-licensed
[haris-musa/excel-mcp-server 2.2.6](https://github.com/haris-musa/excel-mcp-server/tree/v2.2.6),
release commit `7cc548b6a607c2441884db61205e4aac55642c58`, with openpyxl 3.1.5. Its independent
`integrations/excel-mcp/uv.lock` pins all package artifacts. It adds no baseline runtime dependency.
The small entrypoint supplies upstream `Settings(read_only=True, tool_areas=())`, exposing exactly
`describe_workbook`, `describe_sheet`, and `read_range`; it does not replace the upstream parser/tools.

## Use

Host requirements: Podman and Bash. Building downloads versioned Python/uv images and locked PyPI
packages; runtime has no network. From any directory:

```bash
/absolute/wattson/integrations/excel-mcp/run.sh build
/absolute/wattson/integrations/excel-mcp/run.sh stdio /absolute/path/to/model.xlsm
/absolute/wattson/integrations/excel-mcp/run.sh check /absolute/path/to/model.xlsm /absolute/evidence-dir
/absolute/wattson/integrations/excel-mcp/run.sh test
```

`serve` aliases `stdio`, `proof` aliases `check`; `diagnose WORKBOOK` runs the streaming parser probe.
The `excel-mcp` skill lives in `skills/excel-mcp/SKILL.md`. If the repository's unified launcher is
present, it routes `wattson service excel-mcp build|check|stdio` to this script with the same arguments.
A client accepting standard MCP configuration can use:

```json
{"mcpServers":{"excel":{"command":"/absolute/wattson/integrations/excel-mcp/run.sh","args":["stdio","/absolute/path/to/model.xlsm"]}}}
```

For a client expecting VS Code's configuration, use top-level `servers` instead of `mcpServers`.
These are client configuration dialects, not alternative MCP protocols. Do not allocate a terminal;
stdio stdout is reserved for MCP. No credential or cloud account is needed.

The workbook is mounted alone, read-only, at `/input/<original basename>`. Other directory files,
the host home and credentials are not mounted. The server has a read-only root filesystem, UID
65532, no capabilities, no privilege escalation, 2 CPU/2 GiB/128 PID limits and 256 MiB temporary
space. Inputs must be readable by that UID; use a dedicated copy with appropriate SELinux labels
on SELinux systems (the launcher does not relabel originals). Build limits are 2 CPUs/2 GiB and a
256-process ulimit. Base-image tags are versioned but mutable; digest pinning remains a followup.
No container or package publication is performed by this integration.

Example tool calls for a mounted `model.xlsm`:

```json
{"name":"describe_workbook","arguments":{"path":"model.xlsm"}}
{"name":"read_range","arguments":{"path":"model.xlsm","sheet":"Approval sheet","range":"A1:C3","mode":"formulas","max_cells":9}}
```

Upstream `describe_workbook` serializes all sheet metadata/names; it is not paginated. On large
books, select the relevant name once and use explicit bounded ranges. `describe_sheet` can invoke
a whole-workbook load and fail even for a small selected sheet. `values` mode may calculate missing
formula caches with the upstream calculator; `formulas` mode returns formula text without that
recalculation. No result here claims Excel-equivalent evaluation of the entire ISP workbook.

## Exact issue-4 diagnosis

The supplied [issue attachment](https://github.com/fungible-farm/wattson/issues/4) was downloaded
locally into ignored `out/excel-issue4/workbook.xlsm`; the file is not vendored or uploaded.
Its SHA-256 is `e9c0957510783d81d9c9dd0714c3810e81383ff98a1b9f159222cee8bd5db880`,
size 25,117,824 bytes, 374 ZIP members, 181,328,302 uncompressed bytes. ZIP CRC validation passed;
workbook, styles and content-type XML parsed successfully. It contains 85 sheets. No VBA project
member was present in these exact bytes, despite the `.xlsm` filename.

Both streaming and full openpyxl 3.1.5 loads succeeded on the original. Full loading warned:
`Data Validation extension is not supported and will be removed`. Nothing was saved; a load/save
conversion could lose those extensions and is deliberately not this compatibility pathway.

The **actual MCP failure reproduced** is `describe_sheet` for `Approval sheet`:

> The workbook has more than 3,000,000 cells and rows to load, which would use too much memory.

Its response recommends `read_range`. Upstream counts rows/cells plus cells covered by merged or
hyperlink ranges across the workbook; this file measures **4,395,232** budget units against the
3,000,000 default. The load budget is independent of ZIP validity.
This does not establish the cause of any prior unspecified failure, but it is the exact failure
reproduced in the pinned server. Do not label the original file malformed.

The compatible pathway is the same original bytes through MCP `read_range` in streaming/formula
mode. The observed `Approval sheet!A1:C3` begins `Tab`, `Inputs`, `Responsible team`; the next row
identifies `ONSG`. The check independently asserts these known issue-4 cells when its input hash
matches the recorded attachment. Other workbooks receive a generic first-sheet range check.

The proof reports `PASS_STREAMING_READ_WITH_METADATA_LIMIT` while retaining the full metadata
error separately. It asserts initialization, the exact tool set, 85-sheet enumeration for these
bytes, the bounded actual range, an outside-path refusal, a kernel read-only file mount, and
identical source hashes before/after. This is a successful streaming read, **not a successful
`describe_sheet` or workbook recalculation**. Proof JSON is published atomically only after those
checks; a failed run removes the old proof and leaves no stale successful output. `check`'s default
evidence directory is ignored `out/excel-mcp-check`; a supplied evidence directory receives only
`excel-proof.json`. Concurrent host edits to the mounted input are not a snapshot guarantee; use a
stable dedicated copy for reproducible work and retain its hash.

## Verification and licences

`test` runs three clearly synthetic regressions: numeric row order and literal formula fidelity,
source byte preservation, path/symlink confinement, and malformed-ZIP rejection. These synthetic
inputs are not substituted for the actual issue attachment proof. The real workbook proof and
parser probes run offline on Linux aarch64 with caps; other architectures, workbook editing and
full formula recalculation are not validated.

Direct dependencies: excel-mcp-server and openpyxl are MIT; MCP Python SDK is MIT. Pillow 13.0.0 declares MIT-CMU and retains bundled component notices. Python is PSF-2.0; uv is MIT OR Apache-2.0; base OS packages
retain their own notices. See `integrations/excel-mcp/dependency-licenses.json` for installed package
metadata and the lock for exact artifacts. Licence files remain installed in the image.
