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
