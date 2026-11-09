"""Simple, transparent volume forecasting for capacity planning.

Uses an ordinary least-squares linear trend on monthly counts. Forecasts are
labelled as estimates and never used for automated decisions.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def monthly_counts(dates: list[date]) -> pd.Series:
    if not dates:
        return pd.Series(dtype=float)
    s = pd.Series(1, index=pd.to_datetime(pd.Series(dates)))
    counts = s.resample("MS").sum()
    return counts.astype(float)


def linear_forecast(series: pd.Series, periods: int = 3) -> pd.DataFrame:
    """Return a DataFrame with columns month, value, kind (actual|forecast)."""
    if series.empty:
        return pd.DataFrame(columns=["month", "value", "kind"])
    actual = pd.DataFrame({"month": series.index, "value": series.values, "kind": "actual"})
    if len(series) < 3:
        return actual  # not enough history for a trend
    x = np.arange(len(series), dtype=float)
    slope, intercept = np.polyfit(x, series.values.astype(float), 1)
    fx = np.arange(len(series), len(series) + periods, dtype=float)
    fvals = np.clip(slope * fx + intercept, 0, None)
    months = pd.date_range(series.index[-1] + pd.offsets.MonthBegin(1), periods=periods, freq="MS")
    fc = pd.DataFrame({"month": months, "value": np.round(fvals, 1), "kind": "forecast"})
    return pd.concat([actual, fc], ignore_index=True)
