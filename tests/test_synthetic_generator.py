import pandas as pd

from app.synthetic.generator import generate_dataset

REQUIRED_COLUMNS = [
    "customer_id", "name", "email", "phone", "signup_date", "order_date",
    "address", "order_amount", "currency_hint", "customer_age", "category",
    "customer_segment",
]


def test_output_has_required_columns_in_order():
    df, _, _ = generate_dataset(seed=1, n_customers=50)
    assert list(df.columns) == REQUIRED_COLUMNS


def test_generation_is_deterministic_for_same_seed():
    df1, gt1, manifest1 = generate_dataset(seed=7, n_customers=50)
    df2, gt2, manifest2 = generate_dataset(seed=7, n_customers=50)
    pd.testing.assert_frame_equal(df1, df2)
    pd.testing.assert_frame_equal(gt1, gt2)
    assert manifest1 == manifest2


def test_different_seeds_produce_different_data():
    df1, _, _ = generate_dataset(seed=1, n_customers=50)
    df2, _, _ = generate_dataset(seed=2, n_customers=50)
    assert not df1.equals(df2)


def test_sql_injection_payloads_present_and_row_indices_match():
    df, _, manifest = generate_dataset(seed=42, n_customers=200)
    assert len(manifest["sql_injection_rows"]) > 0
    for entry in manifest["sql_injection_rows"]:
        assert df.iloc[entry["row_index"]]["name"] == entry["payload"]
        assert "DROP TABLE" in entry["payload"]


def test_ambiguous_date_rows_have_day_and_month_both_le_12():
    _, gt, manifest = generate_dataset(seed=42, n_customers=200)
    assert len(manifest["ambiguous_date_rows"]) >= 3
    for entry in manifest["ambiguous_date_rows"]:
        true_date = gt.iloc[entry["row_index"]]["order_date_true"]
        year, month, day = (int(p) for p in true_date.split("-"))
        assert day <= 12
        assert month <= 12


def test_outlier_rows_are_far_above_the_mean():
    _, gt, manifest = generate_dataset(seed=42, n_customers=200)
    assert len(manifest["outlier_rows"]) > 0
    mean_amount = gt["order_amount_true"].mean()
    std_amount = gt["order_amount_true"].std()
    for entry in manifest["outlier_rows"]:
        true_amount = gt.iloc[entry["row_index"]]["order_amount_true"]
        assert true_amount > mean_amount + 3 * std_amount


def test_embedded_age_amount_correlation_is_positive_and_significant():
    _, _, manifest = generate_dataset(seed=42, n_customers=500)
    assert manifest["age_amount_correlation"]["pearson_r"] > 0.4


def test_seasonal_anomaly_spike_is_in_target_range():
    _, _, manifest = generate_dataset(seed=42, n_customers=500)
    multiplier = manifest["seasonal_anomaly"]["spike_multiplier"]
    assert 2.0 <= multiplier <= 3.5


def test_segment_pattern_is_counter_intuitive():
    _, _, manifest = generate_dataset(seed=42, n_customers=500)
    orders = manifest["segment_pattern"]["avg_orders_per_customer_by_segment"]
    amounts = manifest["segment_pattern"]["avg_amount_by_segment"]
    assert orders["Budget"] > orders["Premium"]
    assert amounts["Premium"] > amounts["Budget"]


def test_currency_formats_cover_all_four_documented_styles():
    df, _, _ = generate_dataset(seed=42, n_customers=300)
    amounts = df["order_amount"].astype(str)
    assert amounts.str.startswith("$").any()
    assert amounts.str.endswith(" TL").any()
    assert amounts.str.startswith("₺").any()
    assert amounts.str.contains(",").any()


def test_date_formats_cover_all_four_documented_styles():
    df, _, _ = generate_dataset(seed=42, n_customers=300)
    dates = df["order_date"].astype(str)
    assert dates.str.match(r"^\d{4}-\d{2}-\d{2}$").any()
    assert dates.str.match(r"^\d{2}/\d{2}/\d{4}$").any()
    assert dates.str.match(r"^\d{2}-\d{2}-\d{4}$").any()
    assert dates.str.match(r"^[A-Za-z]+ \d{1,2}, \d{4}$").any()
