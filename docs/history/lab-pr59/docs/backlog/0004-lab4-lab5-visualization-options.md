# 0004 — Lab 4 & Lab 5: visualization options

- **Status:** **done** 2026-10-23. Lab 4's chart and Lab 5's topology drawing were done earlier. The
  stretch tier's gate (a CIM/CGMES export) is met (0034), and powsybl-diagram renders real symbolic
  diagrams for snemSA and for Lab 5's chaos-net (below), as an optional extension like Lab 5's
  hardware tier.
- **Depends on:** 0001 (gap), 0002 (options research)

## Lab 4 — `labs/04-aemo-digital-twin-reconciliation/`

**Status: done.** `reconcile.py` compares real AEMO dispatch against the modelled power flow and
writes a markdown memo. The obvious chart is a modelled-vs-actual time series across the day's
5-minute dispatch intervals (or a scatter of modelled-vs-actual per generator/interconnector, if
the intervals aren't the more legible axis) — but `expected_reconciliation.json` is a single
dispatch interval's result, not a day-long series, and pulling many intervals' worth of NEMOSIS
data was out of scope for this fix.

**Recommended option (free tier):** `matplotlib`, reading straight from the already-committed
`expected_reconciliation.json` — same pattern as 0003. Add as a `--step chart` (or fold into the
existing memo step, emitting a PNG alongside the markdown memo) on `reconcile.py`.

**Also available for free:** `pandapower.plotting.pf_res_plotly()` on the network itself, colored
by voltage/loading from the reconciled power flow — this is the one place in the repo where
`pandapower`'s own results-overlay plotting (0002 §a) is a closer fit than a generic bar/line
chart, since Lab 4 already has a solved pandapower network in hand, not just tabular results. Not
implemented here: `plotly` is not a dependency in `pyproject.toml` (only `matplotlib`/`networkx`
are), and this fix was scoped to the free, no-new-dependency matplotlib option.

Implemented: `reconcile.py` now has a `_plot_reconciliation()` helper -- a grouped bar chart of the
two number-pairs the printed reconciliation memo already discusses (modelled-vs-actual
interconnector-equivalent flow, synthetic-vs-actual whole-network losses), each bar value-labeled,
titled with the reconciled date/interval. Folded into the default `--step run` (not a separate
`--step chart`) since the write is already gated on `date == LAB4_DATE` — the same
`refresh_chart: bool = True` gate pattern as Lab 5's `generate_topology.py`
`seed == FIXTURE_SEED and refresh_fixtures`, so an ad-hoc `--date` run or the optional Part C run
(`--date 2016-09-28`) never overwrites the committed chart. Output:
`sample_reconciliation_chart.png`, committed alongside `expected_reconciliation.json`.
`check_step()` calls `reconcile(LAB4_DATE, verbose=False, refresh_chart=False)` and separately
asserts the committed PNG exists (mirroring Lab 5's `SAMPLE_..._FILE.exists()` pattern), so
`test_lab4.py::test_lab4_reconciliation_matches_fixture` covers it (confirmed by temporarily
removing the PNG and re-running the test, which failed with `FAIL: no chart at ...`, then
restoring it). Colors (`#2a78d6` modelled/synthetic, `#e34948` actual) are the same validated pair
as Lab 5's `TOPOLOGY_BUS_NODE_COLOR`/`TOPOLOGY_TAP_NODE_COLOR`, re-validated for this chart type via
the dataviz skill's `validate_palette.js` (ALL CHECKS PASS in both light and dark mode). See
`labs/04-aemo-digital-twin-reconciliation/README.md`'s walkthrough step 3.

## Lab 5 — `labs/05-spartan-chaosnet-transient-stream/`

Two separate gaps, two separate fixes:

1. **Status: done.** **The generated topology is never drawn** (0001 item 1). This is the
   cheapest, highest-value fix in the entire backlog: `chaosnet.py`/`generate_topology.py` already
   hold the NetworkX graph in memory before handing it to pandapower/DPsim — `nx.draw(g,
   pos=nx.spring_layout(g), ...)` plus `plt.savefig()` is a ~10-line addition, zero new
   dependencies (`networkx` and `matplotlib` are both already hard dependencies), and it directly
   answers "what did this run actually generate" for a lab whose entire premise is procedural
   generation. **Recommended as the near-term fix — do this before anything in tier 2.**

   Implemented: `generate_topology.py` now has a `_plot_topology()` helper (reconstructs a small
   `nx.Graph` from the `ChaosTopology` dict's `buses`/`lines`, deterministic via a fixed
   `TOPOLOGY_LAYOUT_SEED` on `nx.spring_layout()`), called from `generate_step()` under the same
   `seed == FIXTURE_SEED and refresh_fixtures` gate as the JSON fixtures. Tap-point buses (one of
   which, SUB-3, is `chaos_schedule.yaml`'s fault target) are drawn larger, in a different color,
   and labelled with their `tap_name`. Output: `sample_topology_plot.png`, committed alongside
   `sample_topology.json`. `check_step()` now also asserts the PNG exists (mirroring
   `verify_stream.py`'s own plot-fixture check), so `test_lab5.py::test_lab5_topology_matches_fixture`
   covers it. See `labs/05-spartan-chaosnet-transient-stream/README.md`'s walkthrough step 1 and
   "Files" section.
2. **Real symbolic (IEC-style) single-line rendering** — the stretch tier from 0002. This is
   gated on more than "add a library": Lab 5's DPsim stack ingests CIM via CIM++, but nothing in
   the current pipeline *emits* a CIM/CGMES file — the topology lives and dies as a NetworkX/
   pandapower object. `pypowsybl`/`powsybl-diagram` (0002) natively renders CIM/CGMES models, so
   the actual missing piece is a CIM export step, not the renderer itself. That export step is a
   nontrivial addition (CIM/CGMES is its own spec surface, not just a file-format change) and
   should be scoped as its own backlog item if picked up — noted here as "have an option," per the
   ask, not proposed as near-term work. `docs/LAB5_SPARTAN_CHAOSNET.md`'s own Definition of Done
   split (laptop-portable core required, hardware-validated extension optional) is a template for
   how to gate this the same way: topology drawing (tier 1) in core, symbolic SLD (tier 2) as an
   explicitly optional extension.

## Common thread

Both labs already hold the exact objects (`pandapower.pandapowerNet`, `networkx.Graph`) that the
free-tier tools in 0002 operate on directly — no data reshaping needed, only a rendering call at
the point where the object already exists in the script.

## Symbolic single-line, unblocked (2026-10-23)
The stretch tier was gated on a CIM/CGMES export, and backlog 0034 now provides one that pypowsybl
imports and solves. `peer_check.py diagram` (`DIAGRAMS=1 scripts/cgmes_peer_check.sh`) renders,
with **powsybl-diagram from pypowsybl 1.16.1** on the x86-64 peer (no ARM64 wheel), the SVG
outputs below:
- a single-line diagram of the inferred substation with the most voltage levels (15; 378 drawn
  elements, feeders with flow arrows), labelled with CSIRO's element names;
- a network-area diagram around it, depth 2, with branch flows.

The substation grouping is **inferred** (buses joined by transformers, 0034), not data, and any
caption must say so. snemSA's `bus_data` `x y` (0024) are layout positions and could place the
voltage levels. **Lab 5, done the same day:** `CASE=lab5-chaosnet DIAGRAMS=1 scripts/cgmes_peer_check.sh`
exports the committed `sample_topology.json` (14 buses, 28 lines, 1 external grid, 13 loads).
pypowsybl imports it, solves and agrees with pandapower to 4.97e-8 pu. The network-area
diagram is committed as `labs/05-.../sample_network_area_diagram.svg`. This needed one more
export fix: CGMES requires an ExternalNetworkInjection's `minP`/`maxP`, which an unbounded
pandapower slack lacks. `cgmes.UNBOUNDED_P_MW` (±1e6 MW, a named "no limit" sentinel) is written
only where the case gives no limit, and the injection's `p`/`q` come from the solved state.
