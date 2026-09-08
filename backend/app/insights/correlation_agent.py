"""Correlation Agent — no LLM. Pairwise Pearson correlation on numeric
columns via pandas, filtered to a minimum strength so noise doesn't drown
out real relationships. Strength banding is a fixed rule, not an LLM
judgment call.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MIN_ABS_R = 0.4
STRONG_THRESHOLD = 0.6
MODERATE_THRESHOLD = 0.3


@dataclass(frozen=True)
class CorrelationInsight:
    col1: str
    col2: str
    r: float
    strength: str  # "weak" | "moderate" | "strong"
    direction: str  # "positive" | "negative"


def _strength_band(abs_r: float) -> str:
    if abs_r > STRONG_THRESHOLD:
        return "strong"
    if abs_r >= MODERATE_THRESHOLD:
        return "moderate"
    return "weak"


def compute_correlations(df: pd.DataFrame, *, min_abs_r: float = MIN_ABS_R) -> list[CorrelationInsight]:
    numeric_df = df.select_dtypes(include="number")
    if numeric_df.shape[1] < 2:
        # Explicit skip — not enough numeric columns to correlate. The
        # caller renders this as a clear "not enough data" message, never
        # a crash (Phase 7 validation requirement).
        return []

    corr_matrix = numeric_df.corr(numeric_only=True)
    columns = list(corr_matrix.columns)
    insights: list[CorrelationInsight] = []
    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            r = corr_matrix.iloc[i, j]
            if pd.isna(r) or abs(r) < min_abs_r:
                continue
            insights.append(CorrelationInsight(
                col1=columns[i],
                col2=columns[j],
                r=round(float(r), 3),
                strength=_strength_band(abs(r)),
                direction="positive" if r > 0 else "negative",
            ))
    insights.sort(key=lambda c: abs(c.r), reverse=True)
    return insights
