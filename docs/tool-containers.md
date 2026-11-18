# Power tools in their own containers (#162)

Each licence-sensitive tool gets its own image and one job, the way NEMOSIS does (`containers/nemosis`). Wattson code never imports these packages; it reads the JSON they write.

| Image | Package | Licence | Job | Output |
|---|---|---|---|---|
| `containers/andes/Containerfile` | ANDES 2.0.0 | GPL-3.0-or-later | power flow on a case in `/in`, or ANDES's bundled IEEE 14-bus | `/out/andes_pflow.json` |
| `containers/opendss/Containerfile` | OpenDSSDirect.py 0.9.4 | Alliance for Sustainable Energy licence (clause 3 and 4, see below) | solve a DSS circuit in `/in`, or a small built-in feeder | `/out/opendss_solve.json` |

Scripts: `scripts/andes_run.py`, `scripts/opendss_run.py`. Run with `--network none` and a memory cap (`--memory 4g --memory-swap 4g`; sm3lly rejects containers without one).

## What was run (aarch64, shinyDGX, 2026-11-18, by pi)

Both images built with `podman build --memory 4g --memory-swap 4g`, then ran with `--network none`:

```
PASS andes-pflow: n=14 converged=True        (andes 2.0.0, ieee14.raw bundled with ANDES)
PASS opendss-solve: n=2 converged=True       (OpenDSSDirect.py 0.9.4, built-in feeder)
```

The OpenDSS file lists six `vmag_pu` values for two buses: three phases per bus.

## Limits

- Install and one solve each. No cross-check against pandapower or VeraGrid yet (the ANDES IEEE 14-bus result could be compared with pandapower's `case14`; not done), no dynamic study with ANDES, no OpenDSS time series.
- Built on aarch64 only; x86_64 not built. The images use Python 3.12, not the project's 3.14, because they are separate from the project environment.
- ANDES 2.0.0 was chosen because it is the latest on PyPI today; its dependencies are not pinned by a lock (pip resolves them at build), so a rebuild can differ.
- Nothing is published to a registry.

## OpenDSSDirect.py licence

Clause 3 of the OpenDSSDirect.py licence requires that the entire corresponding source of a redistribution by a research entity (which names non-profit organisations) be made publicly available under that licence; clause 4 limits how a modified version may be named. The project is a joint CSIRO and AEMO effort, so the `opendss` image is built and used locally only, and is not to be pushed until the partners' counsel has read the licence. This note records the clause, not a legal conclusion.
