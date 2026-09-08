"""Phase 2 validation requirements: SQL-injection safety, transaction
atomicity, concurrent writes, FK enforcement, and capped pagination — all
against a real (temp-file) SQLite database, not mocks."""
import threading

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import repository as repo
from app.db.base import Base, create_app_engine
from app.db.models import CleanedRecord, UploadStatus


@pytest.fixture()
def engine(tmp_path):
    eng = create_app_engine(Settings(database_url=f"sqlite:///{tmp_path}/test.db"))
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_all_four_tables_created(engine):
    tables = set(inspect(engine).get_table_names())
    assert {"raw_uploads", "cleaned_records", "feedback_corrections", "audit_log"} <= tables


# --- SQL injection safety -------------------------------------------------

SQL_PAYLOAD = "Robert'); DROP TABLE cleaned_records;--"


def test_sql_injection_payload_stored_as_inert_text_and_tables_survive(engine, session):
    upload = repo.create_raw_upload(session, filename="dirty.csv")
    repo.bulk_insert_cleaned_records(
        session,
        upload_id=upload.upload_id,
        records=[
            repo.CleanedRecordInput(
                column_name="name",
                original_value=SQL_PAYLOAD,
                cleaned_value=SQL_PAYLOAD,
                confidence_score=10.0,
                agent_type="unclassified",
            )
        ],
    )

    tables_after = set(inspect(engine).get_table_names())
    assert "cleaned_records" in tables_after  # table was NOT dropped

    stored = repo.get_cleaned_records(session, upload_id=upload.upload_id)
    assert len(stored) == 1
    assert stored[0].original_value == SQL_PAYLOAD  # stored verbatim as data
    assert session.execute(text("SELECT COUNT(*) FROM cleaned_records")).scalar() == 1


def test_sql_injection_payload_in_filename_also_inert(engine, session):
    payload = "x'; DROP TABLE raw_uploads;--"
    upload = repo.create_raw_upload(session, filename=payload)
    assert upload.filename == payload
    assert "raw_uploads" in set(inspect(engine).get_table_names())
    assert session.execute(text("SELECT COUNT(*) FROM raw_uploads")).scalar() == 1


# --- Transaction atomicity -------------------------------------------------


def test_bulk_insert_is_all_or_nothing_on_failure(engine, session):
    upload = repo.create_raw_upload(session, filename="batch.csv")
    good = repo.CleanedRecordInput("age", "30", "30", 95.0, "NumericAgent")
    # confidence_score=150 violates the CHECK constraint -> whole batch must roll back
    bad = repo.CleanedRecordInput("age", "bad", None, 150.0, "NumericAgent")

    with pytest.raises(repo.DatabaseWriteError):
        repo.bulk_insert_cleaned_records(session, upload_id=upload.upload_id, records=[good, bad])

    remaining = repo.get_cleaned_records(session, upload_id=upload.upload_id)
    assert remaining == []  # the valid row was NOT partially committed


def test_bulk_insert_succeeds_when_all_rows_are_valid(engine, session):
    upload = repo.create_raw_upload(session, filename="batch.csv")
    records = [repo.CleanedRecordInput("age", str(i), str(i), 90.0, "NumericAgent") for i in range(5)]
    repo.bulk_insert_cleaned_records(session, upload_id=upload.upload_id, records=records)
    assert len(repo.get_cleaned_records(session, upload_id=upload.upload_id, limit=10)) == 5


# --- Foreign key enforcement -------------------------------------------------


def test_cleaned_record_rejects_nonexistent_upload_id(engine, session):
    with pytest.raises(IntegrityError):
        session.add(CleanedRecord(
            upload_id=999999, column_name="age", original_value="1",
            cleaned_value="1", confidence_score=90.0, agent_type="NumericAgent",
        ))
        session.commit()
    session.rollback()


def test_deleting_upload_cascades_to_cleaned_records(engine, session):
    upload = repo.create_raw_upload(session, filename="cascade.csv")
    repo.bulk_insert_cleaned_records(
        session, upload_id=upload.upload_id,
        records=[repo.CleanedRecordInput("age", "1", "1", 90.0, "NumericAgent")],
    )
    session.delete(session.get(type(upload), upload.upload_id))
    session.commit()
    assert session.execute(text("SELECT COUNT(*) FROM cleaned_records")).scalar() == 0


# --- Pagination cap -------------------------------------------------


def test_result_size_is_capped_server_side_regardless_of_requested_limit(engine, session):
    upload = repo.create_raw_upload(session, filename="big.csv")
    records = [repo.CleanedRecordInput("age", str(i), str(i), 90.0, "NumericAgent") for i in range(50)]
    repo.bulk_insert_cleaned_records(session, upload_id=upload.upload_id, records=records)

    from app.config import get_settings
    get_settings.cache_clear()
    max_page = get_settings().max_page_size

    page = repo.get_cleaned_records(session, upload_id=upload.upload_id, limit=max_page + 10_000)
    assert len(page) <= max_page


# --- Feedback upsert / dedup -------------------------------------------------


def test_feedback_correction_upserts_instead_of_duplicating(engine, session):
    repo.upsert_feedback_correction(
        session, column_type="date", original_value="03/04/2024",
        agent_output="2024-03-04", corrected_value="2024-04-03",
    )
    repo.upsert_feedback_correction(
        session, column_type="date", original_value="03/04/2024",
        agent_output="2024-03-04", corrected_value="2024-04-03",
    )
    count = session.execute(text(
        "SELECT COUNT(*) FROM feedback_corrections WHERE column_type='date' AND original_value='03/04/2024'"
    )).scalar()
    assert count == 1


def test_feedback_lookup_returns_most_recent_correction(engine, session):
    repo.upsert_feedback_correction(
        session, column_type="date", original_value="03/04/2024",
        agent_output="2024-03-04", corrected_value="2024-04-03",
    )
    repo.upsert_feedback_correction(
        session, column_type="date", original_value="03/04/2024",
        agent_output="2024-03-04", corrected_value="2024-03-04",
    )
    found = repo.get_feedback_correction(session, column_type="date", original_value="03/04/2024")
    assert found.corrected_value == "2024-03-04"


# --- Concurrent writes -------------------------------------------------


def test_concurrent_writes_from_multiple_uploads_do_not_corrupt_state(engine):
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    n_threads = 8
    rows_per_thread = 20
    errors: list[Exception] = []

    def worker(i: int) -> None:
        db = SessionLocal()
        try:
            upload = repo.create_raw_upload(db, filename=f"concurrent-{i}.csv")
            records = [
                repo.CleanedRecordInput("age", str(j), str(j), 90.0, "NumericAgent")
                for j in range(rows_per_thread)
            ]
            repo.bulk_insert_cleaned_records(db, upload_id=upload.upload_id, records=records)
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    check_db = SessionLocal()
    total_uploads = check_db.execute(text("SELECT COUNT(*) FROM raw_uploads")).scalar()
    total_records = check_db.execute(text("SELECT COUNT(*) FROM cleaned_records")).scalar()
    check_db.close()
    assert total_uploads == n_threads
    assert total_records == n_threads * rows_per_thread


# --- Status transitions -------------------------------------------------


def test_set_upload_status_updates_row_count(engine, session):
    upload = repo.create_raw_upload(session, filename="status.csv")
    repo.set_upload_status(session, upload_id=upload.upload_id, status=UploadStatus.COMPLETED, row_count=42)
    refreshed = session.get(type(upload), upload.upload_id)
    assert refreshed.status == UploadStatus.COMPLETED
    assert refreshed.row_count == 42


def test_set_upload_status_on_missing_upload_raises_clean_error(engine, session):
    with pytest.raises(repo.DatabaseWriteError):
        repo.set_upload_status(session, upload_id=999999, status=UploadStatus.FAILED)


# --- Phase 6: feedback corrections & column_type -------------------------------------------------


def test_cleaned_record_stores_column_type(engine, session):
    upload = repo.create_raw_upload(session, filename="test.csv")
    repo.bulk_insert_cleaned_records(
        session, upload_id=upload.upload_id,
        records=[repo.CleanedRecordInput("order_date", "2024-01-15", "2024-01-15", 95.0, "DateAgent", "date")],
    )
    record = repo.get_cleaned_records(session, upload_id=upload.upload_id, limit=1)[0]
    assert record.column_type == "date"


def test_list_feedback_corrections_filters_by_column_type_and_orders_recent_first(engine, session):
    repo.upsert_feedback_correction(session, column_type="date", original_value="03/04/2024",
                                     agent_output="2024-03-04", corrected_value="2024-04-03")
    repo.upsert_feedback_correction(session, column_type="phone", original_value="12345",
                                     agent_output=None, corrected_value="+905551234567")
    date_corrections = repo.list_feedback_corrections(session, column_type="date")
    assert len(date_corrections) == 1
    assert date_corrections[0].original_value == "03/04/2024"


def test_submit_correction_updates_record_and_upserts_feedback_and_writes_audit(engine, session):
    upload = repo.create_raw_upload(session, filename="test.csv")
    record_ids = repo.bulk_insert_cleaned_records_with_audit(
        session, upload_id=upload.upload_id,
        entries=[(
            repo.CleanedRecordInput("order_date", "03/04/2024", "2024-04-03", 35.0, "DateAgent", "date"),
            repo.CleaningAuditEntry("DateAgent", "clean_value", '{"method": "ambiguous_guessed_low_confidence"}'),
        )],
    )
    record_id = record_ids[0]

    updated = repo.submit_correction(session, record_id=record_id, corrected_value="2024-03-04")
    assert updated.cleaned_value == "2024-03-04"
    assert updated.confidence_score == 100.0

    correction = repo.get_feedback_correction(session, column_type="date", original_value="03/04/2024")
    assert correction is not None
    assert correction.corrected_value == "2024-03-04"
    assert correction.agent_output == "2024-04-03"  # what the agent had produced, for context

    lineage = repo.get_lineage_for_record(session, record_id=record_id)
    assert any(entry.agent_name == "UserFeedback" and entry.action == "user_correction" for entry in lineage)


def test_submit_correction_on_repeat_upserts_rather_than_duplicating(engine, session):
    upload = repo.create_raw_upload(session, filename="test.csv")
    record_ids = repo.bulk_insert_cleaned_records_with_audit(
        session, upload_id=upload.upload_id,
        entries=[(
            repo.CleanedRecordInput("order_date", "03/04/2024", "2024-04-03", 35.0, "DateAgent", "date"),
            repo.CleaningAuditEntry("DateAgent", "clean_value", "{}"),
        )],
    )
    record_id = record_ids[0]
    repo.submit_correction(session, record_id=record_id, corrected_value="2024-03-04")
    repo.submit_correction(session, record_id=record_id, corrected_value="2024-03-04")

    count = session.execute(text(
        "SELECT COUNT(*) FROM feedback_corrections WHERE column_type='date' AND original_value='03/04/2024'"
    )).scalar()
    assert count == 1


def test_submit_correction_on_nonexistent_record_raises(engine, session):
    with pytest.raises(repo.RecordNotFoundError):
        repo.submit_correction(session, record_id=999999, corrected_value="whatever")
