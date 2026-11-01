"""Procedural load-scale fit tasks across networks, for G3's first RL pilot (backlog 0017).

0017's cheapest first step: "a GRPO pilot on the r6 Qwen student over generated load-scale fits
drawn from two networks, scored on calls to reach tolerance against bisection". Today's training
and evaluation fits all come from one network (0008). This draws fits from ``POOL_NETWORKS``:
snem-sa-2000 plus pandapower's IEEE 118 and PEGASE 89 cases. ``HELD_OUT_NETWORKS`` (snem-1803,
IEEE 39) never enter a pool, so evaluating on them tests whether a skill transfers (r7's
judgement and unsolvable suites hold out snem-1803; 2026-10-24 caveat). Elements are
*discovered* per network:

- a load-scale fit scales every load and observes one bus voltage, so the bracketing needs the
  voltage to move monotonically in scale. Discovery solves at ``DISCOVERY_SCALES`` and keeps
  buses whose voltage is strictly monotone across them with at least ``MIN_SPAN_PU`` between
  the interval ends;
- task targets, tolerances and references then come from ``generate._gen_fit_example``, the same
  real-bisection reference the benchmark uses, with the prompt in wording v2;
- every ``OPEN_INTERVAL_EVERY``-th fit states no interval (the judgement suite's open-interval
  variant), so the bracket must come from the tool's declared limits.

Seeds are this module's own (``PROCEDURAL_SEED``). ``main`` audits a pool against every suite in
``EVALUATION_SUITES`` that exists (``audit_rows``) and refuses to freeze an unclean one, so a
training fit can never be an evaluated one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandapower as pp

from wattson.datasets import generate
from wattson.inference import powermcp as pm

PROCEDURAL_SEED: int = 20261024
POOL_NETWORKS: tuple[str, ...] = ("snem-sa-2000", "ieee118", "pegase89")
HELD_OUT_NETWORKS: tuple[str, ...] = ("snem-1803", "ieee39")
assert not set(POOL_NETWORKS) & set(HELD_OUT_NETWORKS), "a held-out network must never be trained on"
NETWORKS: tuple[str, ...] = POOL_NETWORKS
OPEN_INTERVAL_EVERY: int = 4
# Every CONTINGENCY_EVERY-th fit is posed with one line out (N-1), drawn by seed from lines whose
# outage solves and isolates no bus; at most SAFE_LINES_PER_NETWORK are kept per network.
CONTINGENCY_EVERY: int = 3
SAFE_LINES_PER_NETWORK: int = 20
# Free-text objectives: the fit as an engineer would ask it. Each states the network, outage,
# what is scaled and over what range, the observed bus, the target and the tolerance, so the
# prompt is coherent and solvable; the grader is the same. Every FREE_TEXT_EVERY-th fit, offset
# from the open-interval ones.
FREE_TEXT_EVERY: int = 4
# Operating points (IR 0.3.0 load_level): rating and dispatch fits posed with every load at one of
# these multiples of base, inside the tool's declared [0.9, 1.1]; a load-scale fit has none, since
# it already scales every load. OPERATING_POINT_FITS of each kind per network.
OPERATING_POINTS: tuple[float, ...] = (0.92, 0.96, 1.04, 1.08)
OPERATING_POINT_FITS: int = 10
# A line carries flow worth fitting above this loading; a rating fit moves its percent as 1/s.
MIN_LINE_LOADING_PERCENT: float = 1.0
# A dispatch fit's transformer loading must move at least this much (percent) over DISCOVERY_SCALES.
MIN_TRAFO_SPAN_PERCENT: float = 0.1
FREE_TEXT_TEMPLATES: tuple[str, ...] = (
    "On case '{case}'{outage}, by what factor between {lo} and {hi} must every load's active and reactive demand "
    "be multiplied for the voltage at bus {bus} to reach {target} pu, within {tol} pu? Reply with only that factor.",
    "An engineer wants the voltage at bus {bus} of case '{case}'{outage} at {target} pu (tolerance {tol} pu), by "
    "scaling all demand uniformly by one factor in [{lo}, {hi}]. Find the factor and reply with it alone.",
)
# Frozen suites a pool must not overlap (paths under the main tree; absent ones are skipped).
EVALUATION_SUITES: tuple[str, ...] = (
    "data/suites/domain-stratified-100-20261022/examples.jsonl",
    "data/suites/domain-stratified-100-20261024-v2/examples.jsonl",
    "data/suites/smoke-validation-20-20261022/examples.jsonl",
    "data/suites/smoke-validation-20-20261024-v2/examples.jsonl",
    "data/suites/judgement-20261024-v2/examples.jsonl",
    "data/suites/unsolvable-20261024-v2/examples.jsonl",
)
# The tool's interval ends (studies.SCALE_MIN/MAX) and three interior points.
DISCOVERY_SCALES: tuple[float, ...] = (0.9, 0.95, 1.0, 1.05, 1.1)
# A span this small would need a tolerance below the solver's own noise to be a real fit.
MIN_SPAN_PU: float = 1e-3
# Elements kept per network, the most responsive first.
ELEMENTS_PER_NETWORK: int = 40
PER_NETWORK: int = 50


def discover_load_scale_buses(case: str) -> list[dict[str, Any]]:
    """Buses whose voltage is strictly monotone in load scale with a usable span, as generate expects.

    Returns:
        ``[{"bus", "vm_lo", "vm_hi"}]``, most responsive first, at most ELEMENTS_PER_NETWORK.
    """
    curves: dict[int, list[float]] = {}
    for scale in DISCOVERY_SCALES:
        net, _ = pm._load_network_object(case)
        net.load.loc[:, ["p_mw", "q_mvar"]] *= scale
        pm._runpp_counted(net, numba=True)
        for bus in net.bus.index:
            curves.setdefault(int(bus), []).append(float(net.res_bus.vm_pu[bus]))
    kept = []
    for bus, values in curves.items():
        steps = [b - a for a, b in zip(values, values[1:])]
        monotone = all(s > 0 for s in steps) or all(s < 0 for s in steps)
        if monotone and abs(values[-1] - values[0]) >= MIN_SPAN_PU:
            kept.append({"bus": bus, "vm_lo": values[0], "vm_hi": values[-1]})
    kept.sort(key=lambda e: abs(e["vm_hi"] - e["vm_lo"]), reverse=True)
    return kept[:ELEMENTS_PER_NETWORK]


def safe_outages(case: str) -> list[int]:
    """Lines whose outage converges and de-energizes no bus, in a seeded order, at most SAFE_LINES_PER_NETWORK."""
    import math
    import random

    base, _ = pm._load_network_object(case)
    order = list(base.line.index)
    random.Random(f"{PROCEDURAL_SEED}:{case}:outages").shuffle(order)
    kept = []
    for line in order:
        net, _ = pm._load_network_object(case)
        net.line.loc[line, "in_service"] = False
        try:
            pm._runpp_counted(net, numba=True)
        except (pp.LoadflowNotConverged, FloatingPointError):
            continue
        if all(math.isfinite(float(v)) for v in net.res_bus.vm_pu):
            kept.append(int(line))
        if len(kept) == SAFE_LINES_PER_NETWORK:
            break
    return kept


def discover_rating_lines(case: str) -> list[dict[str, Any]]:
    """Lines carrying at least MIN_LINE_LOADING_PERCENT at base, most loaded first, as generate expects."""
    net, _ = pm._load_network_object(case)
    pm._runpp_counted(net, numba=True)
    loading = net.res_line.loading_percent
    kept = sorted((int(line) for line in net.line.index if float(loading[line]) >= MIN_LINE_LOADING_PERCENT),
                  key=lambda line: -float(loading[line]))
    return [{"line": line} for line in kept[:ELEMENTS_PER_NETWORK]]


def discover_dispatch_pairs(case: str) -> list[dict[str, Any]]:
    """(generator, transformer) pairs whose transformer loading is strictly monotone in the generator's dispatch."""
    base, _ = pm._load_network_object(case)
    pairs = []
    for gen in base.gen.index:
        curves: dict[int, list[float]] = {}
        for scale in DISCOVERY_SCALES:
            net, _ = pm._load_network_object(case)
            net.gen.loc[gen, "p_mw"] *= scale
            try:
                pm._runpp_counted(net, numba=True)
            except (pp.LoadflowNotConverged, FloatingPointError):
                break
            for trafo in net.trafo.index:
                curves.setdefault(int(trafo), []).append(float(net.res_trafo.loading_percent[trafo]))
        for trafo, values in curves.items():
            if len(values) != len(DISCOVERY_SCALES):
                continue
            steps = [b - a for a, b in zip(values, values[1:])]
            if (all(d > 0 for d in steps) or all(d < 0 for d in steps)) and abs(values[-1] - values[0]) >= MIN_TRAFO_SPAN_PERCENT:
                pairs.append({"gen": int(gen), "trafo": trafo, "span": abs(values[-1] - values[0])})
    pairs.sort(key=lambda p: -p["span"])
    return pairs[:ELEMENTS_PER_NETWORK]


def free_text(row: dict[str, Any], template: str) -> str:
    """A free-text prompt for a load-scale fit, rendered from its inputs."""
    inputs = row["task"]["inputs"]
    outage = inputs.get("contingency_line")
    return template.format(case=inputs["case"], outage=f" with line {outage} out of service" if outage is not None else "",
                           lo=inputs["scale_lo"], hi=inputs["scale_hi"], bus=int(inputs["element"].split()[-1]),
                           target=f"{inputs['target']:.6f}", tol=inputs["tolerance"])


def build(per_network: int = PER_NETWORK, networks: tuple[str, ...] = POOL_NETWORKS,
          split: str = "train") -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Verified fits from each network, and the discovered element count per network.

    ``networks=HELD_OUT_NETWORKS, split="test"`` builds the G3 pilot's evaluation pool: the same
    generator, on networks no pool trains on.
    """
    rows, discovered = [], {}
    cache = generate.EndpointCache()
    for case in networks:
        elements = discover_load_scale_buses(case)
        outages = safe_outages(case)
        discovered[case] = len(elements)
        made = 0
        for i in range(per_network * 3):
            line = outages[i % len(outages)] if outages and i % CONTINGENCY_EVERY == CONTINGENCY_EVERY - 1 else None
            row = generate._gen_fit_example("pf-load-scale-fit", case, elements, i, PROCEDURAL_SEED, cache,
                                            contingency_line=line)
            if row["lifecycle"]["status"] == "verified":
                row["lifecycle"]["split"] = split
                if made % OPEN_INTERVAL_EVERY == OPEN_INTERVAL_EVERY - 1:
                    row["task"]["prompt"] = generate.fit_prompt(row["subfamily"], row["task"]["inputs"], interval=False)
                    row["task"]["inputs"]["prompt_variant"] = "open-interval"
                elif made % FREE_TEXT_EVERY == 1:
                    k = (made // FREE_TEXT_EVERY) % len(FREE_TEXT_TEMPLATES)
                    row["task"]["prompt"] = free_text(row, FREE_TEXT_TEMPLATES[k])
                    row["task"]["inputs"]["prompt_variant"] = f"free-text-{k}"
                rows.append(row)
                made += 1
            if made == per_network:
                break
        # Rating and dispatch fits at operating points other than the base load.
        import random

        rng = random.Random(f"{PROCEDURAL_SEED}:{case}:operating-points")
        for subfamily, elements in (("pf-line-rating-fit", discover_rating_lines(case)),
                                    ("pf-gen-dispatch-fit", discover_dispatch_pairs(case))):
            discovered[f"{case}:{subfamily}"] = len(elements)
            made = 0
            for i in range(OPERATING_POINT_FITS * 3):
                if not elements or made == OPERATING_POINT_FITS:
                    break
                row = generate._gen_fit_example(subfamily, case, elements, i, PROCEDURAL_SEED, cache,
                                                load_level=rng.choice(OPERATING_POINTS))
                if row["lifecycle"]["status"] == "verified":
                    row["lifecycle"]["split"] = split
                    rows.append(row)
                    made += 1
    return rows, discovered


def main() -> int:
    """Write a frozen pool with its manifest."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--per-network", type=int, default=PER_NETWORK)
    parser.add_argument("--held-out", action="store_true",
                        help="Build the evaluation pool on HELD_OUT_NETWORKS (split test), audited against --against")
    parser.add_argument("--against", type=Path, action="append", default=[],
                        help="A further frozen pool this one must not overlap (e.g. the rollout pool)")
    parser.add_argument("--suites-root", type=Path, default=generate.REPO_ROOT,
                        help="Checkout holding the frozen evaluation suites (gitignored, so a worktree has none)")
    args = parser.parse_args()
    if args.out_dir.exists():
        raise ValueError("Refusing to overwrite a frozen pool")
    networks, split = (HELD_OUT_NETWORKS, "test") if args.held_out else (POOL_NETWORKS, "train")
    rows, discovered = build(args.per_network, networks, split)
    from wattson.datasets.audit import audit_rows, load_rows

    audits = {}
    for suite in [*EVALUATION_SUITES, *map(str, args.against)]:
        path = args.suites_root / suite
        if path.exists():
            audit = audit_rows(rows, load_rows(path))
            audits[suite] = {"clean": audit["clean"], "overlap_ids": audit["overlap_ids"]}
            if not audit["clean"]:
                raise ValueError(f"Pool overlaps {suite}: {audit}")
    if not audits:
        raise ValueError(f"No evaluation suite found under {args.suites_root}; a pool must be audited")
    args.out_dir.mkdir(parents=True)
    examples = args.out_dir / "examples.jsonl"
    with examples.open("x") as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    by_network = {case: sum(r["topology"] == case for r in rows) for case in networks}
    (args.out_dir / "manifest.json").write_text(json.dumps({
        "pool": "procedural-v4", "seed": PROCEDURAL_SEED, "n_tasks": len(rows), "by_network": by_network,
        "held_out_networks": list(HELD_OUT_NETWORKS), "networks": list(networks), "split": split,
        "audited_against": audits,
        "open_interval": sum(r["task"]["inputs"].get("prompt_variant") == "open-interval" for r in rows),
        "free_text": sum(str(r["task"]["inputs"].get("prompt_variant", "")).startswith("free-text") for r in rows),
        "contingency": sum(r["task"]["inputs"].get("contingency_line") is not None for r in rows),
        "operating_points": sum(r["task"]["inputs"].get("load_level") is not None for r in rows),
        "by_subfamily": {name: sum(r["subfamily"] == name for r in rows) for name in sorted({r["subfamily"] for r in rows})},
        "discovered_elements": discovered, "examples_sha256": hashlib.sha256(examples.read_bytes()).hexdigest()},
        indent=2) + "\n")
    print(json.dumps({"tasks": len(rows), "by_network": by_network, "discovered_elements": discovered}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
