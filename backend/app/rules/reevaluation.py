"""Retroactive rule evaluation. Without this, a rule only ever affects
uploads cleaned *after* it was created — every upload that existed before
would look "clean" against a rule it was never actually checked against,
which is misleading rather than merely incomplete. This closes that gap:
right when a new rule is created, it's immediately evaluated against every
already-completed upload's stored `cleaned_records`, using the exact same
pure evaluation function (`evaluate_rules_for_upload`) and audit-log write
path (`insert_rule_violation_audit_logs`) the live cleaning pipeline uses —
retroactive evaluation is not a separate mechanism, just a different
trigger for the same one.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db import repository
from app.db.models import CustomRule, UploadStatus
from app.rules.engine import evaluate_rules_for_upload

logger = logging.getLogger("rules.reevaluation")

# Bounded but generous — mirrors the "unpaginated but still bounded" reads
# already used elsewhere (e.g. list_cleaned_records_for_columns) rather
# than adding pagination for what is, at this project's scale, a small list.
MAX_UPLOADS_TO_REEVALUATE = 100_000


def _group_records_by_column(records) -> tuple[dict[str, tuple], dict[tuple[str, int], int]]:
    """Rebuilds the same (column_name -> (column_type, cleaned_values)) shape
    `pipeline.py` builds live during cleaning, and the (column_name, row_index)
    -> record_id map `insert_rule_violation_audit_logs` needs — from already-
    persisted `cleaned_records` rows instead of an in-memory cleaning run."""
    by_column: dict[str, tuple] = {}
    record_ids_by_column_row: dict[tuple[str, int], int] = {}
    for r in records:
        record_ids_by_column_row[(r.column_name, r.row_index)] = r.record_id
        column_type, by_row = by_column.get(r.column_name, (r.column_type, {}))
        by_row[r.row_index] = r.cleaned_value
        by_column[r.column_name] = (column_type, by_row)

    cleaned_columns: dict[str, tuple] = {}
    for column_name, (column_type, by_row) in by_column.items():
        max_row = max(by_row.keys())
        cleaned_columns[column_name] = (column_type, [by_row.get(i) for i in range(max_row + 1)])
    return cleaned_columns, record_ids_by_column_row


def reevaluate_rule_against_existing_uploads(db: Session, *, rule: CustomRule) -> int:
    """Returns the number of violations written. Never raises — a failure
    here must not fail the rule-creation request that triggered it; the
    rule itself is already saved regardless of whether this succeeds. A
    failure for one upload doesn't stop the others from being checked."""
    total_violations = 0
    try:
        uploads = repository.list_raw_uploads(db, limit=MAX_UPLOADS_TO_REEVALUATE)
    except Exception:
        logger.exception("Could not list uploads for retroactive evaluation of rule %s.", rule.rule_id)
        return 0

    for upload in uploads:
        if upload.status != UploadStatus.COMPLETED:
            continue
        try:
            records = repository.list_all_cleaned_records_for_upload(db, upload_id=upload.upload_id)
            if not records:
                continue
            cleaned_columns, record_ids_by_column_row = _group_records_by_column(records)
            violations = evaluate_rules_for_upload([rule], cleaned_columns)
            if violations:
                repository.insert_rule_violation_audit_logs(
                    db, upload_id=upload.upload_id,
                    record_ids_by_column_row=record_ids_by_column_row,
                    violations=violations,
                )
                total_violations += len(violations)
        except Exception:
            logger.exception(
                "Retroactive evaluation of rule %s failed for upload %s; other uploads are unaffected.",
                rule.rule_id, upload.upload_id,
            )
    return total_violations
