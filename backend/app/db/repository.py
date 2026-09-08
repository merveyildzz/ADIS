"""All reads/writes to the four core tables go through this module.

Every function here uses the ORM exclusively — no f-string/`.format()`-built
SQL, anywhere. This is what makes the SQL-injection guarantee in Phase 2's
validation requirements possible: a malicious cell value is always bound as
a parameter, never spliced into a query string.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuditLog, CleanedRecord, FeedbackCorrection, RawUpload, UploadStatus


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


def create_raw_upload(db: Session, *, filename: str, row_count: int | None = None) -> RawUpload:
    upload = RawUpload(filename=filename, row_count=row_count, status=UploadStatus.PENDING)
    db.add(upload)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not create upload record for '{filename}'.") from exc
    db.refresh(upload)
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
            )
            for r in records
        ])
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(
            f"Could not save cleaned records for upload {upload_id}; the batch was rolled back."
        ) from exc


def get_cleaned_records(
    db: Session,
    *,
    upload_id: int,
    column_name: str | None = None,
    max_confidence: float | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[CleanedRecord]:
    """Paginated, parameterized read. `limit` is always capped server-side —
    a caller can request fewer rows but never more than the configured max,
    so a single request can't pull an unbounded result set into memory."""
    capped_limit = min(limit, get_settings().max_page_size)
    stmt = select(CleanedRecord).where(CleanedRecord.upload_id == upload_id)
    if column_name is not None:
        stmt = stmt.where(CleanedRecord.column_name == column_name)
    if max_confidence is not None:
        stmt = stmt.where(CleanedRecord.confidence_score < max_confidence)
    stmt = stmt.order_by(CleanedRecord.record_id).limit(capped_limit).offset(offset)
    return list(db.scalars(stmt).all())


def get_feedback_correction(db: Session, *, column_type: str, original_value: str) -> FeedbackCorrection | None:
    stmt = select(FeedbackCorrection).where(
        FeedbackCorrection.column_type == column_type,
        FeedbackCorrection.original_value == original_value,
    )
    return db.scalars(stmt).first()


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
            db.refresh(existing)
            return existing

        correction = FeedbackCorrection(
            column_type=column_type,
            original_value=original_value,
            agent_output=agent_output,
            corrected_value=corrected_value,
        )
        db.add(correction)
        db.commit()
        db.refresh(correction)
        return correction
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError("Could not save feedback correction.") from exc


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
    db.refresh(entry)
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
