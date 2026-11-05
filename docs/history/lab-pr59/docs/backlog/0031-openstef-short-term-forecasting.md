# 0031 — OpenSTEF as G7's first forecasting engine

- **Status:** open, **blocked on G5 data**. **Merit: fourth** of the 2026-10-23 tool review.
- **Affects:**
  - G7 ([0021](0021-g7-probabilistic-forecasting.md)): PC-REQ-041 interchangeable engines and
    PC-REQ-043 quantiles;
  - 0027's `/fn/forecast/<engine>/…` derived nodes;
  - PRD Demonstration C.

## Why
OpenSTEF (LF Energy, Alliander; MPL-2.0; v4.6.1 released 2026-10-21; Python ≥ 3.12, checked
2026-10-23) provides probabilistic short-term forecasting pipelines for any measured load or
generation signal. Any scikit-learn model can be used, and it optionally takes weather and price
inputs. Its project page says it is in production at Alliander across thousands of grid
locations. Using it means G7 doesn't have to build its own quantile pipelines.

## Blocker
It needs measured history and weather forecasts with their issue times. G5's data is blocked, and
NEMWeb is blocked for lab agents. Until then, the only honest result is a demo on cached or
synthetic data, labelled as such.

## Scope (once data is available)
1. **Wrap it as an engine behind one interface,** with persistence and a seasonal-naive baseline,
   so every skill score is relative to a baseline.
2. **No-leakage hindcasts:** only data issued before the forecast time (PC-REQ-042). With 0027,
   that's the `issued=` path rule.
3. **Score the forecasts:** pinball loss, calibration, and skill against the baselines.
4. **Feed quantile scenarios into AC power flow** for Demonstration C (the probability of a voltage
   violation).

## Licence
MPL-2.0 (file-level copyleft), the same exception class as DPsim and pypowsybl. Record it in
`docs/PSCADOSSE.md` at adoption.
