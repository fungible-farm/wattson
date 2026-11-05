# 0016 — G2: verified engineering mathematics

- **Status:** open. [PRD-0010](../prd/0010-powercoder-physics-grounded-engineering-agent.md) G2, priority A3. Depends on G1 for the command surface, but each
  tool can be built and tested behind today's MCP server first. Absorbs
  [0013](0013-sensitivity-newton-tool.md).
- **Requirements:** PC-REQ-010–016.

## Deliverables
| Capability | Starting point in this repo |
|---|---|
| AC/DC power flow with the proof contract | `powercoder/inference/studies.py` (`run_study_trial`). Add mismatches and residuals |
| AC/DC OPF: objective, binding constraints, duals | pandapower OPF (BSD-3) |
| N-1 and bounded N-2 screening, with coverage labels | Lab 2 screening (`labs/02-medium-interconnection-screening/workflow.py`) |
| PTDF, LODF, Jacobian, V/Q sensitivities with conditioning | pandapower internals, numpy/scipy |
| WLS state estimation with observability and bad-data checks | pandapower `estimation` |
| Dynamics and short circuit as separate services | DPsim (Lab 5), pandapower `shortcircuit` |

## Required tests
- **Reference cases:** synthetic, IEEE (from RestoreBench's corpora) and CSIRO cases, cross-checked
  against independent solutions.
- **Adversarial cases:** non-convergence, ill-conditioning, infeasible OPF, wrong topology, and
  isolating outages (FM-25 is the precedent: the reference solved it and the tool refused).

## Exit criteria
- Every tool returns the proof contract.
- Solver-specific tolerances, declared in advance, are met.
- Non-converged results are never reported as solved, and violations are recomputed independently.
- Failed, infeasible and indeterminate results are distinguishable.
- Contingency coverage is always reported.

## Cheapest first step
DC OPF and PTDF on `snem-sa-2000`, cross-checked against an independent implementation.

## WLS state estimation done (2026-10-23): `powercoder/engineering/state_estimation.py`
It runs pandapower's WLS estimation, chi-square test and largest-normalised-residual (LNR)
removal, and returns a typed result with the proof contract, an observability verdict and the
identity of every measurement removed. **The measurements are synthetic**: seeded noise around a
solved AC power flow, with the accuracy classes in `MEASUREMENT_STD` *assumed*, because G5 is
blocked.

| Case | Measurements | Clean estimate (max \|V − truth\|) | 50σ gross error on one flow |
|---|---|---|---|
| case9 | 45 | 1.28e-3 pu, no bad data suspected | detected; **exactly that measurement** removed (two trials) |
| snemSA | 3,295 | 4.06e-3 pu, no bad data suspected | detected and removed, **plus 10 good measurements** |

- **Measure both ends of a branch.** With one end only, a flow can be a *critical* measurement:
  its residual is zero, so a 25 MW error went undetected on case9. SCADA usually measures both
  ends, so the synthetic set does too.
- **LNR at |r_N| > 3 over-removes on a large set.** Each good measurement exceeds 3 by chance
  with probability about 0.27%, so 3,295 × 0.0027 ≈ 8.9 false removals are expected. The 10
  observed fit that. The threshold is `LNR_THRESHOLD`, pandapower's default. A size-aware
  threshold is a choice to make deliberately, not a default to change silently.
- **Unobservable is refused** (`observable = False`, `indeterminate`), never estimated.

## AC power flow proof and V/Q sensitivities done (2026-10-23): `powercoder/engineering/ac_analysis.py`
- **`ac_power_flow`** returns the proof contract: iterations, the Kirchhoff residual recomputed
  from the result tables, every limit violation, and cond(J). snemSA: converged in 5 iterations,
  residual 6.7e-9 MVA, cond(J) 3.4e7, `infeasible` (its 39 base voltage violations). case9:
  feasible, cond(J) 59.
- **`vq_sensitivity`** (dV/dQ from the reduced Jacobian) is cross-checked against an independent
  central finite difference (±0.1 Mvar re-solves). It agrees to 1e-5 relative on case9 and 7e-9
  on snemSA (tolerance 1e-3).
- **`qv_modal`** (smallest reduced-Jacobian eigenvalue and participating buses): case9 +5.71.
  **snemSA: two negative modes (−16.29, −13.59) at buses 1753–1755**, confirmed real in the
  linearised model by perturbation (ΔQ along the left eigenvector moves V by ΔQ/λ along the
  right one; correlation 0.9996). They sit at the ends of lines 293 and 294, the case's only
  negative-reactance branches (x ≈ −63 Ω, series-compensation equivalents). The eigenvalue
  criterion assumes inductive branches, so this is **flagged, not read as voltage
  instability**.

## AC OPF done (2026-10-23): `powercoder/engineering/ac_opf.py`
pandapower `runopp` (PIPS) gives a **local** optimum. No global certificate exists here, so
`optimality_gap_or_bound` stays None and the result says so (PC-REQ-011). Feasibility *is*
re-verified independently. The OPF's setpoints (generator P and V, slack V and angle) are
re-solved as a plain AC power flow, which must reproduce the OPF's voltages, satisfy Kirchhoff's
laws, and keep every limit **the case declares** (bus `min/max_vm_pu`, branch
`max_loading_percent`, generator P and Q limits).

| Case | Objective | \|dV\| re-solve vs OPF | Binding limits | Verdict |
|---|---|---|---|---|
| case9 | 5,311.91 | 5.2e-11 pu | none | feasible |
| ieee39-pab-stressed | 51,434.81 | 4.4e-9 pu | **1 line at 100%**, 5 V max, 7 P max, 2 Q | feasible |
| ieee118-restorebench | 129,704.74 | 2.8e-10 pu | 7 V max, 16 P min, 8 Q | feasible |

An overloaded case9 (5× load) is not reported solved.

## Bounded N-2 done (2026-10-23): `contingency.screen_n2_bounded`
Exhaustive N-2 is about 243,000 pairs on snemSA. This screens **every pair among the M (default
10) most severe single outages** of an exhaustive AC N-1 screen, each by an exact AC re-solve. It
is labelled `heuristic` in its coverage, because pairs of individually milder outages are not
examined.

| Case | Pairs | With new violations | Non-convergent | Islanding | Worst loading |
|---|---|---|---|---|---|
| ieee39-pab-stressed | 45 | 12 | 24 | 21 | 178.4% (lines 21 + 29) |
| snem-sa-2000 | 45 | 33 (voltage only) | 0 | 45 | 4.0% |

**G2 status after 2026-10-23:**
- **Done:** DC OPF with the LP/QP cross-check; PTDF/LODF; exhaustive N-1 with coverage, both
  slack conventions, and bounded N-2; WLS state estimation; AC power flow with proof,
  dV/dQ and Q–V modes; AC OPF with a feasibility re-check.
- **Left:** dynamics as a service (DPsim, Lab 5); short circuit (no transmission case with source
  data, 0028); Jacobian-based contingency ranking beyond LODF.
