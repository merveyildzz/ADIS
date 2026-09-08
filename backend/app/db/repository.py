"""All reads/writes to the four core tables go through this module.

Every function here uses the ORM exclusively — no f-string/`.format()`-built
SQL, anywhere. This is what makes the SQL-injection guarantee in Phase 2's
validation requirements possible: a malicious cell value is always bound as
a parameter, never spliced into a query string.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuditLog, CleanedRecord, FeedbackCorrection, RawUpload, UploadStatus


class RecordNotFoundError(Exception):
    """Raised when a correction is submitted for a record_id that doesn't exist."""


class DatabaseWriteError(Exception):
    """Raised when a write fails for reasons outside caller control (lock
    contention, corrupted file, etc.) — callers translate this into a clean,
    user-facing error instead of leaking the underlying DB exception."""


@dataclass(frozen=True)
class CleanedRecordInput:
    column_name: str
    original_value: str | None
    cleaned_value: str | None
    confidence_score: float
    agent_type: str
    column_type: str | None = None
    # 0-based position in the source file. Defaults to 0 for callers that
    # don't care about row alignment (most existing tests); the real
    # cleaning pipeline always sets this to the row's actual index so
    # Phase 7 can reconstruct a wide DataFrame from this long-format table.
    row_index: int = 0


@dataclass(frozen=True)
class CleaningAuditEntry:
    """Paired 1:1 with a CleanedRecordInput — becomes the audit_log row that
    lineage drill-down queries by record_id."""
    agent_name: str
    action: str
    details: str | None


def create_raw_upload(db: Session, *, filename: str, row_count: int | None = None) -> RawUpload:
    upload = RawUpload(filename=filename, row_count=row_count, status=UploadStatus.PENDING)
    db.add(upload)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not create upload record for '{filename}'.") from exc
    # No db.refresh() needed: the session is expire_on_commit=False, so
    # `upload` (including its now-populated autoincrement PK) is already
    # valid in-memory post-commit — an extra refresh is only a fragile,
    # unnecessary round-trip.
    return upload


def set_upload_status(db: Session, *, upload_id: int, status: UploadStatus, row_count: int | None = None) -> None:
    upload = db.get(RawUpload, upload_id)
    if upload is None:
        raise DatabaseWriteError(f"Upload {upload_id} does not exist.")
    upload.status = status
    if row_count is not None:
        upload.row_count = row_count
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not update status for upload {upload_id}.") from exc


def save_insights(db: Session, *, upload_id: int, insights_json: str) -> None:
    upload = db.get(RawUpload, upload_id)
    if upload is None:
        raise DatabaseWriteError(f"Upload {upload_id} does not exist.")
    upload.insights_json = insights_json
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not save insights for upload {upload_id}.") from exc


def bulk_insert_cleaned_records(db: Session, *, upload_id: int, records: Sequence[CleanedRecordInput]) -> None:
    """Writes an entire cleaning run's output as one transaction: either every
    row lands, or (on any failure — a bad row, a lock timeout) none do,
    leaving `cleaned_records` free of partially-written state for this upload."""
    try:
        db.add_all([
            CleanedRecord(
                upload_id=upload_id,
                column_name=r.column_name,
                original_value=r.original_value,
                cleaned_value=r.cleaned_value,
                confidence_score=r.confidence_score,
                agent_type=r.agent_type,
                column_type=r.column_type,
                row_index=r.row_index,
            )
            for r in records
        ])
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(
            f"Could not save cleaned records for upload {upload_id}; the batch was rolled back."
        ) from exc


def bulk_insert_cleaned_records_with_audit(
    db: Session,
    *,
    upload_id: int,
    entries: Sequence[tuple[CleanedRecordInput, CleaningAuditEntry]],
) -> list[int]:
    """Like `bulk_insert_cleaned_records`, but also writes one audit_log row
    per cleaned record, linked by record_id — one transaction, so a cell's
    row and its lineage entry always exist together or not at all."""
    try:
        records = [
            CleanedRecord(
                upload_id=upload_id,
                column_name=r.column_name,
                original_value=r.original_value,
                cleaned_value=r.cleaned_value,
                confidence_score=r.confidence_score,
                agent_type=r.agent_type,
                column_type=r.column_type,
                row_index=r.row_index,
            )
            for r, _ in entries
        ]
        db.add_all(records)
        db.flush()  # assigns record_id to each, without committing yet

        db.add_all([
            AuditLog(
                upload_id=upload_id,
                record_id=record.record_id,
                agent_name=audit.agent_name,
                action=audit.action,
                details=audit.details,
            )
            for record, (_, audit) in zip(records, entries)
        ])
        db.commit()
        return [r.record_id for r in records]
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(
            f"Could not save cleaned records for upload {upload_id}; the batch was rolled back."
        ) from exc


def list_cleaned_records_for_columns(
    db: Session, *, upload_id: int, column_names: Sequence[str], limit: int = 200_000
) -> list[CleanedRecord]:
    """Unpaginated (but still bounded) read used internally by Phase 7 to
    reconstruct a wide DataFrame for statistical analysis. Distinct from
    `get_cleaned_records`: that one exists to serve the UI a page at a time
    (capped at `max_page_size`, ~hundreds of rows); this one needs the
    dataset's full column(s), which for insight computation is the point —
    a `limit` still bounds it against a truly pathological upload."""
    stmt = (
        select(CleanedRecord)
        .where(CleanedRecord.upload_id == upload_id, CleanedRecord.column_name.in_(column_names))
        .order_by(CleanedRecord.column_name, CleanedRecord.row_index)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


_SORTABLE_COLUMNS = {
    "record_id": CleanedRecord.record_id,
    "column_name": CleanedRecord.column_name,
    "confidence_score": CleanedRecord.confidence_score,
    "original_value": CleanedRecord.original_value,
    "cleaned_value": CleanedRecord.cleaned_value,
}


def get_cleaned_records(
    db: Session,
    *,
    upload_id: int,
    column_name: str | None = None,
    max_confidence: float | None = None,
    sort_by: str = "record_id",
    sort_dir: str = "asc",
    limit: int = 100,
    offset: int = 0,
) -> list[CleanedRecord]:
    """Paginated, parameterized read. `limit` is always capped server-side —
    a caller can request fewer rows but never more than the configured max,
    so a single request can't pull an unbounded result set into memory.
    `sort_by`/`sort_dir` are validated against a fixed allowlist of ORM
    column attributes — never interpolated into the query as raw strings."""
    capped_limit = min(limit, get_settings().max_page_size)
    stmt = select(CleanedRecord).where(CleanedRecord.upload_id == upload_id)
    if column_name is not None:
        stmt = stmt.where(CleanedRecord.column_name == column_name)
    if max_confidence is not None:
        stmt = stmt.where(CleanedRecord.confidence_score < max_confidence)

    sort_column = _SORTABLE_COLUMNS.get(sort_by, CleanedRecord.record_id)
    order_expr = sort_column.desc() if sort_dir == "desc" else sort_column.asc()
    # record_id as a tiebreaker keeps pagination stable across pages when
    # the primary sort key has duplicate values.
    stmt = stmt.order_by(order_expr, CleanedRecord.record_id).limit(capped_limit).offset(offset)
    return list(db.scalars(stmt).all())


def list_all_cleaned_records_for_upload(db: Session, *, upload_id: int, limit: int = 500_000) -> list[CleanedRecord]:
    """Every cleaned_records row for this upload, across all columns —
    used by the export feature to rebuild the full cleaned file. Distinct
    from `list_cleaned_records_for_columns` only in not filtering by column."""
    stmt = (
        select(CleanedRecord)
        .where(CleanedRecord.upload_id == upload_id)
        .order_by(CleanedRecord.column_name, CleanedRecord.row_index)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def list_distinct_columns(db: Session, *, upload_id: int) -> list[str]:
    """Every column name that has cleaned_records for this upload, in the
    order first seen — used to populate the UI's column-filter dropdown."""
    stmt = (
        select(CleanedRecord.column_name)
        .where(CleanedRecord.upload_id == upload_id)
        .distinct()
        .order_by(CleanedRecord.column_name)
    )
    return list(db.scalars(stmt).all())


def count_cleaned_records(
    db: Session, *, upload_id: int, column_name: str | None = None, max_confidence: float | None = None
) -> int:
    """Same filters as get_cleaned_records, for the UI's pagination total —
    a bounded COUNT query, not a full fetch-and-len() in the app."""
    stmt = select(func.count()).select_from(CleanedRecord).where(CleanedRecord.upload_id == upload_id)
    if column_name is not None:
        stmt = stmt.where(CleanedRecord.column_name == column_name)
    if max_confidence is not None:
        stmt = stmt.where(CleanedRecord.confidence_score < max_confidence)
    return db.scalar(stmt) or 0


def get_cleaned_record(db: Session, *, record_id: int) -> CleanedRecord | None:
    return db.get(CleanedRecord, record_id)


def get_lineage_for_record(db: Session, *, record_id: int) -> list[AuditLog]:
    """The full pipeline history for one cell — what Phase 5's drill-down
    panel renders. An empty list is expected (not an error) whenever a cell
    predates lineage tracking or simply has no recorded events."""
    stmt = select(AuditLog).where(AuditLog.record_id == record_id).order_by(AuditLog.log_id)
    return list(db.scalars(stmt).all())


def get_raw_upload(db: Session, *, upload_id: int) -> RawUpload | None:
    return db.get(RawUpload, upload_id)


def list_raw_uploads(db: Session, *, limit: int = 100, offset: int = 0) -> list[RawUpload]:
    capped_limit = min(limit, get_settings().max_page_size)
    stmt = select(RawUpload).order_by(RawUpload.upload_id.desc()).limit(capped_limit).offset(offset)
    return list(db.scalars(stmt).all())


def get_feedback_correction(db: Session, *, column_type: str, original_value: str) -> FeedbackCorrection | None:
    stmt = select(FeedbackCorrection).where(
        FeedbackCorrection.column_type == column_type,
        FeedbackCorrection.original_value == original_value,
    )
    return db.scalars(stmt).first()


def list_feedback_corrections(db: Session, *, column_type: str, limit: int = 500) -> list[FeedbackCorrection]:
    """All prior corrections for one column type, most recent first — used
    to build the in-memory lookup an agent checks before cleaning a column,
    and as a source of few-shot examples for the Address Agent's LLM path.
    Bounded by `limit` for the same reason every other read here is capped."""
    capped_limit = min(limit, get_settings().max_page_size * 5)
    stmt = (
        select(FeedbackCorrection)
        .where(FeedbackCorrection.column_type == column_type)
        .order_by(FeedbackCorrection.created_at.desc())
        .limit(capped_limit)
    )
    return list(db.scalars(stmt).all())


def upsert_feedback_correction(
    db: Session, *, column_type: str, original_value: str, agent_output: str | None, corrected_value: str
) -> FeedbackCorrection:
    """A repeat correction for the same (column_type, original_value) updates
    the existing row rather than accumulating duplicates — relies on the
    unique constraint on that pair."""
    existing = get_feedback_correction(db, column_type=column_type, original_value=original_value)
    try:
        if existing is not None:
            existing.corrected_value = corrected_value
            existing.agent_output = agent_output
            db.commit()
            return existing

        correction = FeedbackCorrection(
            column_type=column_type,
            original_value=original_value,
            agent_output=agent_output,
            corrected_value=corrected_value,
        )
        db.add(correction)
        db.commit()
        return correction
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError("Could not save feedback correction.") from exc


def submit_correction(db: Session, *, record_id: int, corrected_value: str) -> CleanedRecord:
    """A user manually fixes a cell: the cleaned_records row is updated (and
    marked fully confident — a human confirmed it), the correction is
    upserted into feedback_corrections for future reuse, and a linked
    audit_log entry records that this was a user correction, not an agent
    decision. One transaction — all three or none."""
    record = db.get(CleanedRecord, record_id)
    if record is None:
        raise RecordNotFoundError(f"Cleaned record {record_id} does not exist.")

    previous_value = record.cleaned_value
    column_type = record.column_type or record.agent_type

    try:
        record.cleaned_value = corrected_value
        record.confidence_score = 100.0

        if record.original_value is not None:
            existing = get_feedback_correction(db, column_type=column_type, original_value=record.original_value)
            if existing is not None:
                existing.corrected_value = corrected_value
                existing.agent_output = previous_value
            else:
                db.add(FeedbackCorrection(
                    column_type=column_type,
                    original_value=record.original_value,
                    agent_output=previous_value,
                    corrected_value=corrected_value,
                ))

        db.add(AuditLog(
            upload_id=record.upload_id,
            record_id=record.record_id,
            agent_name="UserFeedback",
            action="user_correction",
            details=json.dumps({"previous_value": previous_value, "corrected_value": corrected_value}),
        ))

        db.commit()
        return record
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not save correction for record {record_id}.") from exc


def create_audit_log(
    db: Session, *, upload_id: int, agent_name: str, action: str, details: str | None = None
) -> AuditLog:
    entry = AuditLog(upload_id=upload_id, agent_name=agent_name, action=action, details=details)
    db.add(entry)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not write audit log for upload {upload_id}.") from exc
    return entry


def get_audit_log_for_upload(db: Session, *, upload_id: int, limit: int = 100, offset: int = 0) -> list[AuditLog]:
    capped_limit = min(limit, get_settings().max_page_size)
    stmt = (
        select(AuditLog)
        .where(AuditLog.upload_id == upload_id)
        .order_by(AuditLog.log_id)
        .limit(capped_limit)
        .offset(offset)
    )
    return list(db.scalars(stmt).all())
