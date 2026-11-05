# 0021 — G7: probabilistic forecasting

- **Status:** open; **cheapest first step done** 2026-10-23 (below). Real-data evaluation is blocked by G5's access decision. [PRD-0010](../prd/0010-powercoder-physics-grounded-engineering-agent.md) G7,
  priority B4. Depends on G2 and G5.
- **Requirements:** PC-REQ-040–044.

## Deliverables
- **Command layer:** the forecasting DSL, compiled into G1's IR.
- **Model registry:** StatsForecast, LightGBM, Chronos and TimesFM, plus physical models. Each
  licence is recorded at adoption (PSCADOSSE; Chronos and TimesFM are Apache-2.0).
- **Forecasting:** weather-conditioned, with leakage-free hindcasting.
- **Evaluation:** probabilistic, using pinball loss, MAE, WAPE, calibration, and skill against
  persistence and authoritative baselines.
- **Physical scenarios:** forecast-driven AC power-flow scenarios.

## Targets
1. Regional demand.
2. Wind and solar.
3. Voltage and constraint risk.

## Exit criteria
- Every forecast carries its model identity, issue time and valid time.
- Injected future-data leakage is rejected.
- Forecasts are calibrated, and skill is measured on unseen periods.
- Aggregated renewables keep their spatial correlation.
- Forecast-driven AC runs are validated, and infeasible scenarios are reported, not dropped.

## Cheapest first step
The hindcast harness and leakage tests on a public demand series, until NEM access is decided. The
harness, not the data, is the asset.

## First step done (2026-10-23): `powercoder/forecasting/hindcast.py`
- **No leakage by construction.** A forecast is made from an `AsOf` view, and any read after the
  issue time raises `LeakageError`. A test overwrites every value after the issue time with 1e9,
  and no model's forecast changes.
- **Identity:** each forecast carries its model, version, issue time, valid times and quantiles.
- **Baselines:** persistence, seasonal naive, and a seasonal naive whose quantiles are empirical
  quantiles of its past errors, taken from errors observable at the issue time.
- **Metrics:** MAE, WAPE, pinball loss, 80% coverage, and skill against persistence.
- **Data:** the cached AEMO `DISPATCHREGIONSUM.TOTALDEMAND` for June 2026 (Lab 4's cache, 8,640
  intervals per region), read locally with nothing fetched. Named stand-ins: availability at
  interval end, and no weather.
- **Run it:** `just powercoder::hindcast SA1`.

**SA1, 505 issue times (9–30 June, hourly):**

| Horizon | Persistence MAE | Seasonal naive MAE (skill) | Probabilistic, 80% coverage |
|---|---|---|---|
| 5 min | 17.8 MW | 150.4 (−7.47) | 0.80 |
| 1 h | 120.4 | 151.1 (−0.26) | 0.78 |
| 4 h | 348.3 | 150.6 (+0.57) | 0.78 |
| 1 day | 144.6 | 144.6 (0.00; the same forecast) | 0.81 |

These baselines exist to be beaten. Next: the G7 engines behind the same `Model` interface, then
weather inputs once G5 is decided.
