"""ORM models — the normalized schema from the Phase 2 roadmap section.

Every column that can hold uploaded-file content (names, addresses, free
text) is a plain String/Text column read/written exclusively through the
ORM. No raw SQL is built anywhere in this codebase.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint
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


class RuleTargetKind(str, enum.Enum):
    COLUMN_NAME = "column_name"
    DETECTED_TYPE = "detected_type"


class RuleAction(str, enum.Enum):
    FLAG = "flag"
    # v1: recorded identically to FLAG (a stronger/high-severity marker,
    # not a data mutation or export block) — see custom_rules migration.
    REJECT = "reject"


class RawUpload(Base):
    __tablename__ = "raw_uploads"

    upload_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    row_count: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[UploadStatus] = mapped_column(
        SAEnum(UploadStatus, native_enum=False, length=20), default=UploadStatus.PENDING, nullable=False
    )
    # Phase 7 insight cards (correlation/trend/anomaly/narrative), computed
    # once right after cleaning — while the full in-memory dataset (including
    # columns no agent classified, e.g. `category`) is still available — and
    # cached here as JSON. Deterministic stats are cheap to recompute, but
    # the Narrative Agent's LLM call is not, so this also bounds LLM cost to
    # once per upload. Null until computed; the API must handle that as an
    # empty/not-yet-available state, never an error.
    insights_json: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    # 0-based position in the originally uploaded file. Lets Phase 7 rebuild
    # a wide (row x column) DataFrame from this long-format table — without
    # it there'd be no way to know that column A's 5th cleaned value and
    # column B's 5th cleaned value came from the same source row.
    row_index: Mapped[int] = mapped_column(nullable=False)
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


class CustomRule(Base):
    """A user-defined validation rule (e.g. "age cannot be negative"),
    evaluated against already-cleaned values (see app/rules/engine.py).

    Scoped to the upload it was created for (`upload_id`) — a rule defined
    while looking at one dataset (e.g. "Total_Sq.ft must be positive") has
    no meaning for a structurally different dataset with no such column,
    and showing it there was confusing rather than merely irrelevant. Every
    upload starts with zero rules; the user defines rules against the
    columns *this* dataset actually has, then explicitly applies them (see
    AppliedRule below) — nothing carries over from a previous upload.

    condition_value is stored as JSON text, the same convention already
    used by AuditLog.details / RawUpload.insights_json elsewhere in this
    schema. Evaluation never uses eval()/exec() — see app/rules/engine.py's
    fixed operator dispatch table."""

    __tablename__ = "custom_rules"
    __table_args__ = (
        Index("ix_custom_rules_target", "target_kind", "target_value"),
        Index("ix_custom_rules_upload_id", "upload_id"),
    )

    rule_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Nullable at the DB level only for schema flexibility (mirrors
    # AuditLog.record_id) — every rule created through the API always has
    # this set; there is no more "dataset-independent" rule.
    upload_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_uploads.upload_id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_kind: Mapped[RuleTargetKind] = mapped_column(
        SAEnum(RuleTargetKind, native_enum=False, length=20), nullable=False
    )
    # Either a literal column name (target_kind=COLUMN_NAME) or a detected
    # type string like "numeric_age" (target_kind=DETECTED_TYPE) — the
    # latter is what lets one rule generalize across differently-named
    # columns/datasets, e.g. "any numeric_age-typed column".
    target_value: Mapped[str] = mapped_column(String(255), nullable=False)
    condition_operator: Mapped[str] = mapped_column(String(20), nullable=False)
    condition_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[RuleAction] = mapped_column(
        SAEnum(RuleAction, native_enum=False, length=20), default=RuleAction.FLAG, nullable=False
    )
    severity: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    # Soft-delete: keeps historical audit_log.details["rule_id"] resolvable
    # even after a rule is "deleted" from the user's point of view.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AppliedRule(Base):
    """Which rule *definitions* (CustomRule is dataset-independent and
    reusable) are actually turned on for a *specific* upload. A rule
    matching a column by name or type doesn't mean every dataset with that
    column wants it enforced — a fresh upload starts with none applied;
    the user explicitly picks which existing rules apply to this file on
    the Rules screen. This is the join that makes that choice persistent
    (and lets the UI restore which checkboxes were checked)."""

    __tablename__ = "applied_rules"
    __table_args__ = (
        UniqueConstraint("upload_id", "rule_id", name="uq_applied_rules_upload_rule"),
        Index("ix_applied_rules_upload_id", "upload_id"),
    )

    applied_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("raw_uploads.upload_id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[int] = mapped_column(ForeignKey("custom_rules.rule_id", ondelete="CASCADE"), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
