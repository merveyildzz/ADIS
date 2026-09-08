"""Anomaly Agent — no LLM. IQR-based outlier detection: robust to the very
outliers it's looking for (unlike a plain mean/std z-score, whose std gets
inflated by the outliers themselves), and simple enough to explain in one
sentence — "explainability matters more than sophistication" per the
roadmap. A z-score is still computed per flagged point, purely to phrase
"how far above normal" in the narrative (matching the roadmap's own
"4.8σ above the normal order amount" example).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

IQR_MULTIPLIER = 1.5
MIN_SAMPLE_SIZE = 4


@dataclass(frozen=True)
class AnomalyInsight:
    column: str
    row_index: int
    value: float
    z_score: float
    direction: str  # "above" | "below"
    method: str = "iqr"


def compute_anomalies(df: pd.DataFrame, *, columns: list[str] | None = None) -> list[AnomalyInsight]:
    numeric_df = df.select_dtypes(include="number")
    target_columns = columns if columns is not None else list(numeric_df.columns)

    insights: list[AnomalyInsight] = []
    for col in target_columns:
        if col not in numeric_df.columns:
            continue
        series = numeric_df[col].dropna()
        if len(series) < MIN_SAMPLE_SIZE:
            continue

        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lower_bound = q1 - IQR_MULTIPLIER * iqr
        upper_bound = q3 + IQR_MULTIPLIER * iqr

        mean, std = series.mean(), series.std()
        for row_index, value in series.items():
            if lower_bound <= value <= upper_bound:
                continue
            z = (value - mean) / std if std else 0.0
            insights.append(AnomalyInsight(
                column=col,
                row_index=int(row_index),
                value=round(float(value), 2),
                z_score=round(float(z), 1),
                direction="above" if value > upper_bound else "below",
            ))

    insights.sort(key=lambda a: abs(a.z_score), reverse=True)
    return insights
