"""User-triggered rule application. `CustomRule` rows are reusable, dataset-
independent *definitions* (see `db/models.py`) — but a rule matching some
column's name or detected type is not, by itself, a reason to enforce it on
every dataset that happens to have such a column. Which rules actually
apply to a given upload is an explicit, per-upload choice the user makes on
the Rules screen (see `db/repository.py`'s `set_applied_rules_for_upload`).

This module evaluates a chosen set of rules against one upload's already-
persisted `cleaned_records`, using the exact same pure evaluation function
(`evaluate_rules_for_upload`) and audit-log write path
(`insert_rule_violation_audit_logs`) the live cleaning pipeline would use —
applying rules after the fact is not a separate mechanism, just a different
trigger for the same one.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db import repository
from app.db.models import CustomRule
from app.rules.engine import evaluate_rules_for_upload

logger = logging.getLogger("rules.application")


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


def apply_rules_to_upload(db: Session, *, upload_id: int, rules: list[CustomRule]) -> int:
    """Evaluates exactly `rules` against `upload_id`'s stored cleaned_records
    and writes any violations. Returns the number of violations written.
    Never raises — a failure here must not fail the API request that
    triggered it (which rules are marked "applied" is already committed by
    the caller either way); it just means violations don't get recorded."""
    if not rules:
        return 0
    try:
        records = repository.list_all_cleaned_records_for_upload(db, upload_id=upload_id)
        if not records:
            return 0
        cleaned_columns, record_ids_by_column_row = _group_records_by_column(records)
        violations = evaluate_rules_for_upload(rules, cleaned_columns)
        if violations:
            repository.insert_rule_violation_audit_logs(
                db, upload_id=upload_id,
                record_ids_by_column_row=record_ids_by_column_row,
                violations=violations,
            )
        return len(violations)
    except Exception:
        logger.exception("Applying rules to upload %s failed.", upload_id)
        return 0
