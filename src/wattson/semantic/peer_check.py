"""Check the CGMES export against pypowsybl, an independent CGMES implementation (backlog 0034).

pypowsybl publishes no Linux ARM64 wheel and no sdist, so its half runs on an x86-64 peer
(``scripts/cgmes_peer_check.sh``, ``just powercoder::cgmes-peer-check``). Three steps:

    export   (here)   solve snemSA with pandapower; write EQ/TP/SSH as a CGMES zip and the
                      pandapower bus voltages by name
    check    (peer)   pypowsybl imports the zip, solves AC, writes element counts, status and
                      bus voltages by name (needs ``pypowsybl``; nothing else from this repo)
    compare  (here)   per-bus |dV| between the two solvers; exit 1 above the tolerance
    diagram  (peer)   powsybl-diagram SVGs from the same zip: a single-line diagram of the
                      inferred substation with the most voltage levels, and a network-area
                      diagram around it (backlog 0004's symbolic single-line tier)

Measured 2026-10-23 (pypowsybl 1.16.1 on sm3lly): every element imported (503 buses, 295
lines, 403 transformers, 57 generators, 186 loads, 782 shunts), OpenLoadFlow converged,
max |dV| 1.38e-5 pu over all 503 buses (mean 5.1e-7).
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

CASE: str = "snem-sa-2000"
# Gate on the cross-solver agreement: measured max 1.38e-5 pu (2026-10-23), so 1e-4 pu
# fails a real modelling regression (a dropped tap changer gave 0.275 pu) but not solver noise.
VOLTAGE_TOLERANCE_PU: float = 1e-4
# pypowsybl names a bus "<topological node name>_<index within its voltage level>".
BUS_SUFFIX: re.Pattern[str] = re.compile(r"_\d+$")


LAB5_CASE: str = "lab5-chaosnet"


def load_named(case: str, network_path: Path | None = None):  # noqa: ANN201 -- lazy pandapower import
    """Load a served network or an explicit pandapower JSON artifact.

    Lab-specific topology conversion belongs to the producing application. Passing
    its exported network artifact avoids importing code from another checkout.
    """
    if network_path is not None:
        import pandapower as pp

        return pp.from_json(str(network_path))
    if case == LAB5_CASE:
        raise ValueError("lab5-chaosnet requires --network-json from its producing application")
    from wattson.inference import powermcp as pm

    return pm._load_network_object(case)[0]


def export(zip_path: Path, voltages_path: Path, case: str = CASE, network_path: Path | None = None) -> dict[str, int]:
    """Solve the case, write its CGMES profiles and pandapower's bus voltages by name."""
    import pandapower as pp

    from wattson.semantic.cgmes import solved_injections, solved_reactive_power, split_profiles
    from wattson.semantic.export import export_network

    net = load_named(case, network_path)
    pp.runpp(net, numba=True)
    docs = split_profiles(export_network(net, case), case,
                          initial_q=solved_reactive_power(net, case) if len(net.gen) else None,
                          initial_injection=solved_injections(net, case))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for profile in ("EQ", "TP", "SSH"):
            buffer = io.BytesIO()
            docs[profile].serialize(buffer, format="xml")
            archive.writestr(f"{case}_{profile}.xml", buffer.getvalue())
    voltages_path.write_text(json.dumps({str(net.bus.name[b]): float(net.res_bus.vm_pu[b]) for b in net.bus.index}))
    return {profile: len(doc) for profile, doc in docs.items()}


def check(zip_path: Path, out_path: Path) -> dict:
    """Import with pypowsybl, solve AC, and record counts, status and voltages (run on the peer)."""
    import pypowsybl
    import pypowsybl.loadflow as lf
    import pypowsybl.network as pn

    net = pn.load(str(zip_path))
    kinds = ("buses", "lines", "2_windings_transformers", "generators", "loads", "shunt_compensators")
    result = {"pypowsybl": pypowsybl.__version__,
              "counts": {kind: len(getattr(net, f"get_{kind}")()) for kind in kinds},
              "loadflow": [str(component.status) for component in lf.run_ac(net)]}
    generators = net.get_generators()
    result["voltage_regulating_generators"] = int(generators.voltage_regulator_on.sum())
    buses, levels = net.get_buses(), net.get_voltage_levels()
    result["vm_by_name"] = {str(bus["name"]): float(bus.v_mag / levels.loc[bus.voltage_level_id, "nominal_v"])
                            for _, bus in buses.iterrows()}
    out_path.write_text(json.dumps(result, indent=1))
    return result


def compare(voltages_path: Path, peer_path: Path, tolerance: float = VOLTAGE_TOLERANCE_PU) -> dict:
    """Per-bus |dV| between pandapower and pypowsybl; ``passed`` when every bus is within tolerance."""
    ours = json.loads(voltages_path.read_text())
    peer = json.loads(peer_path.read_text())
    differences = {BUS_SUFFIX.sub("", name): abs(v - ours[BUS_SUFFIX.sub("", name)])
                   for name, v in peer["vm_by_name"].items() if BUS_SUFFIX.sub("", name) in ours and v == v}
    worst = max(differences.items(), key=lambda item: item[1]) if differences else (None, float("inf"))
    return {"counts": peer["counts"], "loadflow": peer["loadflow"],
            "voltage_regulating_generators": peer.get("voltage_regulating_generators"),
            "buses_compared": len(differences), "buses_in_case": len(ours),
            "max_abs_dv_pu": worst[1], "worst_bus": worst[0],
            "mean_abs_dv_pu": sum(differences.values()) / len(differences) if differences else None,
            "tolerance_pu": tolerance,
            "passed": len(differences) == len(ours) and worst[1] <= tolerance
                      and all(status.endswith("CONVERGED") for status in peer["loadflow"])}


def diagram(zip_path: Path, out_dir: Path, depth: int = 2) -> dict:
    """Render a single-line and a network-area diagram with pypowsybl (run on the peer), labelled by name."""
    import pypowsybl
    import pypowsybl.loadflow as lf
    import pypowsybl.network as pn

    net = pn.load(str(zip_path))
    lf.run_ac(net)
    levels = net.get_voltage_levels()
    counts = levels.groupby("substation_id").size().sort_values(ascending=False)
    substation = counts.index[0]
    out_dir.mkdir(parents=True, exist_ok=True)
    net.write_single_line_diagram_svg(substation, str(out_dir / "substation_sld.svg"),
                                      parameters=pn.SldParameters(use_name=True))
    level = levels[levels.substation_id == substation].index[0]
    if int(counts.iloc[0]) == 1:
        # No transformers (e.g. Lab 5's radial MV net): each bus is its own substation, so draw it all.
        net.write_network_area_diagram_svg(str(out_dir / "area.svg"))
    else:
        net.write_network_area_diagram_svg(str(out_dir / "area.svg"), voltage_level_ids=[level], depth=depth)
    return {"pypowsybl": pypowsybl.__version__, "substation": str(substation),
            "voltage_levels_in_substation": int(counts.iloc[0]), "depth": depth,
            "files": {p.name: p.stat().st_size for p in out_dir.glob("*.svg")}}


def main() -> int:
    """Run one step (see the module docstring)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    steps = parser.add_subparsers(dest="step", required=True)
    e = steps.add_parser("export")
    e.add_argument("--zip", type=Path, required=True)
    e.add_argument("--voltages", type=Path, required=True)
    e.add_argument("--case", default=CASE, help="a served network id or the artifact's descriptive name")
    e.add_argument("--network-json", type=Path, help="explicit pandapower JSON exported by another application")
    c = steps.add_parser("check")
    c.add_argument("--zip", type=Path, required=True)
    c.add_argument("--out", type=Path, required=True)
    d = steps.add_parser("diagram")
    d.add_argument("--zip", type=Path, required=True)
    d.add_argument("--out-dir", type=Path, required=True)
    m = steps.add_parser("compare")
    m.add_argument("--voltages", type=Path, required=True)
    m.add_argument("--peer", type=Path, required=True)
    args = parser.parse_args()
    if args.step == "export":
        print(json.dumps(export(args.zip, args.voltages, args.case, args.network_json)))
        return 0
    if args.step == "diagram":
        print(json.dumps(diagram(args.zip, args.out_dir)))
        return 0
    if args.step == "check":
        result = check(args.zip, args.out)
        print(json.dumps({k: v for k, v in result.items() if k != "vm_by_name"}))
        return 0
    report = compare(args.voltages, args.peer)
    print(json.dumps(report, indent=1))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
