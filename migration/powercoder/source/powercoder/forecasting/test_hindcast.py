"""The hindcast harness refuses leakage by construction and scores against persistence."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from powercoder.forecasting import hindcast as hc

# Ten synthetic days: a daily sine plus a trend, so every model has a known answer.
DAYS: int = 10


@pytest.fixture
def series() -> pd.Series:
    index = pd.date_range("2026-06-01 00:05", periods=DAYS * hc.DAY, freq=hc.INTERVAL)
    phase = np.arange(len(index)) * 2 * np.pi / hc.DAY
    return pd.Series(1000 + 200 * np.sin(phase) + 0.01 * np.arange(len(index)), index=index)


def test_reading_past_the_issue_time_is_refused(series: pd.Series) -> None:
    issue = series.index[5 * hc.DAY]
    view = hc.AsOf(series, issue)
    assert view.at(issue) == series[issue]
    with pytest.raises(hc.LeakageError):
        view.at(issue + hc.INTERVAL)
    assert view.history().index[-1] == issue


def test_an_injected_future_value_changes_no_forecast(series: pd.Series) -> None:
    """0021 exit criterion: injected future-data leakage is rejected (here: has no effect)."""
    issue = series.index[8 * hc.DAY]
    poisoned = series.copy()
    poisoned[poisoned.index > issue] = 1e9
    for name in hc.MODELS:
        clean = hc.forecast(series, name, issue, hc.DEFAULT_HORIZONS)
        dirty = hc.forecast(poisoned, name, issue, hc.DEFAULT_HORIZONS)
        assert clean.quantiles == dirty.quantiles, name


def test_a_forecast_carries_its_identity_and_valid_times(series: pd.Series) -> None:
    issue = series.index[8 * hc.DAY]
    f = hc.forecast(series, "seasonal-naive", issue, (1, 12))
    assert (f.model, f.version, f.issue_time) == ("seasonal-naive", "1", issue)
    assert f.valid_times == (issue + hc.INTERVAL, issue + 12 * hc.INTERVAL)


def test_seasonal_naive_beats_persistence_on_a_daily_cycle(series: pd.Series) -> None:
    report = hc.hindcast(series, (12, 48), series.index[8 * hc.DAY], every=24)
    for h in ("12", "48"):
        assert report["models"]["seasonal-naive"]["scores"][h]["skill_vs_persistence"] > 0.9


def test_pinball_loss_is_the_quantile_check_function() -> None:
    assert hc.pinball(10.0, 8.0, 0.9) == pytest.approx(1.8)
    assert hc.pinball(10.0, 12.0, 0.9) == pytest.approx(0.2)


@pytest.mark.skipif(not list(hc.CACHE.glob(hc.TABLE_GLOB)), reason="needs Lab 4's data/nemosis_cache")
def test_the_cached_sa_demand_month_loads_complete() -> None:
    sa = hc.load_region("SA1")
    assert len(sa) == 30 * hc.DAY and sa.index.is_monotonic_increasing and (sa.diff().dropna().abs() < 1e6).all()
