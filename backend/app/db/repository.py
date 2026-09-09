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
from app.db.models import AppliedRule, AuditLog, CleanedRecord, CustomRule, FeedbackCorrection, RawUpload, RuleAction, RuleTargetKind, UploadStatus


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


def delete_raw_upload(db: Session, *, upload_id: int) -> None:
    """Deletes the upload and everything derived from it (cleaned_records,
    audit_log rows) via the ORM cascade already declared on RawUpload's
    relationships — mirrored at the DB level by each FK's ondelete=CASCADE,
    so this is a single consistent delete either way."""
    upload = db.get(RawUpload, upload_id)
    if upload is None:
        raise RecordNotFoundError(f"Upload {upload_id} does not exist.")
    try:
        db.delete(upload)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not delete upload {upload_id}.") from exc


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


# --- Custom rules -----------------------------------------------------------


def create_custom_rule(
    db: Session, *, name: str, target_kind: str, target_value: str, condition_operator: str,
    condition_value: str | None, action: str = "flag", severity: str = "medium",
) -> CustomRule:
    rule = CustomRule(
        name=name,
        target_kind=RuleTargetKind(target_kind),
        target_value=target_value,
        condition_operator=condition_operator,
        condition_value=condition_value,
        action=RuleAction(action),
        severity=severity,
    )
    db.add(rule)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not create rule '{name}'.") from exc
    return rule


def list_custom_rules(db: Session, *, active_only: bool = False) -> list[CustomRule]:
    stmt = select(CustomRule).order_by(CustomRule.created_at.desc())
    if active_only:
        stmt = stmt.where(CustomRule.is_active.is_(True))
    return list(db.scalars(stmt).all())


def list_active_rules(db: Session) -> list[CustomRule]:
    """Used by the cleaning pipeline — every currently-active rule,
    evaluated against each upload's cleaned values."""
    return list_custom_rules(db, active_only=True)


def get_custom_rule(db: Session, *, rule_id: int) -> CustomRule | None:
    return db.get(CustomRule, rule_id)


def update_custom_rule(db: Session, *, rule_id: int, **fields: Any) -> CustomRule:
    rule = db.get(CustomRule, rule_id)
    if rule is None:
        raise RecordNotFoundError(f"Rule {rule_id} does not exist.")
    for key, value in fields.items():
        if key == "target_kind" and value is not None:
            value = RuleTargetKind(value)
        if key == "action" and value is not None:
            value = RuleAction(value)
        if value is not None:
            setattr(rule, key, value)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not update rule {rule_id}.") from exc
    return rule


def delete_custom_rule(db: Session, *, rule_id: int) -> None:
    """Soft delete: flips is_active off rather than removing the row, so a
    past violation's audit_log.details["rule_id"] stays resolvable."""
    rule = db.get(CustomRule, rule_id)
    if rule is None:
        raise RecordNotFoundError(f"Rule {rule_id} does not exist.")
    rule.is_active = False
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not delete rule {rule_id}.") from exc


def insert_rule_violation_audit_logs(
    db: Session, *, upload_id: int, record_ids_by_column_row: dict[tuple[str, int], int], violations: Sequence
) -> None:
    """Writes each rule violation as an AuditLog row on the SAME record_id
    an agent's cleaning decision already attached to that cell — this is
    what makes a violation show up in get_lineage_for_record with zero new
    query logic: it's just another lineage event for that cell."""
    if not violations:
        return
    try:
        db.add_all([
            AuditLog(
                upload_id=upload_id,
                record_id=record_ids_by_column_row.get((v.column_name, v.row_index)),
                agent_name="RuleEngine",
                action="rule_violation",
                details=json.dumps({
                    "rule_id": v.rule_id, "severity": v.severity, "action": v.action,
                    "column": v.column_name,
                }),
            )
            for v in violations
        ])
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not save rule violations for upload {upload_id}.") from exc


def list_rule_violations_for_upload(db: Session, *, upload_id: int) -> list[AuditLog]:
    stmt = (
        select(AuditLog)
        .where(AuditLog.upload_id == upload_id, AuditLog.agent_name == "RuleEngine")
        .order_by(AuditLog.log_id)
    )
    return list(db.scalars(stmt).all())


def list_applied_rule_ids_for_upload(db: Session, *, upload_id: int) -> list[int]:
    """Which rule *definitions* are currently turned on for this specific
    upload — what the Rules screen's checkboxes restore to when reopened."""
    stmt = select(AppliedRule.rule_id).where(AppliedRule.upload_id == upload_id)
    return list(db.scalars(stmt).all())


def _delete_rule_violations_for_rule(db: Session, *, upload_id: int, rule_id: int) -> None:
    """Removes a rule's previously-recorded violations for this upload —
    used when the rule is unapplied, so unchecking it doesn't leave stale
    flags behind. `rule_id` lives inside AuditLog.details (JSON text), not
    a real column, so this filters in Python after a bounded fetch — the
    same approach list_rule_violations_for_upload already uses."""
    stmt = select(AuditLog).where(AuditLog.upload_id == upload_id, AuditLog.agent_name == "RuleEngine")
    for entry in db.scalars(stmt).all():
        if not entry.details:
            continue
        try:
            details = json.loads(entry.details)
        except (TypeError, ValueError):
            continue
        if details.get("rule_id") == rule_id:
            db.delete(entry)


def set_applied_rules_for_upload(
    db: Session, *, upload_id: int, rule_ids: Sequence[int]
) -> tuple[set[int], set[int]]:
    """Replaces the full set of rules applied to this upload with exactly
    `rule_ids`. Returns (newly_added, newly_removed) so the caller knows
    which rules actually need (re-)evaluating — unapplying a rule also
    deletes its previously-recorded violations for this upload."""
    existing_rows = {
        r.rule_id: r for r in db.scalars(
            select(AppliedRule).where(AppliedRule.upload_id == upload_id)
        ).all()
    }
    existing = set(existing_rows.keys())
    desired = set(rule_ids)
    to_add = desired - existing
    to_remove = existing - desired

    try:
        for rid in to_remove:
            db.delete(existing_rows[rid])
            _delete_rule_violations_for_rule(db, upload_id=upload_id, rule_id=rid)
        for rid in to_add:
            db.add(AppliedRule(upload_id=upload_id, rule_id=rid))
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise DatabaseWriteError(f"Could not update applied rules for upload {upload_id}.") from exc
    return to_add, to_remove


def get_record_ids_with_rule_violations(db: Session, *, record_ids: Sequence[int]) -> set[int]:
    """Batch-loads which of the given record_ids have at least one
    RuleEngine violation — used by get_cleaned_records to annotate a page
    of results without an N+1 query or a per-row correlated subquery."""
    if not record_ids:
        return set()
    stmt = (
        select(AuditLog.record_id)
        .where(AuditLog.agent_name == "RuleEngine", AuditLog.record_id.in_(record_ids))
        .distinct()
    )
    return {rid for rid in db.scalars(stmt).all() if rid is not None}
