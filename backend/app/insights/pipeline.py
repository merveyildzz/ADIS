"""Ties the Insight layer together: RAW DATA -> deterministic statistics
(Correlation/Trend/Anomaly agents, no LLM) -> Narrative Agent (LLM,
phrasing only). Produces the JSON structure cached in
`raw_uploads.insights_json` and served by the insights API route.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.insights.anomaly_agent import compute_anomalies
from app.insights.correlation_agent import compute_correlations
from app.insights.narrative_agent import narrate_anomaly, narrate_correlation, narrate_trend
from app.insights.trend_agent import compute_category_trends, compute_numeric_trends
from app.llm.client import LLMClient

MAX_ANOMALIES_NARRATED = 5
MAX_TRENDS_NARRATED = 8
MAX_CHART_POINTS = 300


def build_analysis_dataframe(
    df: pd.DataFrame, cleaned_columns: dict[str, tuple[str | None, list]]
) -> pd.DataFrame:
    """Swaps in cleaned values for the columns an agent classified (typed
    appropriately for analysis); columns no agent touched are left as-is
    from the original upload — still legitimate analytical dimensions
    (e.g. `category`), just not ones needing "cleaning" per se."""
    analysis_df = df.copy()
    for column_name, (column_type, cleaned_values) in cleaned_columns.items():
        if column_name not in analysis_df.columns:
            continue
        analysis_df[column_name] = cleaned_values
        if column_type in ("currency", "numeric_age", "quantity"):
            analysis_df[column_name] = pd.to_numeric(analysis_df[column_name], errors="coerce")
        elif column_type == "date":
            analysis_df[column_name] = pd.to_datetime(analysis_df[column_name], errors="coerce")
    return analysis_df


def _build_charts(
    df: pd.DataFrame,
    *,
    correlations: list,
    date_column: str | None,
    trend_numeric_cols: list[str],
    category_column: str | None,
    numeric_cols: list[str],
    anomalies: list,
) -> dict:
    """Phase 8 chart-ready data, derived from the same analysis DataFrame
    and computed once alongside the cards above — no extra DB round trips,
    no re-deriving what's already been established as deterministically true."""
    charts: dict = {}

    if len(numeric_cols) >= 2:
        # Full pairwise matrix (not just the pairs that cleared the
        # significance threshold) — the heatmap is meant to show the whole
        # picture, same pandas.corr() computation Correlation Agent uses.
        matrix = df[numeric_cols].corr(numeric_only=True)
        charts["correlation_matrix"] = {
            "columns": list(matrix.columns),
            "values": [[None if pd.isna(v) else round(float(v), 3) for v in row] for row in matrix.to_numpy()],
        }

    if correlations:
        top = correlations[0]
        sub = df[[top.col1, top.col2]].dropna()
        if len(sub) > MAX_CHART_POINTS:
            sub = sub.sample(MAX_CHART_POINTS, random_state=42)
        trendline = None
        if len(sub) >= 2 and sub[top.col1].nunique() > 1:
            slope, intercept = np.polyfit(sub[top.col1], sub[top.col2], 1)
            x_min, x_max = float(sub[top.col1].min()), float(sub[top.col1].max())
            trendline = [
                {"x": x_min, "y": float(slope * x_min + intercept)},
                {"x": x_max, "y": float(slope * x_max + intercept)},
            ]
        charts["correlation_scatter"] = {
            "col1": top.col1,
            "col2": top.col2,
            "points": [{"x": float(x), "y": float(y)} for x, y in zip(sub[top.col1], sub[top.col2])],
            "trendline": trendline,
        }

    if date_column and trend_numeric_cols:
        col = trend_numeric_cols[0]
        dated = df[[date_column, col]].dropna()
        if not dated.empty:
            monthly = dated.groupby(dated[date_column].dt.to_period("M"))[col].sum().sort_index()
            charts["trend_line"] = {
                "column": col,
                "series": [{"period": str(p), "value": round(float(v), 2)} for p, v in monthly.items()],
            }

    if category_column and numeric_cols:
        value_col = trend_numeric_cols[0] if trend_numeric_cols else numeric_cols[0]
        grouped = df.dropna(subset=[category_column]).groupby(category_column).agg(
            count=(category_column, "size"), avg_value=(value_col, "mean")
        )
        charts["category_bar"] = {
            "value_column": value_col,
            "categories": [
                {"category": str(idx), "count": int(row["count"]), "avg_value": round(float(row["avg_value"]), 2)}
                for idx, row in grouped.iterrows()
            ],
        }

    if anomalies and numeric_cols:
        target_col = anomalies[0].column
        series = df[target_col].dropna()
        anomaly_rows = {a.row_index for a in anomalies if a.column == target_col}
        anomaly_points = [
            {"row_index": int(idx), "value": float(v), "is_anomaly": True}
            for idx, v in series.items() if idx in anomaly_rows
        ]
        normal_series = series[~series.index.isin(anomaly_rows)]
        sample_budget = max(0, MAX_CHART_POINTS - len(anomaly_points))
        if len(normal_series) > sample_budget:
            normal_series = normal_series.sample(sample_budget, random_state=42)
        normal_points = [
            {"row_index": int(idx), "value": float(v), "is_anomaly": False}
            for idx, v in normal_series.items()
        ]
        charts["anomaly_scatter"] = {
            "column": target_col,
            "points": sorted(anomaly_points + normal_points, key=lambda p: p["row_index"]),
        }

    return charts


def compute_insight_cards(
    df: pd.DataFrame,
    *,
    date_column: str | None = None,
    category_column: str | None = None,
    numeric_columns: list[str] | None = None,
    trend_numeric_columns: list[str] | None = None,
    llm_client: LLMClient | None = None,
) -> dict:
    """`numeric_columns` drives correlation/anomaly detection (distribution-
    based — any numeric column is fair game). `trend_numeric_columns` drives
    the monthly-sum trend view specifically, since *summing* only makes
    sense for genuinely additive quantities (revenue), not e.g. ages;
    defaults to `numeric_columns` when not given separately."""
    warnings: list[str] = []
    numeric_cols = numeric_columns or list(df.select_dtypes(include="number").columns)
    trend_numeric_cols = trend_numeric_columns if trend_numeric_columns is not None else numeric_cols

    if len(numeric_cols) < 2:
        warnings.append("Fewer than 2 numeric columns available — correlation analysis skipped.")
        correlations = []
    else:
        correlations = compute_correlations(df[numeric_cols])

    correlation_cards = []
    for c in correlations:
        narrative = narrate_correlation(c, llm_client)
        correlation_cards.append({
            "col1": c.col1, "col2": c.col2, "r": c.r, "strength": c.strength, "direction": c.direction,
            "narrative": narrative["text"], "narrative_method": narrative["method"],
        })

    trend_cards = []
    if date_column is None or date_column not in df.columns:
        warnings.append("No date column detected — trend analysis skipped.")
    else:
        numeric_trends = compute_numeric_trends(df, date_column=date_column, numeric_columns=trend_numeric_cols)
        category_trends = (
            compute_category_trends(df, date_column=date_column, category_column=category_column)
            if category_column and category_column in df.columns
            else []
        )
        all_trends = sorted([*numeric_trends, *category_trends], key=lambda t: abs(t.change_pct), reverse=True)
        for t in all_trends[:MAX_TRENDS_NARRATED]:
            narrative = narrate_trend(t, llm_client)
            trend_cards.append({
                "dimension": t.dimension, "label": t.label, "period": t.period,
                "baseline": t.baseline, "period_value": t.period_value, "change_pct": t.change_pct,
                "direction": t.direction, "narrative": narrative["text"], "narrative_method": narrative["method"],
            })
        if len(all_trends) > MAX_TRENDS_NARRATED:
            warnings.append(f"{len(all_trends) - MAX_TRENDS_NARRATED} additional trends found but not narrated.")

    anomalies = compute_anomalies(df, columns=numeric_cols)
    anomaly_cards = []
    for a in anomalies[:MAX_ANOMALIES_NARRATED]:
        narrative = narrate_anomaly(a, llm_client)
        anomaly_cards.append({
            "column": a.column, "row_index": a.row_index, "value": a.value, "z_score": a.z_score,
            "direction": a.direction, "narrative": narrative["text"], "narrative_method": narrative["method"],
        })
    if len(anomalies) > MAX_ANOMALIES_NARRATED:
        warnings.append(f"{len(anomalies) - MAX_ANOMALIES_NARRATED} additional anomalies found but not narrated.")

    charts = _build_charts(
        df,
        correlations=correlations,
        date_column=date_column if date_column in df.columns else None,
        trend_numeric_cols=trend_numeric_cols,
        category_column=category_column,
        numeric_cols=numeric_cols,
        anomalies=anomalies,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "correlations": correlation_cards,
        "trends": trend_cards,
        "anomalies": anomaly_cards,
        "anomaly_count": len(anomalies),
        "warnings": warnings,
        "charts": charts,
    }
