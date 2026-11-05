"""Leakage-free hindcasting harness for regional demand (PRD-0010 G7, backlog 0021's first step).

0021: "The hindcast harness and leakage tests on a public demand series... The harness, not
the data, is the asset." A forecast is made at an issue time from an :class:`AsOf` view that
holds only observations available by then; reading past it raises :class:`LeakageError`,
so leakage is refused by construction rather than avoided by care (PC-REQ-042). Every
:class:`Forecast` carries its model, version, issue time, valid times and quantiles
(PC-REQ-043), and is scored against persistence (PC-REQ-041's interchangeable engines
start here: a model is any function of an ``AsOf`` view).

Named stand-ins (AGENTS.md):
- **Data:** one cached month of AEMO ``DISPATCHREGIONSUM.TOTALDEMAND`` (June 2026, Lab 4's
  ``data/nemosis_cache``), read locally. NEMWeb access from this host is undecided (0019),
  so nothing is fetched. No weather inputs (G5 is blocked).
- **Availability:** an interval's demand is taken as available at the end of its interval
  (``SETTLEMENTDATE``). AEMO's actual publication delay is not modelled; ``PUBLICATION_LAG``
  is the place to set it.
- **Models:** persistence, seasonal naive (same interval the previous day), and a
  probabilistic seasonal naive whose quantiles are empirical quantiles of its own past
  errors (y(t) - y(t - 1 day), the same for every horizon up to a day). These are
  baselines, not the G7 engines (StatsForecast,
  LightGBM, Chronos, TimesFM).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

# An operator may mount an approved cache independently of the source checkout.
CACHE: Path = Path(os.environ.get("WATTSON_DEMAND_CACHE", str(Path(__file__).resolve().parents[3] / ".artifacts/nemosis_cache")))
TABLE_GLOB: str = "*DISPATCHREGIONSUM*.parquet"
INTERVAL: pd.Timedelta = pd.Timedelta(minutes=5)
# Seasonal period: one day of 5-minute intervals.
DAY: int = 288
PUBLICATION_LAG: pd.Timedelta = pd.Timedelta(0)
QUANTILES: tuple[float, ...] = (0.1, 0.5, 0.9)
# Error history for the probabilistic baseline: the 7 days before each issue time.
RESIDUAL_WINDOW: int = 7 * DAY
# Horizons in intervals: 5 min, 1 h, 4 h, 1 day ahead.
DEFAULT_HORIZONS: tuple[int, ...] = (1, 12, 48, 288)


class LeakageError(RuntimeError):
    """A model asked for data not available at its issue time."""


class AsOf:
    """A demand series as known at ``issue_time``: reads after it raise LeakageError."""

    def __init__(self, series: pd.Series, issue_time: pd.Timestamp) -> None:
        self._series = series
        self.issue_time = issue_time
        self._known = series[series.index + PUBLICATION_LAG <= issue_time]

    def history(self, last: int | None = None) -> pd.Series:
        """The latest ``last`` known observations (all, if None)."""
        return self._known if last is None else self._known.iloc[-last:]

    def at(self, valid_time: pd.Timestamp) -> float:
        """One observation; refuses any not yet available."""
        if valid_time + PUBLICATION_LAG > self.issue_time:
            raise LeakageError(f"{valid_time} is not available at issue time {self.issue_time}")
        return float(self._series.loc[valid_time])


@dataclass(frozen=True)
class Forecast:
    """One model's forecast from one issue time."""

    model: str
    version: str
    issue_time: pd.Timestamp
    valid_times: tuple[pd.Timestamp, ...]
    quantiles: dict[float, tuple[float, ...]] = field(hash=False)


Model = Callable[[AsOf, tuple[pd.Timestamp, ...]], dict[float, tuple[float, ...]]]


def persistence(view: AsOf, valid: tuple[pd.Timestamp, ...]) -> dict[float, tuple[float, ...]]:
    """The last known value, for every horizon (point forecast only)."""
    last = float(view.history(1).iloc[-1])
    return {0.5: tuple(last for _ in valid)}


def seasonal_naive(view: AsOf, valid: tuple[pd.Timestamp, ...]) -> dict[float, tuple[float, ...]]:
    """The same interval one day earlier; for horizons beyond a day, the latest known same-time value."""
    out = []
    for t in valid:
        back = t - pd.Timedelta(days=1)
        while back + PUBLICATION_LAG > view.issue_time:
            back -= pd.Timedelta(days=1)
        out.append(view.at(back))
    return {0.5: tuple(out)}


def probabilistic_seasonal_naive(view: AsOf, valid: tuple[pd.Timestamp, ...]) -> dict[float, tuple[float, ...]]:
    """Seasonal naive plus empirical quantiles of its own errors, from errors already observable.

    The errors are those of seasonal-naive forecasts whose targets were observed by the
    issue time, over the last RESIDUAL_WINDOW intervals, so no future value enters the
    spread. Up to a day ahead the seasonal-naive error is y(t) - y(t - 1 day) whatever the
    horizon, so one error history serves every horizon in that range.
    """
    point = seasonal_naive(view, valid)[0.5]
    known = view.history(RESIDUAL_WINDOW + 2 * DAY)
    shifted = known.shift(DAY)
    errors = (known - shifted).dropna().iloc[-RESIDUAL_WINDOW:]
    spread = {q: float(np.quantile(errors.to_numpy(), q)) for q in QUANTILES}
    return {q: tuple(p + spread[q] for p in point) for q in QUANTILES}


MODELS: dict[str, tuple[str, Model]] = {
    "persistence": ("1", persistence),
    "seasonal-naive": ("1", seasonal_naive),
    "seasonal-naive-empirical-quantiles": ("1", probabilistic_seasonal_naive),
}


def forecast(series: pd.Series, name: str, issue_time: pd.Timestamp, horizons: tuple[int, ...]) -> Forecast:
    """Issue one forecast through an AsOf view."""
    version, model = MODELS[name]
    valid = tuple(issue_time + h * INTERVAL for h in horizons)
    return Forecast(name, version, issue_time, valid, model(AsOf(series, issue_time), valid))


def pinball(y: float, q_value: float, q: float) -> float:
    """Pinball (quantile) loss of one quantile forecast."""
    diff = y - q_value
    return max(q * diff, (q - 1) * diff)


def score(series: pd.Series, forecasts: list[Forecast], horizons: tuple[int, ...]) -> dict:
    """MAE, WAPE, pinball loss and 80% interval coverage per horizon, against observed values."""
    out = {}
    for i, h in enumerate(horizons):
        pairs = [(f, float(series.loc[f.valid_times[i]])) for f in forecasts if f.valid_times[i] in series.index]
        abs_errors = [abs(y - f.quantiles[0.5][i]) for f, y in pairs]
        entry = {"n": len(pairs), "mae": float(np.mean(abs_errors)),
                 "wape": float(sum(abs_errors) / sum(abs(y) for _, y in pairs))}
        if all(set(QUANTILES) <= set(f.quantiles) for f, _ in pairs):
            entry["pinball"] = {str(q): float(np.mean([pinball(y, f.quantiles[q][i], q) for f, y in pairs]))
                                for q in QUANTILES}
            entry["coverage_80"] = float(np.mean([f.quantiles[0.1][i] <= y <= f.quantiles[0.9][i] for f, y in pairs]))
        out[str(h)] = entry
    return out


def load_region(region: str, cache: Path = CACHE) -> pd.Series:
    """TOTALDEMAND (MW) for one region from the cached DISPATCHREGIONSUM files, indexed by interval end.

    Raises:
        FileNotFoundError: if no cached table exists (nothing is fetched).
    """
    files = sorted(cache.glob(TABLE_GLOB))
    if not files:
        raise FileNotFoundError(f"No {TABLE_GLOB} in {cache}; nothing is fetched (NEMWeb access is undecided)")
    df = pd.concat(pd.read_parquet(f, columns=["SETTLEMENTDATE", "REGIONID", "INTERVENTION", "TOTALDEMAND"])
                   for f in files)
    df = df[(df.REGIONID == region) & (df.INTERVENTION.astype(str) == "0")]
    series = pd.Series(df.TOTALDEMAND.astype(float).to_numpy(),
                       index=pd.to_datetime(df.SETTLEMENTDATE, format="%Y/%m/%d %H:%M:%S")).sort_index()
    return series[~series.index.duplicated()]


def hindcast(series: pd.Series, horizons: tuple[int, ...], first_issue: pd.Timestamp, every: int) -> dict:
    """Every model from rolling issue times; scores, and skill (1 - MAE / persistence MAE)."""
    last_valid = series.index[-1] - max(horizons) * INTERVAL
    issues = pd.date_range(first_issue, last_valid, freq=every * INTERVAL)
    report = {"issue_times": len(issues), "first_issue": str(issues[0]), "last_issue": str(issues[-1]),
              "horizons": list(horizons), "models": {}}
    for name in MODELS:
        report["models"][name] = {"version": MODELS[name][0],
                                  "scores": score(series, [forecast(series, name, t, horizons) for t in issues], horizons)}
    base = report["models"]["persistence"]["scores"]
    for scores in report["models"].values():
        for h, entry in scores["scores"].items():
            entry["skill_vs_persistence"] = 1 - entry["mae"] / base[h]["mae"] if base[h]["mae"] else math.nan
    return report


def main() -> int:
    """Run a hindcast on one region's cached demand and print the report."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default="SA1")
    parser.add_argument("--horizons", default=",".join(map(str, DEFAULT_HORIZONS)))
    parser.add_argument("--start-day", type=int, default=9, help="First issue day of the month; earlier days build history")
    parser.add_argument("--every", type=int, default=12, help="Intervals between issue times")
    args = parser.parse_args()
    series = load_region(args.region)
    first = series.index[0].normalize() + pd.Timedelta(days=args.start_day - 1)
    report = hindcast(series, tuple(int(h) for h in args.horizons.split(",")), first, args.every)
    report["region"], report["source"] = args.region, "DISPATCHREGIONSUM.TOTALDEMAND (cached; see module docstring)"
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
