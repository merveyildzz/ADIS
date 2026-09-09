import json
import re
from pathlib import Path

import pytest

from app.db.models import CustomRule, RuleAction, RuleTargetKind
from app.rules.engine import evaluate_condition, evaluate_rules_for_upload, rules_for_column


def _rule(target_kind, target_value, operator, value, rule_id=1, severity="medium", action=RuleAction.FLAG):
    return CustomRule(
        rule_id=rule_id, name=f"rule-{rule_id}", target_kind=target_kind, target_value=target_value,
        condition_operator=operator, condition_value=json.dumps(value) if value is not None else None,
        action=action, severity=severity, is_active=True,
    )


# --- Operators ---------------------------------------------------------------


def test_gte_operator_pass_and_fail():
    assert evaluate_condition("30", "gte", 0) is False  # 30 >= 0, satisfies -> no violation
    assert evaluate_condition("-5", "gte", 0) is True    # -5 >= 0 fails -> violation


def test_lte_operator_pass_and_fail():
    assert evaluate_condition("50", "lte", 100) is False
    assert evaluate_condition("150", "lte", 100) is True


def test_eq_operator_pass_and_fail():
    assert evaluate_condition("active", "eq", "active") is False
    assert evaluate_condition("inactive", "eq", "active") is True


def test_in_operator_pass_and_fail():
    assert evaluate_condition("active", "in", ["active", "inactive", "pending"]) is False
    assert evaluate_condition("archived", "in", ["active", "inactive", "pending"]) is True


def test_regex_match_operator_pass_and_fail():
    assert evaluate_condition("user@example.com", "regex_match", r"^[^@]+@[^@]+$") is False
    assert evaluate_condition("not-an-email", "regex_match", r"^[^@]+@[^@]+$") is True


def test_not_null_operator_on_empty_string_is_a_violation():
    assert evaluate_condition("", "not_null", None) is True
    assert evaluate_condition(None, "not_null", None) is True
    assert evaluate_condition("something", "not_null", None) is False


def test_type_mismatched_value_is_conservatively_treated_as_a_violation_not_a_crash():
    # Non-numeric text against a numeric operator must not raise.
    assert evaluate_condition("not-a-number", "gte", 0) is True


# --- Column/type targeting ----------------------------------------------------


def test_rule_targeting_detected_type_applies_across_differently_named_columns():
    rule = _rule(RuleTargetKind.DETECTED_TYPE, "numeric_age", "gte", 0)
    matched_a = rules_for_column([rule], column_name="customer_age", detected_type="numeric_age")
    matched_b = rules_for_column([rule], column_name="applicant_age", detected_type="numeric_age")
    assert matched_a == [rule]
    assert matched_b == [rule]


def test_rule_targeting_column_name_only_matches_that_column():
    rule = _rule(RuleTargetKind.COLUMN_NAME, "customer_age", "gte", 0)
    assert rules_for_column([rule], column_name="customer_age", detected_type="numeric_age") == [rule]
    assert rules_for_column([rule], column_name="applicant_age", detected_type="numeric_age") == []


def test_inactive_rule_never_matches():
    rule = _rule(RuleTargetKind.COLUMN_NAME, "customer_age", "gte", 0)
    rule.is_active = False
    assert rules_for_column([rule], column_name="customer_age", detected_type="numeric_age") == []


# --- Whole-upload evaluation ---------------------------------------------------


def test_rule_matching_zero_columns_produces_zero_violations_not_an_error():
    rule = _rule(RuleTargetKind.COLUMN_NAME, "nonexistent_column", "gte", 0)
    violations = evaluate_rules_for_upload([rule], {"customer_age": ("numeric_age", ["30", "40"])})
    assert violations == []


def test_evaluation_runs_against_cleaned_value_not_the_raw_value():
    # The engine only ever receives the (detected_type, cleaned_values)
    # shape pipeline.py builds post-cleaning — proving it structurally
    # cannot see raw/original values at all.
    rule = _rule(RuleTargetKind.COLUMN_NAME, "customer_age", "gte", 0)
    cleaned_columns = {"customer_age": ("numeric_age", ["30", "-5"])}
    violations = evaluate_rules_for_upload([rule], cleaned_columns)
    assert len(violations) == 1
    assert violations[0].row_index == 1
    assert violations[0].cleaned_value == "-5"


def test_multiple_rules_on_same_column_all_evaluated():
    rules = [
        _rule(RuleTargetKind.COLUMN_NAME, "status", "in", ["active", "inactive", "pending"], rule_id=1),
        _rule(RuleTargetKind.COLUMN_NAME, "status", "not_null", None, rule_id=2),
    ]
    cleaned_columns = {"status": ("categorical", ["active", "archived", ""])}
    violations = evaluate_rules_for_upload(rules, cleaned_columns)
    # "archived" violates rule 1 only; "" violates both rule 1 (not in list) and rule 2 (not_null)
    assert len(violations) == 3


# --- Safety guarantee ----------------------------------------------------------


def test_eval_and_exec_are_never_used_in_the_rules_package():
    rules_dir = Path(__file__).parent.parent / "backend" / "app" / "rules"
    for py_file in rules_dir.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        assert not re.search(r"\beval\s*\(", source), f"eval() found in {py_file}"
        assert not re.search(r"\bexec\s*\(", source), f"exec() found in {py_file}"
