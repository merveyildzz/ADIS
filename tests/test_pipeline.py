import pandas as pd
import pytest
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import repository as repo
from app.db.base import Base, create_app_engine
from app.db.models import UploadStatus
from app.orchestrator.orchestrator import build_routing_plan
from app.pipeline import run_cleaning_pipeline


@pytest.fixture()
def session(tmp_path):
    engine = create_app_engine(Settings(database_url=f"sqlite:///{tmp_path}/test.db"))
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    yield db
    db.close()
    engine.dispose()


SAMPLE_DF = pd.DataFrame({
    "customer_id": [1, 2, 3],
    "name": ["Alice Smith", "Bob Jones", "Robert'); DROP TABLE cleaned_records;--"],
    "order_date": ["2024-01-15", "2024-02-20", "not a date"],
    "order_amount": ["$120.50", "1.500 TL", "85,50"],
    "customer_age": ["34", " thirty-five ", ""],
    "category": ["Books", "Toys", "Electronics"],
})


def test_pipeline_writes_cleaned_records_only_for_classified_columns(session):
    upload = repo.create_raw_upload(session, filename="test.csv")
    plan = build_routing_plan(SAMPLE_DF)
    summary = run_cleaning_pipeline(session, upload_id=upload.upload_id, df=SAMPLE_DF, routing_plan=plan)

    assert "order_date" in summary["columns_cleaned"]
    assert "order_amount" in summary["columns_cleaned"]
    assert "customer_age" in summary["columns_cleaned"]
    assert "customer_id" in summary["columns_unclassified"]
    assert "name" in summary["columns_unclassified"]
    assert "category" in summary["columns_unclassified"]

    records = repo.get_cleaned_records(session, upload_id=upload.upload_id, limit=100)
    # 3 classified columns x 3 rows = 9 cleaned_records rows; unclassified columns untouched
    assert len(records) == 9
    assert all(r.column_name in {"order_date", "order_amount", "customer_age"} for r in records)


def test_pipeline_assigns_row_index_matching_source_row_position(session):
    # Phase 7 rebuilds a wide DataFrame from row_index — a value at source
    # row i in one column must carry row_index=i, matching row i of every
    # other column, so columns can be correctly re-joined.
    upload = repo.create_raw_upload(session, filename="test.csv")
    plan = build_routing_plan(SAMPLE_DF)
    run_cleaning_pipeline(session, upload_id=upload.upload_id, df=SAMPLE_DF, routing_plan=plan)

    date_records = repo.list_cleaned_records_for_columns(session, upload_id=upload.upload_id, column_names=["order_date"])
    amount_records = repo.list_cleaned_records_for_columns(session, upload_id=upload.upload_id, column_names=["order_amount"])
    by_row_date = {r.row_index: r.original_value for r in date_records}
    by_row_amount = {r.row_index: r.original_value for r in amount_records}

    assert by_row_date[0] == "2024-01-15" and by_row_amount[0] == "$120.50"
    assert by_row_date[2] == "not a date" and by_row_amount[2] == "85,50"


def test_pipeline_stores_missing_cell_as_null_not_the_string_nan(session):
    # A blank CSV cell arrives as pandas NaN (float), not Python None — must
    # not be stringified into the literal text "nan" for storage/display.
    df = pd.DataFrame({"customer_age": ["34", None]})
    upload = repo.create_raw_upload(session, filename="missing.csv")
    plan = build_routing_plan(df)
    run_cleaning_pipeline(session, upload_id=upload.upload_id, df=df, routing_plan=plan)

    records = repo.get_cleaned_records(session, upload_id=upload.upload_id, limit=10)
    missing_row = next(r for r in records if r.cleaned_value is None)
    assert missing_row.original_value is None


def test_pipeline_marks_upload_completed_with_row_count(session):
    upload = repo.create_raw_upload(session, filename="test.csv")
    plan = build_routing_plan(SAMPLE_DF)
    run_cleaning_pipeline(session, upload_id=upload.upload_id, df=SAMPLE_DF, routing_plan=plan)

    refreshed = repo.get_raw_upload(session, upload_id=upload.upload_id)
    assert refreshed.status == UploadStatus.COMPLETED
    assert refreshed.row_count == 3


def test_pipeline_links_each_cleaned_record_to_its_own_lineage_entry(session):
    upload = repo.create_raw_upload(session, filename="test.csv")
    plan = build_routing_plan(SAMPLE_DF)
    run_cleaning_pipeline(session, upload_id=upload.upload_id, df=SAMPLE_DF, routing_plan=plan)

    records = repo.get_cleaned_records(session, upload_id=upload.upload_id, column_name="customer_age", limit=10)
    assert len(records) == 3

    spelled_out_record = next(r for r in records if r.original_value.strip() == "thirty-five")
    lineage = repo.get_lineage_for_record(session, record_id=spelled_out_record.record_id)
    assert len(lineage) == 1
    assert lineage[0].agent_name == "NumericAgent"
    assert "text_to_number_pattern" in lineage[0].details
    assert spelled_out_record.cleaned_value == "35"
    assert spelled_out_record.confidence_score == 98.0


def test_record_with_no_lineage_returns_empty_list_gracefully(session):
    upload = repo.create_raw_upload(session, filename="test.csv")
    # A cleaned_records row inserted without any matching audit_log entry —
    # simulates pre-existing data with no recorded history.
    repo.bulk_insert_cleaned_records(
        session, upload_id=upload.upload_id,
        records=[repo.CleanedRecordInput("age", "30", "30", 90.0, "NumericAgent")],
    )
    record = repo.get_cleaned_records(session, upload_id=upload.upload_id, limit=1)[0]
    lineage = repo.get_lineage_for_record(session, record_id=record.record_id)
    assert lineage == []


def test_sql_injection_payload_survives_pipeline_end_to_end(session):
    upload = repo.create_raw_upload(session, filename="test.csv")
    plan = build_routing_plan(SAMPLE_DF)
    run_cleaning_pipeline(session, upload_id=upload.upload_id, df=SAMPLE_DF, routing_plan=plan)

    # "name" is unclassified, so it never even reaches cleaned_records — but
    # the payload's presence in the source data must not have broken anything.
    from sqlalchemy import text
    tables = {"raw_uploads", "cleaned_records", "audit_log"}
    for t in tables:
        assert session.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() is not None


def test_pipeline_batch_rolls_back_completely_on_failure(session, monkeypatch):
    upload = repo.create_raw_upload(session, filename="test.csv")
    plan = build_routing_plan(SAMPLE_DF)

    original = repo.bulk_insert_cleaned_records_with_audit

    def boom(*args, **kwargs):
        raise repo.DatabaseWriteError("simulated failure")

    monkeypatch.setattr(repo, "bulk_insert_cleaned_records_with_audit", boom)
    with pytest.raises(repo.DatabaseWriteError):
        run_cleaning_pipeline(session, upload_id=upload.upload_id, df=SAMPLE_DF, routing_plan=plan)
    monkeypatch.setattr(repo, "bulk_insert_cleaned_records_with_audit", original)

    refreshed = repo.get_raw_upload(session, upload_id=upload.upload_id)
    assert refreshed.status == UploadStatus.FAILED
    assert repo.get_cleaned_records(session, upload_id=upload.upload_id, limit=10) == []


# --- Phase 6: self-improving feedback loop -------------------------------------------------


def test_correction_on_one_upload_is_reused_by_a_later_pipeline_run(session):
    # First run: an ambiguous date gets a low-confidence guess.
    upload1 = repo.create_raw_upload(session, filename="first.csv")
    df1 = pd.DataFrame({"order_date": ["03/04/2024"]})
    plan1 = build_routing_plan(df1)
    run_cleaning_pipeline(session, upload_id=upload1.upload_id, df=df1, routing_plan=plan1)
    first_record = repo.get_cleaned_records(session, upload_id=upload1.upload_id, limit=1)[0]
    assert first_record.confidence_score < 60

    # A user corrects it.
    repo.submit_correction(session, record_id=first_record.record_id, corrected_value="2024-03-04")

    # A second, independent upload with the exact same raw value should now
    # resolve it from feedback — no re-guessing.
    upload2 = repo.create_raw_upload(session, filename="second.csv")
    df2 = pd.DataFrame({"order_date": ["03/04/2024"]})
    plan2 = build_routing_plan(df2)
    run_cleaning_pipeline(session, upload_id=upload2.upload_id, df=df2, routing_plan=plan2)
    second_record = repo.get_cleaned_records(session, upload_id=upload2.upload_id, limit=1)[0]

    assert second_record.cleaned_value == "2024-03-04"
    assert second_record.confidence_score == 95.0
    lineage = repo.get_lineage_for_record(session, record_id=second_record.record_id)
    assert "reused_prior_feedback" in lineage[0].details
    assert "influenced_by_prior_feedback" in lineage[0].details


def test_pipeline_continues_normally_when_feedback_lookup_query_fails(session, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated DB error")

    monkeypatch.setattr(repo, "list_feedback_corrections", boom)

    upload = repo.create_raw_upload(session, filename="test.csv")
    plan = build_routing_plan(SAMPLE_DF)
    # Must not raise — feedback lookup is an optimization, not a dependency.
    summary = run_cleaning_pipeline(session, upload_id=upload.upload_id, df=SAMPLE_DF, routing_plan=plan)
    assert summary["rows"] == 3


def test_pipeline_works_normally_when_feedback_table_is_empty(session):
    upload = repo.create_raw_upload(session, filename="test.csv")
    plan = build_routing_plan(SAMPLE_DF)
    summary = run_cleaning_pipeline(session, upload_id=upload.upload_id, df=SAMPLE_DF, routing_plan=plan)
    assert summary["rows"] == 3
    for col_summary in summary["columns_cleaned"].values():
        assert col_summary["rows_influenced_by_feedback"] == 0


# --- Phase 7: insight computation wired into the cleaning pipeline -----------------------------


def test_pipeline_computes_and_persists_insights(session):
    import json

    import numpy as np

    rng = np.random.default_rng(0)
    n = 60
    age = rng.integers(18, 80, size=n).astype(float)
    amount_true = age * 4 + rng.normal(0, 8, size=n)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    df = pd.DataFrame({
        "customer_age": [str(int(a)) for a in age],
        "order_amount": [f"${a:.2f}" for a in amount_true],
        "order_date": dates.strftime("%Y-%m-%d"),
        "category": ["Electronics" if i % 10 == 0 else "Books" for i in range(n)],
    })

    upload = repo.create_raw_upload(session, filename="test.csv")
    plan = build_routing_plan(df)
    run_cleaning_pipeline(session, upload_id=upload.upload_id, df=df, routing_plan=plan)

    refreshed = repo.get_raw_upload(session, upload_id=upload.upload_id)
    assert refreshed.insights_json is not None
    cards = json.loads(refreshed.insights_json)
    assert "correlations" in cards and "trends" in cards and "anomalies" in cards
    assert len(cards["correlations"]) >= 1
    assert all("Correlation does not imply causation." in c["narrative"] for c in cards["correlations"])


def test_pipeline_insight_failure_does_not_fail_the_upload(session, monkeypatch):
    import app.pipeline as pipeline_module

    def boom(*args, **kwargs):
        raise RuntimeError("simulated insight computation failure")

    monkeypatch.setattr(pipeline_module, "compute_insight_cards", boom)

    upload = repo.create_raw_upload(session, filename="test.csv")
    plan = build_routing_plan(SAMPLE_DF)
    summary = run_cleaning_pipeline(session, upload_id=upload.upload_id, df=SAMPLE_DF, routing_plan=plan)
    assert summary["rows"] == 3

    refreshed = repo.get_raw_upload(session, upload_id=upload.upload_id)
    assert refreshed.status == UploadStatus.COMPLETED  # cleaning still succeeded
    assert refreshed.insights_json is None  # insights just weren't computed


# --- Content-based routing refactor: profiling + non-fatal rule evaluation ---


def test_all_columns_unclassifiable_dataset_produces_full_profiled_report(session):
    df = pd.DataFrame({
        "notes_a": [f"free text note number {i} with no discernible pattern" for i in range(20)],
        "notes_b": [f"another distinct free-form sentence {i}" for i in range(20)],
    })
    upload = repo.create_raw_upload(session, filename="unclassifiable.csv")
    plan = build_routing_plan(df)
    summary = run_cleaning_pipeline(session, upload_id=upload.upload_id, df=df, routing_plan=plan)

    assert summary["columns_cleaned"] == {}
    assert set(summary["columns_unclassified"]) == {"notes_a", "notes_b"}
    assert set(summary["columns_profiled"].keys()) == {"notes_a", "notes_b"}
    for profile in summary["columns_profiled"].values():
        assert profile["unique_count"] == 20
        assert profile["null_pct"] == 0.0


def test_rule_evaluation_failure_does_not_fail_the_upload(session, monkeypatch):
    import app.pipeline as pipeline_module

    def boom(*args, **kwargs):
        raise RuntimeError("simulated rule engine failure")

    monkeypatch.setattr(pipeline_module, "evaluate_rules_for_upload", boom)

    upload = repo.create_raw_upload(session, filename="test.csv")
    plan = build_routing_plan(SAMPLE_DF)
    summary = run_cleaning_pipeline(session, upload_id=upload.upload_id, df=SAMPLE_DF, routing_plan=plan)

    refreshed = repo.get_raw_upload(session, upload_id=upload.upload_id)
    assert refreshed.status == UploadStatus.COMPLETED
    assert summary["rows"] == 3


def test_detect_category_column_prefers_higher_cardinality_over_first_match():
    from app.pipeline import _detect_category_column

    # currency_hint-like column (3 values) appears before category (8
    # values) in column order — must not win just by being first.
    df = pd.DataFrame({
        "hint_flag": ["TRY", "USD", "TRY", "USD"] * 25,
        "category": [f"Cat{i % 8}" for i in range(100)],
    })
    assert _detect_category_column(df, exclude=set()) == "category"


def test_choose_event_date_column_prefers_narrower_span():
    from app.pipeline import _choose_event_date_column

    df = pd.DataFrame({
        "signup_date": pd.to_datetime(["2020-01-01", "2026-06-01"]),  # ~6.5 year span
        "order_date": pd.to_datetime(["2026-01-01", "2026-02-01"]),  # 1 month span
    })
    chosen = _choose_event_date_column(df, ["signup_date", "order_date"])
    assert chosen == "order_date"
