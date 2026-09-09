"""Creation-time validation for custom rules — rejects rules referencing
non-existent columns/types and rejects a narrow, unambiguous class of
conflicting conditions (numeric-range contradictions between gte/lte rules
on the same target). Structured as Pydantic models so FastAPI gets request
validation for free; this is deliberately not a general constraint solver.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel

# The fixed vocabulary of detected types a rule may target via
# target_kind=DETECTED_TYPE — mirrors column_detection.py's TYPE_TO_AGENT
# keys plus "categorical" (a profiling-only type).
KNOWN_DETECTED_TYPES = {
    "email", "phone", "date", "currency", "quantity", "address", "numeric_age", "categorical",
}

_NUMERIC_OPERATORS = {"gte", "lte"}


class RuleCreateIn(BaseModel):
    name: str
    target_kind: Literal["column_name", "detected_type"]
    target_value: str
    condition_operator: Literal["gte", "lte", "eq", "in", "regex_match", "not_null"]
    condition_value: float | int | str | list[str] | None = None
    action: Literal["flag", "reject"] = "flag"
    severity: Literal["low", "medium", "high"] = "medium"


def validate_rule_definition(
    rule: RuleCreateIn, *, known_columns: set[str] | None = None, existing_rules: list | None = None
) -> list[str]:
    """Returns a list of human-readable validation errors (empty = valid).

    `known_columns`, when provided, is the current dataset's column set —
    a target_kind=column_name rule is only checked against it when given;
    with no dataset context (a rule created from a dataset-agnostic screen)
    a column-name rule is accepted, since it may legitimately apply to a
    future upload not yet seen. `existing_rules` (CustomRule ORM rows) is
    used for the narrow numeric-range conflict check below.
    """
    errors: list[str] = []

    if rule.target_kind == "detected_type" and rule.target_value not in KNOWN_DETECTED_TYPES:
        errors.append(
            f"Unknown detected type '{rule.target_value}'. Expected one of: "
            f"{', '.join(sorted(KNOWN_DETECTED_TYPES))}."
        )
    if rule.target_kind == "column_name" and known_columns is not None and rule.target_value not in known_columns:
        errors.append(f"Column '{rule.target_value}' does not exist in this dataset.")

    if rule.condition_operator in _NUMERIC_OPERATORS and not isinstance(rule.condition_value, (int, float)):
        errors.append(f"Operator '{rule.condition_operator}' requires a numeric value.")
    if rule.condition_operator == "in" and not isinstance(rule.condition_value, list):
        errors.append("Operator 'in' requires a list of allowed values.")
    if rule.condition_operator == "regex_match":
        if not isinstance(rule.condition_value, str):
            errors.append("Operator 'regex_match' requires a string pattern.")
        else:
            try:
                re.compile(rule.condition_value)
            except re.error as exc:
                errors.append(f"Invalid regex: {exc}")
    if rule.condition_operator == "not_null" and rule.condition_value is not None:
        errors.append("Operator 'not_null' takes no condition_value.")

    if not errors and rule.condition_operator in _NUMERIC_OPERATORS and existing_rules:
        errors.extend(_find_numeric_range_conflicts(rule, existing_rules))

    return errors


def _find_numeric_range_conflicts(rule: RuleCreateIn, existing_rules: list) -> list[str]:
    """The only conflict shape with unambiguous "these can never both pass"
    semantics: a `gte X` and a `lte Y` on the same target where X > Y means
    no value can satisfy both simultaneously."""
    conflicts: list[str] = []
    same_target = [
        r for r in existing_rules
        if r.is_active and r.target_kind.value == rule.target_kind and r.target_value == rule.target_value
        and r.condition_operator in _NUMERIC_OPERATORS and r.condition_operator != rule.condition_operator
    ]
    for other in same_target:
        other_value = float(_decode(other.condition_value))
        if rule.condition_operator == "gte" and other.condition_operator == "lte" and rule.condition_value > other_value:
            conflicts.append(
                f"Conflicts with existing rule '{other.name}': gte {rule.condition_value} "
                f"can never be satisfied together with lte {other_value}."
            )
        if rule.condition_operator == "lte" and other.condition_operator == "gte" and rule.condition_value < other_value:
            conflicts.append(
                f"Conflicts with existing rule '{other.name}': lte {rule.condition_value} "
                f"can never be satisfied together with gte {other_value}."
            )
    return conflicts


def _decode(raw: str | None) -> Any:
    return json.loads(raw) if raw is not None else None
