"""Rules are reusable, dataset-independent definitions — whether one
actually applies to a given upload is a separate, explicit choice
(`repository.set_applied_rules_for_upload`). These tests cover both that
choice (the applied_rules join) and the evaluation it triggers
(`apply_rules_to_upload`).
"""
import pandas as pd
import pytest
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import repository as repo
from app.db.base import Base, create_app_engine
from app.orchestrator.orchestrator import build_routing_plan
from app.pipeline import run_cleaning_pipeline
from app.rules.reevaluation import apply_rules_to_upload


@pytest.fixture()
def session(tmp_path):
    engine = create_app_engine(Settings(database_url=f"sqlite:///{tmp_path}/test.db"))
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    yield db
    db.close()
    engine.dispose()


def _upload_and_clean(session, df, filename="test.csv"):
    upload = repo.create_raw_upload(session, filename=filename, row_count=len(df))
    plan = build_routing_plan(df, upload_id=upload.upload_id)
    run_cleaning_pipeline(session, upload_id=upload.upload_id, df=df, routing_plan=plan)
    return upload


# --- A fresh upload starts with zero rules applied ---------------------------


def test_fresh_upload_has_zero_rule_violations_even_when_matching_rules_exist(session):
    df = pd.DataFrame({"customer_age": ["-5"]})
    upload = _upload_and_clean(session, df)

    repo.create_custom_rule(
        session, name="age not negative", target_kind="column_name", target_value="customer_age",
        condition_operator="gte", condition_value="0",
    )

    # Creating a rule must not retroactively touch any upload on its own.
    assert repo.list_applied_rule_ids_for_upload(session, upload_id=upload.upload_id) == []
    assert repo.list_rule_violations_for_upload(session, upload_id=upload.upload_id) == []


# --- apply_rules_to_upload (the evaluation itself) ----------------------------


def test_apply_rules_to_upload_writes_violations(session):
    df = pd.DataFrame({"customer_age": ["30", "-5", "45"]})
    upload = _upload_and_clean(session, df)
    rule = repo.create_custom_rule(
        session, name="age not negative", target_kind="column_name", target_value="customer_age",
        condition_operator="gte", condition_value="0",
    )

    written = apply_rules_to_upload(session, upload_id=upload.upload_id, rules=[rule])
    assert written == 1
    assert len(repo.list_rule_violations_for_upload(session, upload_id=upload.upload_id)) == 1


def test_apply_rules_to_upload_with_empty_rule_list_is_a_noop(session):
    df = pd.DataFrame({"customer_age": ["-5"]})
    upload = _upload_and_clean(session, df)
    written = apply_rules_to_upload(session, upload_id=upload.upload_id, rules=[])
    assert written == 0


def test_apply_rules_to_upload_matching_nothing_writes_zero_and_does_not_error(session):
    df = pd.DataFrame({"customer_age": ["30"]})
    upload = _upload_and_clean(session, df)
    rule = repo.create_custom_rule(
        session, name="unrelated column", target_kind="column_name", target_value="does_not_exist",
        condition_operator="not_null", condition_value=None,
    )
    written = apply_rules_to_upload(session, upload_id=upload.upload_id, rules=[rule])
    assert written == 0


def test_apply_rules_to_upload_via_detected_type_applies_regardless_of_column_name(session):
    df = pd.DataFrame({"applicant_age": ["-3"]})
    upload = _upload_and_clean(session, df)
    rule = repo.create_custom_rule(
        session, name="no negative ages anywhere", target_kind="detected_type", target_value="numeric_age",
        condition_operator="gte", condition_value="0",
    )
    written = apply_rules_to_upload(session, upload_id=upload.upload_id, rules=[rule])
    assert written == 1


# --- set_applied_rules_for_upload (which rules are turned on) ----------------


def test_set_applied_rules_for_upload_persists_the_selection(session):
    df = pd.DataFrame({"customer_age": ["30"]})
    upload = _upload_and_clean(session, df)
    rule = repo.create_custom_rule(
        session, name="age not negative", target_kind="column_name", target_value="customer_age",
        condition_operator="gte", condition_value="0",
    )

    to_add, to_remove = repo.set_applied_rules_for_upload(session, upload_id=upload.upload_id, rule_ids=[rule.rule_id])
    assert to_add == {rule.rule_id}
    assert to_remove == set()
    assert repo.list_applied_rule_ids_for_upload(session, upload_id=upload.upload_id) == [rule.rule_id]


def test_set_applied_rules_for_upload_removes_deselected_rules_and_their_violations(session):
    df = pd.DataFrame({"customer_age": ["-5"]})
    upload = _upload_and_clean(session, df)
    rule = repo.create_custom_rule(
        session, name="age not negative", target_kind="column_name", target_value="customer_age",
        condition_operator="gte", condition_value="0",
    )
    repo.set_applied_rules_for_upload(session, upload_id=upload.upload_id, rule_ids=[rule.rule_id])
    apply_rules_to_upload(session, upload_id=upload.upload_id, rules=[rule])
    assert len(repo.list_rule_violations_for_upload(session, upload_id=upload.upload_id)) == 1

    # Unchecking the rule for this upload removes both the selection and
    # the stale violations it produced.
    to_add, to_remove = repo.set_applied_rules_for_upload(session, upload_id=upload.upload_id, rule_ids=[])
    assert to_add == set()
    assert to_remove == {rule.rule_id}
    assert repo.list_applied_rule_ids_for_upload(session, upload_id=upload.upload_id) == []
    assert repo.list_rule_violations_for_upload(session, upload_id=upload.upload_id) == []


def test_set_applied_rules_is_scoped_per_upload(session):
    df = pd.DataFrame({"customer_age": ["30"]})
    upload_a = _upload_and_clean(session, df, filename="a.csv")
    upload_b = _upload_and_clean(session, df, filename="b.csv")
    rule = repo.create_custom_rule(
        session, name="age not negative", target_kind="column_name", target_value="customer_age",
        condition_operator="gte", condition_value="0",
    )

    repo.set_applied_rules_for_upload(session, upload_id=upload_a.upload_id, rule_ids=[rule.rule_id])

    assert repo.list_applied_rule_ids_for_upload(session, upload_id=upload_a.upload_id) == [rule.rule_id]
    assert repo.list_applied_rule_ids_for_upload(session, upload_id=upload_b.upload_id) == []
