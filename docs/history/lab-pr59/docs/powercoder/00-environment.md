# NEM PowerCoder — Environment Qualification (M00)

**Date:** 2026-10-17
**Node:** shinyDGX.lan (GB10, 128GB unified memory)
**Repository:** fungible-farm/nem-poweragent-lab
**Baseline commit:** 4f41c756 (`docs(systhread): scope v2 SysML KerML visualization (#53)`)

## Hardware

| Component | Specification |
|-----------|---------------|
| GPU | NVIDIA GB10 (DGX Spark) |
| Memory | 128 GB unified (memory.total reported as [N/A] by nvidia-smi) |
| Architecture | aarch64 |
| OS | Linux 7.0.0-1019-nvidia |

## Software Stack

| Tool | Version |
|------|---------|
| Python | 3.12.3 |
| uv | 0.12.23 |
| Podman | 6.1.3 |
| pandapower | 3.5.4 |
| powerio | 0.7.2 |
| mcp | 1.12.2 |

## Platform-Specific Adjustments

Two dependencies were excluded on aarch64 Linux due to missing wheels:

- `pypowsybl>=1.16.1` — no `manylinux_2_39_aarch64` wheel; only x86_64, macOS, and Windows available
- `dpsim>=1.2.1` — builds from source but fails on aarch64 due to CMake configuration

Both were made conditional in `pyproject.toml` using platform markers. Corresponding tests were marked `skipif` on aarch64 Linux.

## Lab Test Status

Core labs (1–3) pass on this platform:

```
9 passed, 2 skipped in 24.50s
```

Skipped tests: `test_pypowsybl_n1_cross_check_matches_fixture` and `test_pypowsybl_spike_matches_fixture` (pypowsybl unavailable on aarch64).

## Diagnostic Command

```bash
python3 scripts/powercoder-doctor.py
```

Exit code 0 = all checks passed; 1 = one or more failures.

## Model Registry (Initial)

| Model | Role | License | Status |
|-------|------|---------|--------|
| Qwen3.8-Flash-Next | Primary teacher | Qwen Community License 1.0 | To be qualified |
| Qwen3.8-27B | Independent teacher / quality challenger | Apache 2.0 | To be qualified |
| Qwen3.5-4B | Windows student | Apache 2.0 | To be qualified |
| Qwen2.5-Coder-1.5B | Qualcomm Q6A student | Apache 2.0 | To be qualified |
| IBM Granite 4.2-3B | Independent laboratory challenger | Apache 2.0 | To be qualified |

## Next Steps

- M01: Set up Qwen3.8-Flash-Next teacher via UltraFast v16b recipe and llama-swap routing
- M02: Establish PowerMCP canonical execution contract
