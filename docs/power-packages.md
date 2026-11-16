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
