# Open-source power-system packages for the power agent (#162)

What was added to the project, what was not and why. Checked 2026-11-16 by `uv lock` under Python 3.14 (the project's floor), PyPI metadata and the upstream LICENSE files. This is an engineering record, not legal advice.

## Already in the project

pandapower 3.5.4 (BSD-3-Clause), powerio 0.7.2, pypowsybl (x86_64 only, labs extra), HiGHS (`highspy`, MIT, engineering extra), RDFLib and pySHACL (semantic extra). See `pyproject.toml`.

## Added or considered

| Package | Version | Licence (source) | Role | Where | Status |
|---|---|---|---|---|---|
| PyPSA | 1.2.4 | MIT (PyPI classifier; `gh api repos/PyPSA/PyPSA --jq .license.spdx_id`) | optimisation: dispatch, capacity expansion, unit commitment | `power` extra | added, pinned |
| veragridengine (VeraGrid, the renamed GridCal) | 6.8.4 | MPL-2.0 (`gh api repos/SanPen/VeraGrid --jq .license.spdx_id`; PyPI `MPL2`) | second power-flow engine beside pandapower | `power` extra | added; source distribution only |
| OpenDSSDirect.py (with dss-python 0.15.7, dss-python-backend 0.14.5, both BSD-3-Clause) | 0.9.4 | its own LICENSE, not a standard BSD (see below) | distribution-network simulation | `power-opendss` extra, apart from `power` | added in isolation, owner decision pending |
| ANDES | 2.0.0 | GPL-3.0-or-later (PyPI) | dynamic simulation, stability | own container, not in the project environment | not added yet |
| Julia PowerModels (lanl-ansi) | n/a | LANL licence: BSD-3-clause terms plus a US Government rights notice (`gh api repos/lanl-ansi/PowerModels.jl/contents/LICENSE.md`) | optimal power flow | not Python, needs a Julia runtime | not installed |

## What each choice rests on

- **PyPSA is held at 1.2.4, not the latest 1.4.0.** `uv lock` with 1.4.0 failed: "pypsa>=1.4.0 depends on pandas>=3.0" against this project's `pandas==2.3.3`, which is pinned so the labs reproduce their recorded evidence. PyPSA 1.2.4 asks for `pandas>=2.0`, has a 3.14 classifier and a pure-Python wheel. Lifting the pandas pin would change the lab environment and is not done here.
- **The lock changed only by addition.** Comparing `uv.lock` before and after: 129 packages before, 202 after, 73 added, 0 removed, 0 version changes (pandas 2.3.3, numpy 2.4.6, pandapower 3.5.4, scipy 1.18.0, mcp 1.12.2 and pydantic 2.13.4 unchanged). Of the new packages, `veragridengine` is the only one without a wheel; every other new package with wheels has one for x86_64 and for aarch64 or is `none-any`.
- **GridCal is now VeraGrid.** Importing the old `GridCalEngine` 5.4.1 printed: "GridCal has changed name to VeraGrid ... The gridcal package cannot be updated anymore". The extra therefore uses `veragridengine` 6.8.4 (repo last pushed 2026-11-16). It builds from source (no wheel on PyPI) and its classifiers stop at Python 3.10, yet it resolved and imported under 3.14.
- **A dead end, recorded.** With `GridCalEngine==5.4.1` the import failed on Python 3.14 because pulp 4.2.0 no longer has `LpStatusOptimal`; holding pulp below 4 fixed it, and the package was then replaced by `veragridengine`, which imports with pulp 4.2.0 and needs no pin.
- **OpenDSSDirect.py licence.** PyPI classifies it "Other/Proprietary" and GitHub reports `NOASSERTION`. The LICENSE text (`gh api repos/dss-extensions/OpenDSSDirect.py/contents/LICENSE`) is BSD-style with extra clauses. Clause 3: "The entire corresponding source code of any redistribution, with or without modification, by a research entity, including but not limited to any contracting manager/operator of a United States National Laboratory, any institution of higher learning, and any non-profit organization, must be made publicly available under this license for as long as the redistribution is made available by the research entity." Clause 4 constrains naming of modified versions. This project is a joint CSIRO and AEMO effort, so an image or kit that bundles it and is distributed by such an entity may fall under clause 3. That is a question for the owner and the partners' counsel, not decided here. Keeping it in its own extra means no default install and no image target pulls it in: `containers/Containerfile` installs `--extra labs --extra hub` only.
- **ANDES is GPL-3.0-or-later and its classifiers stop at 3.13.** Following the NEMOSIS precedent (GPL-3.0, isolated in its own container, never imported by wattson code), it is not put in the project environment. The container is a follow-up.
- **PowerModels** is Julia code, so it cannot be a uv extra. Recorded only.

## Verification

Commands run for this record: `uv lock` (project, with each extra), `uv lock --check`, and a before and after comparison of `uv.lock`. pi ran `UV_PROJECT_ENVIRONMENT=<scratch> uv sync --frozen --extra power` then imported the packages: rc=0 and rc=0 on aarch64 (`uname -m` printed `aarch64`), CPython 3.14.8, printing `pypsa 1.2.4 pulp 4.2.0 pandas 2.3.3 numpy 2.4.6 pandapower 3.5.4 VeraGridEngine imported`. Installing built `veragridengine` and `wattson` from source and took 2 min 7 s for the 9 packages it prepared. The `fmpy` package printed a warning that its bundled native logger (`logging.so`) is not built for aarch64; the import still succeeded. This proves installation and import only: no PyPSA or VeraGrid study was run. x86_64 was not checked.

## Open items

1. Owner decision on OpenDSSDirect.py clause 3 before any image carries it.
2. ANDES container (separate PR).
3. Whether to lift `pandas==2.3.3` so PyPSA 1.4 and later can be used (changes the lab evidence environment).
4. x86_64 install and import of the `power` extra (aarch64 is checked); and a real study with PyPSA and with VeraGrid, which no one has run yet.

## VeraGrid against the pandapower path (cross-check, 2026-11-18)

`scripts/veragrid_crosscheck.py` reads one MATPOWER case in both solvers, scales every load by the same factor, runs each one's Newton-Raphson and compares bus voltage magnitudes by absolute per-unit difference. pi ran it on `snemSA.m` (503 buses) with `veragridengine` 6.8.4 and the pinned pandapower, in the scratch venv from `uv sync --frozen --extra power`, aarch64, CPython 3.14:

| scale | max abs dV | mean abs dV | bus 2008 pandapower | bus 2008 VeraGrid |
|---|---|---|---|---|
| 1.0 | 1.170e-12 | 1.232e-13 | 0.9348006352290091 | 0.9348006352291675 |
| 0.909253 | 2.930e-12 | 2.327e-13 | 0.9422000193653158 | 0.9422000193656556 |

Both solvers converged. The pass tolerance is 1e-4 pu absolute (solvers do not agree to the last digit, and the margin over the measured 3e-12 is large on purpose). pi also ran the 7 tests in `tests/test_veragrid_crosscheck.py`: all passed.

Limits: one case, base load and one scaled load only (outage, loading and dispatch are in the next section); buses are matched by position (a count mismatch is an error, an order mismatch would show as a large difference); x86_64 not checked. This shows VeraGrid can serve as a second solver for `load_scale` studies on this case; it does not change the power tools, which still use pandapower.

## Outage, branch loading and dispatch (2026-11-18)

`scripts/veragrid_studies.py` runs the same inputs through both solvers on `snemSA.m` (503 buses, 698 branches) and compares bus voltages and per-branch apparent power (MVA, at the lower-numbered end, matched by bus pair). 15 runs: the base case, 12 single-branch outages (the 6 most heavily loaded branches that have no parallel circuit, then 6 seeded random ones), generator output x1.1, and the largest generator +25 MW. pandapower side: `wattson.gridfit.load_case` and `_runpp_counted`; VeraGrid 6.8.4 Newton-Raphson, a fresh grid per run.

Result: 15/15 pass. Both solvers converged in every run. Max bus-voltage difference 4.1e-8 pu (outage 101-157; every other run is 6e-12 pu or less). Max branch-flow difference 6.8e-5 MVA, on the same branch (222-400) in every run including the base case, so it is a constant model difference on that one branch (probably a transformer magnetising or tap detail), far under the 1e-3 MVA tolerance and not caused by any study.

Loading: the case's own ratings are all the 10000 MVA placeholder, so no real rating exists. The check uses a derived limit (90% of the base-case 90th-percentile flow, 71.7 MVA, a deliberately tight one) and requires the same set of overloaded branches from both solvers. They matched in all 15 runs (58 to 81 branches over the limit, changing with the outage, so the check can tell cases apart).

Finding, not a failure: two outages (203-204 and 92-103) cut off a small group of buses (13 and 12) from the slack. pandapower reports those buses as NaN (de-energised). VeraGrid reports numbers for them (a mix of solved and setpoint-looking values). That is a policy difference in handling islands, not a solver error; the comparison excludes those buses and counts them, and the energised network agreed to 2e-12 pu. Anyone using VeraGrid for outage screening must treat islanded buses explicitly rather than trust its numbers there.

Limits: one case; N-1 on 12 of the 295 or so unique branches, not the full set; generator limits and reactive-power limits are not tested (no run hit a limit that was checked); optimal power flow and dynamic studies are not covered; derived rather than real ratings; x86_64 untested.
