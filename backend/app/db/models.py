"""ORM models — the normalized schema from the Phase 2 roadmap section.

Every column that can hold uploaded-file content (names, addresses, free
text) is a plain String/Text column read/written exclusively through the
ORM. No raw SQL is built anywhere in this codebase.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UploadStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class RawUpload(Base):
    __tablename__ = "raw_uploads"

    upload_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    row_count: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[UploadStatus] = mapped_column(
        SAEnum(UploadStatus, native_enum=False, length=20), default=UploadStatus.PENDING, nullable=False
    )

    cleaned_records: Mapped[list["CleanedRecord"]] = relationship(
        back_populates="upload", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="upload", cascade="all, delete-orphan"
    )


class CleanedRecord(Base):
    __tablename__ = "cleaned_records"
    __table_args__ = (
        CheckConstraint("confidence_score >= 0 AND confidence_score <= 100", name="ck_confidence_score_range"),
        Index("ix_cleaned_records_upload_column", "upload_id", "column_name"),
        Index("ix_cleaned_records_upload_confidence", "upload_id", "confidence_score"),
    )

    record_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("raw_uploads.upload_id", ondelete="CASCADE"), nullable=False)
    column_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleaned_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    agent_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # The fine-grained value type the Orchestrator detected (e.g. "phone" vs
    # "email" — both handled by ContactAgent). Shares its vocabulary with
    # feedback_corrections.column_type so Phase 6's lookup can join them.
    column_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    upload: Mapped["RawUpload"] = relationship(back_populates="cleaned_records")


class FeedbackCorrection(Base):
    __tablename__ = "feedback_corrections"
    __table_args__ = (
        # One stored correction per distinct (column_type, original_value):
        # Phase 6's lookup-before-LLM path relies on this being unique so a
        # repeat correction upserts instead of accumulating duplicates.
        UniqueConstraint("column_type", "original_value", name="uq_feedback_column_type_original_value"),
    )

    feedback_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    column_type: Mapped[str] = mapped_column(String(100), nullable=False)
    original_value: Mapped[str] = mapped_column(Text, nullable=False)
    agent_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_upload_id", "upload_id"),
        Index("ix_audit_log_record_id", "record_id"),
    )

    log_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("raw_uploads.upload_id", ondelete="CASCADE"), nullable=False)
    # Nullable: some audit events are column- or upload-level (e.g. the
    # Orchestrator's routing decision), not tied to one cleaned_records row.
    # Set whenever an event *is* about one specific cell — this is what
    # Phase 5's per-cell lineage drill-down queries on.
    record_id: Mapped[int | None] = mapped_column(
        ForeignKey("cleaned_records.record_id", ondelete="CASCADE"), nullable=True
    )
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    upload: Mapped["RawUpload"] = relationship(back_populates="audit_logs")
