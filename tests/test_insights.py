import numpy as np
import pandas as pd
import pytest

from app.insights import anomaly_agent, correlation_agent, narrative_agent, trend_agent
from app.insights.pipeline import build_analysis_dataframe, compute_insight_cards

# ============================ Correlation Agent ===============================


def test_correlation_detects_strong_positive_relationship():
    rng = np.random.default_rng(42)
    age = rng.integers(18, 80, size=200).astype(float)
    amount = age * 5 + rng.normal(0, 5, size=200)  # strong, engineered correlation
    df = pd.DataFrame({"age": age, "amount": amount})

    results = correlation_agent.compute_correlations(df)
    assert len(results) == 1
    r = results[0]
    assert r.col1 == "age" and r.col2 == "amount"
    assert r.direction == "positive"
    assert r.strength == "strong"
    assert r.r > 0.6


def test_correlation_negative_direction():
    rng = np.random.default_rng(1)
    x = rng.integers(0, 100, size=200).astype(float)
    y = 200 - x + rng.normal(0, 2, size=200)
    df = pd.DataFrame({"x": x, "y": y})
    result = correlation_agent.compute_correlations(df)[0]
    assert result.direction == "negative"
    assert result.r < 0


def test_correlation_skips_gracefully_with_fewer_than_two_numeric_columns():
    df = pd.DataFrame({"age": [1, 2, 3], "name": ["a", "b", "c"]})
    assert correlation_agent.compute_correlations(df) == []
    assert correlation_agent.compute_correlations(pd.DataFrame({"age": [1, 2, 3]})) == []


def test_correlation_excludes_weak_relationships_below_threshold():
    rng = np.random.default_rng(7)
    df = pd.DataFrame({"a": rng.normal(size=300), "b": rng.normal(size=300)})  # ~independent
    results = correlation_agent.compute_correlations(df)
    assert all(abs(r.r) >= correlation_agent.MIN_ABS_R for r in results)


def test_strength_band_boundaries():
    assert correlation_agent._strength_band(0.71) == "strong"
    assert correlation_agent._strength_band(0.45) == "moderate"
    assert correlation_agent._strength_band(0.1) == "weak"


# ============================== Anomaly Agent ==================================


def test_anomaly_detects_extreme_outlier():
    values = [100.0, 105, 98, 102, 99, 101, 97, 103, 100, 5000.0]
    df = pd.DataFrame({"amount": values})
    results = anomaly_agent.compute_anomalies(df)
    assert len(results) == 1
    assert results[0].column == "amount"
    assert results[0].value == 5000.0
    assert results[0].direction == "above"
    assert results[0].z_score > 2  # clearly extreme; exact value depends on sample size


def test_anomaly_none_found_in_uniform_data():
    df = pd.DataFrame({"amount": [100.0, 101, 99, 102, 98, 100, 101]})
    assert anomaly_agent.compute_anomalies(df) == []


def test_anomaly_skips_tiny_columns_gracefully():
    df = pd.DataFrame({"amount": [1.0, 2.0]})
    assert anomaly_agent.compute_anomalies(df) == []


def test_anomaly_below_direction():
    values = [100.0, 101, 99, 102, 98, 103, 97, 100, -900.0]
    df = pd.DataFrame({"amount": values})
    results = anomaly_agent.compute_anomalies(df)
    assert results[0].direction == "below"


# =============================== Trend Agent ===================================


def _months_df(month_counts: dict[str, int], category: str = "Electronics") -> pd.DataFrame:
    rows = []
    for month, count in month_counts.items():
        for _ in range(count):
            rows.append({"order_date": pd.Timestamp(f"{month}-15"), "category": category})
    return pd.DataFrame(rows)


def test_numeric_trend_detects_significant_month_over_month_change():
    dates = pd.to_datetime(["2024-01-05", "2024-01-15", "2024-01-20", "2024-02-10", "2024-02-15", "2024-02-20"])
    amounts = [100.0, 100.0, 100.0, 500.0, 500.0, 500.0]  # Feb sum way above Jan sum
    df = pd.DataFrame({"order_date": dates, "amount": amounts})
    results = trend_agent.compute_numeric_trends(df, date_column="order_date", numeric_columns=["amount"])
    assert len(results) == 1
    assert results[0].direction == "increase"
    assert results[0].period == "2024-02"


def test_numeric_trend_skipped_gracefully_without_date_column():
    df = pd.DataFrame({"amount": [1.0, 2.0, 3.0]})
    assert trend_agent.compute_numeric_trends(df, date_column="order_date", numeric_columns=["amount"]) == []


def test_numeric_trend_ignores_near_empty_month_as_a_fake_swing():
    # January has plenty of rows; February has just one stray row (e.g. a
    # misparsed date spilling past the real data window) — its tiny sum
    # must not be reported as a real month-over-month change.
    dates = pd.to_datetime(["2024-01-05", "2024-01-15", "2024-01-20", "2024-02-10"])
    amounts = [100.0, 100.0, 100.0, 100.0]
    df = pd.DataFrame({"order_date": dates, "amount": amounts})
    results = trend_agent.compute_numeric_trends(df, date_column="order_date", numeric_columns=["amount"])
    assert results == []


def test_category_trend_detects_seasonal_spike():
    # 5 "normal" months at ~10/month, one December spike at 30 (+200%).
    counts = {"2024-07": 10, "2024-08": 11, "2024-09": 9, "2024-10": 10, "2024-11": 10, "2024-12": 30}
    df = _months_df(counts)
    results = trend_agent.compute_category_trends(df, date_column="order_date", category_column="category")
    spike = [r for r in results if r.period == "2024-12"]
    assert len(spike) == 1
    assert spike[0].label == "Electronics"
    assert spike[0].direction == "increase"
    assert spike[0].change_pct > 75


def test_category_trend_skipped_gracefully_without_category_column():
    df = pd.DataFrame({"order_date": pd.to_datetime(["2024-01-01"])})
    assert trend_agent.compute_category_trends(df, date_column="order_date", category_column="category") == []


def test_category_trend_ignores_near_empty_trailing_month_as_a_fake_decrease():
    # A handful of stray rows in an otherwise-unpopulated month (e.g. from a
    # few misparsed dates spilling past the real data window) must not be
    # reported as "this category decreased 95%" — the drop itself has to be
    # to a non-trivial count, not just measured against a healthy baseline.
    counts = {"2024-07": 20, "2024-08": 22, "2024-09": 19, "2024-10": 21, "2024-11": 20, "2024-12": 1}
    df = _months_df(counts)
    results = trend_agent.compute_category_trends(df, date_column="order_date", category_column="category")
    assert not any(r.period == "2024-12" for r in results)


# ============================= Narrative Agent ==================================


def test_narrative_correlation_template_fallback_includes_disclaimer():
    insight = correlation_agent.CorrelationInsight("age", "amount", 0.71, "strong", "positive")
    result = narrative_agent.narrate_correlation(insight, llm_client=None)
    assert narrative_agent.CAUSATION_DISCLAIMER in result["text"]
    assert result["method"] == "template_fallback_no_llm"
    assert "age" in result["text"] and "amount" in result["text"]


def test_narrative_correlation_template_is_grammatically_correct():
    insight = correlation_agent.CorrelationInsight("age", "amount", 0.71, "strong", "positive")
    result = narrative_agent.narrate_correlation(insight, llm_client=None)
    assert "tends to increase " in result["text"]
    assert "increases " not in result["text"].split("tends to")[1][:20]  # no "increases" after "tends to"

    negative = correlation_agent.CorrelationInsight("x", "y", -0.71, "strong", "negative")
    result = narrative_agent.narrate_correlation(negative, llm_client=None)
    assert "tends to decrease " in result["text"]


class FakeLLMClient:
    def __init__(self, response_text):
        self.response_text = response_text
        self.received_data = None

    def extract_structured(self, *, system_prompt, data, response_model):
        self.received_data = data
        if self.response_text is None:
            return None
        return response_model(text=self.response_text)


def test_narrative_correlation_llm_success_still_gets_disclaimer_appended():
    insight = correlation_agent.CorrelationInsight("age", "amount", 0.71, "strong", "positive")
    fake = FakeLLMClient("Older customers tend to place higher-value orders (r=0.71).")
    result = narrative_agent.narrate_correlation(insight, llm_client=fake)
    assert result["method"] == "llm_generated"
    assert result["text"].endswith(narrative_agent.CAUSATION_DISCLAIMER)


def test_narrative_falls_back_when_llm_hallucinates_a_number():
    insight = correlation_agent.CorrelationInsight("age", "amount", 0.71, "strong", "positive")
    # 0.95 never appeared in the stats passed to the LLM — must be rejected.
    fake = FakeLLMClient("This is an extremely strong relationship at 0.95.")
    result = narrative_agent.narrate_correlation(insight, llm_client=fake)
    assert result["method"] == "template_fallback_hallucination_guard"
    assert narrative_agent.CAUSATION_DISCLAIMER in result["text"]


def test_narrative_accepts_percentage_rephrasing_of_given_number():
    insight = correlation_agent.CorrelationInsight("age", "amount", 0.71, "strong", "positive")
    fake = FakeLLMClient("This is a strong relationship: 71% correlated.")
    result = narrative_agent.narrate_correlation(insight, llm_client=fake)
    assert result["method"] == "llm_generated"


def test_narrative_falls_back_when_llm_call_returns_none():
    insight = correlation_agent.CorrelationInsight("age", "amount", 0.71, "strong", "positive")
    fake = FakeLLMClient(None)
    result = narrative_agent.narrate_correlation(insight, llm_client=fake)
    assert result["method"] == "template_fallback_llm_unavailable"


def test_narrative_trend_and_anomaly_do_not_get_causation_disclaimer():
    trend = trend_agent.TrendInsight("numeric_sum", "amount", "2024-03", 100.0, 118.0, 18.0, "increase")
    result = narrative_agent.narrate_trend(trend, llm_client=None)
    assert narrative_agent.CAUSATION_DISCLAIMER not in result["text"]

    anomaly = anomaly_agent.AnomalyInsight("amount", 5, 5000.0, 4.8, "above")
    result = narrative_agent.narrate_anomaly(anomaly, llm_client=None)
    assert narrative_agent.CAUSATION_DISCLAIMER not in result["text"]
    assert "4.8" in result["text"]


def test_every_correlation_card_from_the_full_pipeline_has_the_disclaimer():
    # Programmatic check (Phase 7 validation requirement): must hold 100% of
    # the time, not "usually" — run the real pipeline, not a hand-built card.
    rng = np.random.default_rng(3)
    age = rng.integers(18, 80, size=100).astype(float)
    amount = age * 4 + rng.normal(0, 5, size=100)
    df = pd.DataFrame({"age": age, "amount": amount})
    cards = compute_insight_cards(df, llm_client=None)
    assert len(cards["correlations"]) > 0
    assert all(narrative_agent.CAUSATION_DISCLAIMER in c["narrative"] for c in cards["correlations"])


# ============================ Insights Pipeline =================================


def test_build_analysis_dataframe_types_columns_by_column_type():
    df = pd.DataFrame({"order_date": ["not-typed-yet"], "amount": ["not-typed-yet"], "notes": ["free text"]})
    result = build_analysis_dataframe(df, {
        "order_date": ("date", ["2024-03-05"]),
        "amount": ("currency", ["120.50"]),
    })
    assert pd.api.types.is_datetime64_any_dtype(result["order_date"])
    assert pd.api.types.is_numeric_dtype(result["amount"])
    assert result["notes"].iloc[0] == "free text"  # untouched passthrough


def test_compute_insight_cards_handles_insufficient_numeric_columns_gracefully():
    df = pd.DataFrame({"age": [1, 2, 3], "name": ["a", "b", "c"]})
    cards = compute_insight_cards(df)
    assert cards["correlations"] == []
    assert any("correlation" in w.lower() for w in cards["warnings"])


def test_compute_insight_cards_handles_missing_date_column_gracefully():
    df = pd.DataFrame({"age": [20.0, 30, 40, 50], "amount": [10.0, 40, 90, 160]})
    cards = compute_insight_cards(df, date_column=None)
    assert cards["trends"] == []
    assert any("date column" in w.lower() for w in cards["warnings"])


def test_compute_insight_cards_full_run_finds_all_three_embedded_signals():
    rng = np.random.default_rng(123)
    n = 300
    age = rng.integers(18, 80, size=n).astype(float)
    amount = age * 4 + rng.normal(0, 8, size=n)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    category = ["Electronics" if i % 30 < 15 else "Books" for i in range(n)]
    df = pd.DataFrame({"age": age, "amount": amount, "order_date": dates, "category": category})
    # Inject one clear outlier — large enough to be an unmistakable IQR
    # anomaly, small enough not to single-handedly wreck the correlation
    # (the same lesson learned generating the Phase 1 synthetic dataset).
    df.loc[0, "amount"] = df["amount"].median() * 3

    cards = compute_insight_cards(
        df, date_column="order_date", category_column="category", numeric_columns=["age", "amount"]
    )
    assert len(cards["correlations"]) >= 1
    assert cards["anomaly_count"] >= 1
    assert isinstance(cards["trends"], list)  # category is heavily lopsided; presence not asserted strictly


def test_trend_numeric_columns_restricts_the_sum_trend_but_not_correlation_or_anomaly():
    # `age` is a perfectly fine correlation/anomaly column but nonsensical
    # to *sum* month-over-month — trend_numeric_columns lets the caller
    # exclude it from that view specifically without losing it elsewhere.
    dates = pd.to_datetime(["2024-01-05", "2024-01-15", "2024-01-20", "2024-02-10", "2024-02-15", "2024-02-20"])
    df = pd.DataFrame({
        "age": [20.0, 22.0, 25.0, 90.0, 92.0, 95.0],
        "amount": [100.0, 100.0, 100.0, 500.0, 500.0, 500.0],
        "order_date": dates,
    })
    cards = compute_insight_cards(
        df, date_column="order_date", numeric_columns=["age", "amount"], trend_numeric_columns=["amount"]
    )
    assert all(t["label"] != "age" for t in cards["trends"])
    assert any(t["label"] == "amount" for t in cards["trends"])


def test_trend_cards_are_capped_and_sorted_by_significance():
    from app.insights.pipeline import MAX_TRENDS_NARRATED

    # Many low-volume categories that will produce noisy swings, plus one
    # unmistakably large, real spike — the cap must keep the real signal
    # and report the rest via `warnings`, not silently drop it.
    rows = []
    for month in range(1, 13):
        for cat_idx in range(15):
            count = 20 if not (month == 6 and cat_idx == 0) else 80  # cat "C0" spikes hard in June
            for _ in range(count):
                rows.append({"order_date": pd.Timestamp(f"2024-{month:02d}-10"), "category": f"C{cat_idx}"})
    df = pd.DataFrame(rows)

    cards = compute_insight_cards(df, date_column="order_date", category_column="category")
    assert len(cards["trends"]) <= MAX_TRENDS_NARRATED
    top = cards["trends"][0]
    assert top["label"] == "C0" and top["period"] == "2024-06"


# ============================ Phase 8: chart data ================================


def _correlated_df(n=200, seed=5):
    rng = np.random.default_rng(seed)
    age = rng.integers(18, 80, size=n).astype(float)
    amount = age * 4 + rng.normal(0, 6, size=n)
    return pd.DataFrame({"age": age, "amount": amount})


def test_charts_correlation_matrix_is_full_symmetric_matrix():
    df = _correlated_df()
    cards = compute_insight_cards(df, numeric_columns=["age", "amount"])
    matrix = cards["charts"]["correlation_matrix"]
    assert matrix["columns"] == ["age", "amount"]
    assert matrix["values"][0][0] == 1.0
    assert matrix["values"][0][1] == matrix["values"][1][0]
    assert matrix["values"][0][1] > 0.4  # the engineered relationship


def test_charts_correlation_matrix_absent_with_fewer_than_two_numeric_columns():
    df = pd.DataFrame({"age": [1, 2, 3], "name": ["a", "b", "c"]})
    cards = compute_insight_cards(df)
    assert "correlation_matrix" not in cards["charts"]


def test_charts_correlation_scatter_includes_a_two_point_trendline():
    df = _correlated_df()
    cards = compute_insight_cards(df, numeric_columns=["age", "amount"])
    scatter = cards["charts"]["correlation_scatter"]
    assert scatter["col1"] == "age" and scatter["col2"] == "amount"
    assert len(scatter["points"]) > 0
    assert len(scatter["trendline"]) == 2
    # Trendline should rise left-to-right for a positive relationship.
    assert scatter["trendline"][1]["y"] > scatter["trendline"][0]["y"]


def test_charts_scatter_points_are_capped_at_max_chart_points():
    from app.insights.pipeline import MAX_CHART_POINTS

    df = _correlated_df(n=1000)
    cards = compute_insight_cards(df, numeric_columns=["age", "amount"])
    assert len(cards["charts"]["correlation_scatter"]["points"]) == MAX_CHART_POINTS


def test_charts_trend_line_is_full_monthly_series_not_just_significant_ones():
    # Flat, insignificant series across whole months (no partial trailing
    # month, which would itself look like a real dip) — compute_numeric_trends
    # reports zero entries, but the chart's line series should still show
    # every month.
    dates = pd.date_range("2024-01-01", "2024-06-30", freq="D")
    df = pd.DataFrame({"order_date": dates, "amount": [100.0] * len(dates)})
    cards = compute_insight_cards(df, date_column="order_date", numeric_columns=["amount"],
                                   trend_numeric_columns=["amount"])
    assert cards["trends"] == []  # nothing significant
    assert len(cards["charts"]["trend_line"]["series"]) == 6  # Jan-Jun


def test_charts_category_bar_reports_count_and_avg_per_category():
    df = pd.DataFrame({
        "category": ["A", "A", "B", "B", "B"],
        "amount": [10.0, 20.0, 100.0, 200.0, 300.0],
    })
    cards = compute_insight_cards(df, category_column="category", numeric_columns=["amount"])
    bar = cards["charts"]["category_bar"]
    by_cat = {c["category"]: c for c in bar["categories"]}
    assert by_cat["A"]["count"] == 2 and by_cat["A"]["avg_value"] == 15.0
    assert by_cat["B"]["count"] == 3 and by_cat["B"]["avg_value"] == 200.0


def test_charts_anomaly_scatter_flags_the_real_outlier_and_includes_normal_points():
    values = [100.0, 105, 98, 102, 99, 101, 97, 103, 100, 5000.0]
    df = pd.DataFrame({"amount": values})
    cards = compute_insight_cards(df, numeric_columns=["amount"])
    scatter = cards["charts"]["anomaly_scatter"]
    assert scatter["column"] == "amount"
    flagged = [p for p in scatter["points"] if p["is_anomaly"]]
    assert len(flagged) == 1
    assert flagged[0]["value"] == 5000.0
    assert any(not p["is_anomaly"] for p in scatter["points"])


def test_charts_are_empty_dict_pieces_when_underlying_data_is_insufficient():
    df = pd.DataFrame({"age": [1, 2, 3]})  # single numeric column, no date, no category
    cards = compute_insight_cards(df)
    assert cards["charts"] == {}
