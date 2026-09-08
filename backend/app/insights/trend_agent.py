"""Trend Agent — no LLM. Two period-over-period views on a date column:

- Overall monthly sum of a numeric column, compared month-over-month (the
  natural reading of "trend" — "revenue increased 18% in March").
- Per-category monthly counts, compared against that category's own average
  across all *other* months (spike detection) — this is what surfaces a
  seasonal anomaly like "Electronics orders spiked in December" that a
  simple consecutive-month comparison could miss depending on which month
  it lands next to.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MONTH_OVER_MONTH_THRESHOLD = 0.20  # 20% change flagged as significant
CATEGORY_SPIKE_THRESHOLD = 0.75  # 75% above a category's own other-month average
MIN_CATEGORY_MONTHLY_COUNT = 3  # ignore near-zero-volume months (division noise)
MIN_NUMERIC_MONTHLY_ROWS = 3  # same idea for the numeric-sum trend: a month with
# almost no rows produces a noisy sum, not a meaningful period-over-period signal


@dataclass(frozen=True)
class TrendInsight:
    dimension: str  # "numeric_sum" | "category_count"
    label: str  # numeric column name, or the category value
    period: str  # "2025-12"
    baseline: float
    period_value: float
    change_pct: float
    direction: str  # "increase" | "decrease"


def compute_numeric_trends(
    df: pd.DataFrame, *, date_column: str, numeric_columns: list[str]
) -> list[TrendInsight]:
    if date_column not in df.columns:
        return []  # no date column — skip gracefully, not an error
    dated = df[[date_column] + [c for c in numeric_columns if c in df.columns]].dropna(subset=[date_column])
    if dated.empty:
        return []

    periods = dated[date_column].dt.to_period("M")
    insights: list[TrendInsight] = []
    for col in numeric_columns:
        if col not in dated.columns:
            continue
        monthly = dated.groupby(periods)[col].sum().sort_index()
        monthly_row_counts = dated.groupby(periods)[col].count().sort_index()
        if len(monthly) < 2:
            continue
        for i in range(1, len(monthly)):
            previous, current = float(monthly.iloc[i - 1]), float(monthly.iloc[i])
            if previous == 0:
                continue
            # A near-empty month (few underlying rows) produces a noisy sum,
            # not a meaningful trend — same reasoning as the category-count
            # floor below.
            if monthly_row_counts.iloc[i - 1] < MIN_NUMERIC_MONTHLY_ROWS or (
                monthly_row_counts.iloc[i] < MIN_NUMERIC_MONTHLY_ROWS
            ):
                continue
            change_pct = (current - previous) / previous
            if abs(change_pct) < MONTH_OVER_MONTH_THRESHOLD:
                continue
            insights.append(TrendInsight(
                dimension="numeric_sum",
                label=col,
                period=str(monthly.index[i]),
                baseline=round(previous, 2),
                period_value=round(current, 2),
                change_pct=round(change_pct * 100, 1),
                direction="increase" if change_pct > 0 else "decrease",
            ))
    return insights


def compute_category_trends(
    df: pd.DataFrame, *, date_column: str, category_column: str
) -> list[TrendInsight]:
    if date_column not in df.columns or category_column not in df.columns:
        return []
    dated = df[[date_column, category_column]].dropna()
    if dated.empty:
        return []

    periods = dated[date_column].dt.to_period("M")
    insights: list[TrendInsight] = []
    for category, group in dated.groupby(category_column):
        monthly_counts = group.groupby(periods.loc[group.index]).size()
        if len(monthly_counts) < 2:
            continue
        for period, count in monthly_counts.items():
            other_months = monthly_counts.drop(period)
            baseline = float(other_months.mean())
            if baseline < MIN_CATEGORY_MONTHLY_COUNT:
                continue
            # A near-empty period (e.g. a handful of stray rows in an
            # otherwise-unpopulated trailing month) can look like a huge
            # *decrease* in percentage terms without being a real signal —
            # require the flagged period's own count to clear the floor too.
            # Spikes (increases) are unaffected: a real spike's count is
            # large by construction.
            if count < baseline and count < MIN_CATEGORY_MONTHLY_COUNT:
                continue
            change_pct = (count - baseline) / baseline
            if abs(change_pct) < CATEGORY_SPIKE_THRESHOLD:
                continue
            insights.append(TrendInsight(
                dimension="category_count",
                label=str(category),
                period=str(period),
                baseline=round(baseline, 1),
                period_value=float(count),
                change_pct=round(change_pct * 100, 1),
                direction="increase" if change_pct > 0 else "decrease",
            ))
    insights.sort(key=lambda t: abs(t.change_pct), reverse=True)
    return insights
