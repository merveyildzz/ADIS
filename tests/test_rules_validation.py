import json

from app.db.models import CustomRule, RuleAction, RuleTargetKind
from app.rules.validation import RuleCreateIn, validate_rule_definition


def _existing(name, target_kind, target_value, operator, value, rule_id=1):
    return CustomRule(
        rule_id=rule_id, name=name, target_kind=target_kind, target_value=target_value,
        condition_operator=operator, condition_value=json.dumps(value), action=RuleAction.FLAG,
        severity="medium", is_active=True,
    )


def test_valid_rule_has_no_errors():
    rule = RuleCreateIn(
        name="age not negative", target_kind="column_name", target_value="customer_age",
        condition_operator="gte", condition_value=0,
    )
    assert validate_rule_definition(rule) == []


def test_unknown_detected_type_is_rejected():
    rule = RuleCreateIn(
        name="bad type", target_kind="detected_type", target_value="not_a_real_type",
        condition_operator="not_null",
    )
    errors = validate_rule_definition(rule)
    assert any("Unknown detected type" in e for e in errors)


def test_unknown_column_name_is_rejected_when_dataset_context_given():
    rule = RuleCreateIn(
        name="typo column", target_kind="column_name", target_value="customr_age",
        condition_operator="not_null",
    )
    errors = validate_rule_definition(rule, known_columns={"customer_age", "email"})
    assert any("does not exist" in e for e in errors)


def test_column_name_rule_accepted_with_no_dataset_context():
    # May legitimately apply to a future upload not yet seen.
    rule = RuleCreateIn(
        name="future column", target_kind="column_name", target_value="not_seen_yet",
        condition_operator="not_null",
    )
    assert validate_rule_definition(rule, known_columns=None) == []


def test_gte_with_non_numeric_value_is_rejected():
    rule = RuleCreateIn(
        name="bad value", target_kind="column_name", target_value="age",
        condition_operator="gte", condition_value="not a number",
    )
    errors = validate_rule_definition(rule)
    assert any("requires a numeric value" in e for e in errors)


def test_in_without_a_list_is_rejected():
    rule = RuleCreateIn(
        name="bad in", target_kind="column_name", target_value="status",
        condition_operator="in", condition_value="active",
    )
    errors = validate_rule_definition(rule)
    assert any("requires a list" in e for e in errors)


def test_invalid_regex_is_rejected():
    rule = RuleCreateIn(
        name="bad regex", target_kind="column_name", target_value="email",
        condition_operator="regex_match", condition_value="[unclosed",
    )
    errors = validate_rule_definition(rule)
    assert any("Invalid regex" in e for e in errors)


def test_not_null_with_a_value_is_rejected():
    rule = RuleCreateIn(
        name="bad not_null", target_kind="column_name", target_value="email",
        condition_operator="not_null", condition_value="something",
    )
    errors = validate_rule_definition(rule)
    assert any("takes no condition_value" in e for e in errors)


def test_numeric_range_conflict_is_detected():
    existing = [_existing("min age", RuleTargetKind.COLUMN_NAME, "age", "lte", 50)]
    new_rule = RuleCreateIn(
        name="max age", target_kind="column_name", target_value="age",
        condition_operator="gte", condition_value=100,
    )
    errors = validate_rule_definition(new_rule, existing_rules=existing)
    assert any("Conflicts with existing rule" in e for e in errors)


def test_non_overlapping_numeric_range_is_not_a_conflict():
    existing = [_existing("min age", RuleTargetKind.COLUMN_NAME, "age", "gte", 0)]
    new_rule = RuleCreateIn(
        name="max age", target_kind="column_name", target_value="age",
        condition_operator="lte", condition_value=120,
    )
    assert validate_rule_definition(new_rule, existing_rules=existing) == []
