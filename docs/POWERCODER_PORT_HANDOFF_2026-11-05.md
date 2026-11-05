# PowerCoder port into Wattson — 2026-11-05

Wattson owns local model training, tool protocols, engineering studies, evaluation,
and the associated tests. The NEM lab remains a consumer and teaching repository.
This port preserves newer Wattson implementations while moving missing capabilities
into its package. Historical source under `migration/powercoder/source` and
`docs/history/lab-pr59` is an exact, non-executable preservation archive.

Canonical interfaces are `wattson` (CLI), `wattson.mcp_server` (tool service),
`src/wattson` (Python imports), and the root justfile (container workflows).
The `local-runtime` subtree remains an experimental IPC/HF/GenCost profile.
Separate service integrations have their own locked environments: PLEXOS XML,
ODBC MCP and dbt MCP. Unifying their user entry points is a release integration task.

## Verified before consolidation

- CPU port: 400 passed, 11 skipped on Python 3.14.8. Skips cover Torch,
  live teacher services, optional chart dependencies and RestoreBench fetch/slow proof.
- After the final launcher quoting correction: eight boundary/regression checks passed;
  an independent reviewer verified actual Podman regex arguments.
- Rust command IR passed locked offline tests.
- Developer image built; 15 checks passed with networking disabled and without
  the legacy repository mounted. The final launcher quoting fix was subsequently
  verified in source; that image was not rebuilt or published after the fix.
- Four operator-approved external data inputs were hash-verified into `.artifacts`.
  They are excluded from Git, wheels and container software layers.

The adversarial review found task leakage across dataset splits, timezone lookup
failure and a shell boundary dropping regex escapes. Grouped splitting, UTC-normalized
lookup and quoted final shell arguments address those findings, with regressions.

## Remaining release gates

The merged composition needs a clean checkout check and shared command contract.
GPU training, selected-model inference/tool performance, disconnected package
restore/repackage and a complete redistributable SBOM are not proven by CPU tests.
Legacy inference launch defaults still require a hardware-specific resource profile;
the new SFT launcher is bounded, but this is not shared-node deployment approval.
Do not infer national-grid operational certification from public-case benchmark proofs.

Service PRs: [PLEXOS #1](https://github.com/fungible-farm/wattson/pull/1),
[ODBC #2](https://github.com/fungible-farm/wattson/pull/2),
[dbt #3](https://github.com/fungible-farm/wattson/pull/3).
Actual Azure access, remote ODBC credentials/TLS/grants and arbitrary client files
remain untested. dbt's mandatory vortex/uuid licensing is unresolved: source recipes
are optional and no redistribution approval or published service image is claimed.

Lab PR59 was scoped to lab-only changes at `14af15c`; its original head is preserved
on `archive/pr59-before-wattson-20261105`. PR57 already descends from the current
lab main and retains all Windows work; no Windows execution is claimed here.

On resumption the configured neo-coder endpoint refused connection. Its existing
sm3lly systemd service was found inactive on an idle GPU and started for the requested
pi review. Report readiness and actual review evidence separately; service activation
alone is not an inference or tool-calling proof.
