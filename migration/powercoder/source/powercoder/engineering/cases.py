"""Networks with real limits and costs for G2/G3 tasks, with provenance (backlog 0023, proposal 1).

snemSA has no real branch ratings, one generator cost and a single slack, so OPF and
thermal tasks on it carry no signal (0023). These cases are read from checkouts already
fetched into the gitignored ``data/`` (Lab 10's PowerAgentBench pin), never vendored. They
are kept out of the evaluation's ``powermcp.CASE_FILES``: adding a network id changes the
tool schema's enum and with it every prompt.

Measured 2026-10-23 with ``powercoder.engineering`` (DC OPF cross-checked by the
independent LP or QP; exhaustive AC N-1), recorded per case in ``CASES``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import pandapower as pp

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
PAB: str = "data/poweragentbench"


class CaseRecord(TypedDict):
    """Where a case comes from, under what terms, and what it can measure."""

    path: str
    source: str
    licence: str
    measured: str


CASES: dict[str, CaseRecord] = {
    "ieee39-pab-stressed": {
        "path": f"{PAB}/cases/case39/pandapower/case39.json",
        "source": "PowerAgentBench Steady Level 1's stressed IEEE 39-bus case (commit b39c48b)",
        "licence": "no root licence; used as MIT by user direction 2026-10-23, an assumption (docs/PSCADOSSE.md)",
        "measured": "OPF and thermal signal: DC OPF solved, LP/QP cross-check agrees; 1 flow limit and 8 "
                    "generator limits bind; LMPs 11.5-36.0 EUR/MWh. N-1: 31 of 46 outages add a thermal "
                    "violation (max loading 175%). Generator costs are identical across units.",
    },
    "ieee118-restorebench": {
        "path": f"{PAB}/benchmarks/steady/level_3/dataset/ieee118/IEEE118_BASE_CASE.json",
        "source": "RestoreBench (PowerAgentBench Level 3) IEEE 118-bus base case, The RestoreBench Authors",
        "licence": "CC BY 4.0 (dataset/LICENSE)",
        "measured": "Cost signal only: varied quadratic costs (2 linear, 20 quadratic coefficients); DC OPF solved, "
                    "QP cross-check agrees, but only p_min binds and LMPs are flat (39.38 EUR/MWh). Ratings are "
                    "16.6-41.4 kA (effectively unlimited): N-1 adds no thermal violation (max 5.3%).",
    },
    "pegase89-restorebench": {
        "path": f"{PAB}/benchmarks/steady/level_3/dataset/pegase89/PEGASE89_BASE_CASE.json",
        "source": "RestoreBench (PowerAgentBench Level 3) PEGASE 89-bus base case, The RestoreBench Authors",
        "licence": "CC BY 4.0 (dataset/LICENSE)",
        "measured": "Stressed: DC OPF infeasible (HiGHS certifies); the base AC case already has 16 thermal and "
                    "21 voltage violations; 101 of 210 ratings are the 99999 kA placeholder. N-1: 31 of 210 "
                    "outages add a thermal violation.",
    },
}


def load_case(case_id: str) -> pp.pandapowerNet:
    """Load one recorded case.

    Raises:
        KeyError: for an id not in CASES.
        FileNotFoundError: if its checkout is not fetched (``just lab10-fetch``).
    """
    path = REPO_ROOT / CASES[case_id]["path"]
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `just lab10-fetch`")
    return pp.from_json(str(path))
