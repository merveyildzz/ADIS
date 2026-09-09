"""Custom rule evaluation — no general-purpose code execution (Python's
built-in dynamic-evaluation functions) is ever applied to user-submitted
rule logic, anywhere in this module. The entire evaluation surface is the
fixed dict below: adding an operator means adding a dict entry, never
accepting arbitrary code.

Rules are evaluated against already-*cleaned* values (agents run first,
then rules apply to their output) — this module never sees raw/original
values, and has no DB access of its own, so it's trivially unit-testable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from app.db.models import CustomRule, RuleTargetKind


@dataclass(frozen=True)
class RuleViolation:
    rule_id: int
    column_name: str
    row_index: int
    cleaned_value: str | None
    severity: str
    action: str


def _to_float(value: Any) -> float:
    return float(value)


_SAFE_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "gte": lambda v, target: _to_float(v) >= target,
    "lte": lambda v, target: _to_float(v) <= target,
    "eq": lambda v, target: str(v) == str(target),
    "in": lambda v, target: str(v) in target,
    "regex_match": lambda v, target: re.match(target, str(v)) is not None,
    "not_null": lambda v, _target: v is not None and str(v).strip() != "",
}


def evaluate_condition(cleaned_value: str | None, operator: str, condition_value: Any) -> bool:
    """Returns True if the value VIOLATES the rule (fails the check).
    Never raises: a value that can't be evaluated against this operator
    (e.g. non-numeric text against `gte`) is conservatively treated as a
    violation rather than crashing the batch, consistent with
    `safe_clean_row`'s per-row-safety philosophy."""
    fn = _SAFE_OPERATORS[operator]
    try:
        satisfies = fn(cleaned_value, condition_value)
    except (ValueError, TypeError, re.error):
        return True
    return not satisfies


def rules_for_column(rules: list[CustomRule], *, column_name: str, detected_type: str | None) -> list[CustomRule]:
    return [
        r for r in rules
        if r.is_active and (
            (r.target_kind == RuleTargetKind.COLUMN_NAME and r.target_value == column_name)
            or (r.target_kind == RuleTargetKind.DETECTED_TYPE and detected_type is not None and r.target_value == detected_type)
        )
    ]


def evaluate_rules_for_upload(
    all_rules: list[CustomRule],
    cleaned_columns: dict[str, tuple[str | None, list]],
) -> list[RuleViolation]:
    """`cleaned_columns` maps column_name -> (detected_type, cleaned_values)
    — the exact shape `pipeline.py` already builds as
    `cleaned_columns_for_analysis`. A rule matching zero columns simply
    produces zero violations; that's success, not an error."""
    violations: list[RuleViolation] = []
    for column_name, (detected_type, values) in cleaned_columns.items():
        matching = rules_for_column(all_rules, column_name=column_name, detected_type=detected_type)
        if not matching:
            continue
        for row_idx, value in enumerate(values):
            for rule in matching:
                condition_value = _decode_condition_value(rule.condition_value)
                if evaluate_condition(value, rule.condition_operator, condition_value):
                    violations.append(RuleViolation(
                        rule_id=rule.rule_id, column_name=column_name, row_index=row_idx,
                        cleaned_value=value, severity=rule.severity, action=rule.action.value,
                    ))
    return violations


def _decode_condition_value(raw: str | None) -> Any:
    if raw is None:
        return None
    return json.loads(raw)
